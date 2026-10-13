from pathlib import Path

import pytest

from nav_qa_processing import formats
from nav_qa_processing.cli import main
from nav_qa_processing.formats import catalog
from nav_qa_processing.formats.nmea import parse_sentence

DEFINITIONS = Path(formats.__file__).parent / "definitions"


def test_every_navmanager_format_id_is_defined():
    expected = {f"nav{n}" for n in range(1, 34) if n != 28} | {"uhdas", "siomet", "metoc_nav"}
    assert set(catalog.formats()) == expected


def _file_stem(format_id: str) -> str:
    """nav1 -> nav01: file names pad the number to 2 digits; the IDs stay R2R's"""
    number = format_id.removeprefix("nav")
    return f"nav{int(number):02d}" if number.isdigit() else format_id


def test_file_names_match_ids():
    stems = {p.stem for p in DEFINITIONS.glob("*.toml")} - {"sentences"}
    assert stems == {_file_stem(format_id) for format_id in catalog.formats()}


def test_formats_are_in_numeric_order():
    ids = list(catalog.formats())
    assert ids[:3] == ["nav1", "nav2", "nav3"] and ids.index("nav10") > ids.index("nav9")


def test_referenced_sentences_are_defined():
    sentences = catalog.sentences()
    for definition in catalog.formats().values():
        assert set(definition.sentences) <= set(sentences), definition.id


def test_every_reader_has_a_definition():
    assert set(formats.READERS) <= set(catalog.formats())


def _nmea_parts(example: str):
    """The NMEA sentences in an example line (several when joined by commas, as in nav4)"""
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
                    assert parse_sentence(sentence, navmanager_checksum=False), (
                        definition.id,
                        sentence,
                    )


def test_unsupported_format_is_reported():
    with pytest.raises(ValueError, match="not supported yet"):
        formats.get("nav1")
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


def test_examples_cite_their_source():
    """Every format with examples says which cruise and fileset (or document) they came from,
    except those whose examples are NavManager's own samples
    """
    navmanager_samples = {
        f"nav{n}"
        for n in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 24, 33)
    } | {"uhdas"}
    for definition in catalog.formats().values():
        if definition.examples and definition.id not in navmanager_samples:
            assert any("fileset" in note for note in definition.notes), definition.id
