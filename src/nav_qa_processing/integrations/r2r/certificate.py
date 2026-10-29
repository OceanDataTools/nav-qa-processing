"""R2R's certificate: the overall rating, the six tests, the infos and R2R's ranges (over
every record before QC; the core report's statistics are over the fixes that pass QC), as PDF
pages after the title (`pdf_report.FrontPages`) and as notebook cells. The ratings, results,
bounds and ranges are written through `quantities.py` as the QA 2.0 XML writes them; the rating
colors are the theme's.
"""

from nbformat.v4 import new_code_cell, new_markdown_cell
from reportlab.lib import colors
from reportlab.platypus import Flowable, KeepTogether, Paragraph, Spacer, Table, TableStyle

from nav_qa_processing import quantities as q
from nav_qa_processing.integrations.r2r.config import CruiseConfig
from nav_qa_processing.integrations.r2r.rules import Bound, R2RResult, TestResult
from nav_qa_processing.integrations.r2r.xml_report import supplemental_ranges
from nav_qa_processing.pdf_report import FrontPages, Layout, escape, table, tint
from nav_qa_processing.qa import NavQAResult

RATING_NAMES = {"G": "GREEN", "Y": "YELLOW", "R": "RED"}
NOT_COMPUTED = "not computed"


def r2r_facts(config: CruiseConfig) -> list[str]:
    """R2R's catalog details for the certificate, leaving out what isn't known"""
    ports = " to ".join(p.name for p in (config.depart_port, config.arrive_port) if p.name)
    receiver = " ".join(n for n in (config.device.make_id, config.device.model_name) if n)
    raw_format = " ".join(
        n
        for n in (config.format_id, f"({config.format_alias})" if config.format_alias else "")
        if n
    )
    facts = [
        f"R2R fileset {config.fileset_id}" if config.fileset_id else "",
        f"vessel ID {config.vessel_id}" if config.vessel_id else "",
        ports,
        f"receiver {receiver}" if receiver else "",
        f"R2R format {raw_format}" if raw_format else "",
    ]
    return [fact for fact in facts if fact]


def result_text(test: TestResult) -> str:
    """The result with its unit, as rated"""
    if test.text == q.MISSING:
        return NOT_COMPUTED
    return f"{test.text} {test.quantity.unit}"


def bound_text(bound: Bound) -> str:
    """e.g. ≥ 95.00 for a minimum"""
    return f"{'≥' if 'Minimum' in bound.name else '≤'} {bound.text}"


def pdf_subject(r2r: R2RResult) -> str:
    return f"R2R rating {r2r.rating}"


def certificate(result: NavQAResult, r2r: R2RResult) -> FrontPages:
    """The certificate page, for write_pdf's front"""

    def pages(lay: Layout) -> list[Flowable]:
        rating_colors = lay.colors.rating
        story = []
        facts = r2r_facts(result.config)
        if facts:
            story += [Paragraph(escape(" · ".join(facts)), lay.small), Spacer(1, 6)]
        banner = Table(
            [[Paragraph(f"R2R rating: {RATING_NAMES[r2r.rating]} ({r2r.rating})", lay.banner)]],
            colWidths=[lay.frame_width],
        )
        banner.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(rating_colors[r2r.rating])),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story += [
            banner,
            Spacer(1, 3),
            Paragraph(escape(r2r.description), lay.small),
            Spacer(1, 10),
        ]
        rows, styles = [], []
        for i, test in enumerate(r2r.tests, start=1):
            name = Paragraph(
                f"{escape(test.name)}<br/><font size={lay.small.fontSize}>"
                f"{escape(test.description)}</font>",
                lay.cell,
            )
            green, yellow = (bound_text(b) for b in test.bounds)
            rows.append([name, test.rating, result_text(test), green, yellow, test.detail])
            styles.append(("BACKGROUND", (1, i), (1, i), tint(rating_colors[test.rating])))
        story += [
            Paragraph("R2R tests", lay.heading),
            table(
                lay,
                ["Test", "Rating", "Result", "G", "Y", "Detail"],
                rows,
                [0.34, 0.08, 0.13, 0.11, 0.13, 0.21],
                styles,
            ),
            Spacer(1, 12),
            Paragraph("Infos", lay.subheading),
            table(lay, ["Info", "Value"], [[i.name, i.text] for i in r2r.infos], [0.3, 0.2]),
            Spacer(1, 12),
            KeepTogether(
                [
                    Paragraph("Ranges", lay.subheading),
                    Paragraph(
                        "Over every record before QC, as R2R reports them; the statistics "
                        "below are over the fixes that pass QC.",
                        lay.small,
                    ),
                    table(
                        lay,
                        ["Range", "Quantity", "Minimum", "Maximum"],
                        [
                            [name, quantity.label, minimum, maximum]
                            for name, quantity, minimum, maximum in supplemental_ranges(result)
                        ],
                        [0.22, 0.42, 0.18, 0.18],
                    ),
                ]
            ),
        ]
        return story + [Spacer(1, 12)]  # the checks follow on the same page if they fit

    return pages


NOTEBOOK_CELLS = [
    new_markdown_cell(
        "## R2R certificate\n\n"
        "R2R's six tests, rated G/Y/R against R2R's bounds from the statistics above, the "
        "infos, and R2R's ranges, over every record before QC: what the R2R QA 2.0 XML and the "
        "PDF's certificate say."
    ),
    new_code_cell(
        "display(pd.DataFrame([\n"
        '    {"test": t.name, "rating": t.rating, "result": result_text(t),\n'
        '     "G": bound_text(t.bounds[0]), "Y": bound_text(t.bounds[1]), "detail": t.detail}\n'
        "    for t in r2r.tests\n"
        "]))\n"
        'display(pd.DataFrame([{"info": i.name, "value": i.text} for i in r2r.infos]))\n'
        "display(pd.DataFrame([\n"
        '    {"range": name, "quantity": quantity.label, "minimum": low, "maximum": high}\n'
        "    for name, quantity, low, high in supplemental_ranges(result)\n"
        "]))"
    ),
]
