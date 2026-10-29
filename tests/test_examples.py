"""The example TOMLs in configs/ show every key their schema takes, commented or not, so a key
added to a schema has to be added to its example.
"""

import re
import tomllib

from conftest import EXAMPLES

from nav_qa_processing.config import CHECKED, CORE_KEYS, Thresholds, thresholds_from_table
from nav_qa_processing.integrations.openvdm.hook import SITE_KEYS
from nav_qa_processing.theme import _COLOR_KEYS, _KEYS

CONFIG = EXAMPLES / "config.example.toml"
SITE = EXAMPLES / "site.example.toml"
THEME = EXAMPLES / "theme.example.toml"


def shown(path) -> set[str]:
    """Every `table.key` the file sets or shows commented out (`# key = value`)"""
    table, keys = "", set()
    for line in path.read_text().splitlines():
        bare = line.lstrip("#").strip()
        if heading := re.fullmatch(r"\[\[?([\w.]+)\]\]?.*", bare):
            table = heading.group(1).split()[0]
        elif setting := re.match(r"(\w+)\s*=", bare):
            keys.add(f"{table}.{setting.group(1)}" if table else setting.group(1))
    return keys


def schema_keys(schema: dict, prefix: str = "") -> set[str]:
    keys = set()
    for name, value in schema.items():
        path = f"{prefix}.{name}" if prefix else name
        keys |= schema_keys(value, path) if isinstance(value, dict) else {path}
    return keys


def test_the_site_example_shows_every_key():
    keys = {key for key in schema_keys(SITE_KEYS) if key != "theme"}
    assert SITE_KEYS["theme"] is CHECKED  # theme.py checks it: its example shows its keys
    assert keys - shown(SITE) == set()
    assert "configs/theme.example.toml" in SITE.read_text()


def test_the_config_example_shows_every_key():
    """The core config TOML's keys; R2R's are the cruise TOMLs `r2r config-from-catalog` writes"""
    keys = {key for key in schema_keys(CORE_KEYS) if key != "theme"}
    assert keys - shown(CONFIG) == set()
    assert "configs/theme.example.toml" in CONFIG.read_text()


def commented_thresholds(path) -> dict:
    """The [thresholds] an example shows commented out, as a table"""
    text = path.read_text()
    block = text[text.index("[thresholds]") : text.index("# The reports' theme")]
    return tomllib.loads(
        "\n".join(line[2:] for line in block.splitlines() if re.match(r"# \w+ = ", line))
    )


def test_the_examples_thresholds_are_the_defaults():
    """Commented thresholds, uncommented, are Thresholds' defaults: they can't drift. The site
    example sets max_speed, as a ship would
    """
    every = schema_keys(CORE_KEYS["thresholds"])
    config, site = commented_thresholds(CONFIG), commented_thresholds(SITE)
    assert set(config) == every and set(site) == every - {"max_speed"}
    assert thresholds_from_table(config) == thresholds_from_table(
        site | {"max_speed": Thresholds().max_speed}
    )
    assert thresholds_from_table(config) == Thresholds()


def test_the_theme_example_shows_every_setting():
    keys = shown(THEME)
    assert {k for k in _KEYS if k != "colors"} - keys == set()
    assert {f"colors.{k}" for k in _COLOR_KEYS} - keys == set()
