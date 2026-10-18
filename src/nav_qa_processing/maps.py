"""Natural Earth map data for the trackline map, via cartopy.

cartopy downloads Natural Earth layers on first use into its data directory
(cartopy.config["data_dir"]). At sea without internet, run `nav-qa-processing fetch-map-data`
beforehand; if a layer still can't be loaded, the map is drawn without it.

Natural Earth geometries are large (a land polygon can be a whole continent), so they are clipped
to the map view before plotting; otherwise every SVG would carry entire coastlines.
"""

import logging
from collections.abc import Iterable

import cartopy
import cartopy.feature as cfeature
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


def _feature(category: str, name: str, scale: str) -> cfeature.NaturalEarthFeature:
    return cfeature.NaturalEarthFeature(category, name, scale)


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
    west, east, south, north = extent
    clipped = []
    try:
        feature = _feature(category, name, scale)
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
    except Exception as error:  # noqa: BLE001 - network, cache and shapefile errors all mean "no layer"
        logger.warning("Natural Earth %s %s %s unavailable: %s", scale, category, name, error)
        return None
    return clipped


def world_geometries(category: str, name: str) -> list[BaseGeometry] | None:
    try:
        return list(_feature(category, name, "110m").geometries())
    except Exception as error:  # noqa: BLE001
        logger.warning("Natural Earth 110m %s %s unavailable: %s", category, name, error)
        return None


def prefetch() -> list[str]:
    """Downloads every layer the maps use into cartopy's data directory"""
    downloader = cartopy.config["downloaders"].get(("shapefiles", "natural_earth"))
    url = getattr(downloader, "url_template", "https://naturalearth.s3.amazonaws.com/")
    fetched = []
    for scale, layers in (("10m", DETAIL_LAYERS), ("110m", WORLD_LAYERS)):
        for category, name in layers:
            # Touching the geometries triggers cartopy's download and caches the shapefile
            with reaching("Natural Earth", url):
                next(iter(_feature(category, name, scale).geometries()), None)
            fetched.append(f"{scale} {category} {name}")
    return fetched
