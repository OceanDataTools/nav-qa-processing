"""nav-qa-processing doing what NavManager's pipeline does, for docs/performance.md: read the raw
files, QC them, write the three products (bestres, 1min, control) and compute the QA statistics.
Not the PDF, figures or notebook, nor the over-water check, which NavManager has no equivalent
of (no map data).

usage: same_work.py RAW_DIR FORMAT DEPART ARRIVE OUTPUT_DIR   (dates YYYY-MM-DD)
"""

import os
import sys
from datetime import date
from pathlib import Path

os.environ["NAV_QA_NO_MAP_DATA"] = "1"  # no over-water check: NavManager has none

from nav_qa_processing import geocsv, qa
from nav_qa_processing.config import QAConfig

raw, nav_format, depart, arrive, output = sys.argv[1:]
config = QAConfig(
    cruise_id="BENCHMARK",
    nav_format=nav_format,
    depart_date=date.fromisoformat(depart),
    arrive_date=date.fromisoformat(arrive),
)
result = qa.run_qa(Path(raw), config)
Path(output).mkdir(parents=True, exist_ok=True)
for name, frame in (
    ("bestres", result.bestres),
    ("1min", result.one_minute),
    ("control", result.control),
):
    geocsv.write(Path(output), geocsv.PRODUCTS[name], frame, result.nav.decimals, result.source)
print(result)
