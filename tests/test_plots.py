"""The report figures (#37), on small synthetic filesets, with the map layers offline."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import numpy as np
import pytest

from nav_qa_processing import maps, plots, reports
from nav_qa_processing import quantities as q
from nav_qa_processing.config import Extent, QAConfig
from nav_qa_processing.formats.nmea import checksum
from nav_qa_processing.qa import run_qa
from nav_qa_processing.qc import Flag
from nav_qa_processing.theme import COLORBLIND, Theme

START = datetime(2026, 7, 9, 12, tzinfo=UTC)
METRE = 1 / 111_320  # [degree] of latitude


@pytest.fixture(autouse=True)
def offline_maps(monkeypatch):
    """No Natural Earth downloads: maps are drawn without coastlines"""
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def nmea(body: str) -> str:
    return f"${body}*{checksum(body):02X}"


def degrees(value: float, positive: str, negative: str, width: int) -> tuple[str, str]:
    hemisphere = positive if value >= 0 else negative
    whole, minutes = divmod(abs(value) * 60, 60)
    return f"{int(whole):0{width}d}{minutes:09.6f}", hemisphere


def fileset(tmp_path, fixes: list[tuple[float, float, float, int]]):
    """A nav29 fileset of (seconds after START, lon, lat, quality) fixes, ZDA every 10 s"""
    lines = []
    for seconds, lon, lat, quality in fixes:
        moment = START + timedelta(seconds=seconds)
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hhmmss = moment.strftime("%H%M%S.00")
        if seconds % 10 == 0:
            body = f"GPZDA,{hhmmss},{moment:%d},{moment:%m},{moment:%Y},00,00"
            lines.append(f"{stamp} {nmea(body)}")
        la, ns = degrees(lat, "N", "S", 2)
        lo, ew = degrees(lon, "E", "W", 3)
        body = f"GPGGA,{hhmmss},{la},{ns},{lo},{ew},{quality},10,0.9,7.5,M,-35.0,M,,"
        lines.append(f"{stamp} {nmea(body)}")
    data = tmp_path / "data"
    data.mkdir(parents=True)
    (data / "nav.txt").write_text("\n".join(lines) + "\n")
    return tmp_path


def cruise(tmp_path, lon0=-117.3, extent=None, **changes):
    """20 min at 1 Hz moving north at 3 m s-1, with a 400 s gap, a 50 m jump and 5 no-fix
    records
    """
    fixes = []
    for second in range(1200):
        if 500 <= second < 900:
            continue  # the gap
        lat = 32.6 + second * 3 * METRE
        quality = 0 if 1000 <= second < 1005 else 2
        lon = lon0 + (50 * METRE if second == 300 else 0.0)
        fixes.append((second, (lon + 180) % 360 - 180, lat, quality))
    config = QAConfig(cruise_id="TEST", nav_format="nav29", extent=extent, **changes)
    return run_qa(fileset(tmp_path, fixes), config)


def labels(axis) -> list[str]:
    return axis.get_legend_handles_labels()[1]


def test_track_map(tmp_path):
    result = cruise(tmp_path, extent=Extent(-117.4, -117.2, 32.5, 32.8))
    figure = plots.track_map(result)
    axis = figure.axes[0]
    good = int((result.qc.flags == 0).sum())
    found = labels(axis)
    assert found[:2] == ["Expected area", f"Track ({good} good fixes)"]
    assert "Flagged: acceleration (1)" in found and "Flagged: bad fix (5)" in found
    assert {"Start", "End"} <= set(found)
    texts = [t.get_text() for t in axis.texts]
    assert "coastline data unavailable" in texts
    assert axis.get_title() == "TEST: trackline"
    # the track breaks at the gap: two lines, and the gap between them dashed
    primary = [
        line for line in axis.lines if line.get_color() == plots.DEFAULT_THEME.colors.primary
    ]
    assert sum(1 for line in primary if line.get_linestyle() == "-") == 2
    assert "Gaps of 300 s or longer (1)" in found


def test_a_track_in_short_bursts_draws_every_burst(tmp_path):
    """Bursts of fixes too short to show as a line are drawn as dots, joined by dashed gaps
    (#70, SP2220)
    """
    speed = 9.0 * METRE  # [degree s-1] north
    bursts = [(n * 600, 1 if n == 2 else 3) for n in range(6)]  # (start [s], fixes)
    fixes = [
        (start + i, -117.3, 32.6 + (start + i) * speed, 2)
        for start, count in bursts
        for i in range(count)
    ]
    result = run_qa(fileset(tmp_path, fixes), QAConfig(cruise_id="TEST", nav_format="nav29"))
    assert (result.qc.flags == 0).all()
    axis = plots.track_map(result).axes[0]
    dots = [
        c
        for c in axis.collections
        if len(c.get_offsets()) == len(bursts)
        and np.allclose(
            c.get_facecolor()[0][:3], matplotlib.colors.to_rgb(plots.DEFAULT_THEME.colors.primary)
        )
    ]
    assert len(dots) == 1  # one dot per burst, the lone fix's too
    assert "Gaps of 300 s or longer (5)" in labels(axis)


def test_track_map_across_the_antimeridian(tmp_path):
    result = cruise(tmp_path, lon0=179.9995)  # the jump crosses 180
    axis = plots.track_map(result).axes[0]
    west, east, _, _ = axis.get_extent(ccrs.PlateCarree(central_longitude=180))
    assert east - west < 1  # [degree] not the whole globe


def loop(tmp_path, width: float, height: float, lon0=-117.3, lat0=32.6):
    """A ship going round a width x height [degree] box at 10 m s-1, one fix every 10 s,
    so the track runs along every edge of the map, as FKt230417's does round Puerto Rico
    """
    corners = [(0, 0), (width, 0), (width, height), (0, height), (0, 0)]
    fixes, seconds = [], 0
    for (x0, y0), (x1, y1) in pairwise(corners):
        metres = np.hypot((x1 - x0) * np.cos(np.radians(lat0)), y1 - y0) / METRE
        for f in np.linspace(0, 1, int(metres // 100), endpoint=False):
            fixes.append((seconds, lon0 + x0 + f * (x1 - x0), lat0 + y0 + f * (y1 - y0), 2))
            seconds += 10
    config = QAConfig(cruise_id="LOOP", nav_format="nav29")
    return run_qa(fileset(tmp_path, fixes), config)


def diagonal(tmp_path):
    """A transit from the south-west corner of the map to the north-east, leaving two corners
    clear
    """
    fixes = [(10 * i, -117.3 + i * 0.001, 32.6 + i * 0.0005, 2) for i in range(200)]
    return run_qa(fileset(tmp_path, fixes), QAConfig(cruise_id="DIAG", nav_format="nav29"))


def covered(figure, result) -> int:
    """How many fixes the legend (when on the map) and the world inset are drawn over"""
    figure.canvas.draw()
    axis = figure.axes[0]
    best = result.bestres
    projected = axis.projection.transform_points(
        ccrs.PlateCarree(), best["lon"].to_numpy(float), best["lat"].to_numpy(float)
    )
    display = axis.transData.transform(projected[:, :2])
    boxes = [inset.get_window_extent() for inset in axis.child_axes]
    if axis.get_legend() is not None:
        boxes.append(axis.get_legend().get_window_extent())
    x, y = display[:, 0], display[:, 1]
    return sum(int(((x >= b.x0) & (x <= b.x1) & (y >= b.y0) & (y <= b.y1)).sum()) for b in boxes)


@pytest.mark.parametrize(
    "make",
    [
        lambda tmp_path: loop(tmp_path, 0.3, 0.1),  # wide, like FKt230417
        lambda tmp_path: loop(tmp_path, 0.05, 0.15),  # tall
        diagonal,
        cruise,
    ],
    ids=["wide loop", "tall loop", "diagonal", "northward"],
)
def test_the_legend_and_inset_cover_no_fix(tmp_path, make):
    result = make(tmp_path)
    figure = plots.track_map(result)
    assert covered(figure, result) == 0
    # The legend is on the map or below it, with every entry
    legend = figure.axes[0].get_legend() or figure.legends[0]
    assert [t.get_text() for t in legend.get_texts()] == labels(figure.axes[0])


def test_a_map_with_clear_corners_keeps_the_inset_and_legend_on_it(tmp_path):
    for result in (diagonal(tmp_path / "diagonal"), cruise(tmp_path / "cruise")):
        figure = plots.track_map(result)
        axis = figure.axes[0]
        assert axis.get_legend() is not None and not figure.legends
        assert len(axis.child_axes) == 1


def test_a_track_round_the_map_moves_the_legend_below_it(tmp_path):
    figure = plots.track_map(loop(tmp_path, 0.3, 0.1))
    assert figure.axes[0].get_legend() is None and len(figure.legends) == 1


def test_speed_and_acceleration(tmp_path):
    result = cruise(tmp_path)
    top, bottom = plots.speed_and_acceleration(result).axes
    assert top.get_ylabel().replace("\n", " ") == q.SPEED_MADE_GOOD.label
    assert bottom.get_ylabel().replace("\n", " ") == q.HORIZONTAL_ACCELERATION.label
    assert bottom.get_xlabel() == q.ISO_TIME.label
    assert labels(top) == ["Good fixes", "max_speed 8.7 m s-1"]
    assert labels(bottom)[1] == f"QC limit {result.qc.accel_limit:g} m s-2"
    # nothing drawn across the gap: the band has a NaN there
    centers, lows, _ = plots._envelope(
        plots._naive_utc(result.bestres["time"]),
        result.bestres["sog"].to_numpy(dtype=float),
        plots._gaps(result),
    )
    in_gap = (centers > np.datetime64("2026-07-09T12:08:19")) & (
        centers < np.datetime64("2026-07-09T12:15:00")
    )
    assert in_gap.any() and np.isnan(lows[in_gap]).all()


def test_envelope_bins_long_series():
    times = np.datetime64("2026-07-09T00:00") + np.arange(100_000) * np.timedelta64(1, "s")
    values = np.sin(np.arange(100_000) / 50.0)
    centers, lows, highs = plots._envelope(times, values, [])
    assert len(centers) == plots.TIME_BINS
    assert np.nanmin(lows) == pytest.approx(-1, abs=1e-3)
    assert np.nanmax(highs) == pytest.approx(1, abs=1e-3)


def test_timeline(tmp_path):
    axis = plots.timeline(cruise(tmp_path)).axes[0]
    rows = [label.get_text() for label in axis.get_yticklabels()]
    assert rows[:2] == ["Fixes", "Gaps of 300 s or longer"]
    assert "acceleration (1)" in rows and "bad fix (5)" in rows


def test_flag_counts_match_the_qc_table(tmp_path):
    """A record with two flags is drawn once, under its lowest; the labels still give the QC
    table's counts and say where the rest are (#69)
    """
    result = cruise(tmp_path)
    bad = np.flatnonzero(result.qc.flags & int(Flag.BAD_FIX))  # the 5 no-fix records
    result.qc.flags[bad[:3]] |= int(Flag.POSITION)  # 3 also lack a position
    result.bestres["flags"] = result.qc.flags  # the map draws bestres's copy
    assert (result.qc.count(Flag.POSITION), result.qc.count(Flag.BAD_FIX)) == (3, 5)
    rows = [t.get_text() for t in plots.timeline(result).axes[0].get_yticklabels()]
    assert "position (3)" in rows and "bad fix (5)\n3 in the rows above" in rows
    found = labels(plots.track_map(result).axes[0])
    assert "Flagged: bad fix (5; 2 drawn)" in found  # the 3 without a position aren't
    assert not any(label.startswith("Flagged: position") for label in found)


def test_fixes_over_land_are_ringed(tmp_path):
    """The over-water check's fixes (#79), marked on the map with its count"""
    result = cruise(tmp_path)
    over = np.zeros(len(result.bestres), dtype=bool)
    over[100:110] = True
    result.stats.counted["over_land"] = over
    found = labels(plots.track_map(result).axes[0])
    assert "Over land (10)" in found


def test_histograms(tmp_path):
    figure = plots.histograms(cruise(tmp_path))
    x_labels = [axis.get_xlabel() for axis in figure.axes]
    assert x_labels == [
        q.SAMPLING_INTERVAL.label,
        q.NSV.label,
        q.HDOP.label,
        q.NMEA_QUALITY.label,
    ]
    quality = figure.axes[3]
    assert [t.get_text() for t in quality.get_xticklabels()] == ["0 invalid", "2 DGPS"]
    assert [round(bar.get_height()) for bar in quality.patches] == [5, 795]


def test_the_theme_colors_the_figures(tmp_path):
    result = cruise(tmp_path)
    axis = plots.track_map(result, Theme(palette="colorblind", colors=COLORBLIND)).axes[0]
    assert COLORBLIND.primary in [line.get_color() for line in axis.lines]


def test_write_reports_writes_every_figure_as_svg(tmp_path):
    result = cruise(tmp_path / "in")
    written = reports.write_reports(result, tmp_path / "out")
    svgs = sorted(p.name for p in written if p.suffix == ".svg")
    assert svgs == sorted(f"{name}.svg" for name in plots.FIGURES)
    assert all(p.parent.name == "TEST_plots" for p in written if p.suffix == ".svg")
    # re-running gives identical files
    first = (tmp_path / "out" / "TEST_plots" / "timeline.svg").read_bytes()
    reports.write_reports(result, tmp_path / "out")
    assert (tmp_path / "out" / "TEST_plots" / "timeline.svg").read_bytes() == first
