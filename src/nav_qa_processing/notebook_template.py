"""The notebook `notebook.build_notebook` writes, as a Python file (#144), so Ruff checks its code
and renaming a function it calls is caught here. It's read as text, never imported.

Cells are in jupytext's percent format: `# %%` starts a code cell, `# %% [markdown]` a text
cell, whose lines are written after `# `. Everything before the first cell is this header.
In a code cell, a line ending in `# @name` is replaced by the run's code for `name` (nothing,
when it has none), and `# @raw TEXT` becomes TEXT (for what isn't Python, such as a magic);
`# fmt: off` and `# fmt: on` (keeping Ruff from moving a marker) are dropped. A cell of only
`# @cells` is where an integration's cells go. In text, `{name}` is the run's value.
"""

# %% [markdown]
# # Navigation QA: {cruise_id}{heading}
#
# {cruise_name}
#
# Re-runs the quality assessment behind this fileset's products and PDF report{reports}: it reads the primary GNSS feed ({nav_format}), flags bad fixes, makes bestres, 1min and control, and computes the QA statistics and checks{checks}. Edit the paths or the config's `[thresholds]` and re-run to change the assessment.
#
# Generated with {versions}.

# %%
from pathlib import Path

# Inputs and outputs
CONFIG = Path()  # @config_path
FILESET = Path()  # @fileset
THEME_FILE = None  # @theme_file
OUTPUT_DIR = Path()  # @output_dir

# %%
# @raw %matplotlib inline
import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import display

from nav_qa_processing import plots, reports, stats
from nav_qa_processing import quantities as q
from nav_qa_processing.config import load_config  # @config_imports
from nav_qa_processing.qa import run_qa
from nav_qa_processing.qc import DESCRIPTIONS, Flag
from nav_qa_processing.theme import resolve_theme

# @extras_imports

pd.set_option("display.max_colwidth", 120)

config = load_config(CONFIG)  # @config_lines
result = run_qa(FILESET, config)
theme = resolve_theme(THEME_FILE, result.config)
print(result)
print(result.stats.verdict[1])  # the checks in a line
# @after_run

# %%
# @cells

# %% [markdown]
# ## Checks
#
# Completeness, gaps, records out of sequence, bad quality indicators, unreasonable speeds and accelerations, and the expected area, against the config's `[thresholds]`.

# %%
display(pd.DataFrame([vars(c) for c in result.stats.checks]))

# %% [markdown]
# ### Checks by file
#
# What each check counted, per nav file. A speed, a step out of sequence or a gap is counted in the file of the fix that ends it.

# %%
display(stats.counts_by_file(result.nav, result.stats))
# The sentences the reader couldn't use, and why
display(stats.skipped_by_file(result.nav))

# %% [markdown]
# ### Gaps
#
# The steps of `max_gap` or longer between fixes, the longest first, with the files either side.

# %%
gaps = stats.gaps(result.nav, result.qc, result.config.thresholds)
# The length in seconds, and as hh:mm:ss
gaps.insert(3, "hh:mm:ss", gaps["length"].map(q.SAMPLING_INTERVAL.format_duration))
display(gaps)

# %% [markdown]
# ## Statistics

# %%
s = result.stats
ranges = {
    q.NSV: ("nsv", s.nsv, None),
    q.HDOP: ("hdop", s.hdop, result.nav.decimals.get("hdop")),
    q.ANTENNA_HEIGHT: ("altitude", s.altitude, result.nav.decimals.get("alt")),
    q.SPEED_MADE_GOOD: ("speed", s.speed, None),
    q.HORIZONTAL_ACCELERATION: ("acceleration", s.acceleration, None),
}
# Over every record, as R2R reports them, and over the fixes that pass QC
display(
    pd.DataFrame(
        [
            {
                "quantity": quantity.label,
                "minimum": quantity.format(r.minimum, places),
                "maximum": quantity.format(r.maximum, places),
                "good minimum": quantity.format(getattr(s.good, name).minimum, places),
                "good maximum": quantity.format(getattr(s.good, name).maximum, places),
            }
            for quantity, (name, r, places) in ranges.items()
        ],
    )
)
print(q.FIXES.label, q.FIXES.format(s.records))
print(q.FIXES.label, "that pass QC", q.FIXES.format(s.good.records))
print(q.MODAL_INTERVAL.label, q.MODAL_INTERVAL.format(s.modal_interval))
print(q.LONGEST_GAP.clock_label, q.LONGEST_GAP.format_duration(s.longest_gap))

# %% [markdown]
# ## Quality control
#
# Records flagged by each flag (a record can have more than one), QC's notes, and the reader's and manifest's errors.

# %%
display(
    pd.DataFrame(
        [
            {"flag": f.name, "meaning": DESCRIPTIONS[f], "records": result.qc.count(f)}
            for f in Flag
            if result.qc.count(f)
        ],
    )
)
for note in result.qc.notes:
    print(note)
for error in result.errors:
    print("error:", error)

# %% [markdown]
# ## Figures

# %%
for make in plots.FIGURES.values():
    figure = make(result, theme)
    display(figure)
    plt.close(figure)

# %% [markdown]
# ## Products
#
# bestres is every record, with its QC flags (0 for a good fix) and speed and course over ground; 1min is the good fix nearest each whole minute; control is Douglas–Peucker on 1min.

# %%
display(result.bestres.head())
display(result.one_minute.head())
display(result.control)

# %% [markdown]
# ## Write the reports

# %%
# fmt: off
written = reports.write_reports(
    result, OUTPUT_DIR, theme_file=THEME_FILE, notebook=False  # @report_arguments
)
# fmt: on
for path in written:
    print(path)
# @write
