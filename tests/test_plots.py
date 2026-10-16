"""The report figures (#37), on small synthetic filesets, with the map layers offline."""

from datetime import UTC, datetime, timedelta

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
    # the track breaks at the gap: two lines
    assert (
        sum(1 for line in axis.lines if line.get_color() == plots.DEFAULT_THEME.colors.primary)
        == 2
    )


def test_track_map_across_the_antimeridian(tmp_path):
    result = cruise(tmp_path, lon0=179.9995)  # the jump crosses 180
    axis = plots.track_map(result).axes[0]
    west, east, _, _ = axis.get_extent(ccrs.PlateCarree(central_longitude=180))
    assert east - west < 1  # [degree] not the whole globe


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
