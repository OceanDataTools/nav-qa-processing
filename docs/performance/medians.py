"""The medians of benchmark.sh's runs of one fileset, as docs/performance.md's table rows: wall
time [s], CPU time [s] and peak memory [MB] for each step, NavManager's total (its steps' times
added, the largest of their peaks) and each tool's records.

usage: medians.py RUN.tsv...      e.g. medians.py results/SP2613.*.tsv
"""

import statistics
import sys
from pathlib import Path

NAVMANAGER = ["navcopy", "navqc", "navsogcog", "navsample 1min", "navsample control", "navqa"]
OURS = ["same work", "run"]

runs = []
for path in sys.argv[1:]:
    rows = dict(line.split("\t", 1) for line in Path(path).read_text().splitlines())
    runs.append({label: [float(v) for v in values.split("\t")] for label, values in rows.items()})
for rows in runs:
    wall, cpu, memory = zip(*(rows[step] for step in NAVMANAGER))
    rows["NavManager"] = [sum(wall), sum(cpu), max(memory)]


def row(label: str) -> str:
    wall, cpu, memory = (statistics.median(column) for column in zip(*(r[label] for r in runs)))
    return f"| {label} | {wall:,.2f} | {cpu:,.2f} | {memory:,.0f} |"


print(
    f"{len(runs)} runs; records: NavManager {runs[0]['records'][0]:,.0f}, "
    f"nav-qa-processing {runs[0]['records'][1]:,.0f}"
)
for label in [*NAVMANAGER, "NavManager", *OURS]:
    print(row(label))
