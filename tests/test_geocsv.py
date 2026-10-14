from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import DATA, FIXTURES

from nav_qa_processing import geocsv, products, qc, rawnav

CREATED = datetime(2026, 10, 14, tzinfo=UTC)
SOURCE = geocsv.Source("TEST01", "doi:10.0/1", "doi:10.0/2", "Test operator", "")
DECIMALS = {"time": 2, "lon": 6, "lat": 6, "hdop": 1, "alt": 3}
R2R_FILES = {
    geocsv.BESTRES: "SP2613_bestres_head.geoCSV",
    geocsv.ONE_MINUTE: "SP2613_1min.geoCSV",
    geocsv.CONTROL: "SP2613_control.geoCSV",
}


def bestres_frame():
    times = pd.to_datetime(
        ["2026-07-09T15:11:57.5Z", "2026-07-09T15:11:58Z", "2026-07-09T15:11:58.5Z"],
        format="ISO8601",
    )
    return pd.DataFrame(
        {
            "time": times,
            "lon": [-117.233771, -117.233765, -117.233758],
            "lat": [32.702813, 32.702799, 32.702785],
            "quality": pd.array([2, 2, 0], dtype="Int64"),
            "nsat": pd.array([13, 13, 13], dtype="Int64"),
            "hdop": [0.8, 0.8, np.nan],
            "alt": [7.887, 7.76, 7.892],
            "sog": [3.31, 3.30, np.nan],
            "cog": [157.712, 157.601, np.nan],
            "flags": np.array([0, 0, int(qc.Flag.BAD_FIX)], dtype=np.uint16),
        }
    )


# -- Structure: R2R's --


@pytest.mark.parametrize("product", list(R2R_FILES))
def test_header_structure_is_r2rs(tmp_path, product):
    """Same keyword lines in the same order, title, field types, standard names and columns"""
    r2r = geocsv.read(FIXTURES / R2R_FILES[product])
    frame = bestres_frame()[[c for c, _ in product.columns] + ["flags"]]
    ours = geocsv.read(geocsv.write(tmp_path, product, frame, DECIMALS, SOURCE, CREATED))
    assert list(ours.keywords) == list(r2r.keywords)
    for key in ("dataset", "title", "field_type", "field_standard_name", "standard_name_cv",
                "ellipsoid", "delimiter", "field_missing"):  # fmt: skip
        assert ours.keywords[key] == r2r.keywords[key], key
    assert ours.columns == r2r.columns


@pytest.mark.parametrize("product", list(R2R_FILES))
def test_units_and_long_names_come_only_from_quantities(tmp_path, product):
    frame = bestres_frame()[[c for c, _ in product.columns]]
    keywords = geocsv.read(geocsv.write(tmp_path, product, frame, DECIMALS, SOURCE)).keywords
    quantities = [quantity for _, quantity in product.columns]
    assert keywords["field_unit"].split(",") == [x.geocsv_unit for x in quantities]
    assert keywords["field_long_name"].split(",") == [x.description for x in quantities]


def test_file_names():
    assert geocsv.file_name("SP2613", geocsv.ONE_MINUTE) == "SP2613_1min.geoCSV"


def test_source_values_and_creation_date(tmp_path):
    keywords = geocsv.read(
        geocsv.write(tmp_path, geocsv.CONTROL, bestres_frame(), DECIMALS, SOURCE, CREATED)
    ).keywords
    assert keywords["source_event"] == "doi:10.0/1" and keywords["source_dataset"] == "doi:10.0/2"
    assert keywords["attribution"] == "Test operator" and keywords["source_repository"] == ""
    assert (
        keywords["cruise_id"] == "TEST01" and keywords["creation_date"] == "2026-10-14T00:00:00Z"
    )


# -- Values --


def test_round_trip_with_a_flagged_record_and_missing_values(tmp_path):
    frame = bestres_frame()
    path = geocsv.write(tmp_path, geocsv.BESTRES, frame, DECIMALS, SOURCE, CREATED)
    lines = path.read_text().splitlines()
    assert lines[17] == "2026-07-09T15:11:57.50Z,-117.233771,32.702813,2,13,0.8,7.887,3.31,157.712"
    assert lines[18].endswith(",7.760,3.30,157.601")  # receiver precision, never trimmed
    # a flagged record: time through antenna height only, as R2R writes it (SP1925)
    assert lines[19] == "#2026-07-09T15:11:58.50Z,-117.233758,32.702785,0,13,NAN,7.892"
    back = geocsv.read(path).records
    assert back["flagged"].tolist() == [False, False, True]
    assert (back["iso_time"] == frame["time"]).all()
    np.testing.assert_allclose(back["ship_longitude"], frame["lon"])
    assert back["nmea_quality"].tolist() == [2, 2, 0]
    assert np.isnan(back["speed_made_good"][2]) and np.isnan(back["hdop"][2])


def test_reader_types_r2rs_columns():
    records = geocsv.read(FIXTURES / "SP2613_bestres_head.geoCSV").records
    assert str(records["iso_time"].dtype).startswith("datetime64")
    assert str(records["nsv"].dtype) == "Int64" and records["hdop"].dtype == float
    assert not records["flagged"].any() and len(records) == 23


# -- SP2613 against R2R's published products --


def test_sp2613_products_match_r2r(tmp_path):
    directory = DATA / "SP2613_169851_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            "SP2613 not downloaded; run: uv run scripts/fetch_r2r_fileset.py SP2613 169851"
        )
    nav = rawnav.read_fileset(
        sorted((directory / "data").iterdir()), "nav29",
        start=datetime(2026, 7, 9, tzinfo=UTC), end=datetime(2026, 7, 10, tzinfo=UTC),
    )  # fmt: skip
    best = products.bestres(nav, qc.flag(nav))
    one = products.sample(best)
    frames = {geocsv.BESTRES: best, geocsv.ONE_MINUTE: one, geocsv.CONTROL: products.control(one)}
    full_bestres = directory / "products" / "data" / "SP2613_bestres.geoCSV"
    for product, frame in frames.items():
        ours = geocsv.read(geocsv.write(tmp_path, product, frame, nav.decimals, SOURCE)).records
        theirs_path = full_bestres if product is geocsv.BESTRES else FIXTURES / R2R_FILES[product]
        theirs = geocsv.read(theirs_path).records
        assert len(ours) == len(theirs), product.name
        assert (ours["iso_time"] == theirs["iso_time"]).all()
        assert (ours["flagged"] == theirs["flagged"]).all()
        for name in ours.columns.drop(["iso_time", "flagged"]):
            mine, r2r = ours[name].to_numpy(dtype=float), theirs[name].to_numpy(dtype=float)
            if name == "speed_made_good":  # pyproj vs NavManager's Vincenty (test_geodesy.py)
                assert np.nanmax(np.abs(mine - r2r)) < 0.0101
            elif name == "course_made_good":
                # test_geodesy.py checks course closely: within 0.001° + 0.005°·m / step. Over
                # 1 m/s at 2 Hz, steps are at least 0.5 m, so 0.011°
                moving = ours["speed_made_good"].to_numpy(dtype=float) > 1.0
                turn = np.abs((mine - r2r + 180) % 360 - 180)[moving]
                assert turn.max() < 0.0111
            else:
                np.testing.assert_array_equal(mine, r2r, err_msg=f"{product.name} {name}")


# -- Flagged records, as R2R writes them (SP1925) --


def test_r2rs_flagged_records_have_only_the_measured_fields():
    """R2R writes a flagged bestres record as '#' + time through antenna height (SP1925)"""
    path = FIXTURES / "SP1925_bestres_head.geoCSV"
    flagged = [line for line in path.read_text().splitlines() if line[:2] == "#2"]
    assert len(flagged) == 3 and all(len(line.split(",")) == 7 for line in flagged)
    records = geocsv.read(path).records
    assert records["flagged"].sum() == 3 and len(records) == 43
    assert records.loc[records["flagged"], "speed_made_good"].isna().all()
    assert records.loc[records["flagged"], "antenna_height"].notna().all()


def test_our_flagged_records_have_r2rs_structure(tmp_path):
    ours = geocsv.write(tmp_path, geocsv.BESTRES, bestres_frame(), DECIMALS, SOURCE, CREATED)
    flagged = [line for line in ours.read_text().splitlines() if line[:2] == "#2"]
    r2r = [line for line in (FIXTURES / "SP1925_bestres_head.geoCSV").read_text().splitlines()
           if line[:2] == "#2"]  # fmt: skip
    assert {len(line.split(",")) for line in flagged} == {len(line.split(",")) for line in r2r}


def test_sp1925_flags_compared_with_r2r(tmp_path):
    """303 fixes flagged by R2R, 219 by us, 209 by both. Every difference is a stale-repeat
    decision on a repeated position while the ship barely moves (median of the preceding speeds
    near the 0.25 m/s threshold). NavManager's lagged median and its 3,600-record buffers don't
    explain them; its inverted acceleration loop, which works only from fixes already flagged,
    likely flags more repeats next to them.
    """
    directory = DATA / "SP1925_134952_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            "SP1925 not downloaded; run: uv run scripts/fetch_r2r_fileset.py SP1925 134952"
        )
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29")
    result = qc.flag(nav)
    theirs = geocsv.read(directory / "products" / "data" / "SP1925_bestres.geoCSV").records
    assert len(nav.frame) == len(theirs) == 45_723
    assert (nav.frame["time"].to_numpy() == theirs["iso_time"].to_numpy()).all()
    ours, r2r = result.flags != 0, theirs["flagged"].to_numpy()
    assert (r2r.sum(), ours.sum(), (ours & r2r).sum()) == (303, 219, 209)
    assert result.count(qc.Flag.STALE_REPEAT) == 219
    lon, lat = nav.frame["lon"].to_numpy(), nav.frame["lat"].to_numpy()
    repeated = np.r_[False, (lon[1:] == lon[:-1]) & (lat[1:] == lat[:-1])]
    assert repeated[ours != r2r].mean() > 0.99
    # our file has R2R's records and flags
    best = products.bestres(nav, result)
    written = geocsv.read(geocsv.write(tmp_path, geocsv.BESTRES, best, nav.decimals, SOURCE))
    assert (written.records["flagged"].to_numpy() == ours).all()
