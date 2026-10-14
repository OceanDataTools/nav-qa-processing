"""Results and configs show readable text in print() and in IPython/Jupyter."""

from nav_qa_processing.config import Extent, QAConfig, Thresholds
from nav_qa_processing.readable import Readable, plural


class Printer:  # what IPython's pretty printer passes to _repr_pretty_
    def __init__(self):
        self.out = ""

    def text(self, value):
        self.out += value


def shown(value) -> str:
    printer = Printer()
    value._repr_pretty_(printer, cycle=False)
    return printer.out


def test_jupyter_shows_str_not_the_dataclass_repr():
    for value in (QAConfig(cruise_id="X"), Thresholds(), Extent(-1, 1, -1, 1)):
        assert isinstance(value, Readable) and shown(value) == str(value) != repr(value)


def test_plural():
    assert plural(1, "fix", "fixes") == "1 fix" and plural(2, "fix", "fixes") == "2 fixes"
    assert plural(2, "file") == "2 files"
