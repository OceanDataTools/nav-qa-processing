import os
import re
import tarfile
import tempfile
from pathlib import Path

import cartopy
import pytest

from nav_qa_processing import maps

os.environ.setdefault("MPLBACKEND", "Agg")

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
DATA = ROOT / "data"
# The reference filesets' cruise TOMLs, from `r2r config-from-catalog` (#153)
CONFIGS = Path(__file__).parent / "configs"
# The example site and theme TOMLs; users' own configs sit beside them, ignored by git
EXAMPLES = ROOT / "configs"
# Reference filesets committed to the repo (#115), unpacked into data/ when they aren't there,
# so the tests and CI need nothing from R2R; Natural Earth clipped to their areas
COMMITTED = Path(__file__).parent / "data"
CLIPPED_MAP_DATA = COMMITTED / "natural_earth"


def pytest_sessionstart(session):
    """Unpack each committed reference fileset that data/ doesn't have (a fetched copy stays).
    Into a temporary directory, then renamed into place: with `pytest -n`, each worker starts a
    session too, and one that loses the race drops its copy, so none sees half a fileset
    """
    for archive in sorted(COMMITTED.glob("*_gnss.tar.xz")):
        fileset = DATA / archive.name.removesuffix(".tar.xz")
        if fileset.exists():
            continue
        DATA.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=DATA) as unpacking:
            with tarfile.open(archive) as tar:
                tar.extractall(unpacking, filter="data")
            try:
                Path(unpacking, fileset.name).rename(fileset)
            except OSError:  # another worker's copy got there first
                pass


# A reference fileset's ID, e.g. SP2220_150139, as a test's `name` parameter
REFERENCE = re.compile(r"[A-Za-z0-9-]+_\d+")


def pytest_collection_modifyitems(items):
    """With `pytest -n auto --dist loadgroup`, the tests on one reference fileset run on one
    worker, so its run (test_r2r_references.ours, cached per process) is made once. The
    fileset's tests in test_r2r_references are marked; a test taking it as `name` is grouped here
    """
    for item in items:
        name = getattr(item, "callspec", None) and item.callspec.params.get("name")
        if isinstance(name, str) and REFERENCE.fullmatch(name):
            item.add_marker(pytest.mark.xdist_group(name))


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
    """A test marked `map_data` uses Natural Earth when it's already cached, never downloading.
    Marked `map_data(clipped=True)`, its area is in the committed clip (the committed filesets'),
    which it uses without the full data, as in CI
    """
    marker = request.node.get_closest_marker("map_data")
    if marker is None:
        return
    if not maps.cached() and marker.kwargs.get("clipped"):
        monkeypatch.setitem(cartopy.config, "data_dir", str(CLIPPED_MAP_DATA))
    if maps.cached():
        monkeypatch.delenv(maps.NO_MAP_DATA)
