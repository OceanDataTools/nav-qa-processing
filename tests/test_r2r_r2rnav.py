"""Legacy r2rnav products, an optional output of `r2r run` (#146)."""

import numpy as np
import pandas as pd
from conftest import CONFIGS, DATA
from test_plots import cruise

from nav_qa_processing import geocsv, rawnav
from nav_qa_processing.cli import main
from nav_qa_processing.integrations.r2r import r2rnav

SP2613 = DATA / "SP2613_169851_gnss"  # committed in tests/data/


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


def test_r2r_run_writes_the_products_as_r2rnav_too(tmp_path, capsys):
    """SP2613 (committed): the three files in format-r2rnav.txt's layouts, with the GeoCSV
    products' records; refused without --force once they exist
    """
    argv = ["r2r", "run", str(CONFIGS / "SP2613_169851.toml"), str(SP2613), "-o", str(tmp_path)]
    assert main([*argv, "--r2rnav"]) == 0
    for product, width in (("bestres", 9), ("1min", 5), ("control", 3)):
        path = tmp_path / f"SP2613_{product}.r2rnav"
        lines = path.read_text().splitlines()
        assert [line[:2] for line in lines[:3]] == ["//"] * 3
        assert lines[1].endswith("format-r2rnav.txt") and lines[2].startswith("// Created ")
        assert {len(line.split("\t")) for line in lines[3:]} == {width}, product
        ours = geocsv.read(tmp_path / f"SP2613_{product}.geoCSV")
        back = r2rnav.read(path)
        assert len(back) == len(ours.records), product
        assert np.allclose(back["lon"], ours.records["ship_longitude"]), product
    assert "SP2613_control.r2rnav" in capsys.readouterr().out
    assert main([*argv, "--r2rnav"]) == 1  # the products exist: --force


def test_without_the_option_no_r2rnav_is_written(tmp_path):
    argv = ["r2r", "run", str(CONFIGS / "SP2613_169851.toml"), str(SP2613), "-o", str(tmp_path)]
    assert main(argv) == 0
    assert not list(tmp_path.glob("*.r2rnav"))


def test_a_flagged_record_is_time_through_antenna_height(tmp_path):
    """As R2R writes flagged bestres records (SP1925's GeoCSV): `#`, and no speed or course"""
    result = cruise(tmp_path / "in")
    written = r2rnav.write_products(result, tmp_path / "out")
    assert [p.name for p in written] == [
        "TEST_bestres.r2rnav",
        "TEST_1min.r2rnav",
        "TEST_control.r2rnav",
    ]
    lines = (tmp_path / "out" / "TEST_bestres.r2rnav").read_text().splitlines()[3:]
    flagged = [line for line in lines if line.startswith("#")]
    assert len(flagged) == int((result.bestres["flags"] != 0).sum()) > 0
    assert {len(line.split("\t")) for line in flagged} == {7}
    back = r2rnav.read(tmp_path / "out" / "TEST_bestres.r2rnav")
    assert back["flagged"].sum() == len(flagged)
