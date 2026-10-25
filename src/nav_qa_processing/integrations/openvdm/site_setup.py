"""Builds the per-ship site TOML for the OpenVDM hook by asking for each field (ported from
sbe-qa-processing).

Prompts go to stderr, so the TOML alone can go to stdout. Each prompt shows its default in
brackets: Enter keeps it, ``-`` clears it. At the end of input every remaining field keeps its
default, so with no terminal an existing file is written back unchanged.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TextIO

from nav_qa_processing import formats
from nav_qa_processing.config import NAV_NAME, Extent, NavDevice
from nav_qa_processing.integrations.openvdm.hook import SiteConfig, SiteFeed

CLEAR = "-"


@dataclass
class Prompter:
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    out: TextIO = field(default_factory=lambda: sys.stderr)
    ended: bool = False  # end of input: every later prompt keeps its default

    def say(self, text: str) -> None:
        self.out.write(text + "\n")

    def ask(
        self, label: str, default: str = "", check: Callable[[str], str | None] | None = None
    ) -> str:
        """The answer, or default; check returns a problem to re-ask with, or None"""
        while True:
            self.out.write(f"{label} [{default}]: " if default else f"{label}: ")
            self.out.flush()
            line = "" if self.ended else self.stdin.readline()
            if not line:
                self.ended = True
                self.out.write("\n")
                return default
            answer = line.strip()
            answer = "" if answer == CLEAR else answer or default
            problem = check(answer) if check else None
            if problem is None:
                return answer
            if self.ended:  # no one to re-ask; keep the default rather than loop
                return default
            self.say(f"  {problem}")


def _email(text: str) -> str | None:
    return None if not text or ("@" in text and " " not in text) else "not an email address"


def _speed(text: str) -> str | None:
    """None for a speed in m s-1 above 0"""
    try:
        return None if float(text) > 0 else "must be above 0"
    except ValueError:
        return "a number, in m s-1 (1 knot is 0.514 m s-1)"


def _nav_format(text: str) -> str | None:
    """None for a format ID with a reader; the hook can't run without one"""
    if formats.catalog.canonical_id(text) in formats.READERS:
        return None
    readable = ", ".join(formats.readable())
    problem = "required" if not text else f"no reader for {text}"
    return f"{problem}: one of {readable} (`nav-qa-processing formats` describes them)"


def _degrees(limit: float, required: bool = False) -> Callable[[str], str | None]:
    def check(text: str) -> str | None:
        if not text:
            return "required" if required else None
        try:
            value = float(text)
        except ValueError:
            return "not a number"
        return None if -limit <= value <= limit else f"outside ±{limit:g}"

    return check


def _ask_feed(prompt: Prompter, default: SiteFeed) -> SiteFeed:
    """A feed's format, files, device and transfer, starting from default"""
    nav_format = prompt.ask("  nav format", default.format, _nav_format)
    nav_files = prompt.ask(
        "  glob of its files within the collection system's directory, e.g. "
        "raw/POSMV_GGA-*.txt (blank: every file)",
        default.files,
    )
    prompt.say("  The receiver or INS that logs it, for the reports (each may be left blank):")
    device = NavDevice(
        **{
            name: prompt.ask(f"    {label}", getattr(default.device, name))
            for name, label in (
                ("make", "make (e.g. Applanix)"),
                ("model", "model (e.g. POS MV)"),
                ("serial_number", "serial number"),
                ("location", "location (e.g. main lab)"),
            )
        }
    )
    transfer = prompt.ask(
        "  the collection system transfer it's in, e.g. OpenRVDAS (blank: any the hook runs for)",
        default.transfer,
    )
    return replace(
        default,
        format=formats.catalog.canonical_id(nav_format),
        files=nav_files,
        device=device,
        transfer=transfer,
    )


def _name_check(taken: set[str], required: bool = True) -> Callable[[str], str | None]:
    def check(text: str) -> str | None:
        if not text:
            return "required" if required else None
        if not NAV_NAME.fullmatch(text):
            return "use letters, digits, _ and -"
        return f"{text} is taken" if text in taken else None

    return check


def _ask_feeds(prompt: Prompter, defaults: SiteConfig) -> list[SiteFeed]:
    prompt.say("\nThe primary GNSS feed. Formats with a reader:")
    for format_id, spec in sorted(formats.all_formats().items()):
        if spec.supported:
            prompt.say(f"  {format_id}  {spec.definition.title}")
    primary = replace(_ask_feed(prompt, defaults.primary), primary=True)
    prompt.say(
        "\nOther position sensors (e.g. a second INS or a backup GPS), each assessed with its "
        "own reports named <CRUISE>_<name>_...: a name for each, blank to finish, - to drop "
        "one already here."
    )
    others: list[SiteFeed] = []
    taken = {primary.name} - {""}
    for default in [*defaults.feeds[1:], None]:
        while True:
            name = prompt.ask(
                "  sensor name (e.g. seapath)",
                default.name if default else "",
                _name_check(taken, required=False),
            )
            if not name:
                break
            taken.add(name)
            others.append(
                replace(_ask_feed(prompt, default or SiteFeed()), name=name, primary=False)
            )
            if default is not None:  # an existing sensor: one name; new ones: until blank
                break
    if others and not primary.name:
        name = prompt.ask("  a name for the primary feed too (e.g. posmv)", "", _name_check(taken))
        primary = replace(primary, name=name)
    return [primary, *others]


def _ask_extent(prompt: Prompter, default: Extent | None) -> Extent | None:
    prompt.say(
        "\nExpected cruise area, used when the hook doesn't set OpenVDM's cruise extent "
        "(--no-set-extent) and OpenVDM has none. "
        "Leave westernmost blank to skip. westernmost > easternmost is a box across the "
        "antimeridian (180°)."
    )
    while True:
        text = lambda edge: str(getattr(default, edge)) if default else ""
        west = prompt.ask("  westernmost longitude [deg E]", text("westernmost"), _degrees(180))
        if not west:
            return None
        values = {"westernmost": west}
        for edge, limit, what in (
            ("easternmost", 180, "longitude [deg E]"),
            ("southernmost", 90, "latitude [deg N]"),
            ("northernmost", 90, "latitude [deg N]"),
        ):
            values[edge] = prompt.ask(f"  {edge} {what}", text(edge), _degrees(limit, True))
            if not values[edge]:  # input ended part way through
                return default
        extent = Extent(**{k: float(v) for k, v in values.items()})
        if extent.southernmost <= extent.northernmost:
            if extent.crosses_antimeridian:
                prompt.say("  westernmost > easternmost: a box across the antimeridian")
            return extent
        prompt.say("  southernmost must be <= northernmost; try again")
        if prompt.ended:
            return default


def prompt_site_config(
    defaults: SiteConfig, prompt: Prompter | None = None, from_openvdm: dict | None = None
) -> SiteConfig:
    """A site config from answers to prompts, starting from defaults. Each vessel field's
    default is the site config's value, or else from_openvdm's (openvdm.yaml's vessel settings,
    by SiteConfig field): the site config's win when the hook runs, and openvdm.yaml fills
    those left blank (#112)
    """
    prompt = prompt or Prompter()
    from_openvdm = from_openvdm or {}
    prompt.say(f"Enter keeps the [default]; {CLEAR} clears it.\n")
    if from_openvdm:
        prompt.say("openvdm.yaml's vessel block has these, offered where the site TOML has none:")
        for name, value in from_openvdm.items():
            prompt.say(f"  {name} = {value}")
        prompt.say("")

    def ask(field_name: str, label: str, check=None) -> str:
        default = getattr(defaults, field_name) or from_openvdm.get(field_name, "")
        return prompt.ask(label, default, check)

    site = replace(
        defaults,
        vessel_name=ask("vessel_name", "Vessel name (e.g. Roger Revelle)"),
        contact_institution=ask("contact_institution", "Contact institution for the QA report"),
        contact_email=ask("contact_email", "Contact email", _email),
    )
    max_speed = prompt.ask(
        "Vessel's top speed [m s-1] (1 knot is 0.514 m s-1); faster steps count as unreasonable",
        f"{defaults.thresholds.max_speed:g}",
        _speed,
    )
    site.thresholds = replace(site.thresholds, max_speed=float(max_speed))
    site.feeds = _ask_feeds(prompt, defaults)
    site.attribution = prompt.ask(
        "\nGeoCSV products' attribution (e.g. R/V Roger Revelle, Scripps Institution of "
        "Oceanography)",
        defaults.attribution,
    )
    site.output_extra_directory = prompt.ask(
        "OpenVDM extra directory for the reports", defaults.output_extra_directory
    )
    site.extent = _ask_extent(prompt, defaults.extent)
    return site
