from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import DATA, FIXTURES
from test_gga_zda import gga, zda

from nav_qa_processing import r2rnav, rawnav

SP2613 = DATA / "SP2613_169851_gnss"
FETCH = "uv run scripts/fetch_r2r_fileset.py SP2613 169851"


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
        assert (ours[column].astype(float).to_numpy() == r2r[field].astype(float).to_numpy()).all()


def test_sp2613_bestres_head_fixture(sp2613):
    _compare(sp2613, read_geocsv(FIXTURES / "SP2613_bestres_head.geoCSV"))


def test_sp2613_all_records_match_r2r_bestres(sp2613):
    path = SP2613 / "products" / "data" / "SP2613_bestres.geoCSV"
    if not path.exists():
        pytest.skip(f"SP2613 products not downloaded; run: {FETCH}")
    r2r = read_geocsv(path)
    assert sp2613.files == ["SP2613_mru_trimble_bx992-2026-07-09.txt"]
    assert sp2613.errors == []
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
            "SP2424 not downloaded; run: uv run scripts/fetch_r2r_fileset.py SP2424 161900"
        )
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), "nav29")
    r2r = read_geocsv(path)
    assert nav.errors == []
    assert len(nav.frame) == len(r2r) + 1 == 150_925
    assert nav.frame["time"].iloc[-1] == pd.Timestamp("2024-11-04T03:16:44.50Z")
    _compare(nav, r2r)
