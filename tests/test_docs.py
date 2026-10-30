"""The docs' commands stay true to the CLI: the openvdm.yaml hooks, which only INSTALL.md shows,
and every nav-qa-processing command in the docs' shell examples parse with the CLI's own parser,
so a renamed or removed option fails here rather than leaving the docs stale.
"""

import re
import shlex

import pytest
import yaml
from conftest import ROOT

from nav_qa_processing.cli import build_parser

DOCS = ("README.md", "INSTALL.md", "COOKBOOK.md", "docs/performance.md")
INSTALLED = "/opt/nav-qa-processing/.venv/bin/nav-qa-processing"
# OpenVDM's placeholders, as it fills them in
PLACEHOLDERS = {
    "{collectionSystemTransferName}": "OpenRVDAS",
    "{newFiles}": "raw/POSMV_GGA-2026-07-09.txt",
    "{updatedFiles}": "raw/POSMV_GGA-2026-07-08.txt",
}


def blocks(name: str, language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", (ROOT / name).read_text(), re.DOTALL)


def hook_blocks(name: str) -> list[str]:
    return [block for block in blocks(name, "yaml") if "postHookCommands" in block]


def test_only_install_md_shows_the_hooks():
    """The openvdm.yaml hooks have one home; the README and COOKBOOK link to it"""
    assert len(hook_blocks("INSTALL.md")) == 1
    assert hook_blocks("README.md") == hook_blocks("COOKBOOK.md") == []


def parsed(command: list[str]):
    assert command[0] == INSTALLED  # the install from INSTALL.md's step 1
    args = command[1:]
    for placeholder, value in PLACEHOLDERS.items():
        args = [arg.replace(placeholder, value) for arg in args]
    return build_parser().parse_args(args)


def test_the_hooks_commands_parse():
    (block,) = hook_blocks("INSTALL.md")
    hooks = yaml.safe_load(block)["postHookCommands"]
    (transfer,) = hooks["postCollectionSystemTransfer"]
    post = parsed(transfer["commandList"][0]["command"])
    final = parsed(hooks["preFinalizeCurrentCruise"]["commandList"][0]["command"])
    # The same transfer and site TOML; the transfer's name written out where OpenVDM has none
    assert post.transfer == final.transfer == transfer["collectionSystemTransferName"]
    assert post.site_config == final.site_config
    assert post.changed_files and post.min_interval
    assert final.changed_files is None and final.min_interval is None  # the final reports


# The command starting a line, maybe after `uv run` or `sudo`: not a path or a name it's part of
COMMAND = re.compile(r"\s*(?:uv run\s+|sudo\s+)?(?:\S*/bin/)?nav-qa-processing\s+(.*)$")


def documented_commands() -> list[tuple[str, str, list[str]]]:
    """(doc, line, arguments) for each nav-qa-processing command in the docs' bash blocks"""
    found = []
    for name in DOCS:
        for block in blocks(name, "bash"):
            for line in block.replace("\\\n", " ").splitlines():
                command = COMMAND.match(line)
                if command:
                    words = shlex.split(command.group(1), comments=True)
                    found.append((name, line.strip(), words))
    return found


@pytest.mark.parametrize(
    "doc, line, args",
    documented_commands(),
    ids=lambda value: value if isinstance(value, str) and value.endswith(".md") else "",
)
def test_documented_commands_parse(doc, line, args):
    try:
        build_parser().parse_args(args)
    except SystemExit as stop:
        assert stop.code == 0, f"{doc}: {line}"  # --version and --help exit 0; an error, 2
