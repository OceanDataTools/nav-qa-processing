"""Natural Earth map data for the trackline map, via cartopy.

cartopy downloads Natural Earth layers on first use into its data directory
(cartopy.config["data_dir"]). At sea without internet, run `nav-qa-processing fetch-map-data`
beforehand; if a layer still can't be loaded, the map is drawn without it.

Natural Earth geometries are large (a land polygon can be a whole continent), so they are clipped
to the map view before plotting; otherwise every SVG would carry entire coastlines.

cartopy's download notices (`DownloadWarning`) are logged rather than warned, so they don't land
in a notebook's first output. With the environment variable NAV_QA_NO_MAP_DATA set, no layer is
loaded or downloaded: maps are drawn without coastlines and the over-water check is n/a, as at
sea without the data. The tests set it (#110); it reaches a notebook's kernel, as a patch
wouldn't.
"""

import logging
import os
import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import cartopy
import cartopy.feature as cfeature
from cartopy.io import DownloadWarning
from shapely.affinity import translate
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from nav_qa_processing.network import reaching

logger = logging.getLogger(__name__)

# (category, name) of the layers the maps and the over-water check use; detail maps use 10m,
# the world inset 110m
DETAIL_LAYERS = (
    ("physical", "land"),
    ("physical", "lakes"),
    ("physical", "coastline"),
    ("physical", "rivers_lake_centerlines"),  # water for the over-water check (#85)
)
WORLD_LAYERS = (("physical", "land"),)
NO_MAP_DATA = "NAV_QA_NO_MAP_DATA"


def _feature(category: str, name: str, scale: str) -> cfeature.NaturalEarthFeature:
    if os.environ.get(NO_MAP_DATA):
        raise OSError(f"map data disabled ({NO_MAP_DATA} is set)")
    return cfeature.NaturalEarthFeature(category, name, scale)


def _layers() -> Iterator[tuple[str, str, str]]:
    """(scale, category, name) of every layer the maps and the over-water check use"""
    for scale, layers in (("10m", DETAIL_LAYERS), ("110m", WORLD_LAYERS)):
        for category, name in layers:
            yield scale, category, name


def cached() -> bool:
    """Whether every layer is in cartopy's data directory, so nothing would be downloaded"""
    root = Path(cartopy.config["data_dir"]) / "shapefiles" / "natural_earth"
    return all(
        (root / category / f"ne_{scale}_{name}.shp").exists()
        for scale, category, name in _layers()
    )


@contextmanager
def _downloads_logged() -> Iterator[None]:
    """cartopy's download notices as log messages, not warnings"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DownloadWarning)
        yield
    for warning in caught:
        if issubclass(warning.category, DownloadWarning):
            logger.info("%s", warning.message)
        else:
            warnings.warn_explicit(
                warning.message, warning.category, warning.filename, warning.lineno
            )


def clipped_geometries(
    category: str,
    name: str,
    scale: str,
    extent: tuple[float, float, float, float],
    central_longitude: float = 0.0,
) -> list[BaseGeometry] | None:
    """Geometries of a Natural Earth layer clipped to (west, east, south, north), with longitudes
    relative to central_longitude, or None if the layer can't be loaded (e.g. not downloaded and
    no network). The window may run past ±180 (e.g. 170 to 190 across the antimeridian): each
    side of 180 is clipped separately and shifted into place
    """
    try:
        with _downloads_logged():
            clipped = _clip(_feature(category, name, scale), extent, central_longitude)
    except Exception as error:  # noqa: BLE001 - network, cache and shapefile errors all mean "no layer"
        logger.warning("Natural Earth %s %s %s unavailable: %s", scale, category, name, error)
        return None
    return clipped


def _clip(feature, extent, central_longitude: float) -> list[BaseGeometry]:
    """A feature's geometries clipped to (west, east, south, north), shifted relative to
    central_longitude; each side of 180 clipped separately
    """
    west, east, south, north = extent
    clipped = []
    for shift in (-360.0, 0.0, 360.0):
        low, high = max(west + shift, -180.0), min(east + shift, 180.0)
        if low >= high:
            continue
        window = box(low, south, high, north)
        geometries: Iterable[BaseGeometry] = feature.intersecting_geometries(
            (low, high, south, north)
        )
        for geometry in geometries:
            part = geometry.intersection(window)
            if not part.is_empty:
                clipped.append(translate(part, xoff=-shift - central_longitude))
    return clipped


def world_geometries(category: str, name: str) -> list[BaseGeometry] | None:
    try:
        with _downloads_logged():
            return list(_feature(category, name, "110m").geometries())
    except Exception as error:  # noqa: BLE001
        logger.warning("Natural Earth 110m %s %s unavailable: %s", category, name, error)
        return None


def prefetch() -> list[str]:
    """Downloads every layer the maps use into cartopy's data directory"""
    downloader = cartopy.config["downloaders"].get(("shapefiles", "natural_earth"))
    url = getattr(downloader, "url_template", "https://naturalearth.s3.amazonaws.com/")
    fetched = []
    for scale, category, name in _layers():
        # Touching the geometries triggers cartopy's download and caches the shapefile
        with reaching("Natural Earth", url), _downloads_logged():
            next(iter(_feature(category, name, scale).geometries()), None)
        fetched.append(f"{scale} {category} {name}")
    return fetched
