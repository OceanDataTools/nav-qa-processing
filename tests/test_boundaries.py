"""Core modules never import the integrations, so core works without them."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / "src" / "nav_qa_processing"
NOT_CORE = {"cli.py"}  # puts every command together


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules |= {f"{node.module}.{alias.name}" for alias in node.names}
    return modules


CORE = [
    p
    for p in sorted(PACKAGE.rglob("*.py"))
    if p.name not in NOT_CORE and "integrations" not in p.relative_to(PACKAGE).parts
]


@pytest.mark.parametrize("path", CORE, ids=lambda p: p.relative_to(PACKAGE).as_posix())
def test_core_doesnt_import_integrations(path):
    integrations = {m for m in imported_modules(path) if "nav_qa_processing.integrations" in m}
    assert not integrations, f"{path.name} imports {sorted(integrations)}"
