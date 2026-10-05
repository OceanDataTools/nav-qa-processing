# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`nav-qa-processing` ports R2R's **NavManager** (PHP/Java, at `../../NavManager`, i.e.
`GitHub_Repos/NavManager`) to a modern Python package. It follows the architecture and conventions of
the sibling project `../sbe-qa-processing`. When this file doesn't settle a structural or tooling
question, do what `sbe-qa-processing` does: read its `CLAUDE.md`, `pyproject.toml` and
`src/sbe_qa_processing/` (especially `qa.py`, `r2r.py`, `xml_report.py`, `config.py`,
`openvdm.py`, `cli.py`).

GitHub: `OceanDataTools/nav-qa-processing`, private for now. The repo so far has only a
scaffold: `pyproject.toml`, a stub `cli.py` (`--version` only) and `quantities.py`. Add
dependencies (matplotlib, reportlab, svglib, cartopy, nbformat, …) as the modules that need
them land, and commit `uv.lock` with each change. Update this file as modules come into existence, and remove any section below
that stops describing reality.

## Target tooling (mirror sbe-qa-processing)

- uv, hatchling, `src/` layout, package `nav_qa_processing`, console script `nav-qa-processing`
  (`nav_qa_processing.cli:main`). Python ≥3.11.
- Install: `uv sync`. Tests: `uv run pytest`. One test: `uv run pytest tests/test_x.py::test_name`.
- Lint and format: `uv run ruff check .` and `uv run ruff format --check .` (line length 99).
- `data/` (downloaded R2R filesets) and `output/` (generated reports) are gitignored. `configs/`
  holds cruise TOMLs plus `site.example.toml`. `scripts/` holds fetch helpers. Base them on
  sbe-qa-processing's `fetch_r2r_fileset.py` and `fetch_r2r_config.py`.
- Outputs follow the same set: an R2R QA 2.0 XML, a PDF report (ReportLab, with the SVGs
  embedded), every figure as an SVG, and a notebook that re-runs the assessment. It runs from the CLI
  (`run`, `config-from-r2r`) and as an OpenVDM post-transfer hook (`openvdm`).

`docs/architecture.md` has the full analysis of NavManager (pipeline, rating thresholds, QC
rules, known bugs) and the proposed module layout, design decisions and phased plan. Keep it
in sync when the design changes.

## What NavManager does (the behavior to port)

The pipeline runs over the primary GPS feed. Each stage is a `bin/*.php` script backed by an
`include/*.inc.php` library:

1. **navdatalist** (`navdatalist.inc.php`) builds a time-ordered list of raw nav files.
   **decompressor** unpacks compressed inputs.
2. **navcopy** (`navcopy.inc.php`, about 4k lines) converts raw files into the r2rnav standard
   format. It dispatches on an input format ID (`nav1`…`nav33`, `uhdas`, `siomet`, `metoc_nav`).
   Some formats have parsers in `include/parsers/`, and others are inline `case` blocks in
   `navcopy.inc.php`. NMEA sentence classes are in `nmeatools.inc.php`. Each format is documented
   in `doc/fileformat/format-*.txt`, and `bin/navformat.php` lists them. Port parsers per format
   from these docs together with the PHP.
3. **navqc** (`navqc.inc.php`) flags bad records with a leading `#` to produce **NavBestRes**.
   It flags:
   - an invalid or dead-reckoning GGA fix (`valid_gga_fix`: quality, satellite count, HDOP),
   - a duplicate or backwards timestamp,
   - horizontal acceleration above the limit.
4. **navsogcog** appends speed and course over ground, computed on WGS-84 with Vincenty
   (`navtools.inc.php`).
5. **navsample** decimates without interpolating, producing **Nav1Min**. **NavControl** applies
   Douglas–Peucker to Nav1Min (`include/NavControl/`, Java, plus a partial `navsimplifier.py`).
6. **navqa** (`navqa.inc.php`) computes the QA statistics:
   - completeness, the longest gap and the number of gaps over `MAX_GAP`,
   - records out of sequence, bad quality indicators,
   - speed, acceleration, altitude, HDOP and satellite count ranges,
   - distance from the start and end ports.

   **xmltools2** (`xmltools2.inc.php`) writes R2R QA 2.0 XML. Its tests are
   `PercentCompleteness`, `LongestGapEpoch`, `PercentRecordsOutOfSequence`,
   `PercentRecordsWithBadQualityIndicator` and others, with hard-coded G/Y/R thresholds.
   **navbounds** computes the cruise geographic extent. The templates are in
   `include/Templates/`.

Default limits are in `include/globals.inc.php`:
- `MAX_SPEED` 8.7 m/s
- `MAX_ACCEL` 1 m/s²
- `MAX_GAP` 300 s
- valid GPS dates 1994-03-01 to 2034-12-31

In the port, these belong in a `Thresholds` dataclass in `config.py`, overridable from the
cruise TOML.

The r2rnav format is specified in `doc/fileformat/format-r2rnav.txt`:
- tab-separated
- 3 header lines prefixed with `//`
- ISO-8601 UTC timestamps with `Z`
- lon, lat in [-180, 180) and [-90, 90]
- NavBestRes columns: time, lon, lat, quality, nsat, HDOP, altitude, SOG [m/s], COG [°]
- file names `<CRUISE>_bestres|1min|control.r2rnav`

Preserve the receiver's precision as the spec describes.

**R2R now publishes the products as GeoCSV 2.0** (`<CRUISE>_bestres|1min|control.geoCSV`, R2R
format 100157). The columns are the same, but the files have a `#keyword: value` header block
and are comma-delimited. GeoCSV is the primary output and `.r2rnav` is secondary. R2R's header
layout is described in `docs/architecture.md` ("What R2R actually publishes today").

**Datatypes, formats, units and descriptions are standardized across the entire project**:
GeoCSV, QA XML, PDF and notebook. Never copy R2R's inconsistencies, such as swapped long names,
`meter\second` vs `meter/second`, `degree_east` vs `degrees_east`, `Dimensionless`/`Count`/`Percent`/
`m/s^2`, trimmed coordinates or PHP number printing. Every quantity is defined once in
`quantities.py` (name, description, datatype, CF/UDUNITS unit, fixed-decimal format), and every
writer formats values and labels units only through that table. Don't put format strings or
unit names in writers. The proposed table and rules are in "Standardized quantities" in
`docs/architecture.md`. Identifiers stay as R2R spells them, because R2R's tools key on them:
test, bound, info and range names, GeoCSV `field_standard_name`s and XML element names. Parity
with R2R is therefore **by value**: the same names, ratings, records, times and flags, with
numbers equal after parsing. It is never byte for byte.

R2R's control points are what NavManager's `navsimplifier.py` (recursive Douglas–Peucker)
selects with a **0.01°** tolerance, not the committed 0.1°. NavManager has no GeoCSV writer.

Nav QA reports are named `<CRUISE>_<FILESET>_qa.2.0.<timestamp>.xml`. Find them through the
`documents` field of `service.rvdata.us/api/qa_info/?fileset_id=…`. The product bag is in
`api/product/?cruise_id=…`. First parity reference: SP2613 (raw GNSS fileset 169851, format
nav29-style ISO-timestamp + NMEA; product fileset 620146; QA rated G).

## Porting rules

- R2R's XML output is a **parity target**, as it is in sbe-qa-processing. For released R2R nav
  filesets, the QA 2.0 `certificate` (test names, ratings, results, bounds, infos) and the three
  `.geoCSV` products must match R2R's by value (see above). Reference files are in
  `tests/fixtures/` (see its README). Take the rating thresholds and the rounding applied before
  rating from `xmltools2.inc.php` and `navqa.inc.php` exactly. Document every rule that is inferred rather than documented in
  the module docstring of `r2r.py`.
- When the PHP has quirks (buffering, least-count/precision handling, median-based checks in
  `navqc`), reproduce the observable output first, then note the quirk. Don't silently "fix"
  behavior that changes ratings.
- Use numpy/pandas arrays instead of the PHP's line-by-line streaming with fixed buffers. For
  geodesics, use a maintained library (e.g. `pyproj.Geod`) rather than porting Vincenty by hand,
  unless parity requires it.
- Per-file failures are recorded on the result, not raised, so one bad raw file never stops a
  cruise (the same pattern as `CastData.errors` in sbe-qa-processing).
- This project is MIT-licensed, and NavManager is GPL-3.0. Port behavior from specs and observed
  output, and don't copy PHP code or comments verbatim.

## Conventions (from sbe-qa-processing)

- Modern Python, dataclasses, short docstrings without `:param:` boilerplate, units in brackets
  in comments (`# [m/s]`).
- One `qa.run_qa(config, fileset)` entry point returns a result object that every writer
  consumes. `plots.py` has one matplotlib Figure per function, shared by the PDF and the
  notebook. `cli.py` forces the `Agg` backend.
- `notebook.py` writes import lines and calls as strings, so renaming a public function or
  module changes the generated notebook. Check by hand with `run ... --execute-notebook`.
- Tests are plain pytest functions. Use small synthetic inputs, and use downloaded R2R filesets
  for end-to-end parity. Parity tests skip when the data is absent, and the skip message names
  the fetch command.
