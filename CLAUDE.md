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

GitHub: `OceanDataTools/nav-qa-processing`, private for now. What exists so far (Phases 1 to 4 of the plan in `docs/architecture.md`):
- `quantities.py`: the project-wide quantity table (see below).
- `formats/definitions/*.toml`: a definition of every R2R raw nav format ID (layout, sentences,
  vessels, examples) plus `sentences.toml`, written in this project's words (see its README).
  `formats/catalog.py` loads them and `nav-qa-processing formats [ID]` prints them. Add or fix a
  format's description there, not in code.
- `formats/__init__.py`: the registry. `READERS` maps format IDs to a `Reader` (`read`, and
  `span`: a file's first and last time, to select and order files by): so far `nav01`,
  `nav20`, `nav24`, `nav25` and `nav29` (`GGA_ZDA`), `nav33` (`TIMESTAMPED_GGA`), `nav02`
  (`SCS_GGA`, #81), `nav12` (`ZDA_OR_STAMP`, #84), `nav15` (`OSU_GGA`, #88), `nav22` (`FILE_STAMP_GGA`, #90) `nav27` (`GLL_ZDA`, #92: `gga_zda.read(kind="GLL")`, `nmea.GLL.as_gga()` without satellites, HDOP or altitude), `nav30` (`TIMESTAMPED_GGA`: OpenRVDAS's GGA, not its RMC), `nav32` (`SCS_RMC`: `timestamped_gga.read(kind="RMC")`, dated by the logger, RMC dates that differ reported; #94), `nav31` (`PYRTM_GGA`) and `uhdas` (`UNIXD_GGA`, named: the year from the file name) (#97, `uhdas.py`: GGA dated by UHDAS's clock sentence), and `nav03` (`SCS_LOGGER_TIMED`, #99, `logger_timed.py`: GLL, often cut after the longitude, and GGA, all timed by the logger); `get(id)` refuses formats without one.
  `nmea.py` (sentences, checksum, GGA, ZDA); `gga_zda.py` (each GGA dated by the nearest ZDA);
  `timestamped_gga.py` (each GGA dated by its line's logger timestamp: `iso_time` for
  OpenRVDAS and Falkor (too), `scs_time` for NOAA SCS's `MM/DD/YYYY,hh:mm:ss.sss`, `osu_time`
  for OSU's `DATA, <ISO>,` with the sentence quoted or not, `nmea.unquoted`);
  `zda_or_stamp.py` (nav12: a file with ZDA by `gga_zda`, one without by its
  `year:day-of-year` logger timestamps, `doy_time`); `file_stamp.py` (nav22: no date in the
  data; a file's first GGA nearest its name's `YYYYMMDDhhmmss`, each next one nearest the fix
  before; `Reader.named` passes the name to `span`). All keep every valid GGA; NavManager's
  differences are in their docstrings.
- `rawnav.py`: `read_fileset(paths, format_id, start, end)` -> `RawNav` (DataFrame, receiver
  decimals, files read/skipped, errors, GGA talker IDs counted, `nmea.TALKERS` names them;
  `skipped_lines`, the sentences each reader couldn't use per file and reason, from its
  `Result.skipped` with `gga_zda`'s reasons, and `unread_files`, #67). It selects files by the reader's span (first to last
  ZDA, or logger timestamp), as navdatalist does.
- `r2rnav.py`: read/write legacy r2rnav.
- `geodesy.py`: distance and azimuth with pyproj, segment speed/course, acceleration, and
  `sog_cog` (navsogcog's definition of SOG/COG, without its arithmetic quirks).
- `config.py`: `QAConfig` (every field optional; integration tables ignored; one feed:
  `nav_format`, `nav_files`, `nav_device`, `nav_name`, `nav_primary`, and `stem`, the outputs'
  prefix: the cruise ID, plus the name of a non-primary feed), `load_config(path, nav=None)`
  (one feed, the primary by default) and `load_configs` (each `[[nav]]`, primary first;
  `nav_tables` checks them), `NavDevice` (`[nav.device]`: make, model, serial number,
  location; #58), `Extent`
  (`Extent.around`: the box around positions, OpenVDM's largest-gap rule across 180°),
  `Thresholds` (QC and QA limits, TOML-friendly: dates and day counts), `load_config`,
  `config_to_toml` (configs print as their TOML).
- `fileset.py`: `load_fileset(path, files, manifest_path)` -> `Fileset` (a bag or a directory;
  the nav files by glob; the bag's manifest with R2R's file ids, or an external one);
  `verify_manifest` lists problems as text.
- `qa.py`: `run_qa(fileset, config=None)` -> `NavQAResult` (the core pipeline: reader within
  the cruise-date window, QC, products; `source` for GeoCSV; `track_extent`, the box around
  the good fixes; errors as text). `ConfigError`
  when the config can't run the QA (no `[nav] format`).
- `stats.py`: `compute(nav, qc_result, thresholds, extent)` -> `NavStats` (navqa's statistics on
  the pre-QC records, done soundly: completeness from interval slots, QC's acceleration) and the
  core checks (`CheckResult`: pass/warn/info/n/a against `Thresholds`). In `NavQAResult.stats`.
  `NavStats.counted` keeps which records each check counted; `counts_by_file` splits them by
  nav file for the PDF's and notebook's "Checks by file" (#61). `gaps(nav, qc_result,
  thresholds)` lists the gaps, longest first, with their files (#65). `NavStats.good`
  (`GoodFixes`) has the ranges over the fixes that pass QC, beside navqa's pre-QC ones (#66).
  `NavStats.verdict` is the checks in a line, (status, text), under the PDF's title (#68).
  `compute(..., land)` adds the "Over water" check (#79): `NavStats.inland` [m] per record,
  `counted["over_land"]` beyond `Thresholds.max_inland` (warn); fixes beyond `land_tolerance`
  but within it are reported in the detail (#95); n/a without the land.
- `land.py`: `load(lon, lat)` -> `Land` (Natural Earth 1:10m land less lakes around the fixes,
  via `maps.clipped_geometries`, with the river centerlines as water, #85; None without the
  map data), `Land.inland(lon, lat)` [m to the nearest coast or river]. `qa.run_qa` loads it.
  A check, not a QC flag: harbors are land at 1:10m (RR2212: 1,438 m) and river lines are
  generalized (RR1802 on the Willamette: 2,630 m), hence `land_tolerance` 3000 m; canals the
  map lacks (RC0014: 3,929 m) are reported, and only fixes beyond `max_inland` 10 km warn.
- `network.py` (`reaching`), `readable.py` (`Readable`, `plural`, ...);
  `tests/test_boundaries.py` keeps core from importing `integrations/`.
- `integrations/r2r/config.py`: `CruiseConfig(QAConfig)` (cruise ID and dates required; R2R's
  IDs, catalog dates, `Port`s, `Device`, format ID/alias, provenance; `identifier`
  `<cruise>_<fileset>` or `<cruise>_gnss`), `load_cruise_config`, `config_to_toml`.
  `configs/<CRUISE>_<FILESET>.toml` are written by `r2r config-from-catalog` (SP2613, SP2503,
  SP2424).
- `integrations/r2r/rules.py`: `run_r2r_tests(stats)` -> `R2RResult` (R2R's six tests, G/Y
  bounds stated once, results rated as written through `quantities.py`; infos TotalTests…).
- `integrations/r2r/xml_report.py`: `build_qa_xml`/`write_qa_xml(result, r2r, path)` with the
  structure of R2R's current nav report (the duplicate `<r2r:id>` omitted, register X1) and
  `qa_xml_name`. `tests/test_r2r_xml.py` compares it with R2R's SP2613, SP2503 and SP2424
  reports: same element paths, ratings, infos and values but for accelerations (A5).
- `qc.py`: `flag(nav, thresholds, start, end)` -> `QCResult` (per-record `Flag` bitmask, the
  acceleration limit used, notes). No speed rule; unreachable fixes (`max_reach_speed`, far
  above any ship) flagged whole stretches at a time; acceleration over a time base with velocity
  vectors; excursions flagged, steps reported (decisions in its docstring).
- `products.py`: `bestres(nav, qc_result)`, `sample(bestres, interval)` (1min: the good record
  nearest each whole minute, within half a minute) and `control(one_minute)` (Douglas–Peucker
  at 0.01°, by shapely, on unwrapped longitudes).
- `geocsv.py`: write and read the GeoCSV 2.0 products (`BESTRES`, `ONE_MINUTE`, `CONTROL`) with
  R2R's keyword structure and our quantities; source values (attribution, DOIs) are passed in as
  a `Source`, never defaulted to R2R's. Flagged bestres records are `#` + time through antenna
  height, as R2R writes them (SP1925).
- `integrations/r2r/catalog.py`: `config_from_r2r_catalog(cruise, fileset)` (api/cruise,
  api/fileset, info/vessel/…/device/…, filled from R2R's QA report where the API has nothing:
  port coordinates, format alias, catalog dates), `config_from_qa_report`, `nav_format` (R2R
  format ID -> ours via the definitions' `r2r_formats`), `R2RError`.
- `integrations/r2r/download.py`: `download_fileset` -> `data/<CRUISE>_<FILESET>_gnss/` (raw
  bag, `r2r_qa.2.0.xml` via api/qa_info, `products/` via api/product).
- `integrations/r2r/certificate.py`: `certificate(result, r2r)` (a `FrontPages`: R2R's facts,
  the G/Y/R banner from the theme, the tests with results and bounds as the XML writes them,
  the infos), `NOTEBOOK_CELLS`, `pdf_subject`. `tests/test_r2r_certificate.py` checks it
  against the XML on the five reference filesets.
- `integrations/openvdm/hook.py` (#49, ported from sbe-qa-processing): `SiteConfig` (the site
  TOML: `[vessel]`, `feeds` (`SiteFeed`s from `[nav]` or `[[nav]]`: name, primary, format,
  files, device, transfer; `feeds_for(transfer)`, #58), `[extent]`, `[openvdm]
  output_extra_directory` (`NAV_QA`), `[provenance]`, `[geocsv] attribution`, `[thresholds]`,
  `[theme]`), `load_site_config`, `site_config_to_toml`,
  `resolve(getCruiseConfig, site, transfer, vessel, set_extent, feed)` -> `HookRun` for one
  feed (the `QAConfig`, directories, owner, extent and vessel sources, notes); the CLI loops
  over the transfer's feeds, primary first, and one feed's failure doesn't stop the others,
  `cruise_toml`, `has_nav_changes` (`--changed-files` against `[nav] files`),
  `queue_md5_update`, `chown_tree`, `set_cruise_extent` (#1: OpenVDM 2.17's `cruiseExtent`
  from `NavQAResult.track_extent`, with `workerApiKey`; `--no-set-extent` checks against it
  instead); pyyaml only in `load_openvdm_yaml`. The `openvdm` and
  `config-from-openvdm` commands (`tests/test_openvdm.py`, a stand-in API).
- `integrations/openvdm/site_setup.py` (#50, ported from sbe-qa-processing): `Prompter`,
  `prompt_site_config(defaults, prompt, from_openvdm)`: the `site-config` command's prompts on
  stderr (the nav format checked against `READERS`; `openvdm.yaml`'s vessel fields not asked
  for). `tests/test_site_config.py` scripts the input.
- `integrations/r2r/reports.py`: `run_r2r(fileset, config)` -> `R2RRun`; `write_r2r_reports`
  writes the core reports with R2R's source values and certificate (`notebook_extras`), then
  the XML, by R2R's names. `config.loads_cruise_config` reads a cruise TOML's text (for a
  notebook whose config had no file).
- `theme.py`: `Theme` (page, fonts, logo, footer, `Colors` incl. status and G/Y/R rating colors,
  `colorblind` palette), `load_theme`, `resolve_theme(theme_file, config)` (the config's
  `[theme]` over a `--theme` file), `ThemeError`; ported from sbe-qa-processing.
- `maps.py`: Natural Earth layers via cartopy, clipped to the view; `prefetch` for
  `fetch-map-data`; a missing layer is drawn without (ported from sbe-qa-processing).
- `plots.py`: `track_map`, `speed_and_acceleration`, `timeline`, `histograms` (`FIGURES`), each
  `(result, theme) -> Figure`; labels from `quantities.py`; long series reduced for drawing.
  `QCResult.steps` gives the position steps' times for the map. The map's legend and inset
  are placed after a layout pass, where they cover no fix (#59; `_place_legend_and_inset`).
- `pdf_report.py`: `write_pdf(result, path, svgs, front=None, subject, theme)` (ReportLab +
  svglib): title block, checks, statistics, products; QC with the timeline; map, series,
  histograms. `Layout` (theme made concrete) and `FrontPages`, through which integrations add
  pages after the title; `table`, `drawing`, `checks_table` for them to reuse.
- `notebook.py`: `build_notebook`/`write_notebook(result, path, output_dir, execute, extras,
  theme_file)`: re-runs the QA step by step; a config without a file goes in as TOML
  (`config.loads_config`, or the integration's `config_toml`/`loads_config`); `NotebookExtras`
  for integrations. `execute` runs every cell but the last, which rewrites the reports. Its
  code is text: renaming a public function changes it (`tests/test_notebook.py` and
  `tests/test_r2r_certificate.py` execute it).
- `reports.py` (core): `report_paths`, `write_figures`, `write_reports(result, output,
  source=None, theme=None, pdf_front, pdf_subject, theme_file, notebook=True, execute_notebook,
  notebook_extras)`: the GeoCSV products, the SVGs in `<CRUISE>_plots/`,
  `<CRUISE>_qa_report.pdf` and `<CRUISE>_qa.ipynb` (the notebook itself passes `notebook=False`).
- `cli.py`: `formats`, `run FILESET_DIR [-c CONFIG] [-f FORMAT_ID]`, `fetch-map-data`, and the `r2r` group (`run`,
  `config-from-catalog`, `config-from-qa`, `fetch-fileset`). Conventions in its docstring
  (`-o`/`--force`, integer fileset IDs, uppercase metavars, `-v`/`-vv` on every parser, expected
  errors one line), checked for every command by `tests/test_cli.py`. Document every command,
  argument and TOML key in the README's tables when adding one.

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
  holds cruise TOMLs plus `site.example.toml`. Downloads and catalog configs are `r2r`
  commands, as in sbe-qa-processing.
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
fixes), SP2503 (rated R: completeness, gap), and for the tests R2R rates Y or R (#26): RR2406
(sentences split across logger lines), RR2212 (files out of order), SP2220 (no fix); FKt230417
(fileset 157454, Falkor (too)'s POS/MV, an INS fileset: nav33, rated G; R2R lacks its 0A–0F
checksums, R1); and one per new reader (#81): RR1802 (nav01, record for record; no QA 2.0
report), EN735 (nav02, G) and SKQ202516S (nav24, Sikuliaq's Seapath, G); and (#84, record for
record against R2R's products, no QA 2.0 reports) HLY14TF (nav25), KN220-02 (nav20), HLY08TD
(nav12 with ZDA) and MGL1509 (nav12 without, rated Y); and W1108A and W1109B (#88, nav15,
within R2R's Pacific-day window; R2R misdates 17 minutes of W1109B, R12); NH1209 (#90, nav22,
against R2R's primary-GPS bestres, as R2R made no products from it); SP1531 (#92, nav27, record
for record, quality from GLL); RC0014 and RC0011 (#94, nav32, record for record, R and Y as
R2R); BH19-30 (#97, nav31, record for record) and UHDAS's GPS logs in SP2613's and KM1519's
UHDAS ADCP filesets (nav31, uhdas: against the primary GPS); BH10-17 (#99, nav03, record for
record); SR2612 (#108, nav29 at 5 Hz: G here, Y in R2R's report, whose completeness truncates
steps to whole milliseconds, A8); see
`tests/fixtures/README.md` and `tests/test_r2r_references.py`.

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
