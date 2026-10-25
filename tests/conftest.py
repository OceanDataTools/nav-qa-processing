import os
from pathlib import Path

import pytest

from nav_qa_processing import maps

os.environ.setdefault("MPLBACKEND", "Agg")

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
DATA = ROOT / "data"
CONFIGS = ROOT / "configs"


@pytest.fixture(autouse=True, scope="session")
def no_map_data():
    """No test downloads Natural Earth, or depends on whether it's cached (#110): the whole
    session runs without map data (maps without coastlines, the over-water check n/a), set
    before any fixture, module-scoped ones included, and through the environment, so a
    notebook's kernel has none either
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(maps.NO_MAP_DATA, "1")
        yield


@pytest.fixture(autouse=True)
def map_data(request, monkeypatch):
    """A test marked `map_data` uses Natural Earth when it's already cached, never downloading"""
    if request.node.get_closest_marker("map_data") and maps.cached():
        monkeypatch.delenv(maps.NO_MAP_DATA)
