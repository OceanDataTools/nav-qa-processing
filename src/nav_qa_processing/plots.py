"""Figures for the PDF report and the notebook. Each function takes a `NavQAResult` and a
`Theme` and returns a matplotlib Figure:

- `track_map`: the good track over coastlines, flagged fixes by QC flag, the 1min control
  points, the start and end, QC's position steps and the expected area, with a world inset.
  The legend and inset go where they cover no fix or marker: the legend below the map when
  there's no such place on it, and the inset left out when there's none;
- `speed_and_acceleration`: speed over ground and the acceleration QC tests, against time, with
  their limits;
- `timeline`: when there are fixes, the gaps (`max_gap` or longer) and the flagged fixes by
  flag;
- `histograms`: the sampling interval, satellites, HDOP and GGA quality.

Axis labels and units come from `quantities.py`. Large filesets are reduced for drawing (a
simplified track line, min/max envelopes per time bin, thinned markers), never in the counts
shown.
"""

import textwrap
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import shapely
from matplotlib import font_manager
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import FixedLocator, MaxNLocator, NullFormatter

from nav_qa_processing import geodesy, maps
from nav_qa_processing import quantities as q
from nav_qa_processing.qa import NavQAResult
from nav_qa_processing.qc import Flag, acceleration_at_fixes
from nav_qa_processing.stats import UNUSABLE
from nav_qa_processing.theme import DEFAULT_THEME, Theme

FIGURE_WIDTH = 7.5  # [in] US letter less margins; the PDF scales figures to its frame
MAP_MIN_HALF_SPAN = 0.05  # [degree] smallest half-width of the map view
MAP_POLAR_LATITUDE = 70.0  # [degree] beyond this, the map is polar stereographic
TRACK_POINTS = 4000  # about how many vertices the simplified track line keeps
TIME_BINS = 1500  # bins of the min/max envelopes against time
MAX_MARKERS = 3000  # flagged fixes drawn per flag; the legend gives the full count
OCCUPIED_TRACK_POINTS = 20_000  # track fixes a legend or the inset is checked against
QUALITY_NAMES = {
    0: "invalid",
    1: "GPS",
    2: "DGPS",
    3: "PPS",
    4: "RTK fixed",
    5: "RTK float",
    6: "dead reckoning",
    7: "manual",
    8: "simulator",
}


def _font_family(path: Path) -> str:
    """matplotlib's name for a TTF, registering it"""
    font_manager.fontManager.addfont(str(path))
    return font_manager.FontProperties(fname=str(path)).get_name()


@contextmanager
def themed(theme: Theme) -> Iterator[None]:
    """matplotlib settings for the theme's fonts, while a figure is made"""
    settings = {}
    if theme.font is not None:
        settings["font.family"] = _font_family(theme.font)
        if theme.bold_font is not None:
            _font_family(theme.bold_font)  # the same family's bold, for bold text
    if theme.figure_font_scale != 1:
        settings["font.size"] = plt.rcParams["font.size"] * theme.figure_font_scale
    with plt.rc_context(settings):
        yield


def _pt(theme: Theme, size: float) -> float:
    """A figure font size [pt] scaled by the theme"""
    return size * theme.figure_font_scale


def _y_label(quantity: q.Quantity) -> str:
    """A quantity's label, wrapped to fit beside a short vertical axis"""
    return "\n".join(textwrap.wrap(quantity.label, 32))


def _log_y(axis) -> None:
    """Counts on a log axis, from 1, so a rare value still shows beside a common one"""
    axis.set_yscale("log")
    axis.set_ylim(bottom=0.8)
    axis.yaxis.set_minor_formatter(NullFormatter())


# -- Data --


def _naive_utc(times: pd.Series) -> np.ndarray:
    """UTC times as numpy datetime64 without a zone, for matplotlib's date axes"""
    return times.dt.tz_convert(None).to_numpy() if times.dt.tz is not None else times.to_numpy()


def _flag_name(flag: Flag) -> str:
    return flag.name.lower().replace("_", " ")


def _flag_index(flags: np.ndarray) -> np.ndarray:
    """The lowest QC flag bit of each record, as an index into `Flag`, or -1 for a good fix"""
    index = np.full(len(flags), -1)
    for i, flag in reversed(list(enumerate(Flag))):
        index[(flags & int(flag)) != 0] = i
    return index


def _in_sequence(result: NavQAResult) -> np.ndarray:
    """Records the gap statistics use: usable and in time order (as in stats.py)"""
    flags = result.qc.flags
    return ((flags & int(UNUSABLE)) == 0) & ((flags & int(Flag.TIME_SEQUENCE)) == 0)


def _gaps(result: NavQAResult) -> list[tuple[np.datetime64, np.datetime64]]:
    """(start, end) of each step of `max_gap` or longer between in-sequence records"""
    times = _naive_utc(result.nav.frame["time"][_in_sequence(result)])
    if len(times) < 2:
        return []
    steps = np.diff(times) / np.timedelta64(1, "s")
    at = np.flatnonzero(steps >= result.config.thresholds.max_gap)
    return [(times[i], times[i + 1]) for i in at]


def _envelope(times: np.ndarray, values: np.ndarray, breaks: list) -> tuple:
    """(times, minimum, maximum) per time bin, NaN in bins without values and across gaps, so
    a long series draws as a band rather than hundreds of thousands of points
    """
    keep = ~np.isnan(values)
    times, values = times[keep], values[keep]
    if len(times) <= 2 * TIME_BINS:
        lows, highs = values.astype(float), values.astype(float)
        centers = times
    else:
        edges = np.linspace(
            times[0].astype("int64"), times[-1].astype("int64"), TIME_BINS + 1
        ).astype("datetime64[ns]")
        which = np.clip(np.searchsorted(edges, times, side="right") - 1, 0, TIME_BINS - 1)
        lows = np.full(TIME_BINS, np.nan)
        highs = np.full(TIME_BINS, np.nan)
        np.fmin.at(lows, which, values)
        np.fmax.at(highs, which, values)
        centers = edges[:-1] + (edges[1:] - edges[:-1]) / 2
    for start, end in breaks:  # nothing drawn across a gap
        inside = (centers > start) & (centers < end)
        lows[inside] = highs[inside] = np.nan
    if breaks:  # and a break in the line within each gap, binned or not
        middles = np.array([start + (end - start) / 2 for start, end in breaks])
        order = np.argsort(np.concatenate([centers, middles]), kind="stable")
        nan = np.full(len(middles), np.nan)
        centers = np.concatenate([centers, middles])[order]
        lows = np.concatenate([lows, nan])[order]
        highs = np.concatenate([highs, nan])[order]
    return centers, lows, highs


def _thin(index: np.ndarray, limit: int = MAX_MARKERS) -> np.ndarray:
    return index if len(index) <= limit else index[:: int(np.ceil(len(index) / limit))]


# -- Trackline map --


def _map_view(latitudes, longitudes) -> tuple[float, float, float, float]:
    """(west, east, south, north) around the points with a margin"""
    center_lat = (max(latitudes) + min(latitudes)) / 2
    center_lon = (max(longitudes) + min(longitudes)) / 2
    half_lat = max((max(latitudes) - min(latitudes)) * 0.6, MAP_MIN_HALF_SPAN)
    half_lon = max((max(longitudes) - min(longitudes)) * 0.6, MAP_MIN_HALF_SPAN)
    south, north = max(center_lat - half_lat, -89.0), min(center_lat + half_lat, 89.0)
    return center_lon - half_lon, center_lon + half_lon, south, north


def _wraps(extent, longitudes: np.ndarray) -> bool:
    """Whether the map is drawn in 0-360° longitudes: when the extent crosses the antimeridian,
    or, without one, when the track is narrower that way
    """
    if extent is not None:
        return extent.crosses_antimeridian
    if not len(longitudes):
        return False
    return np.ptp(longitudes % 360) < np.ptp(longitudes)


def _corners_by_emptiness(points: np.ndarray, view) -> list[str]:
    """The view's corners, the one whose quadrant has the fewest (x, lat) points first: the
    order in which the world inset tries them
    """
    west, east, south, north = view
    mid_x, mid_lat = (west + east) / 2, (south + north) / 2
    x, lat = (points[:, 0], points[:, 1]) if len(points) else (np.array([]), np.array([]))
    counts = {
        "lower left": int(((lat < mid_lat) & (x < mid_x)).sum()),
        "lower right": int(((lat < mid_lat) & (x >= mid_x)).sum()),
        "upper left": int(((lat >= mid_lat) & (x < mid_x)).sum()),
        "upper right": int(((lat >= mid_lat) & (x >= mid_x)).sum()),
    }
    return sorted(counts, key=counts.get)


Rect = tuple[float, float, float, float]  # (x, y, width, height) in axes coordinates

# The legend's places on the map, in the order they're tried after the corner opposite the inset
LEGEND_PLACES = (
    "upper right",
    "upper left",
    "lower left",
    "lower right",
    "upper center",
    "lower center",
    "center left",
    "center right",
)
INSET_EDGES = ("upper center", "lower center", "center left", "center right")  # after corners
OPPOSITE = {
    "upper right": "lower left",
    "upper left": "lower right",
    "lower right": "upper left",
    "lower left": "upper right",
}
INSET_SIZES = (0.28, 0.2)  # share of the map's width and height the inset may take, tried in order
INSET_PAD = 0.02  # [axes fraction] the inset's distance from the map's edges
INSET_MARGIN = 0.01  # [axes fraction] clearance kept between the track and a legend or inset


def _corner_rect(corner: str, width: float, height: float, pad_x: float, pad_y: float) -> Rect:
    """The rectangle of a legend or inset of this size at a matplotlib legend location"""
    if "left" in corner:
        x = pad_x
    elif "right" in corner:
        x = 1 - pad_x - width
    else:
        x = (1 - width) / 2
    if "lower" in corner:
        y = pad_y
    elif "upper" in corner:
        y = 1 - pad_y - height
    else:
        y = (1 - height) / 2
    return x, y, width, height


def _covers(rect: Rect, points: np.ndarray, margin: float = INSET_MARGIN) -> bool:
    """Whether any of the points (axes coordinates) is within margin of the rectangle"""
    x, y, width, height = rect
    inside = (
        (points[:, 0] >= x - margin)
        & (points[:, 0] <= x + width + margin)
        & (points[:, 1] >= y - margin)
        & (points[:, 1] <= y + height + margin)
    )
    return bool(inside.any())


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _place_legend_and_inset(
    figure: Figure,
    axis,
    frame,
    occupied: tuple[np.ndarray, np.ndarray],
    corners: list[str],
    latitude: float,
    longitude: float,
    theme: Theme,
) -> None:
    """The legend and world inset where they cover no track or marker (#59). Both are measured
    once the map is laid out, and each candidate place is checked against the occupied points
    (x, lat in frame). In order of preference: the inset in a clear corner and the legend in a
    clear place on the map; the inset with the legend below the map; the legend on the map
    without the inset; the legend below the map without it
    """
    fontsize = _pt(theme, 6.5)
    legend = axis.legend(fontsize=fontsize, loc="upper left", framealpha=0.85)
    figure.draw_without_rendering()
    area = axis.get_window_extent()
    xs, lats = occupied
    projected = axis.projection.transform_points(frame, np.asarray(xs), np.asarray(lats))
    points = axis.transLimits.transform(projected[:, :2]) if len(xs) else np.empty((0, 2))
    points = points[np.isfinite(points).all(axis=1)]

    size = legend.get_window_extent()
    pad = legend.borderaxespad * legend.prop.get_size_in_points() * figure.dpi / 72
    width, height = size.width / area.width, size.height / area.height
    pad_x, pad_y = pad / area.width, pad / area.height
    robinson = ccrs.Robinson(central_longitude=longitude)
    aspect = np.ptp(robinson.x_limits) / np.ptp(robinson.y_limits)

    insets = []  # (corner, rect) clear of the track, by preference
    for share in INSET_SIZES:
        inset_width = min(share * area.width, share * area.height * aspect)
        w, h = inset_width / area.width, inset_width / aspect / area.height
        insets += [
            (place, rect)
            for place in [*corners, *INSET_EDGES]
            if not _covers(rect := _corner_rect(place, w, h, INSET_PAD, INSET_PAD), points)
        ]

    def legend_place(inset: tuple[str, Rect] | None) -> str | None:
        first = [OPPOSITE[inset[0]]] if inset and inset[0] in OPPOSITE else []
        for place in first + [p for p in LEGEND_PLACES if p not in first]:
            rect = _corner_rect(place, width, height, pad_x, pad_y)
            if not _covers(rect, points) and not (inset and _overlap(rect, inset[1])):
                return place
        return None

    # Keep the inset if anywhere is clear for it, moving the legend below the map if need be
    inset = next((i for i in insets if legend_place(i) is not None), None)
    inset = inset or (insets[0] if insets else None)
    place = legend_place(inset)
    if place is not None:
        legend.set_loc(place)
    else:
        legend.remove()
        handles, labels = axis.get_legend_handles_labels()
        figure.legend(
            handles,
            labels,
            loc="outside lower center",
            ncols=min(3, len(labels)),
            fontsize=fontsize,
            framealpha=0.85,
        )
    if inset is not None:
        _world_inset(axis, inset[1], latitude, longitude, theme)


def _world_inset(axis, rect: Rect, latitude: float, longitude: float, theme: Theme) -> None:
    """A small world map marking the cruise, at rect on the map"""
    colors = theme.colors
    inset = axis.inset_axes(rect, projection=ccrs.Robinson(central_longitude=longitude))
    inset.set_global()
    inset.set_facecolor(colors.water)
    land = maps.world_geometries("physical", "land")
    if land:
        inset.add_geometries(
            land, ccrs.PlateCarree(), facecolor=colors.land, edgecolor=colors.coast, linewidth=0.2
        )
    inset.plot(
        longitude,
        latitude,
        marker="o",
        markersize=5,
        color=colors.secondary,
        markeredgecolor="white",
        markeredgewidth=0.6,
        transform=ccrs.PlateCarree(),
    )
    inset.spines["geo"].set_edgecolor("0.5")


MAP_HEIGHTS = (4.0, 9.0)  # [in] the map figure's height follows the view's shape, within these


def _map_height(view, projection) -> float:
    """The figure height [in] that fits the view's projected shape at FIGURE_WIDTH"""
    west, east, south, north = view
    corners = projection.transform_points(
        ccrs.PlateCarree(),
        np.array([west, east, west, east, (west + east) / 2]),
        np.array([south, south, north, north, (south + north) / 2]),
    )
    width = np.ptp(corners[:, 0])
    height = np.ptp(corners[:, 1])
    if not (np.isfinite(width) and np.isfinite(height)) or width <= 0:
        return MAP_HEIGHTS[0]
    # the axes' share of the figure, leaving room for the title and labels
    return float(np.clip(FIGURE_WIDTH * 0.9 * height / width + 0.8, *MAP_HEIGHTS))


def _track_line(x: np.ndarray, lat: np.ndarray, times: np.ndarray, max_gap: float, view):
    """The track as lines (lists of x, lat) broken at gaps and simplified for drawing"""
    if len(x) < 2:
        return []
    steps = np.diff(times) / np.timedelta64(1, "s")
    pieces = np.split(np.arange(len(x)), np.flatnonzero(steps >= max_gap) + 1)
    tolerance = max(view[1] - view[0], view[3] - view[2]) / TRACK_POINTS
    lines = []
    for piece in pieces:
        if len(piece) < 2:
            continue
        line = shapely.simplify(
            shapely.LineString(np.column_stack([x[piece], lat[piece]])), tolerance
        )
        coords = np.asarray(line.coords)
        lines.append((coords[:, 0], coords[:, 1]))
    return lines


def track_map(result: NavQAResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """The good track over coastlines, with flagged fixes by QC flag, 1min control points, the
    start and end, QC's position steps and the expected area
    """
    with themed(theme):
        return _track_map(result, theme)


def _track_map(result: NavQAResult, theme: Theme) -> Figure:
    colors = theme.colors
    best = result.bestres
    flags = best["flags"].to_numpy()
    lon = best["lon"].to_numpy(dtype=float)
    lat = best["lat"].to_numpy(dtype=float)
    times = _naive_utc(best["time"])
    good = flags == 0
    placed = (
        ~np.isnan(lon) & ~np.isnan(lat) & ((flags & int(Flag.POSITION | Flag.NULL_ISLAND)) == 0)
    )
    extent = result.config.extent

    # The view: the good track and the expected area (flagged fixes may lie far outside it)
    basis = good & placed if (good & placed).any() else placed
    box_lats = [] if extent is None else [extent.southernmost, extent.northernmost]
    box_lons = [] if extent is None else [extent.westernmost, extent.easternmost]
    wraps = _wraps(extent, lon[basis])
    unwrap = (lambda v: np.asarray(v) % 360) if wraps else (lambda v: np.asarray(v, dtype=float))
    lats = np.concatenate([lat[basis], box_lats]) if basis.any() or box_lats else np.array([0.0])
    lons = (
        np.concatenate([unwrap(lon[basis]), unwrap(box_lons)])
        if len(lats) > 1
        else np.array([0.0])
    )
    view = _map_view(lats, lons)
    center_lat, center_lon = (view[2] + view[3]) / 2, (view[0] + view[1]) / 2
    central = (center_lon + 180) % 360 - 180  # the same meridian, within ±180
    # Everything is drawn relative to the center, so it stays whole across the antimeridian
    frame = ccrs.PlateCarree(central_longitude=central)
    x = unwrap(lon) - center_lon
    relative = (view[0] - center_lon, view[1] - center_lon, view[2], view[3])
    if view[3] > MAP_POLAR_LATITUDE:
        projection = ccrs.NorthPolarStereo(central_longitude=central)
    elif view[2] < -MAP_POLAR_LATITUDE:
        projection = ccrs.SouthPolarStereo(central_longitude=central)
    else:
        projection = ccrs.Mercator(central_longitude=central)

    figure = plt.figure(
        figsize=(FIGURE_WIDTH, _map_height(view, projection)), layout="constrained"
    )
    axis = figure.add_subplot(projection=projection)
    axis.set_extent(relative, crs=frame)
    axis.set_facecolor(colors.water)

    window = (view[0] - 0.5, view[1] + 0.5, view[2] - 0.5, view[3] + 0.5)
    layer = lambda name: maps.clipped_geometries("physical", name, "10m", window, center_lon)
    land = layer("land")
    if land:
        axis.add_geometries(land, frame, facecolor=colors.land, edgecolor="none", zorder=0.5)
    lakes = layer("lakes")
    if lakes:
        axis.add_geometries(
            lakes, frame, facecolor=colors.water, edgecolor=colors.coast, linewidth=0.4, zorder=0.6
        )
    coastline = layer("coastline")
    if coastline:
        axis.add_geometries(
            coastline, frame, facecolor="none", edgecolor=colors.coast, linewidth=0.5, zorder=0.7
        )
    if land is None:
        axis.text(
            0.01,
            0.01,
            "coastline data unavailable",
            transform=axis.transAxes,
            fontsize=_pt(theme, 6),
            color="0.4",
        )

    if extent is not None:
        west, east = unwrap([extent.westernmost, extent.easternmost])
        axis.add_patch(
            Rectangle(
                (west - center_lon, extent.southernmost),
                east - west,
                extent.northernmost - extent.southernmost,
                fill=False,
                ls="--",
                color=colors.secondary,
                linewidth=0.8,
                label="Expected area",
                transform=frame,
                zorder=2,
            )
        )

    track = good & placed
    max_gap = result.config.thresholds.max_gap
    for i, (line_x, line_lat) in enumerate(
        _track_line(x[track], lat[track], times[track], max_gap, view)
    ):
        axis.plot(
            line_x,
            line_lat,
            color=colors.primary,
            linewidth=1.0,
            label=f"Track ({q.FIXES.format(int(track.sum()))} good fixes)" if i == 0 else None,
            transform=frame,
            zorder=3,
        )
    if len(result.control):
        control_x = unwrap(result.control["lon"].to_numpy(dtype=float)) - center_lon
        axis.scatter(
            control_x,
            result.control["lat"],
            s=10,
            facecolors="white",
            edgecolors=colors.primary,
            linewidths=0.7,
            label=f"Control points ({len(result.control)})",
            transform=frame,
            zorder=4,
        )

    # Each record is drawn once, under its lowest flag; the legend gives the QC table's count
    # (every record with the flag) and how many are drawn here (#69)
    which = _flag_index(flags)
    for i, flag in enumerate(Flag):
        mine = np.flatnonzero((which == i) & placed)
        if not len(mine):
            continue
        shown = _thin(mine)
        total = result.qc.count(flag)
        drawn = "" if len(mine) == total else f"; {q.COUNT.format(len(mine))} drawn"
        axis.scatter(
            x[shown],
            lat[shown],
            s=6,
            color=theme.cycle[i % len(theme.cycle)],
            linewidths=0,
            label=f"Flagged: {_flag_name(flag)} ({q.COUNT.format(total)}{drawn})",
            transform=frame,
            zorder=5,
        )

    if result.qc.steps:
        good_seconds = geodesy.epoch_seconds(best["time"][track])
        at = np.clip(
            np.searchsorted(good_seconds, [t for t, _ in result.qc.steps]),
            0,
            len(good_seconds) - 1,
        )
        step_index = np.flatnonzero(track)[at]
        axis.scatter(
            x[step_index],
            lat[step_index],
            marker="x",
            s=30,
            color="0.15",
            linewidths=0.9,
            label=f"Position steps ({len(result.qc.steps)})",
            transform=frame,
            zorder=6,
        )

    ends = np.flatnonzero(track)
    if len(ends):
        for index, marker, label in ((ends[0], "^", "Start"), (ends[-1], "s", "End")):
            axis.scatter(
                x[index],
                lat[index],
                marker=marker,
                s=40,
                color=colors.primary,
                edgecolors="white",
                linewidths=0.8,
                label=label,
                transform=frame,
                zorder=7,
            )

    gridlines = axis.gridlines(draw_labels=True, linewidth=0.3, color="0.6", alpha=0.6)
    if wraps:
        # cartopy's default meridians are worked out in ±180°, which leaves only 180° in view
        ticks = MaxNLocator(nbins=6).tick_values(view[0], view[1])
        gridlines.xlocator = FixedLocator([(tick + 180) % 360 - 180 for tick in ticks])
    gridlines.top_labels = gridlines.right_labels = False
    gridlines.xlabel_style = gridlines.ylabel_style = {"size": _pt(theme, 7)}
    points = np.column_stack([x[track] + center_lon, lat[track]])
    corners = _corners_by_emptiness(points[:: max(1, len(points) // 2000)], view)
    # What a legend or the inset mustn't cover: the track at an even spacing in time, and
    # every kind of marker
    on_track = np.flatnonzero(track)
    on_track = on_track[:: max(1, len(on_track) // OCCUPIED_TRACK_POINTS)]
    flagged = _thin(np.flatnonzero(~good & placed))
    marked = [on_track, flagged, ends[:1], ends[-1:]]
    if result.qc.steps:
        marked.append(step_index)
    occupied = np.concatenate(marked).astype(int)
    occupied_x, occupied_lat = x[occupied], lat[occupied]
    if len(result.control):
        occupied_x = np.concatenate([occupied_x, control_x])
        occupied_lat = np.concatenate([occupied_lat, result.control["lat"].to_numpy(float)])
    _place_legend_and_inset(
        figure, axis, frame, (occupied_x, occupied_lat), corners, center_lat, central, theme
    )
    axis.set_title(f"{result.config.identifier}: trackline", fontsize=_pt(theme, 10))
    return figure


# -- Time series --


def _date_axis(axis, theme: Theme) -> None:
    locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axis.tick_params(labelsize=_pt(theme, 7))


def _band(axis, times, values, breaks, color, label) -> None:
    centers, lows, highs = _envelope(times, values, breaks)
    axis.fill_between(centers, lows, highs, color=color, alpha=0.35, linewidth=0, label=label)
    axis.plot(centers, highs, color=color, linewidth=0.6)


def speed_and_acceleration(result: NavQAResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """Speed over ground and QC's acceleration at each good fix against time, with the limits
    (the acceleration limit as QC used it, after any allowance for the coordinates' resolution)
    """
    with themed(theme):
        return _speed_and_acceleration(result, theme)


def _speed_and_acceleration(result: NavQAResult, theme: Theme) -> Figure:
    colors = theme.colors
    th = result.config.thresholds
    best = result.bestres[result.bestres["flags"] == 0]
    times = _naive_utc(best["time"])
    seconds = geodesy.epoch_seconds(best["time"])
    acceleration = acceleration_at_fixes(
        seconds,
        best["lon"].to_numpy(dtype=float),
        best["lat"].to_numpy(dtype=float),
        th.accel_time_base,
    )
    breaks = _gaps(result)

    figure, (top, bottom) = plt.subplots(
        2, 1, figsize=(FIGURE_WIDTH, 5.0), sharex=True, layout="constrained"
    )
    _band(top, times, best["sog"].to_numpy(dtype=float), breaks, colors.primary, "Good fixes")
    top.axhline(
        th.max_speed,
        color=colors.secondary,
        linestyle="--",
        linewidth=0.9,
        label=f"max_speed {th.max_speed:g} {q.SPEED_MADE_GOOD.unit}",
    )
    top.set_ylabel(_y_label(q.SPEED_MADE_GOOD), fontsize=_pt(theme, 8))
    top.set_ylim(bottom=0)
    _band(bottom, times, acceleration, breaks, colors.primary, "Good fixes")
    bottom.axhline(
        result.qc.accel_limit,
        color=colors.secondary,
        linestyle="--",
        linewidth=0.9,
        label=f"QC limit {result.qc.accel_limit:g} {q.HORIZONTAL_ACCELERATION.unit}",
    )
    bottom.set_ylabel(_y_label(q.HORIZONTAL_ACCELERATION), fontsize=_pt(theme, 8))
    bottom.set_ylim(bottom=0)
    bottom.set_xlabel(q.ISO_TIME.label, fontsize=_pt(theme, 8))
    for axis in (top, bottom):
        _date_axis(axis, theme)
        axis.grid(linewidth=0.3, alpha=0.5)
        axis.legend(fontsize=_pt(theme, 6.5), loc="upper right")
    top.set_title(
        f"Speed and acceleration (over {th.accel_time_base:g} s, as QC tests it)",
        fontsize=_pt(theme, 10),
    )
    return figure


def timeline(result: NavQAResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """When there are fixes, the gaps of `max_gap` or longer, and the flagged fixes by flag"""
    with themed(theme):
        return _timeline(result, theme)


def _timeline(result: NavQAResult, theme: Theme) -> Figure:
    colors = theme.colors
    th = result.config.thresholds
    frame = result.nav.frame
    flags = result.qc.flags
    times = _naive_utc(frame["time"])
    # A row per flag the QC table has, with its count (every record with the flag); each record
    # is marked once, in the row of its lowest flag, so a row may say how many are above (#69)
    which = _flag_index(flags)
    present = [(i, flag) for i, flag in enumerate(Flag) if result.qc.count(flag)]
    rows = ["Fixes", f"Gaps of {th.max_gap:g} s or longer"]
    for i, flag in present:
        total, here = result.qc.count(flag), int((which == i).sum())
        label = f"{_flag_name(flag)} ({q.COUNT.format(total)})"
        if here < total:
            label += f"\n{q.COUNT.format(total - here)} in the rows above"
        rows.append(label)

    figure, axis = plt.subplots(
        figsize=(FIGURE_WIDTH, 1.2 + 0.32 * len(rows)), layout="constrained"
    )
    sequence = _in_sequence(result)
    in_order = times[sequence]
    breaks = _gaps(result)
    if len(in_order):
        starts = [in_order[0], *[end for _, end in breaks]]
        ends = [*[start for start, _ in breaks], in_order[-1]]
        for start, end in zip(starts, ends, strict=True):
            axis.barh(0, end - start, left=start, height=0.6, color=colors.primary)
    for start, end in breaks:
        axis.barh(1, end - start, left=start, height=0.6, color=colors.secondary)
    for row, (i, _flag) in enumerate(present, start=2):
        mine = np.flatnonzero(which == i)
        shown = _thin(mine)
        valid = ~np.isnat(times[shown])
        axis.scatter(
            times[shown][valid],
            np.full(int(valid.sum()), row),
            marker="|",
            s=40,
            color=theme.cycle[i % len(theme.cycle)],
            linewidths=0.8,
        )
    axis.set_yticks(range(len(rows)), rows, fontsize=_pt(theme, 7))
    axis.set_ylim(len(rows) - 0.5, -0.5)
    axis.set_xlabel(q.ISO_TIME.label, fontsize=_pt(theme, 8))
    _date_axis(axis, theme)
    axis.grid(axis="x", linewidth=0.3, alpha=0.5)
    axis.set_title(
        f"Coverage, gaps and QC flags ({q.FIXES.format(len(frame))} records)",
        fontsize=_pt(theme, 10),
    )
    return figure


# -- Histograms --


def histograms(result: NavQAResult, theme: Theme = DEFAULT_THEME) -> Figure:
    """The sampling interval, satellites, HDOP and GGA quality of the records within the dates"""
    with themed(theme):
        return _histograms(result, theme)


def _histograms(result: NavQAResult, theme: Theme) -> Figure:
    colors = theme.colors
    th = result.config.thresholds
    frame = result.nav.frame
    dated = (result.qc.flags & int(Flag.DATE_RANGE)) == 0
    seconds = geodesy.epoch_seconds(frame["time"][_in_sequence(result)])
    steps = np.diff(seconds)
    steps = steps[steps > 0]

    figure, axes = plt.subplots(2, 2, figsize=(FIGURE_WIDTH, 5.2), layout="constrained")
    (interval, satellites), (hdop, quality) = axes

    if len(steps):
        low, high = max(steps.min(), 1e-3), max(steps.max(), steps.min() * 1.01, 1e-3)
        bins = np.geomspace(low, high * 1.0001, 40)
        interval.hist(steps, bins=bins, color=colors.primary)
        interval.set_xscale("log")
        interval.axvline(
            th.max_gap,
            color=colors.secondary,
            linestyle="--",
            linewidth=0.9,
            label=f"max_gap {th.max_gap:g} s",
        )
        interval.legend(fontsize=_pt(theme, 6.5))
    _log_y(interval)
    interval.set_xlabel(q.SAMPLING_INTERVAL.label, fontsize=_pt(theme, 8))

    nsat = frame["nsat"][dated].dropna().to_numpy(dtype=int)
    if len(nsat):
        values, counts = np.unique(nsat, return_counts=True)
        satellites.bar(values, counts, color=colors.primary, width=0.8)
        satellites.axvline(
            th.min_satellites - 0.5,
            color=colors.secondary,
            linestyle="--",
            linewidth=0.9,
            label=f"min_satellites {th.min_satellites}",
        )
        satellites.legend(fontsize=_pt(theme, 6.5))
    satellites.set_xlabel(q.NSV.label, fontsize=_pt(theme, 8))

    values = frame["hdop"][dated].to_numpy(dtype=float)
    values = values[~np.isnan(values)]
    if len(values):
        hdop.hist(np.clip(values, 0, th.max_hdop), bins=40, color=colors.primary)
        _log_y(hdop)
    hdop.set_xlabel(q.HDOP.label, fontsize=_pt(theme, 8))

    codes = frame["quality"][dated].dropna().to_numpy(dtype=int)
    if len(codes):
        values, counts = np.unique(codes, return_counts=True)
        valid = (values >= th.min_quality) & (values <= th.max_quality)
        quality.bar(
            range(len(values)),
            counts,
            color=[colors.primary if ok else colors.secondary for ok in valid],
        )
        quality.set_xticks(
            range(len(values)),
            [f"{v} {QUALITY_NAMES.get(v, '')}".strip() for v in values],
            fontsize=_pt(theme, 6.5),
            rotation=30,
            ha="right",
        )
        _log_y(quality)
    quality.set_xlabel(q.NMEA_QUALITY.label, fontsize=_pt(theme, 8))

    for axis in axes.flat:
        axis.set_ylabel(q.FIXES.label, fontsize=_pt(theme, 8))
        axis.tick_params(labelsize=_pt(theme, 7))
        axis.grid(axis="y", linewidth=0.3, alpha=0.5)
    figure.suptitle("Sampling and fix quality", fontsize=_pt(theme, 10))
    return figure


FIGURES = {
    "track_map": track_map,
    "speed_acceleration": speed_and_acceleration,
    "timeline": timeline,
    "histograms": histograms,
}
