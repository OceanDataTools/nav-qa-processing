"""Writes the PDF QA report: a title block, any pages an integration adds (e.g. R2R's
certificate), the checks, statistics and products; then quality control with the coverage
timeline, the trackline map, speed and acceleration, and the histograms.

The figures are written as SVG files first (`reports.write_figures`), and the PDF embeds those
same SVGs as vector drawings (ReportLab with svglib), so they stay sharp at any zoom and the
SVGs can be used on their own. Values are formatted and units labelled through `quantities.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matplotlib
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from svglib.svglib import svg2rlg

from nav_qa_processing import formats, geocsv, stats
from nav_qa_processing import quantities as q
from nav_qa_processing.qa import NavQAResult
from nav_qa_processing.qc import DESCRIPTIONS, Flag
from nav_qa_processing.theme import DEFAULT_THEME, Theme

TITLE = "Navigation QA report"
# [in] tallest figures; each is also kept within the frame, which A4 or larger margins shrink
MAP_HEIGHT = 8.4
SERIES_HEIGHT = 5.4
TIMELINE_HEIGHT, HISTOGRAMS_HEIGHT = 3.2, 6.0
HEADING_ROOM = 0.5 * inch  # a page heading above a figure
MAX_ERRORS = 12  # reader and manifest errors listed; the rest are counted
BY_FILE_PADDING = 3  # [pt] each side of a cell in the checks-by-file table, which is crowded;
# its columns get 2 pt more, so a word measured to fit isn't wrapped by rounding

# DejaVu Sans ships with matplotlib and, unlike ReportLab's built-in base fonts, has glyphs
# such as ≤ ≥ ° used in the report text
_DEJAVU = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
DEFAULT_FONTS = (_DEJAVU / "DejaVuSans.ttf", _DEJAVU / "DejaVuSans-Bold.ttf")
PAGE_SIZES = {"letter": letter, "A4": A4}


def _register_font(path: Path) -> str:
    """ReportLab's name for a TTF, registering it once"""
    name = path.stem
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    return name


@dataclass
class Layout:
    """A theme made concrete for ReportLab: page and frame sizes [pt], fonts and paragraph
    styles. Integrations build their pages (write_pdf's front) from the same Layout
    """

    theme: Theme
    page: tuple[float, float]
    margin: float
    frame_width: float
    frame_height: float
    font: str
    font_bold: str
    title: ParagraphStyle
    heading: ParagraphStyle
    subheading: ParagraphStyle
    body: ParagraphStyle
    small: ParagraphStyle
    cell: ParagraphStyle
    cell_bold: ParagraphStyle
    banner: ParagraphStyle

    @property
    def colors(self):
        return self.theme.colors

    def fit(self, height: float) -> float:
        """height [pt], at most what's left of the frame below a page heading"""
        return min(height, self.frame_height - HEADING_ROOM)


def layout(theme: Theme = DEFAULT_THEME) -> Layout:
    page = PAGE_SIZES[theme.page_size]
    margin = theme.margin * inch
    font = _register_font(theme.font or DEFAULT_FONTS[0])
    font_bold = _register_font(theme.bold_font or theme.font or DEFAULT_FONTS[1])
    scale = theme.font_scale
    styles = getSampleStyleSheet()
    heading2, heading3 = styles["Heading2"], styles["Heading3"]
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=9 * scale, leading=11 * scale, fontName=font
    )
    cell = ParagraphStyle("cell", parent=body, fontSize=7 * scale, leading=8.4 * scale)
    return Layout(
        theme=theme,
        page=page,
        margin=margin,
        frame_width=page[0] - 2 * margin,
        frame_height=page[1] - 2 * margin - 12,  # the frame's 6 pt top and bottom padding
        font=font,
        font_bold=font_bold,
        title=ParagraphStyle(
            "title",
            parent=styles["Title"],
            alignment=TA_LEFT,
            fontSize=18 * scale,
            leading=styles["Title"].leading * scale,
            spaceAfter=4,
            fontName=font_bold,
        ),
        heading=ParagraphStyle(
            "heading",
            parent=heading2,
            fontSize=heading2.fontSize * scale,
            leading=heading2.leading * scale,
            spaceBefore=0,
            spaceAfter=6,
            fontName=font_bold,
        ),
        subheading=ParagraphStyle(
            "subheading",
            parent=heading3,
            fontSize=heading3.fontSize * scale,
            leading=heading3.leading * scale,
            spaceBefore=2,
            spaceAfter=4,
            fontName=font_bold,
        ),
        body=body,
        small=ParagraphStyle(
            "small",
            parent=body,
            fontSize=7 * scale,
            leading=8.5 * scale,
            textColor=colors.HexColor(theme.colors.muted_text),
        ),
        cell=cell,
        cell_bold=ParagraphStyle("cellbold", parent=cell, fontName=font_bold),
        banner=ParagraphStyle(
            "banner",
            parent=body,
            fontSize=15 * scale,
            leading=18 * scale,
            textColor=colors.white,
            fontName=font_bold,
        ),
    )


def drawing(path: Path, width: float, max_height: float | None = None) -> Flowable:
    """An SVG as a vector drawing scaled to `width` (and at most `max_height`)"""
    figure = svg2rlg(str(path))
    scale = width / figure.width
    if max_height is not None:
        scale = min(scale, max_height / figure.height)
    figure.scale(scale, scale)
    figure.width *= scale
    figure.height *= scale
    figure.hAlign = "CENTER"
    return figure


def escape(text) -> str:
    """text for a Paragraph, whose mini-markup would otherwise read < > & as tags"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cell(text, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def table(
    lay: Layout, header: list[str], rows: list[list], widths: list[float], extra_styles=()
) -> Table:
    """A table across the frame; `widths` are fractions of its width"""
    data = [[_cell(h, lay.cell_bold) for h in header]]
    data += [[c if isinstance(c, Flowable) else _cell(c, lay.cell) for c in row] for row in rows]
    result = Table(data, colWidths=[w * lay.frame_width for w in widths], repeatRows=1)
    result.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(lay.colors.table_header)),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(lay.colors.table_grid)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                *extra_styles,
            ]
        )
    )
    return result


def tint(hex_color: str, alpha: float = 0.35) -> colors.Color:
    color = colors.HexColor(hex_color)
    return colors.Color(
        1 - alpha * (1 - color.red), 1 - alpha * (1 - color.green), 1 - alpha * (1 - color.blue)
    )


# -- The title block --

LOGO_HEIGHT = 0.6 * inch
LOGO_MAX_WIDTH = 2.0 * inch


def _logo(path: Path) -> Image:
    """The theme's logo, at most LOGO_HEIGHT tall and LOGO_MAX_WIDTH wide"""
    logo = Image(str(path))
    scale = min(LOGO_HEIGHT / logo.imageHeight, LOGO_MAX_WIDTH / logo.imageWidth)
    logo.drawWidth, logo.drawHeight = logo.imageWidth * scale, logo.imageHeight * scale
    return logo


def _cruise_facts(config) -> list[str]:
    """Cruise metadata for the title block, leaving out what isn't known"""
    dates = (
        f"{config.depart_date} to {config.arrive_date}"
        if config.depart_date and config.arrive_date
        else ""
    )
    facts = [
        f"PI {config.cruise_pi}" if config.cruise_pi else "",
        f"vessel {config.vessel_name}" if config.vessel_name else "",
        dates,
        config.cruise_location,
        f"expected area {config.extent}" if config.extent else "",
        f"contact {config.contact_institution}" if config.contact_institution else "",
    ]
    return [fact for fact in facts if fact]


def _source(result: NavQAResult) -> str:
    """The fileset, its nav files and their format"""
    config = result.config
    try:
        title = formats.get(config.nav_format).definition.title
    except ValueError:
        title = ""
    files = f"{len(result.nav.files)} of {len(result.fileset.files)} nav files"
    fmt = f"{config.nav_format} ({title})" if title else config.nav_format
    return f"Source: {result.fileset.root} · {files} · format {fmt}"


def _title(lay: Layout, result: NavQAResult) -> list[Flowable]:
    config = result.config
    facts = _cruise_facts(config)
    heading = [
        Paragraph(f"{TITLE}: {escape(config.identifier)}", lay.title),
        Paragraph(escape(config.cruise_name or ""), lay.body),
    ]
    if lay.theme.logo is not None:
        # The title and name beside the logo, right-aligned
        logo = _logo(lay.theme.logo)
        heading = [
            Table(
                [[heading, logo]],
                colWidths=[lay.frame_width - logo.drawWidth - 6, logo.drawWidth + 6],
                style=[
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ],
            )
        ]
    return [
        *heading,
        Spacer(1, 4),
        *([Paragraph(escape(" · ".join(facts)), lay.small)] if facts else []),
        Paragraph(escape(_source(result)), lay.small),
        Paragraph(escape(f"Receiver: {result.receiver}"), lay.small),
        Paragraph(
            f"Generated {result.finished:%Y-%m-%d %H:%M} UTC by "
            + escape(", ".join(f"{k} {v}" for k, v in result.versions.items())),
            lay.small,
        ),
        Spacer(1, 10),
    ]


# -- Checks, statistics and QC --


def checks_table(lay: Layout, checks) -> Table:
    rows = [[c.name, c.status, c.value, c.detail] for c in checks]
    styles = [
        ("BACKGROUND", (1, i), (1, i), colors.HexColor(lay.colors.status[c.status]))
        for i, c in enumerate(checks, start=1)
    ]
    return table(lay, ["Check", "Status", "Value", "Detail"], rows, [0.3, 0.08, 0.2, 0.42], styles)


def _statistics(lay: Layout, result: NavQAResult) -> Table:
    """The QA statistics' extent and ranges, each written through its quantity"""
    s = result.stats
    decimals = result.nav.decimals
    when = lambda moment: q.ISO_TIME.format(moment, decimals.get("time", 0))
    rows = [
        [q.FIXES.label + " used", q.FIXES.format(s.records), ""],
        ["First and last fix", when(s.first), when(s.last)],
        [q.MODAL_INTERVAL.label, q.MODAL_INTERVAL.format(s.modal_interval), ""],
        [q.LONGEST_GAP.label, q.LONGEST_GAP.format(s.longest_gap), ""],
    ]
    for quantity, values, places in (
        (q.NSV, s.nsv, None),
        (q.HDOP, s.hdop, decimals.get("hdop", 1)),
        (q.ANTENNA_HEIGHT, s.altitude, decimals.get("alt", 1)),
        (q.SPEED_MADE_GOOD, s.speed, None),
        (q.HORIZONTAL_ACCELERATION, s.acceleration, None),
    ):
        rows.append(
            [
                quantity.label,
                quantity.format(values.minimum, places),
                quantity.format(values.maximum, places),
            ]
        )
    return table(lay, ["Quantity", "Minimum or value", "Maximum"], rows, [0.5, 0.25, 0.25])


def _qc(lay: Layout, result: NavQAResult) -> list[Flowable]:
    records = len(result.nav.frame)
    rows = [
        [
            flag.name.lower().replace("_", " "),
            DESCRIPTIONS[flag],
            q.FIXES.format(result.qc.count(flag)),
            q.PERCENTAGE.format(100 * result.qc.count(flag) / records if records else None),
        ]
        for flag in Flag
        if result.qc.count(flag)
    ]
    flagged = int((result.qc.flags != 0).sum())
    story: list[Flowable] = [
        Paragraph("Quality control", lay.heading),
        Paragraph(
            f"{q.FIXES.format(flagged)} of {q.FIXES.format(records)} records flagged; a record "
            "can have more than one flag. Flagged records keep a leading # in bestres and are "
            "left out of 1min, control and the speeds.",
            lay.body,
        ),
    ]
    if rows:
        story.append(
            table(lay, ["Flag", "Meaning", "Records", "Percent"], rows, [0.18, 0.54, 0.14, 0.14])
        )
    for note in result.qc.notes:
        story.append(Paragraph(escape(note), lay.small))
    if result.errors:
        rows = [[error] for error in result.errors[:MAX_ERRORS]]
        more = len(result.errors) - len(rows)
        if more:
            rows.append([f"and {more} more"])
        story += [
            Spacer(1, 6),
            Paragraph("Reader and manifest errors", lay.subheading),
            table(lay, [f"{len(result.errors)} errors"], rows, [1.0]),
        ]
    return story


def _by_file(lay: Layout, result: NavQAResult) -> Table:
    """What each check counted, per nav file (#61): the files read, those skipped as outside the
    cruise window, and the total
    """
    counts = stats.counts_by_file(result.nav, result.stats)
    heads = list(stats.COUNTED.values())
    number = ParagraphStyle("number", parent=lay.cell, alignment=TA_RIGHT)
    number_bold = ParagraphStyle("numberbold", parent=lay.cell_bold, alignment=TA_RIGHT)
    rows, styles = [], []
    records = counts.to_dict("records")
    for i, row in enumerate(records, start=1):
        if row["status"] == stats.SKIPPED:
            rows.append([row["file"], f"skipped: {stats.SKIPPED}"] + [""] * len(heads))
            styles.append(("SPAN", (1, i), (-1, i)))
            continue
        total = i == len(records)
        style = number_bold if total else number
        cells = [
            Paragraph("n/a" if pd.isna(row[c]) else q.COUNT.format(int(row[c])), style)
            for c in ["fixes", *heads]
        ]
        rows.append([_cell(row["file"], lay.cell_bold if total else lay.cell), *cells])
    header = ["File", q.FIXES.label, *heads]
    # Each count's column as wide as its header's longest word or its widest (bold) value, the
    # file name taking the rest
    bold = lay.cell_bold
    width = lambda text: stringWidth(text, bold.fontName, bold.fontSize) + BY_FILE_PADDING * 2 + 2
    counts_width = [
        max(
            *(width(word) for word in head.split()),
            *(width(q.COUNT.format(int(v))) for v in counts[column].dropna()),
        )
        for head, column in zip(header[1:], ["fixes", *heads], strict=True)
    ]
    widths = [w / lay.frame_width for w in [lay.frame_width - sum(counts_width), *counts_width]]
    styles += [
        ("LEFTPADDING", (0, 0), (-1, -1), BY_FILE_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), BY_FILE_PADDING),
    ]
    return table(lay, header, rows, widths, styles)


def _products(lay: Layout, result: NavQAResult) -> Table:
    rows = [
        [geocsv.file_name(result.config.stem, product), q.FIXES.format(len(frame))]
        for product, frame in (
            (geocsv.BESTRES, result.bestres),
            (geocsv.ONE_MINUTE, result.one_minute),
            (geocsv.CONTROL, result.control),
        )
    ]
    return table(lay, ["File", "Records"], rows, [0.7, 0.3])


def _summary(lay: Layout, result: NavQAResult) -> list[Flowable]:
    return [
        KeepTogether([Paragraph("Checks", lay.heading), checks_table(lay, result.stats.checks)]),
        Spacer(1, 10),
        Paragraph("Checks by file", lay.subheading),
        Paragraph(
            "What each check counted, per nav file. A speed, a step out of sequence or a gap is "
            "counted in the file of the fix that ends it.",
            lay.small,
        ),
        _by_file(lay, result),
        Spacer(1, 10),
        KeepTogether([Paragraph("Statistics", lay.subheading), _statistics(lay, result)]),
        Spacer(1, 10),
        KeepTogether([Paragraph("Products", lay.subheading), _products(lay, result)]),
    ]


def _figures(lay: Layout, result: NavQAResult, svgs: dict[str, Path]) -> list[Flowable]:
    width = lay.frame_width
    return [
        PageBreak(),
        *_qc(lay, result),
        Spacer(1, 10),
        KeepTogether(
            [
                Paragraph("Coverage, gaps and flags", lay.subheading),
                drawing(svgs["timeline"], width, max_height=lay.fit(TIMELINE_HEIGHT * inch)),
            ]
        ),
        PageBreak(),
        Paragraph("Trackline", lay.heading),
        drawing(svgs["track_map"], width, max_height=lay.fit(MAP_HEIGHT * inch)),
        PageBreak(),
        Paragraph("Speed and acceleration", lay.heading),
        drawing(svgs["speed_acceleration"], width, max_height=lay.fit(SERIES_HEIGHT * inch)),
        PageBreak(),
        Paragraph("Sampling and fix quality", lay.heading),
        drawing(svgs["histograms"], width, max_height=lay.fit(HISTOGRAMS_HEIGHT * inch)),
    ]


# Pages an integration puts after the title (e.g. R2R's certificate), built from the PDF's Layout
FrontPages = Callable[[Layout], list[Flowable]]


def write_pdf(
    result: NavQAResult,
    path: Path | str,
    svgs: dict[str, Path],
    front: FrontPages | None = None,
    subject: str = "Navigation quality assessment",
    theme: Theme = DEFAULT_THEME,
) -> Path:
    """Writes the PDF, embedding the figures' SVGs (`reports.write_figures`). `front` makes the
    pages an integration puts between the title and the checks, e.g. R2R's certificate
    """
    path = Path(path)
    lay = layout(theme)
    title = f"{TITLE}: {result.config.identifier}"
    footer = theme.footer or title
    if theme.organization:
        footer = f"{theme.organization} · {footer}"

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFont(lay.font, lay.small.fontSize)
        canvas.setFillColor(colors.HexColor(lay.colors.footer_text))
        canvas.drawString(lay.margin, 0.4 * inch, footer)
        canvas.drawRightString(lay.page[0] - lay.margin, 0.4 * inch, f"page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(path),
        pagesize=lay.page,
        leftMargin=lay.margin,
        rightMargin=lay.margin,
        topMargin=lay.margin,
        bottomMargin=lay.margin,
        title=title,
        subject=subject,
        creator=f"nav-qa-processing {result.versions.get('nav-qa-processing', '')}",
    )
    story = (
        _title(lay, result)
        + (front(lay) if front else [])
        + _summary(lay, result)
        + _figures(lay, result, svgs)
    )
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return path
