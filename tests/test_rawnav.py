from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from conftest import DATA, FIXTURES
from test_gga_zda import gga, zda

from nav_qa_processing import geocsv, rawnav

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


@pytest.mark.map_data
def test_rr1802_is_over_water_at_portland():
    """Swan Island on the Willamette, and a run down to the Columbia: land in Natural Earth's
    polygons, on the water by its river centerlines and the 3000 m tolerance (#85)
    """
    from nav_qa_processing.config import QAConfig
    from nav_qa_processing.fileset import load_fileset
    from nav_qa_processing.qa import run_qa

    directory = DATA / "RR1802_132277_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            "RR1802 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset RR1802 132277"
        )
    day = date(2018, 2, 27)
    config = QAConfig(nav_format="nav01", depart_date=day, arrive_date=day)
    result = run_qa(load_fileset(directory), config)
    if result.stats.inland is None:
        pytest.skip("no Natural Earth data; run: uv run nav-qa-processing fetch-map-data")
    check = next(c for c in result.stats.checks if c.name == "Over water")
    assert (check.status, check.value) == ("pass", "0 over land")
    assert 2600 < np.nanmax(result.stats.inland) < 2700


@pytest.mark.parametrize(
    "name, format_id, depart, arrive, records",
    [
        # Healy's Seapath 330+, 2014
        ("HLY14TF_114739", "nav25", date(2014, 9, 14), date(2014, 9, 18), 349_199),
        # Knorr's C-Nav 2000, 2014
        ("KN220-02_114491", "nav20", date(2014, 6, 14), date(2014, 6, 17), 227_436),
        # Healy's POS/MV, gzipped, with ZDA
        ("HLY08TD_110937", "nav12", date(2008, 5, 7), date(2008, 5, 11), 363_486),
        # Langseth's C-Nav 3050: no ZDA, logger time
        ("MGL1509_122372", "nav12", date(2015, 5, 22), date(2015, 5, 28), 601_027),
        # Sproul's GP-90: GLL, not GGA (quality from GLL)
        ("SP1531_119707", "nav27", date(2015, 12, 8), date(2015, 12, 9), 93_417),
        # Rachel Carson's CORIOLIX RMC (rated R by both)
        ("RC0014_137928", "nav32", date(2018, 12, 27), date(2018, 12, 27), 22_203),
        # Blue Heron's UHDAS GPS feed: GGA dated by PYRTM
        ("BH19-30_136141", "nav31", date(2019, 10, 3), date(2019, 10, 3), 9_097),
        # Blue Heron's SCS: GLL and GGA, logger time
        ("BH10-17_105061", "nav03", date(2010, 9, 16), date(2010, 9, 17), 6_530),
    ],
)
def test_records_match_r2r_bestres(name, format_id, depart, arrive, records):
    """Each reference's records equal R2R's bestres, record for record, within the cruise's
    dates in R2R's catalog (#153: the reader and the dates are all these tests need, so they're
    here rather than in cruise TOMLs). R2R has no QA 2.0 report for these filesets, so only the
    products compare
    """
    from nav_qa_processing.config import QAConfig
    from nav_qa_processing.qa import cruise_window

    cruise, fileset = name.rsplit("_", 1)
    directory = DATA / f"{name}_gnss"
    path = directory / "products" / "data" / f"{cruise}_bestres.geoCSV"
    if not path.exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
            f"{cruise} {fileset}"
        )
    start, end = cruise_window(QAConfig(depart_date=depart, arrive_date=arrive))
    nav = rawnav.read_fileset(sorted((directory / "data").iterdir()), format_id, start, end)
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


def test_w1109b_r2r_dates_seventeen_minutes_a_day_early():
    """W1109B (sentences unquoted from September 2011): the same 68,302 records as R2R's within
    R2R's own window, which its 2012 QA report gives as 08:00Z to 07:59:59Z, the Pacific day
    (#88), but R2R dates 1,017 of them, 09-15 00:00:00 to 00:16:56, on 09-14, while the logger
    ran behind the GPS (R12); it flags them out of sequence
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


def _against_bestres(fixes, bestres, tolerance="0.5s"):
    """Each fix matched to R2R's nearest bestres record within `tolerance`: the distances [m]"""
    from nav_qa_processing import geodesy

    left = fixes.sort_values("time").assign(time=lambda f: f["time"].dt.as_unit("ns"))
    right = bestres.drop_duplicates("iso_time").sort_values("iso_time")
    right = right.assign(iso_time=right["iso_time"].dt.as_unit("ns"))
    matched = pd.merge_asof(
        left,
        right,
        left_on="time",
        right_on="iso_time",
        direction="nearest",
        tolerance=pd.Timedelta(tolerance),
    ).dropna(subset=["ship_longitude"])
    return geodesy.inverse(
        matched["lon"], matched["lat"], matched["ship_longitude"], matched["ship_latitude"]
    )[0]


@pytest.mark.parametrize(
    "name, feed, format_id, product, records, matched, median",
    [
        # UHDAS's GP170 feeds on SP2613 (PYRTM) against the BX992's bestres
        ("SP2613_169848", "gp170a", "nav31", "SP2613_169851", 26_509, 26_509, 10),
        ("SP2613_169848", "gp170b", "nav31", "SP2613_169851", 26_509, 26_509, 10),
        # Kilo Moana's Simrad in UHDAS, 2015 (UNIXD), against the POS/MV's bestres
        ("KM1519_119346", "simrad", "uhdas", "KM1519_119346", 31_586, 11_981, 20),
    ],
)
def test_uhdas_gps_feeds_agree_with_the_primary_gps(
    name, feed, format_id, product, records, matched, median
):
    """UHDAS's GPS logs, inside R2R's UHDAS ADCP filesets: R2R made no products from them, but
    at R2R's times their fixes lie within meters of the cruise's bestres, so they're dated
    right (#97)
    """
    cruise, fileset = name.split("_")
    directory = DATA / f"{name}_gnss" / "data" / "raw" / feed
    path = DATA / f"{product}_gnss" / "products" / "data" / f"{cruise}_bestres.geoCSV"
    if not directory.is_dir() or not path.exists():
        pytest.skip(
            f"{cruise} not downloaded; run: uv run nav-qa-processing r2r fetch-fileset "
            f"{cruise} {fileset} (and {product.split('_')[1]})"
        )
    nav = rawnav.read_fileset(sorted(directory.glob("*.gps")), format_id)
    assert nav.errors == [] and len(nav.frame) == records
    distance = _against_bestres(nav.frame, geocsv.read(path).records)
    assert len(distance) == matched and np.median(distance) < median


@pytest.mark.map_data(clipped=True)
def test_rc0014_canal_is_reported_not_warned_of():
    """Seattle's ship canal, which Natural Earth lacks: up to 3.9 km from its water, beyond
    land_tolerance but within max_inland, so reported and the check passes (#95)
    """
    from nav_qa_processing.config import QAConfig
    from nav_qa_processing.fileset import load_fileset
    from nav_qa_processing.qa import run_qa

    directory = DATA / "RC0014_137928_gnss"
    if not (directory / "data").is_dir():
        pytest.skip(
            "RC0014 not downloaded; run: uv run nav-qa-processing r2r fetch-fileset RC0014 137928"
        )
    day = date(2018, 12, 27)
    config = QAConfig(nav_format="nav32", depart_date=day, arrive_date=day)
    result = run_qa(load_fileset(directory), config)
    if result.stats.inland is None:
        pytest.skip("no Natural Earth data; run: uv run nav-qa-processing fetch-map-data")
    check = next(c for c in result.stats.checks if c.name == "Over water")
    assert (check.status, check.value) == ("pass", "0 over land")
    assert "284 on land 3000 m to 10000 m from water, up to 3929 m" in check.detail
