# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`nav-qa-processing` ports R2R's **NavManager** (PHP/Java, https://github.com/rvdata/NavManager,
cloned at `../../NavManager`, i.e. `GitHub_Repos/NavManager`) to a modern Python package. Port from
`master`; the other upstream branches are summarized in `docs/architecture.md` ("Upstream
branches"). Check them, e.g. `git diff master...origin/<branch>`, before concluding NavManager
lacks something. It follows the architecture and conventions of
the sibling project `../sbe-qa-processing`. When this file doesn't settle a structural or tooling
question, do what `sbe-qa-processing` does: read its `CLAUDE.md`, `pyproject.toml` and
`src/sbe_qa_processing/` (especially `qa.py`, `r2r.py`, `xml_report.py`, `config.py`,
`openvdm.py`, `cli.py`).

GitHub: `OceanDataTools/nav-qa-processing`, private for now. What exists so far (Phase 1 and part
of Phase 2 of the plan in `docs/architecture.md`):
- `quantities.py`: the project-wide quantity table (see below).
- `formats/definitions/*.toml`: a definition of every R2R raw nav format ID (layout, sentences,
  vessels, examples) plus `sentences.toml`, written in this project's words (see its README).
  `formats/catalog.py` loads them and `nav-qa-processing formats [ID]` prints them. Add or fix a
  format's description there, not in code.
- `formats/__init__.py`: the registry. `READERS` maps format IDs to readers (so far only
  `nav29`); `get(id)` refuses formats without one. `nmea.py` (sentences, checksum, GGA, ZDA) and
  `gga_zda.py` (each GGA dated by the nearest ZDA; every valid GGA kept; NavManager's
  differences in its docstring).
- `rawnav.py`: `read_fileset(paths, format_id, start, end)` -> `RawNav` (DataFrame, receiver
  decimals, files read/skipped, errors). It selects files by their ZDA span, as navdatalist does.
- `r2rnav.py`: read/write legacy r2rnav.
- `geodesy.py`: distance and azimuth with pyproj, segment speed/course, acceleration, and
  `sog_cog` (navsogcog's definition of SOG/COG, without its arithmetic quirks).
- `config.py`: `QAConfig` (every field optional; integration tables ignored), `Extent`,
  `Thresholds` (QC and QA limits, TOML-friendly: dates and day counts), `load_config`,
  `config_to_toml` (configs print as their TOML).
- `fileset.py`: `load_fileset(path, files, manifest_path)` -> `Fileset` (a bag or a directory;
  the nav files by glob; the bag's manifest with R2R's file ids, or an external one);
  `verify_manifest` lists problems as text.
- `qa.py`: `run_qa(fileset, config=None)` -> `NavQAResult` (the core pipeline: reader within
  the cruise-date window, QC, products; `source` for GeoCSV; errors as text). `ConfigError`
  when the config can't run the QA (no `[nav] format`).
- `stats.py`: `compute(nav, qc_result, thresholds, extent)` -> `NavStats` (navqa's statistics on
  the pre-QC records, done soundly: completeness from interval slots, QC's acceleration) and the
  core checks (`CheckResult`: pass/warn/info/n/a against `Thresholds`). In `NavQAResult.stats`.
- `network.py` (`reaching`), `readable.py` (`Readable`, `plural`, ...), and an empty
  `integrations/` package; `tests/test_boundaries.py` keeps core from importing it.
- `qc.py`: `flag(nav, thresholds, start, end)` -> `QCResult` (per-record `Flag` bitmask, the
  acceleration limit used, notes). No speed rule; acceleration over a time base with velocity
  vectors; excursions flagged, steps reported (decisions in its docstring).
- `products.py`: `bestres(nav, qc_result)`, `sample(bestres, interval)` (1min: the good record
  nearest each whole minute, within half a minute) and `control(one_minute)` (Douglas–Peucker
  at 0.01°, by shapely, on unwrapped longitudes).
- `geocsv.py`: write and read the GeoCSV 2.0 products (`BESTRES`, `ONE_MINUTE`, `CONTROL`) with
  R2R's keyword structure and our quantities; source values (attribution, DOIs) are passed in as
  a `Source`, never defaulted to R2R's. Flagged bestres records are `#` + time through antenna
  height, as R2R writes them (SP1925).
- `scripts/fetch_r2r_fileset.py CRUISE FILESET`: raw GNSS bag, QA report and r2rnav product bag
  into `data/<CRUISE>_<FILESET>_gnss/`.
- `cli.py`: only the `formats` subcommand so far.

SP2613's and SP2424's pre-QC records match R2R's full bestres by value (SP2424 has one more:
the final fix, which NavManager drops) (`tests/test_rawnav.py`; R2R evidently ran it
with the 2026-07-09 cruise-day window, which excludes the 07-07 file). Add
dependencies (matplotlib, reportlab, svglib, cartopy, nbformat, …) as the modules that need
them land, and commit `uv.lock` with each change. Update this file as modules come into existence, and remove any section below
that stops describing reality.

## Architecture: core and integrations (mirror sbe-qa-processing)

Decided 2026-10-14. A **core** runs the nav QA for anyone (`qa.run_qa(fileset, config=None)` ->
`NavQAResult`; `reports.write_reports`), and optional **integrations** build on it:
`integrations/r2r/` (R2R's tests, ratings, QA 2.0 XML, catalog configs, downloads; the `r2r`
commands) and `integrations/openvdm/` (the hook, `site-config`, `config-from-openvdm`; the
`openvdm` extra). **Core never imports an integration** (`tests/test_boundaries.py`, once Phase 3
adds it); integrations extend core through hooks (PDF `front` pages, `NotebookExtras`). Configs
are TOML, as in sbe-qa-processing: the config TOML (`QAConfig`, every field optional), the R2R
cruise TOML (`CruiseConfig`) and the site TOML, with a `[nav]` table for the GNSS feed. The layout
and examples are in `docs/architecture.md` ("Package layout", "Configuration TOMLs").

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
   in `doc/fileformat/format-*.txt` (more in the `docs` branch), and `bin/navformat.php` lists
   them. Our own definitions of them are in `formats/definitions/`. Port parsers per format from
   those together with the PHP.
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
test, bound, info and range names, GeoCSV `field_standard_name`s and XML element names.

**What "parity with R2R" means (decided 2026-10-13).** The target is the **format** of R2R's
published products (the three `.geoCSV` files and the QA 2.0 report), not NavManager's
arithmetic:
- **Structure must match exactly:** file names; GeoCSV keyword lines and their order; columns,
  their order, `field_standard_name`s and datatypes; delimiter; missing value; how flagged records
  are written; the QA report's XML elements, test/bound/info/range names and their order.
- **Text follows our standard,** not R2R's: units, long names and number formats come from
  `quantities.py` (see above).
- **Values are computed the sound way.** Tests compare them with R2R's and document every
  difference and its cause, with tolerances measured on real filesets. Ratings may differ where
  NavManager's arithmetic is wrong or fragile; say so in the test and in `docs/architecture.md`.

R2R's control points are what NavManager's `navsimplifier.py` (recursive Douglas–Peucker)
selects with a **0.01°** tolerance, not the committed 0.1°; shapely's `simplify` selects the same
points. NavManager has no GeoCSV writer: its
`geocsv` branch only switches to comma delimiters and `#` headers, so R2R's GeoCSV header block
comes from R2R's own tooling.

Nav QA reports are named `<CRUISE>_<FILESET>_qa.2.0.<timestamp>.xml`. Find them through the
`documents` field of `service.rvdata.us/api/qa_info/?fileset_id=…`. The product bag is in
`api/product/?cruise_id=…`. First parity reference: SP2613 (raw GNSS fileset 169851, format
nav29-style ISO-timestamp + NMEA; product fileset 620146; QA rated G). Others: SP2424 (past navsogcog's buffer), SP1925 (flagged
fixes) and SP2503 (rated R: completeness, gap); see `tests/fixtures/README.md`.

## Porting rules

- R2R's published products are a **format** target (see "What parity with R2R means" above).
  Reference files are in `tests/fixtures/` (see its README), larger ones in `data/`. Take the
  QC rules, rating thresholds and test definitions from NavManager, but implement them soundly.
  Document every rule that is inferred rather than documented in the module docstring.
- **Record every difference from NavManager in `docs/navmanager-differences.md`** as soon as it's
  found: NavManager's behavior with file and line, ours, the evidence (filesets, counts, test),
  *why* NavManager does it (confirmed in code, or suspected), and the effect on R2R's products.
  Add a row to its summary table. The user presents this register as a report.
- When the PHP has quirks (buffer boundaries, least-count/precision handling, stale variables,
  off-by-one records), **don't reproduce them**. Note each one, with evidence of its effect on
  R2R's products where a fileset shows it, in the module docstring and in the "Shortcomings"
  section of `docs/architecture.md`.
- Use numpy/pandas arrays instead of the PHP's line-by-line streaming with fixed buffers. Prefer
  maintained libraries to NavManager's hand-written math: for geodesics, `pyproj.Geod`.
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
