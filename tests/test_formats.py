import re
from pathlib import Path

import pytest

from nav_qa_processing import formats
from nav_qa_processing.cli import main
from nav_qa_processing.formats import catalog
from nav_qa_processing.formats.nmea import parse_sentence

DEFINITIONS = Path(formats.__file__).parent / "definitions"


def test_every_navmanager_format_id_is_defined():
    # NavManager's nav1 … nav33 (no nav28), written with two digits
    expected = {f"nav{n:02d}" for n in range(1, 34) if n != 28} | {"uhdas", "siomet", "metoc_nav"}
    assert set(catalog.formats()) == expected


def test_file_names_match_ids():
    stems = {p.stem for p in DEFINITIONS.glob("*.toml")} - {"sentences"}
    assert stems == set(catalog.formats())


def test_navmanagers_one_digit_ids_are_read_padded():
    assert catalog.canonical_id("nav2") == catalog.canonical_id(" nav02 ") == "nav02"
    assert catalog.canonical_id("nav29") == "nav29" and catalog.canonical_id("uhdas") == "uhdas"
    assert formats.get("nav1").id == "nav01"


def test_formats_are_in_numeric_order():
    ids = list(catalog.formats())
    assert ids[:3] == ["nav01", "nav02", "nav03"] and ids.index("nav10") > ids.index("nav09")


def test_referenced_sentences_are_defined():
    sentences = catalog.sentences()
    for definition in catalog.formats().values():
        assert set(definition.sentences) <= set(sentences), definition.id


def test_every_reader_has_a_definition():
    assert set(formats.READERS) <= set(catalog.formats())


def test_titles_name_the_system_then_the_layout():
    """`<system>: <what a line holds>`, without vessel names (they're in `vessels`; #118)"""
    for definition in catalog.formats().values():
        system, _, layout = definition.title.partition(": ")
        assert system and layout, definition.id
        assert not any(v in definition.title for v in definition.vessels), definition.id


# nav07 was never implemented: no layout is known. No sample of either has been found (nav23:
# the Point Sur filesets checked are all nav14)
UNDOCUMENTED = {"nav07"}
NO_SAMPLE = {"nav07", "nav23"}


def test_definitions_have_the_required_keys():
    for definition in catalog.formats().values():
        keys = ["title", "system", "file_type", "vessels", "description"]
        if definition.id not in UNDOCUMENTED:
            keys += ["position", "time", "fields"]
        if definition.id not in NO_SAMPLE:
            keys += ["examples", "examples_from"]
        for key in keys:
            assert getattr(definition, key), (definition.id, key)
        assert not any(n.startswith("Examples") for n in definition.notes), definition.id


def test_formats_with_a_reader_say_how_they_are_read():
    """A format with a reader says how it's read and which reference filesets test it; one
    without has no reader text (#118)
    """
    for definition in catalog.formats().values():
        has_reader = definition.id in formats.READERS
        assert bool(definition.reader) == has_reader, definition.id
        assert bool(definition.filesets) == has_reader, definition.id


def test_register_ids_cited_by_definitions_exist():
    """Every register row a definition cites (R5, Q7, …) is in docs/navmanager-differences.md"""
    register = (Path(__file__).parents[1] / "docs" / "navmanager-differences.md").read_text()
    rows = set(re.findall(r"^\| ([A-Z]\d+) \|", register, re.MULTILINE))
    for definition in catalog.formats().values():
        text = " ".join([*definition.notes, *definition.reader, *definition.filesets])
        cited = set(re.findall(r"\b([RQAGPFDX]\d{1,2})\b", text))
        assert cited <= rows, (definition.id, cited - rows)


def _nmea_parts(example: str):
    """The NMEA sentences in an example line (several when joined by commas, as in nav04)"""
    text = example.replace('"', "")
    if "$" not in text:
        return
    for part in text[text.index("$") :].split(",$"):
        yield part if part.startswith("$") else "$" + part


def test_example_checksums_are_correct():
    definitions = [*catalog.formats().values(), *catalog.sentences().values()]
    for definition in definitions:
        for example in definition.examples:
            for sentence in _nmea_parts(example):
                if "*" in sentence:
                    assert parse_sentence(sentence), (
                        definition.id,
                        sentence,
                    )


def test_unsupported_format_is_reported():
    with pytest.raises(ValueError, match="not supported yet"):
        formats.get("nav04")
    assert formats.get("nav29").supported


def test_cli_lists_formats(capsys):
    assert main(["formats"]) == 0
    out = capsys.readouterr().out
    assert "nav29      yes" in out and "metoc_nav" in out


@pytest.mark.parametrize("format_id", ["nav29", "gga", "PSXN"])
def test_cli_describes_format_or_sentence(capsys, format_id):
    assert main(["formats", format_id]) == 0
    assert capsys.readouterr().out.startswith(
        format_id.upper() if format_id != "nav29" else "nav29"
    )


def test_cli_unknown_format(capsys):
    assert main(["formats", "nav99"]) == 1


def test_r2r_format_ids_are_unique_to_one_definition():
    seen = {}
    for definition in catalog.formats().values():
        for r2r_id in definition.r2r_formats:
            assert r2r_id not in seen, (r2r_id, seen.get(r2r_id), definition.id)
            seen[r2r_id] = definition.id
    assert seen[100150] == "nav29"


def test_a_config_with_a_one_digit_id_still_loads():
    from nav_qa_processing.config import loads_config

    assert loads_config('[nav]\nformat = "nav2"\n').nav_format == "nav02"


def test_the_formats_command_takes_a_one_digit_id(capsys):
    assert main(["formats", "nav2"]) == 0
    assert "nav02" in capsys.readouterr().out


def test_a_numeric_format_is_an_unknown_format_not_a_crash():
    """`format = 29` in a TOML: one line, not a traceback (#83 review)"""
    from nav_qa_processing.config import loads_config

    assert catalog.canonical_id(29) == "29"
    config = loads_config("[nav]\nformat = 29\n")
    with pytest.raises(ValueError, match="nav format '29' is unknown"):
        formats.get(config.nav_format)
