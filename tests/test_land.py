"""The land under the fixes, for the "Over water" check (#79), on synthetic land."""

import numpy as np
import pytest
import shapely
from shapely.affinity import translate

from nav_qa_processing import land, maps
from nav_qa_processing.land import Land


def test_distance_inland():
    island = Land(shapely.box(-1, -1, 1, 1))
    inland = island.inland([0.0, 0.99, 2.0, np.nan], [0.5, 0.0, 0.0, 0.0])
    assert inland[0] == pytest.approx(55_300, rel=1e-3)  # half a degree south of 1° N
    assert inland[1] == pytest.approx(1_113, rel=1e-2)  # 0.01° of longitude at the equator
    assert inland[2] == 0.0  # at sea
    assert np.isnan(inland[3])  # no position


def test_land_across_the_antimeridian():
    island = Land(shapely.box(-1, -1, 1, 1), central=180.0)  # 179° E to 179° W
    inland = island.inland([-179.5, 179.5, 170.0], [0.0, 0.0, 0.0])
    assert inland[:2] == pytest.approx([55_660, 55_660], rel=1e-3)
    assert inland[2] == 0.0


def test_load_takes_the_lakes_out(monkeypatch):
    layers = {
        "land": [shapely.box(-1, -1, 1, 1)],
        "lakes": [shapely.box(-0.5, -0.5, 0.5, 0.5)],
        "rivers_lake_centerlines": [],
    }
    monkeypatch.setattr(
        maps,
        "clipped_geometries",
        lambda c, name, s, w, central: [translate(g, xoff=-central) for g in layers[name]],
    )
    found = land.load([0.0, 0.8], [0.0, 0.0])
    assert found is not None and found.central == pytest.approx(0.4)
    inland = found.inland([0.0, 0.8], [0.0, 0.0])
    assert inland[0] == 0.0 and inland[1] > 0  # on the lake, and on land


def test_a_river_is_water_too():
    """A dock up a river the land polygons don't draw (#85: RR1802 on the Willamette): the
    distance is to the river's centerline, not the coast
    """
    river = shapely.LineString([(0.0, -1.5), (0.0, 0.5)])  # from the sea into the island
    island = Land(shapely.box(-1, -1, 1, 1), rivers=river)
    inland = island.inland([0.01, 0.5], [0.0, 0.0])
    assert inland[0] == pytest.approx(1_113, rel=1e-2)  # 0.01° from the river
    assert inland[1] == pytest.approx(55_660, rel=1e-3)  # nearer the river than the coast


def test_without_the_rivers_layer_land_and_lakes_still_count(monkeypatch):
    layers = {"land": [shapely.box(-1, -1, 1, 1)], "lakes": [], "rivers_lake_centerlines": None}
    monkeypatch.setattr(maps, "clipped_geometries", lambda c, name, *a: layers[name])
    found = land.load([0.0], [0.0])
    assert found is not None and found.rivers is None
    assert found.inland([0.0], [0.0])[0] > 100_000


def test_without_map_data_there_is_no_land(monkeypatch):
    monkeypatch.setattr(maps, "_feature", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert land.load([-117.3], [32.6]) is None


def test_the_nearest_coast_away_from_the_equator():
    """At 60° N a degree of longitude is half a degree of latitude: the coast 0.6° east is
    nearer than the one 0.5° north
    """
    island = Land(shapely.box(-10, 50, 0.6, 60.5), x_scale=np.cos(np.radians(60)))
    assert island.inland([0.0], [60.0])[0] == pytest.approx(33_550, rel=1e-2)


def test_no_positions_no_land():
    found = land.load([np.nan], [np.nan])
    assert found is not None and found.geometry.is_empty
    assert np.isnan(found.inland([np.nan], [np.nan])[0])
