"""Core modules never import the integrations, so core works without them."""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parents[1] / "src" / "nav_qa_processing"
NOT_CORE = {"cli.py"}  # puts every command together: see the test below


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


def test_the_cli_meets_the_integrations_only_through_their_cli_modules():
    """#145: core's cli.py adds each integration's commands (its cli.add_commands) and imports
    nothing else of them; the commands themselves live in the integrations
    """
    imported = imported_modules(PACKAGE / "cli.py")
    integrations = {m for m in imported if m.startswith("nav_qa_processing.integrations")}
    allowed = {
        "nav_qa_processing.integrations.openvdm",
        "nav_qa_processing.integrations.openvdm.cli",
        "nav_qa_processing.integrations.r2r",
        "nav_qa_processing.integrations.r2r.cli",
    }
    assert integrations <= allowed, sorted(integrations - allowed)
