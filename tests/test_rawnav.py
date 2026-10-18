from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import CONFIGS, DATA, FIXTURES
from test_gga_zda import gga, zda

from nav_qa_processing import geocsv, r2rnav, rawnav

SP2613 = DATA / "SP2613_169851_gnss"
FETCH = "uv run nav-qa-processing r2r fetch-fileset SP2613 169851"


def read_geocsv(path):
    frame = pd.read_csv(path, comment="#", na_values="NAN", dtype=str)
    return frame


def test_read_fileset_selects_files_by_zda_window(tmp_path):
    (tmp_path / "early.txt").write_text("\n".join([gga("120000.00"), zda("120000.01", 7)]))
    (tmp_path / "cruise.txt").write_text("\n".join([gga("120000.00"), zda("120000.01", 9)]))
    (tmp_path / "junk.txt").write_text("no nav here\n")
    nav = rawnav.read_fileset(
        sorted(tmp_path.iterdir()),
        "nav29",
        datetime(2026, 7, 9, tzinfo=UTC),
        datetime(2026, 7, 10, tzinfo=UTC),
    )
    assert nav.files == ["cruise.txt"] and nav.skipped_files == ["early.txt"]
    assert any("junk.txt" in e for e in nav.errors)
    assert nav.frame["time"].iloc[0] == pd.Timestamp("2026-07-09T12:00:00Z")
    assert nav.decimals == {"time": 2, "lon": 6, "lat": 6, "hdop": 1, "alt": 3}


def test_unknown_format():
    with pytest.raises(ValueError, match="is unknown"):
        rawnav.read_fileset([], "nav99")


def test_r2rnav_round_trip(tmp_path):
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2026-07-09T15:11:57.5Z", "2026-07-09T15:11:58Z"], format="ISO8601"
            ),
            "lon": [-117.2337714755, np.nan],
            "lat": [32.7028130227, 32.7027991935],
            "quality": pd.array([2, None], dtype="Int64"),
            "nsat": pd.array([13, 13], dtype="Int64"),
            "hdop": [0.8, 0.8],
            "alt": [7.76, 7.887],
        }
    )
    path = tmp_path / "x.r2rnav"
    r2rnav.write(
        path,
        frame,
        rawnav.QUANTITIES,
        {"time": 2, "lon": 10, "lat": 10, "hdop": 1, "alt": 3},
        flagged=np.array([False, True]),
    )
    lines = path.read_text().splitlines()
    assert lines[3] == "2026-07-09T15:11:57.50Z\t-117.2337714755\t32.7028130227\t2\t13\t0.8\t7.760"
    assert lines[4].startswith("#2026-07-09T15:11:58.00Z\tNAN\t")
    back = r2rnav.read(path)
    assert list(back["flagged"]) == [False, True]
    assert back["alt"].tolist() == [7.76, 7.887]


@pytest.fixture(scope="module")
def sp2613():
    if not (SP2613 / "data").is_dir():
        pytest.skip(f"SP2613 raw GNSS data not downloaded; run: {FETCH}")
    return rawnav.read_fileset(
        sorted((SP2613 / "data").iterdir()),
        "nav29",
        datetime(2026, 7, 9, tzinfo=UTC),
        datetime(2026, 7, 10, tzinfo=UTC),
    )


def _compare(nav, r2r):
    """Our records, written at the receiver's precision, equal R2R's by value"""
    ours = nav.frame.iloc[: len(r2r)]
    assert (ours["time"].to_numpy() == pd.to_datetime(r2r["iso_time"]).to_numpy()).all()
    for column, field in [("lon", "ship_longitude"), ("lat", "ship_latitude")]:
        text = ours[column].map(lambda v, d=nav.decimals[column]: f"{v:.{d}f}")
        assert (text.astype(float).to_numpy() == r2r[field].astype(float).to_numpy()).all()
    for column, field in [
        ("quality", "nmea_quality"),
        ("nsat", "nsv"),
        ("hdop", "hdop"),
        ("alt", "antenna_height"),
    ]:
        a, b = ours[column].astype(float).to_numpy(), r2r[field].astype(float).to_numpy()
        # NavManager writes an empty field as 0 (R11: HLY08TD's 2 empty HDOPs)
        b = np.where(np.isnan(a) & (b == 0), np.nan, b)
        assert np.array_equal(a, b, equal_nan=True), column


def test_sp2613_bestres_head_fixture(sp2613):
    _compare(sp2613, read_geocsv(FIXTURES / "SP2613_bestres_head.geoCSV"))


def test_sp2613_all_records_match_r2r_bestres(sp2613):
    path = SP2613 / "products" / "data" / "SP2613_bestres.geoCSV"
    if not path.exists():
        pytest.skip(f"SP2613 products not downloaded; run: {FETCH}")
    r2r = read_geocsv(path)
    assert sp2613.files == ["SP2613_mru_trimble_bx992-2026-07-09.txt"]
    assert sp2613.errors == []
    assert sp2613.talkers == {"GN": 56_925}  # a multi-constellation receiver
    # SP2613 has no flagged records, so pre-QC equals bestres record for record
    assert len(sp2613.frame) == len(r2r) == 56_925
    _compare(sp2613, r2r)


def test_sp2424_records_match_r2r_bestres_plus_the_final_fix():
    """150,924 R2R records, all equal to ours; NavManager drops the final GGA, after the last
    ZDA, which we keep (#14)
    """
    directory = DATA / "SP2424_161900_gnss"
    path = directory / "products" / "data" / "SP2424_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            "SP2424 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset SP2424 161900"
        )
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29")
    r2r = read_geocsv(path)
    assert nav.errors == []
    assert len(nav.frame) == len(r2r) + 1 == 150_925
    assert nav.frame["time"].iloc[-1] == pd.Timestamp("2024-11-04T03:16:44.50Z")
    _compare(nav, r2r)


def test_rr1802_nav1_records_match_r2r_bestres():
    """Revelle's raw NMEA (nav01, Furuno GP-150): 28,527 records equal R2R's bestres, record for
    record (#81). R2R has no QA 2.0 report for this fileset
    """
    directory = DATA / "RR1802_132277_gnss"
    path = directory / "products" / "data" / "RR1802_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            "RR1802 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset RR1802 132277"
        )
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav01")
    r2r = geocsv.read(path).records  # with its 292 flagged records
    assert nav.errors == [] and nav.talkers == {"GP": 28_527}
    assert len(nav.frame) == len(r2r) == 28_527
    _compare(nav, r2r)


def test_rr1802_is_over_water_at_portland():
    """Swan Island on the Willamette, and a run down to the Columbia: land in Natural Earth's
    polygons, on the water by its river centerlines and the 3000 m tolerance (#85)
    """
    from nav_qa_processing.fileset import load_fileset
    from nav_qa_processing.integrations.r2r.config import load_cruise_config
    from nav_qa_processing.qa import run_qa

    directory = DATA / "RR1802_132277_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            "RR1802 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset RR1802 132277"
        )
    result = run_qa(load_fileset(directory), load_cruise_config(CONFIGS / "RR1802_132277.toml"))
    if result.stats.inland is None:
        pytest.skip("no Natural Earth data; run: uv run nav-qa-processing fetch-map-data")
    check = next(c for c in result.stats.checks if c.name == "Over water")
    assert (check.status, check.value) == ("pass", "0 over land")
    assert 2600 < np.nanmax(result.stats.inland) < 2700


@pytest.mark.parametrize(
    "name, format_id, records",
    [
        ("HLY14TF_114739", "nav25", 349_199),  # Healy's Seapath 330+, 2014
        ("KN220-02_114491", "nav20", 227_436),  # Knorr's C-Nav 2000, 2014
        ("HLY08TD_110937", "nav12", 363_486),  # Healy's POS/MV, gzipped, with ZDA
        ("MGL1509_122372", "nav12", 601_027),  # Langseth's C-Nav 3050: no ZDA, logger time
    ],
)
def test_records_match_r2r_bestres(name, format_id, records):
    """Each reference's records equal R2R's bestres, record for record. R2R has no QA 2.0 report
    for these filesets, so only the products compare
    """
    from nav_qa_processing.integrations.r2r.config import load_cruise_config
    from nav_qa_processing.qa import cruise_window

    cruise, fileset = name.rsplit("_", 1)
    directory = DATA / f"{name}_gnss"
    path = directory / "products" / "data" / f"{cruise}_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
            f"{cruise} {fileset}"
        )
    config = load_cruise_config(CONFIGS / f"{name}.toml")
    assert config.nav_format == format_id
    start, end = cruise_window(config)
    window = {k: v for k, v in (("start", start), ("end", end)) if v is not None}
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), format_id, **window)
    r2r = geocsv.read(path).records
    assert nav.errors == []
    assert len(nav.frame) == len(r2r) == records
    _compare(nav, r2r)


def _wecoma(name):
    cruise, fileset = name.split("_")
    directory = DATA / f"{name}_gnss"
    path = directory / "products" / "data" / f"{cruise}_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
            f"{cruise} {fileset}"
        )
    return directory, geocsv.read(path).records


def test_w1108a_nav15_records_match_r2r_bestres():
    """Wecoma's Ashtech ADU5 (nav15: DATA, ISO timestamp, quoted GGA): 50,678 records equal to
    R2R's within R2R's own window, which its 2012 QA report gives as 08:00Z to 07:59:59Z, the
    Pacific day (#88)
    """
    directory, r2r = _wecoma("W1108A_105073")
    start = datetime(2011, 8, 6, 8, tzinfo=UTC)
    nav = rawnav.read_fileset(
        sorted((directory / "data").iterdir()), "nav15", start, start + pd.Timedelta(days=1)
    )
    assert nav.errors == []
    assert len(nav.frame) == len(r2r) == 50_678
    _compare(nav, r2r)


def test_w1109b_r2r_dates_seventeen_minutes_a_day_early():
    """W1109B (sentences unquoted from September 2011): the same 68,302 records as R2R's, but
    R2R dates 1,017 of them, 09-15 00:00:00 to 00:16:56, on 09-14, while the logger ran behind
    the GPS (R12); it flags them out of sequence
    """
    directory, r2r = _wecoma("W1109B_105076")
    start = datetime(2011, 9, 14, 8, tzinfo=UTC)
    nav = rawnav.read_fileset(
        sorted((directory / "data").iterdir()), "nav15", start, start + pd.Timedelta(days=1)
    )
    ours = nav.frame["time"]
    theirs = r2r["iso_time"]
    assert len(ours) == len(theirs) == 68_302
    moved = ~ours.isin(theirs)
    assert int(moved.sum()) == 1_017
    assert (ours[moved].min(), ours[moved].max()) == (
        pd.Timestamp("2011-09-15T00:00:00Z"),
        pd.Timestamp("2011-09-15T00:16:56Z"),
    )
    assert ours[moved].sub(pd.Timedelta(days=1)).isin(theirs).all()
    assert r2r.loc[~theirs.isin(ours), "flagged"].all()


def test_nh1209_nav22_agrees_with_the_primary_gps():
    """New Horizon's Ashtech ADU2 (nav22: no date but the file name's). R2R made no products
    from it, but each file's fixes lie within meters of R2R's bestres from the primary GPS at
    the same times, so they're dated right (#90)
    """
    directory = DATA / "NH1209_107219_gnss"
    path = directory / "products" / "data" / "NH1209_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            "NH1209 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset NH1209 107219"
        )
    from nav_qa_processing import geodesy

    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav22")
    assert nav.errors == [] and len(nav.frame) == 137_407
    r2r = geocsv.read(path).records.drop_duplicates("iso_time")
    for name, fixes in nav.frame.groupby("source_file", observed=True):
        common = fixes.merge(r2r, left_on="time", right_on="iso_time")
        if name.startswith("ashtech_20120918"):  # before R2R's first fix
            assert common.empty
            continue
        assert len(common) >= 0.99 * len(fixes), name
        distance = geodesy.inverse(
            common["lon"], common["lat"], common["ship_longitude"], common["ship_latitude"]
        )[0]
        assert np.median(distance) < 10, name
