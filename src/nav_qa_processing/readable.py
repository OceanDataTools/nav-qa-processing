"""Readable text for the classes people inspect, in print() and in Jupyter.

A class gives its text with ``__str__``; `Readable` makes IPython and Jupyter display that same
text for a bare ``result``, where they would otherwise show the dataclass ``repr``. ``repr()``
stays the dataclass's, for debugging.
"""

from datetime import datetime
from textwrap import indent

INDENT = "  "


class Readable:
    """Mixin: IPython and Jupyter display str(self) instead of repr(self)"""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        # IPython's pretty printer uses the first class in the MRO that defines __repr__ or
        # _repr_pretty_, and @dataclass gives every class its own __repr__
        cls._repr_pretty_ = Readable._repr_pretty_

    def _repr_pretty_(self, printer, cycle: bool) -> None:
        printer.text(repr(self) if cycle else str(self))


def block(heading: str, lines: list[str]) -> str:
    """heading, then lines indented under it (each line may itself span several lines)"""
    return "\n".join([heading, *(indent(line, INDENT) for line in lines)])


def status_line(status: str, text: str) -> str:
    """e.g. "WARN  Dual temperature: 0.004", aligned for every status (pass/warn/fail/info/n/a)"""
    return f"{status.upper():<4}  {text}"


def latitude(value: float) -> str:
    return f"{abs(value):g}°{'S' if value < 0 else 'N'}"


def longitude(value: float) -> str:
    return f"{abs(value):g}°{'W' if value < 0 else 'E'}"


def plural(count: int, noun: str, nouns: str = "") -> str:
    """e.g. 1 file, 2 files; `nouns` for an irregular plural (1 fix, 2 fixes)"""
    return f"{count} {noun if count == 1 else nouns or noun + 's'}"


def timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    zone = f" {value.tzname()}" if value.tzinfo else ""
    return f"{value:%Y-%m-%d %H:%M:%S}{zone}"


STATUS_ORDER = ("pass", "warn", "fail", "info", "n/a")


def counts(statuses: list[str]) -> str:
    """e.g. "12 pass, 1 warn", leaving out statuses with none"""
    order = [*STATUS_ORDER, *sorted(set(statuses) - set(STATUS_ORDER))]
    return ", ".join(f"{statuses.count(s)} {s}" for s in order if s in statuses) or "none"
