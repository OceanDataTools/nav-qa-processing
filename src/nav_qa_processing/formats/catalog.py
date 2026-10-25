"""The raw navigation format definitions in formats/definitions/: what each R2R format ID
looks like, independent of whether a reader exists for it yet.
"""

import re
import tomllib
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files


@dataclass(frozen=True)
class Field:
    name: str
    example: str
    meaning: str


@dataclass(frozen=True)
class FormatDefinition:
    id: str
    title: str
    system: str = ""
    file_type: str = ""
    vessels: tuple[str, ...] = ()
    sentences: tuple[str, ...] = ()
    r2r_formats: tuple[int, ...] = ()  # R2R's format IDs (service.rvdata.us/info/format/<id>)
    position: str = ""  # where the fix comes from
    time: str = ""  # where the date and time come from
    description: str = ""
    notes: tuple[str, ...] = ()
    reader: tuple[str, ...] = ()  # how this package reads it, for a format with a reader (#118)
    filesets: tuple[str, ...] = ()  # the reference filesets that test the reader
    references: tuple[str, ...] = ()
    examples_from: str = ""  # where the examples come from
    examples: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()


@dataclass(frozen=True)
class SentenceDefinition:
    id: str
    title: str
    description: str = ""
    notes: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    fields: tuple[Field, ...] = field(default=())


def _build(cls, data: dict):
    data = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items()}
    data["fields"] = tuple(Field(**f) for f in data.get("fields", ()))
    return cls(**data)


def canonical_id(format_id: object) -> str:
    """A format ID as this project writes it: NavManager's one-digit IDs padded to two digits
    (nav2 -> nav02); anything else as text, so a TOML's `format = 29` is an unknown format, not
    a crash (#83 review)
    """
    text = str(format_id).strip()
    match = re.fullmatch(r"nav([1-9])", text)
    return f"nav0{match.group(1)}" if match else text


def natural_key(format_id: str) -> tuple:
    """Sorts nav01, nav02, …, nav33, then the named formats"""
    digits = "".join(c for c in format_id if c.isdigit())
    return (0, int(digits)) if format_id.startswith("nav") and digits else (1, format_id)


@cache
def formats() -> dict[str, FormatDefinition]:
    """Every format definition by ID, in nav01, nav02, … order"""
    directory = files("nav_qa_processing.formats") / "definitions"
    loaded = {}
    for entry in directory.iterdir():
        if entry.name.endswith(".toml") and entry.name != "sentences.toml":
            definition = _build(FormatDefinition, tomllib.loads(entry.read_text()))
            loaded[definition.id] = definition
    return {k: loaded[k] for k in sorted(loaded, key=natural_key)}


@cache
def sentences() -> dict[str, SentenceDefinition]:
    """Every sentence definition by ID (GGA, ZDA, …)"""
    path = files("nav_qa_processing.formats") / "definitions" / "sentences.toml"
    return {
        sid: _build(SentenceDefinition, {"id": sid, **data})
        for sid, data in tomllib.loads(path.read_text()).items()
    }


def _wrap(text: str, indent: str, width: int = 99) -> list[str]:
    """text wrapped to width, its first line after indent and the rest aligned with it (a
    bullet's "  - " continues under its text)
    """
    import textwrap

    following = " " * len(indent)
    return textwrap.wrap(text, width, initial_indent=indent, subsequent_indent=following) or [
        indent
    ]


def describe(definition: FormatDefinition | SentenceDefinition) -> str:
    """A definition as plain text for the terminal"""
    lines = [f"{definition.id}: {definition.title}"]
    meta = []
    for label, value in [
        ("System", getattr(definition, "system", "")),
        ("Vessels", ", ".join(getattr(definition, "vessels", ()))),
        ("File", getattr(definition, "file_type", "")),
        ("Sentences", ", ".join(getattr(definition, "sentences", ()))),
        ("R2R IDs", ", ".join(str(i) for i in getattr(definition, "r2r_formats", ()))),
        ("Position", getattr(definition, "position", "")),
        ("Time", getattr(definition, "time", "")),
    ]:
        if value:
            meta += _wrap(f"{label + ':':11}{value}", "")
    if meta:
        lines += ["", *meta]
    if definition.description:
        lines += ["", *_wrap(definition.description, "")]
    if definition.fields:
        width = max(len(f.name) for f in definition.fields)
        lines += ["", "Fields:"]
        for f in definition.fields:
            example = f" (e.g. {f.example})" if f.example else ""
            lines += _wrap(f"{f.name:{width}}  {f.meaning}{example}", "  ")
    for heading, items in [
        ("Notes", definition.notes),
        ("How it's read", getattr(definition, "reader", ())),
        ("Reference filesets", getattr(definition, "filesets", ())),
    ]:
        if items:
            lines += ["", f"{heading}:"]
            for item in items:
                lines += _wrap(item, "  - ")
    if definition.examples:
        source = getattr(definition, "examples_from", "")
        lines += ["", *_wrap(f"Examples, from {source}:" if source else "Examples:", "")]
        lines += ["  " + e.replace("\t", "<TAB>") for e in definition.examples]
    references = getattr(definition, "references", ())
    if references:
        lines += ["", "References: " + ", ".join(references)]
    return "\n".join(lines) + "\n"
