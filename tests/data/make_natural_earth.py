"""Writes the Natural Earth layers clipped to the committed reference filesets' areas (#115).

    uv run python tests/data/make_natural_earth.py

reads the full layers from cartopy's data directory (`nav-qa-processing fetch-map-data`) and
writes tests/data/natural_earth/, in cartopy's layout, so a test can point cartopy's data_dir
there and run the over-water check offline. Only the boxes below are covered: a test over
anywhere else would find no land, so only tests marked `map_data(clipped=True)` use it.
"""

from pathlib import Path

import cartopy
import shapefile
import shapely
from cartopy.io import shapereader
from shapely.geometry.polygon import orient

from nav_qa_processing import maps

OUT = Path(__file__).parent / "natural_earth"
# (west, east, south, north) [degree]: each fileset's track with 2° around it, beyond the
# over-water check's window (0.5° of latitude, widened in longitude at high latitude)
AREAS = {
    "SP2613, SP2503 (San Diego)": (-119.5, -115.2, 30.6, 34.7),
    "BH19-30 (Duluth)": (-94.1, -90.0, 44.8, 48.8),
    "RC0014 (Seattle)": (-124.5, -120.3, 45.6, 49.8),
}


def clipped(path: Path) -> list:
    """The layer's geometries within the areas, clipped to them"""
    parts = []
    for west, east, south, north in AREAS.values():
        window = shapely.box(west, south, east, north)
        for geometry in shapereader.Reader(
            str(path), bbox=(west, south, east, north)
        ).geometries():
            part = geometry.intersection(window)
            if not part.is_empty:
                parts.append(part)
    return parts


def write(path: Path, geometries: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    polygons = any(g.geom_type in ("Polygon", "MultiPolygon") for g in geometries)
    with shapefile.Writer(
        str(path), shapeType=shapefile.POLYGON if polygons else shapefile.POLYLINE
    ) as out:
        out.field("featurecla", "C", 40)
        for geometry in geometries:
            for part in shapely.get_parts(geometry):
                if polygons and part.geom_type == "Polygon":
                    # shapefile rings: the exterior clockwise, holes counterclockwise
                    part = orient(part, sign=-1.0)
                    rings = [list(part.exterior.coords)] + [list(r.coords) for r in part.interiors]
                    out.poly(rings)
                elif not polygons and part.geom_type == "LineString":
                    out.line([list(part.coords)])
                else:
                    continue
                out.record(path.stem)


def main() -> None:
    source = Path(cartopy.config["data_dir"]) / "shapefiles" / "natural_earth"
    for scale, category, name in maps._layers():
        relative = Path("shapefiles") / "natural_earth" / category / f"ne_{scale}_{name}.shp"
        write(OUT / relative, clipped(source / category / f"ne_{scale}_{name}.shp"))
        print(f"wrote {OUT / relative}")


if __name__ == "__main__":
    main()
