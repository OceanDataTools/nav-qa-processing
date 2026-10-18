"""The land under the fixes, for the "Over water" check (#79).

`load(lon, lat)` -> `Land`: Natural Earth's 1:10m land less its lakes (the layers the track map
draws), around the positions, or None when the layers can't be loaded (e.g. at sea without
`fetch-map-data`), with its river centerlines as water too (#85). `Land.inland(lon, lat)` gives
each position's distance on land to the nearest water [m]: a coast or a river centerline.

The 1:10m coastline is coarse in harbors and channels: on RR2212 (Oakland's inner harbor), RR2406
(Morehead City) and FKt230417 (the dock at San Juan), fixes of a ship under way or alongside lie
up to 1.44 km inside it. The check therefore warns only of fixes more than `max_inland` inland,
and isn't a QC flag: the coastline is too coarse to drop fixes from the products by.

Ports up rivers aren't water in the land and lakes polygons: RR1802 at Portland's Swan Island
on the Willamette came out 43 km inland, KN220-02 on Charleston's Cooper River 2.5 km. Their
river centerlines pass 1.8 km and 1.1 km away, so a fix near one counts as on the water
(`rivers_lake_centerlines`). Without that layer, the check uses land and lakes alone.
"""

import logging
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.affinity import scale
from shapely.geometry.base import BaseGeometry

from nav_qa_processing import geodesy, maps
from nav_qa_processing.config import Extent

logger = logging.getLogger(__name__)

# [degree of latitude] around the positions, so the nearest coast is in the window; widened in
# longitude by 1/cos(latitude), so it's as far east and west as north and south
MARGIN = 0.5


@dataclass(frozen=True)
class Land:
    """Land polygons with longitudes relative to `central` [degree east], so a window across
    the antimeridian stays whole, and river centerlines (lines) in the same frame. The nearest
    water (a coast or a river) is found on a plane whose longitudes are scaled by
    `cos(latitude)` at the window's middle (`x_scale`), then measured geodesically
    """

    geometry: BaseGeometry
    central: float = 0.0
    x_scale: float = 1.0
    rivers: BaseGeometry | None = None
    # (west, east, south, north) the layers were clipped to, relative to `central`: its edges
    # aren't coast (#79 review)
    window: tuple[float, float, float, float] | None = None
    plane: BaseGeometry = field(init=False, repr=False, compare=False)
    water: BaseGeometry = field(init=False, repr=False, compare=False)  # coasts and rivers
    edge: BaseGeometry | None = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        flat = lambda g: scale(g, xfact=self.x_scale, yfact=1.0, origin=(0, 0))
        plane = flat(polygons(self.geometry))
        shapely.prepare(plane)
        water = plane.boundary
        edge = None
        if self.window is not None:
            west, east, south, north = self.window
            edge = flat(shapely.box(west, south, east, north)).exterior
            water = water.difference(edge.buffer(1e-9))
        if self.rivers is not None and not self.rivers.is_empty:
            water = shapely.union(water, flat(self.rivers))
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "water", water)
        object.__setattr__(self, "edge", edge)

    def inland(self, lon, lat) -> np.ndarray:
        """Each position's geodesic distance on land to the nearest coast or river centerline
        [m], 0 at sea, NaN without a position. With no coast or river in the window, the
        distance to the window's edge: a lower bound, beyond `MARGIN`
        """
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        result = np.where(np.isnan(lon) | np.isnan(lat), np.nan, 0.0)
        index = np.flatnonzero(~np.isnan(result))
        if self.geometry.is_empty or not len(index):
            return result
        x = (lon[index] - self.central + 180.0) % 360.0 - 180.0
        points = shapely.points(x * self.x_scale, lat[index])
        inside = shapely.contains(self.plane, points)
        if inside.any():
            # shortest_line runs from the water to the point
            nearest = self.water if not self.water.is_empty else self.edge
            if nearest is None or nearest.is_empty:  # no window given and no coast: unmeasured
                result[index[inside]] = np.nan
                return result
            ends = shapely.get_coordinates(shapely.shortest_line(nearest, points[inside]))
            water = ends.reshape(-1, 2, 2)[:, 0]
            k = index[inside]
            water_lon = water[:, 0] / self.x_scale + self.central
            result[k] = geodesy.inverse(lon[k], lat[k], water_lon, water[:, 1])[0]
        return result


def load(lon, lat) -> Land | None:
    """Natural Earth's 1:10m land less its lakes around the positions, with its river
    centerlines, or None when the land or lakes can't be loaded
    """
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    placed = ~(np.isnan(lon) | np.isnan(lat))
    extent = Extent.around(lon[placed], lat[placed])
    if extent is None:
        return Land(shapely.Polygon())
    west, east = extent.westernmost, extent.easternmost
    if extent.crosses_antimeridian:
        east += 360.0
    central = (west + east) / 2
    poleward = max(abs(extent.southernmost), abs(extent.northernmost)) + MARGIN
    wide = MARGIN / max(np.cos(np.radians(min(poleward, 89.0))), 0.05)
    window = (
        west - wide,
        east + wide,
        max(extent.southernmost - MARGIN, -90.0),
        min(extent.northernmost + MARGIN, 90.0),
    )
    land = maps.clipped_geometries("physical", "land", "10m", window, central)
    lakes = maps.clipped_geometries("physical", "lakes", "10m", window, central)
    if land is None or lakes is None:
        return None
    geometry = shapely.union_all(land) if land else shapely.Polygon()
    if lakes:
        geometry = geometry.difference(shapely.union_all(lakes))
    rivers = maps.clipped_geometries("physical", "rivers_lake_centerlines", "10m", window, central)
    if rivers is None:
        logger.warning("no river centerlines: the over-water check uses land and lakes alone")
    middle = (window[2] + window[3]) / 2
    return Land(
        geometry,
        central,
        max(np.cos(np.radians(middle)), 0.05),
        shapely.union_all(rivers) if rivers else None,
        (window[0] - central, window[1] - central, window[2], window[3]),
    )


def polygons(geometry: BaseGeometry) -> BaseGeometry:
    """The polygons of a geometry: clipping a layer to the window can leave lines or points
    where a polygon only touches its edge, and a collection with them has no boundary (#79
    review)
    """
    parts = [
        part
        for part in shapely.get_parts(geometry)
        if part.geom_type in ("Polygon", "MultiPolygon") and not part.is_empty
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()
