"""Report themes: a theme file and a config's [theme] table, checked, merged and round-tripped
(#36). How they change the PDF, figures and notebook is tested with those (#37-#39).
"""

import tomllib
from pathlib import Path

import pytest
from conftest import CONFIGS

from nav_qa_processing.cli import main
from nav_qa_processing.config import QAConfig, config_to_toml, load_config
from nav_qa_processing.integrations.r2r.config import config_to_toml as cruise_config_to_toml
from nav_qa_processing.integrations.r2r.config import load_cruise_config
from nav_qa_processing.theme import (
    COLORBLIND,
    DEFAULT_THEME,
    ThemeError,
    load_theme,
    resolve_theme,
)


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_default_theme():
    colors = DEFAULT_THEME.colors
    assert (colors.primary, colors.secondary) == ("#1f5f8b", "#d1495b")
    assert set(colors.status) == {"pass", "warn", "fail", "info", "n/a"}
    assert set(colors.rating) == {"G", "Y", "R"}
    assert (DEFAULT_THEME.page_size, DEFAULT_THEME.margin, DEFAULT_THEME.font) == (
        "letter",
        0.6,
        None,
    )


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("colour = 1\n", "unknown theme settings: colour"),
        ("[colors]\nprimry = '#000000'\n", "unknown theme colors: primry"),
        ("[colors]\nprimary = 'blue'\n", "isn't a #rrggbb color"),
        ("page_size = 'legal'\n", "page_size must be one of letter, A4"),
        ("palette = 'neon'\n", "palette must be one of default, colorblind"),
        ("font_scale = 0\n", "font_scale must be a positive number"),
        ("logo = 'missing.png'\n", "logo"),
    ],
)
def test_bad_theme_settings(tmp_path, text, problem):
    with pytest.raises(ThemeError, match=problem):
        load_theme(write(tmp_path / "theme.toml", text))
    # the same in a config's [theme] table
    config = "[theme]\n" + text.replace("[colors]", "[theme.colors]")
    with pytest.raises(ThemeError, match=problem):
        load_config(write(tmp_path / "config.toml", config))


def test_paths_are_relative_to_the_toml(tmp_path):
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "logo.png").write_bytes(b"\x89PNG\r\n")
    theme = load_theme(write(tmp_path / "theme.toml", "logo = 'brand/logo.png'\n"))
    assert theme.logo == (tmp_path / "brand" / "logo.png").resolve()
    config = load_config(write(tmp_path / "c.toml", "[theme]\nlogo = 'brand/logo.png'\n"))
    assert config.theme == {"logo": str((tmp_path / "brand" / "logo.png").resolve())}


def test_config_applies_over_the_theme_file(tmp_path):
    house = write(
        tmp_path / "house.toml",
        "page_size = 'A4'\norganization = 'House'\npalette = 'colorblind'\n"
        "[colors]\nwater = '#000001'\n",
    )
    config = load_config(
        write(
            tmp_path / "c.toml",
            "[theme]\norganization = 'Cruise'\n[theme.colors]\nland = '#000002'\n",
        )
    )
    theme = resolve_theme(house, config)
    assert (theme.page_size, theme.organization) == ("A4", "Cruise")
    # the palette replaces the colors; [colors] then changes single ones
    assert theme.colors.primary == COLORBLIND.primary
    assert (theme.colors.water, theme.colors.land) == ("#000001", "#000002")
    assert resolve_theme(None, QAConfig()) == DEFAULT_THEME


def test_theme_round_trips_through_config_toml(tmp_path):
    config = load_config(
        write(
            tmp_path / "c.toml",
            "[cruise]\nid = 'LAKE1'\n[theme]\npage_size = 'A4'\nfont_scale = 1.1\n"
            "[theme.colors]\nprimary = '#123456'\n",
        )
    )
    expected = {"page_size": "A4", "font_scale": 1.1, "colors": {"primary": "#123456"}}
    assert tomllib.loads(config_to_toml(config))["theme"] == expected
    assert (
        load_config(write(tmp_path / "again.toml", config_to_toml(config))).theme == config.theme
    )
    # an R2R cruise TOML keeps it too
    cruise = load_cruise_config(CONFIGS / "SP2613_169851.toml")
    cruise.theme = config.theme
    assert tomllib.loads(cruise_config_to_toml(cruise))["theme"] == expected


def test_the_example_theme_loads():
    theme = load_theme(CONFIGS / "theme.example.toml")
    assert (theme.page_size, theme.colors) == ("A4", COLORBLIND)


def test_a_bad_theme_is_one_line_before_the_run(tmp_path, capsys):
    bad = write(tmp_path / "bad.toml", "colour = 1\n")
    argv = ["run", str(tmp_path), "-f", "nav29", "-o", str(tmp_path / "out"), "--theme", str(bad)]
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("nav-qa-processing: ") and "unknown theme settings: colour" in err
    assert err.count("\n") == 1 and not (tmp_path / "out").exists()
