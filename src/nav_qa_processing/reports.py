"""The core reports of a QA run. For now these are the three GeoCSV products,
`<CRUISE>_bestres|1min|control.geoCSV`; the PDF, figures and notebook join them in Phase 4.
"""

from pathlib import Path

from nav_qa_processing import geocsv
from nav_qa_processing.qa import NavQAResult


def report_paths(output: Path, cruise_id: str) -> dict[str, Path]:
    """What write_reports writes, by product name"""
    return {
        name: Path(output) / geocsv.file_name(cruise_id, product)
        for name, product in geocsv.PRODUCTS.items()
    }


def write_reports(
    result: NavQAResult, output: Path, source: geocsv.Source | None = None
) -> list[Path]:
    """The three GeoCSV products, created at the run's end. `source` replaces the config's
    GeoCSV source values (e.g. R2R's)
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    frames = {
        geocsv.BESTRES.name: result.bestres,
        geocsv.ONE_MINUTE.name: result.one_minute,
        geocsv.CONTROL.name: result.control,
    }
    return [
        geocsv.write(
            output,
            geocsv.PRODUCTS[name],
            frame,
            result.nav.decimals,
            source or result.source,
            created=result.finished,
        )
        for name, frame in frames.items()
    ]
