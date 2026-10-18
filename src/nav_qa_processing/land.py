"""The land under the fixes, for the "Over water" check (#79).

`load(lon, lat)` -> `Land`: Natural Earth's 1:10m land less its lakes (the layers the track map
draws), around the positions, or None when the layers can't be loaded (e.g. at sea without
`fetch-map-data`). `Land.inland(lon, lat)` gives each position's distance inside the land [m].

The 1:10m coastline is coarse in harbors and channels: on RR2212 (Oakland's inner harbor), RR2406
(Morehead City) and FKt230417 (the dock at San Juan), fixes of a ship under way or alongside lie
up to 1.44 km inside it. The check therefore counts only fixes more than `land_tolerance` inland,
and isn't a QC flag: the coastline is too coarse to drop fixes from the products by.
"""

from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.affinity import scale
from shapely.geometry.base import BaseGeometry

from nav_qa_processing import geodesy, maps
from nav_qa_processing.config import Extent

MARGIN = 0.5  # [degree] around the positions, so the nearest coast is in the window


@dataclass(frozen=True)
class Land:
    """Land polygons with longitudes relative to `central` [degree east], so a window across
    the antimeridian stays whole. The nearest coast is found on a plane whose longitudes are
    scaled by `cos(latitude)` at the window's middle (`x_scale`), then measured geodesically
    """

    geometry: BaseGeometry
    central: float = 0.0
    x_scale: float = 1.0
    plane: BaseGeometry = field(init=False, repr=False, compare=False)
    coast: BaseGeometry = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        plane = scale(self.geometry, xfact=self.x_scale, yfact=1.0, origin=(0, 0))
        shapely.prepare(plane)
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "coast", plane.boundary)

    def inland(self, lon, lat) -> np.ndarray:
        """Each position's geodesic distance to the nearest coast when it's on land [m], 0 at
        sea, NaN without a position
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
            # shortest_line runs from the coast to the point
            ends = shapely.get_coordinates(shapely.shortest_line(self.coast, points[inside]))
            coast = ends.reshape(-1, 2, 2)[:, 0]
            k = index[inside]
            coast_lon = coast[:, 0] / self.x_scale + self.central
            result[k] = geodesy.inverse(lon[k], lat[k], coast_lon, coast[:, 1])[0]
        return result


def load(lon, lat) -> Land | None:
    """Natural Earth's 1:10m land less its lakes around the positions, or None when the layers
    can't be loaded
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
    window = (
        west - MARGIN,
        east + MARGIN,
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
    middle = (window[2] + window[3]) / 2
    return Land(geometry, central, max(np.cos(np.radians(middle)), 0.05))
