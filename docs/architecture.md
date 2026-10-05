# Architecture: NavManager and its Python port

This document describes how R2R's [NavManager](https://github.com/rvdata/NavManager) is built
(last upstream commit 2025-07-02). It assesses what NavManager does well and badly, then proposes
the architecture for `nav-qa-processing`. The proposal follows the patterns already proven in
[sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing).

File references such as `navqc.inc.php:917` point into the NavManager repository.

---

## Part 1: NavManager today

### What it produces

From a ship's primary GPS feed, NavManager produces three R2R navigation standard products and a
quality assessment. The products are tab-separated text in the r2rnav format
(`doc/fileformat/format-r2rnav.txt`):

| Product | File | Content |
|---|---|---|
| NavBestRes | `<CRUISE>_bestres.r2rnav` | Every fix at the native rate. Bad fixes are kept, with a leading `#`. Columns: time, lon, lat, GGA quality, satellite count, HDOP, altitude, SOG [m/s], COG [°] |
| Nav1Min | `<CRUISE>_1min.r2rnav` | Good fixes decimated to one per minute, with SOG and COG carried over from BestRes |
| NavControl | `<CRUISE>_control.r2rnav` | Douglas–Peucker control points: time, lon, lat |

The QA output is a statistics object, written as text, JSON and an R2R QA 2.0 XML
`certificate`.

### Runtime and layout

- PHP ≥5.2 command line scripts in `bin/`. Each one is a thin `getopts` wrapper around a library
  in `include/*.inc.php`. You install NavManager by adding `bin/` to `PATH`.
- Java ≥1.5 for NavControl. The compiled `.class` files are committed in `include/NavControl/`.
  There is also an unwired Python port, `navsimplifier.py`.
- GMT 4 is needed only for the experimental `navplot.php`.
- Constants live in `include/globals.inc.php`:

  | Constant | Value |
  |---|---|
  | `MAX_SPEED` | 8.7 m/s |
  | `MAX_ACCEL` | 1 m/s² |
  | `MAX_GAP` | 300 s |
  | Valid GPS dates | 1994-03-01 to 2034-12-31 |

  The QC flag character is defined in `include/flags.inc.php`.
- There are no tests and no packaging. The code is roughly 20k lines of PHP, almost half of it
  in two files: `navcopy.inc.php` (4,270 lines) and `navdatalist.inc.php` (3,190 lines).

### Pipeline

The stages run as separate commands, connected by files on disk:

```mermaid
flowchart LR
  raw[(raw nav files)] --> dl[navdatalist<br/>time-ordered file list]
  dl --> cp[navcopy<br/>format → r2rnav]
  cp --> pre[bestres.preqc.r2rnav]
  pre --> qa[navqa<br/>statistics + QA 2.0 XML]
  pre --> qc[navqc<br/>flag bad fixes]
  qc --> sc[navsogcog<br/>append SOG/COG]
  sc --> best[NavBestRes]
  best --> s1[navsample -t 60]
  s1 --> min[Nav1Min]
  best --> ctl[navsample -c<br/>Java Douglas–Peucker]
  ctl --> control[NavControl]
```

1. **navdatalist** reads the first and last timestamp of each raw file, using format-specific
   code. It orders the files by those timestamps and drops files outside the cruise dates. It
   reports gaps over 12 h and files it cannot parse. The **decompressor** first unpacks
   zip/gz/bz2 files into a temporary directory.
2. **navcopy** converts the ordered files into r2rnav. The caller has to name the input format
   (`-f nav1`). There is no format detection. About 37 format IDs are supported (`nav1`…`nav33`,
   `uhdas`, `siomet`, `metoc_nav`), and each is tied to the vessels and acquisition systems that
   produced it. Each format is documented in `doc/fileformat/format-*.txt`. About 15 formats are
   parsed in `include/parsers/navN.php`; the rest are inline `case` blocks in `navcopy.inc.php`.
   NMEA parsing (GGA, GLL, RMC, ZDA, VTG, plus UHDAS PYRTM and UNIXD) is in `nmeatools.inc.php`.
   GGA carries no date, so formats pair it with ZDA, RMC or a logger timestamp.
3. **navqa** makes two passes over the pre-QC file:
   - The first pass finds the modal epoch interval and the time precision.
   - The second pass counts statistics:
     - gaps, and gaps over `MAX_GAP`,
     - records out of sequence,
     - bad GGA quality indicators,
     - speeds and accelerations over their limits,
     - min/max ranges of satellite count, HDOP, altitude, speed and acceleration.

   Percent completeness is `actual countable epochs / (duration / modal interval)`.
   `xmltools2.inc.php` turns the statistics into six rated tests:

   | Test | G | Y | otherwise R |
   |---|---|---|---|
   | PercentCompleteness | ≥95 % | ≥75 % | |
   | LongestGapEpoch | ≤900 s | ≤86,400 s | |
   | PercentRecordsOutOfSequence | ≤5 % | ≤10 % | |
   | PercentRecordsWithBadQualityIndicator | ≤5 % | ≤10 % | |
   | PercentUnreasonableSpeeds | ≤5 % | ≤10 % | |
   | PercentUnreasonableAccelerations | ≤5 % | ≤10 % | |

   The overall rating is G if every test is G, R if any test is R, and Y otherwise. The certificate
   also gets infos (`TotalTests`, `GTests`, `YTests`, `RTests`) and `filesetinfo_supplemental`
   ranges (NSV, HDOP, Altitude, HorizontalSpeed, HorizontalAcceleration). Percentages are
   formatted `%0.2f` before they are compared with the thresholds.
4. **navqc** flags fixes for any of these reasons:
   - outside the cruise dates;
   - `00:00:00` at 0,0;
   - invalid lon or lat;
   - an invalid GGA fix: quality outside 1–5, 0 satellites, HDOP of 0 or over 50, or more than
     50 satellites;
   - \|altitude\| ≥ 500 m;
   - a timestamp that isn't later than the last good one, or is more than 30 days after it;
   - identical positions while the running median speed is above 0.25 m/s, which catches stale
     repeats from loggers with uncalibrated clocks;
   - speed or acceleration over the limits. Speed is computed with Vincenty on WGS-84. These
     checks run in up to 3 iterations over each 3,600-record buffer.

   The acceleration limit is raised to 3× the position resolution when coordinates have fewer than
   6 decimals. This was a workaround for one vessel (Endeavor).
5. **navsogcog** appends instantaneous SOG and COG, computed from each fix to the next.
6. **navsample** decimates to a fixed interval without interpolating. With `-c`, it shells out to
   Java `navsimplifier`, which runs Douglas–Peucker with a planar tolerance of 0.01° on lon/lat.

### Strengths

- **A clear product standard.** The r2rnav format, the three products and the QA tests are
  specified and documented, and R2R has applied them across the UNOLS fleet for over a decade.
  That makes them a good parity target.
- **Hard-won format knowledge.** The format documents and parsers encode the quirks of real
  acquisition systems (SCS, Calliope, UHDAS, OpenRVDAS-era loggers, DAS prefixes, comma- or
  space-delimited variants). This knowledge is the most valuable thing to carry over.
- **Field-tested QC heuristics.** NavManager handles uncalibrated clocks, duplicate positions while
  underway, null-island records, low-precision receivers, out-of-order files and >30-day
  timestamp jumps. Each one is a lesson learned from real data.
- **Preserves the receiver's precision.** It tracks the number of digits right of the decimal
  ("NROD") for time, lon, lat and HDOP, and writes values back at that precision instead of
  inventing or dropping digits.
- **Non-destructive QC.** Bad fixes are flagged with `#`, not deleted, so BestRes keeps every
  record.
- **Small, composable stages and modest memory.** Records stream through fixed buffers, and each
  stage can run and be inspected on its own.
- **Geodesically correct speeds.** It uses Vincenty on the WGS-84 ellipsoid, not a spherical
  approximation.

### Shortcomings

**Platform**
- PHP 5-era code. In `navqa`, `count()` is called on a scalar mode value
  (`navqa.inc.php:375`), which throws a `TypeError` on PHP ≥8.
- Compiled Java `.class` files are committed. GMT 4 is required for plots.
- No packaging, no versioning, no dependency management. Installation means editing `PATH`.
- No automated tests, so nothing guards a change against altering ratings.

**Structure**
- Format knowledge is duplicated. Each format needs a `case` in `navdatalist` (only the
  timestamps) and another in `navcopy` (the full parse), and the two can disagree. About half the
  formats are inline in a 4,270-line `switch`.
- The user has to know the format ID. Nothing detects it or reports that the wrong one was
  chosen.
- QA and QC are separate implementations of overlapping logic (navqa's 1,565 lines and navqc's
  1,233). Their speed and acceleration rules can diverge.
- QC works on fixed 3,600-record buffers. Speeds and accelerations are not evaluated across buffer
  boundaries, so which fixes get flagged depends on where the chunks fall. The end-of-file buffer
  is processed by a second, near-duplicate copy of the loop (`navqc.inc.php:917`).
- Library functions call `exit(1)` on errors, and callers suppress warnings with `@`
  (`@navqa(...)`, `@navdatalist(...)`). Failures surface as missing output, not as messages.
- Missing values are the string `"NAN"`, and min/max use sentinels (±10000). Types are checked by
  string comparison.
- Unused globals (`DATAPATH=/data/r2r`, `SPLIT_ON_DATELINE`), commented-out algorithm variants
  and a hard-wired `$debug = true` block obscure which logic is actually live.

**Correctness**
- `calcSpeedHori` checks `$lonTo == "NAN"` twice and never checks `$latTo`
  (`navtools.inc.php:176`).
- The `navqa.php` CLI takes the "ports" from the first and last records of the file it is
  assessing, so distance from port is always about 0. It also prints the start distance as the
  end distance.
- Percent completeness assumes one constant sampling rate. With several modal intervals it can
  exceed 100 %. The cookbook says to discard the value in that case, but the XML still rates it.
- The acceleration-limit inflation for low-precision receivers is silent and depends only on the
  first record's precision.
- Douglas–Peucker runs in planar degrees with a fixed 0.01° tolerance. Nothing handles the
  antimeridian beyond shifting longitudes over 180.

**Outputs**
- Without a template, `xml_write2` emits only the `certificate`: no `qareport` root, provenance or
  manifest. The full R2R report is assembled elsewhere in R2R's pipeline. The legacy
  `xmltools.inc.php` (QA 1.0) is still present.
- The rating thresholds are hard-coded twice: once in the rating `switch` and again as literal
  bound values.
- No human-readable report. The output is stdout text, JSON and XML, and the plot needs GMT.
- No OpenVDM or other shipboard integration. It is built for R2R's shore-side post-cruise
  processing.

### What R2R actually publishes today: GeoCSV

NavManager writes `.r2rnav`, but R2R no longer distributes that format. These findings come from
a released cruise: SP2613, raw GNSS fileset 169851 (Trimble BX992, format 100150 `starc_raw`), and
its product fileset 620146, both fetched 2026-10-05.

**The products are GeoCSV.** Product fileset 620146 (format 100157, `r2rnav_geocsv`, "R2R GeoCSV
Navigation") is a BagIt bag. Its manifest lists:
- `SP2613_bestres.geoCSV`
- `SP2613_1min.geoCSV`
- `SP2613_control.geoCSV`

Its `bag-info.txt` links the product back to the raw fileset (`R2R-ParentFileset: 169851`).
GeoCSV 2.0 is an IRIS/UNAVCO convention, specified in
`service.rvdata.us/docs/format_docs/100157/GeoCSV.pdf`:
- `#keyword: value` comment lines, then one CSV header line, then CSV data lines;
- lines starting with `#` are comments wherever they occur.

R2R's header block is the same for all three products, apart from the title and the columns:

```
#dataset: GeoCSV 2.0
#title: Processed Trackline Navigation Data: Best Resolution     (… One Minute Resolution / Control Points)
#field_unit: ISO_8601,degree_east,degree_north,(unitless),(unitless),(unitless),meter,meter\second,degree
#field_type: datetime,float,float,integer,integer,float,float,float,float
#field_standard_name: iso_time,ship_longitude,ship_latitude,nmea_quality,nsv,hdop,antenna_height,speed_made_good,course_made_good
#field_long_name: date and time,longitude of vessel,latitude of vessel,NMEA quality indicator,number of satellite vehicles observed,horizontal dilution of precision,height of antenna above mean sea level,course made good,speed made good
#standard_name_cv: http://www.rvdata.us/voc/fieldname
#ellipsoid: WGS-84 (EPSG:4326)
#delimiter: ,
#field_missing: NAN
#attribution: Rolling Deck to Repository (R2R) Program; http://www.rvdata.us/
#source_repository: doi:10.17616/R39C8D
#source_event: doi:10.7284/911454          (cruise DOI)
#source_dataset: doi:10.7284/169851        (raw fileset DOI)
#cruise_id: SP2613
#creation_date: 2026-07-31T20:05:56Z
iso_time,ship_longitude,ship_latitude,nmea_quality,nsv,hdop,antenna_height,speed_made_good,course_made_good
2026-07-09T15:11:57.50Z,-117.2337714755,32.7028130227,2,13,0.8,7.887,3.31,157.712
```

The data columns are the same as in r2rnav. Nav1Min has time, lon, lat, SOG and COG; NavControl has
time, lon, lat. Only the container is different.

**R2R's inconsistencies.** This project does **not** reproduce them. See "Standardized product
metadata" in Part 2.
- In bestres, `field_long_name` gives speed and course in the wrong order ("course made good,speed
  made good").
- The bestres SOG unit is `meter\second`, while 1min uses `meter/second`.
- Unit names differ between R2R products: nav uses `degree_east`, while R2R's CTD GeoCSV and the
  GeoCSV spec's own R2R example use `degrees_east`.
- The control file writes coordinates with Python's `repr` (`32.702744442`, trailing zeros
  dropped). Bestres and 1min keep fixed precision (`32.7027444420`).

**Format documentation is unreliable.** The format document `r2rnav_geocsv.txt` actually
describes Ocean Exploration Trust's tab-separated r2rnav files, not GeoCSV. The processing
description (`service.rvdata.us/docs/product/r2rnav/R2R_Navigation_DataProcessing_Description.pdf`,
v1.1) is the better reference. Its key points:
- The QA runs on the converted data before QC.
- QC flags bad points and never discards them.
- 1min takes the first valid record of each minute.
- Control runs Ramer–Douglas–Peucker on 1min.
- An "XML DP report" also exists, but I haven't found it online.

**NavControl comes from the Python simplifier, with the Java tolerance.** Running NavManager's
`include/NavControl/navsimplifier.py` on R2R's `SP2613_1min.geoCSV` reproduces
`SP2613_control.geoCSV` **byte for byte, including point order and number formatting**. That only
holds with the tolerance set to **0.01°**, the Java value. The script as committed uses 0.1°,
which returns 3 points instead of 5. So R2R's production simplifier is the Python one with a
locally changed tolerance.

**The raw format is OpenRVDAS-style.** Each line is an ISO 8601 logger timestamp, a comma, then the
NMEA sentence, e.g. `2026-07-09T15:11:57.510016Z,$GNGGA,…`. The sentences are GGA, GLL, HDT, VTG,
RMC and ZDA, from the GN talker. This matches NavManager's `nav29`, whose most recent upstream
commit (2025-07-02) added comma-delimited input. Product timestamps come from the GGA time plus
the ZDA date (`15:11:57.50`), not from the logger timestamp (`15:11:57.510016`). Longitudes with 8
decimal places in minutes become 10 decimal places in degrees.

**What the bestres file shows:**
- It has 56,925 records, one for every GGA in the 2026-07-09 file. The three GGAs in the
  2026-07-07 file, which is before departure, were dropped by the date window.
- No record is flagged. How a flagged record appears in GeoCSV is still unknown: GeoCSV has no
  flag column, and a leading `#` would make the line a comment.

**R2R's nav QA report.** It is at
`service.rvdata.us/data/cruise/SP2613/fileset/169851/qa/SP2613_169851_qa.2.0.2026-07-31T20:05:56Z.xml`.
That file name includes a timestamp, so it does **not** match the `<CRUISE>_<FILESET>_qa.2.0.xml`
pattern sbe-qa-processing fetches for CTD. Find it through the `documents` field of
`service.rvdata.us/api/qa_info/?fileset_id=…`. Its structure:
- **Wrapper:** the same QA 2.0 layout as the CTD reports (`qareport`, `provenance` with
  `r2r_catalog_qa_bootstrap.php` v1.1, `filesetinfo` from the catalog, `references`, `manifest` of
  the raw files).
- **Certificate and supplements:** exactly the structure `xml_write2` emits, with the six tests,
  the `TotalTests`/`GTests`/`YTests`/`RTests` infos and the NSV, HDOP, Altitude, HorizontalSpeed
  and HorizontalAcceleration ranges. Values are formatted the PHP way (`7.00`, `0.00`, `1` for an
  HDOP of 1.0).
- **Timing:** the QA timestamp equals the bestres `creation_date`, so the QA and the products come
  from a single run.

The wrapper and the GeoCSV writer are not in the NavManager repository. They live in R2R's
unpublished catalog pipeline.

---

## Part 2: Proposed Python architecture

### Goals

1. **Parity first.** For R2R-released nav filesets:
   - the QA 2.0 certificate should match R2R's **by value**: the same test, info and range
     names, the same ratings, and numerically equal results and bounds;
   - the three products should match R2R's published `.geoCSV` files **by value**: the same
     records, timestamps, flags, and lon/lat/SOG/COG equal at the published precision.

   Datatypes, number formats, unit names and descriptions are standardized across the entire
   project (see "Standardized quantities" below). So neither the XML nor the GeoCSV is expected to
   match R2R byte for byte.

   NavManager's `.r2rnav` output is an intermediate parity target, used to localize differences.

   Wherever we deliberately depart from NavManager, the deviation sits behind a documented switch.
2. **One description per format.** Adding a vessel's format means adding one declarative
   definition plus a test fixture. No more parallel `switch` statements.
3. **Ship and shore.** The same package runs post-cruise from the CLI and at sea as an OpenVDM
   post-transfer hook, and produces the same outputs as sbe-qa-processing:
   - QA 2.0 XML,
   - PDF,
   - SVGs,
   - a notebook,
   - plus the three nav products as GeoCSV (R2R's current format), and optionally as `.r2rnav`.
4. **Errors are data.** A bad file or line is recorded on the result and shown in the report. It
   never aborts the run.

### Package layout

```
src/nav_qa_processing/
  cli.py            run | openvdm | config-from-r2r | formats
  quantities.py     the project-wide quantity table: name, description, datatype, unit, format
  config.py         CruiseConfig, Extent, Port, Thresholds (MAX_SPEED, MAX_ACCEL, MAX_GAP, rating bounds)
  fileset.py        discover files in a bag or directory; transparent gz/bz2/zip; manifest-md5
  formats/
    __init__.py     registry: FormatSpec lookup by ID, detect(path) -> ranked candidates
    nmea.py         sentence parsing (GGA, GLL, RMC, ZDA, VTG, HDT, GST, GSA), checksum, date carry-forward
    framing.py      line framings: bare NMEA, ISO/SCS/UNIX-time prefixes, delimited records
    specs.py        one FormatSpec per R2R format ID (nav1 … nav33, uhdas, siomet, metoc_nav)
    custom/         the few formats that need code rather than a spec
  rawnav.py         read_fileset -> RawNav (DataFrame + per-file errors + per-column precision)
  r2rnav.py         read/write legacy .r2rnav files, precision-preserving value formatting
  geocsv.py         read/write GeoCSV 2.0 nav products (header block, DOIs; column metadata from quantities.py)
  geodesy.py        distance/azimuth on WGS-84 (pyproj.Geod), speed, acceleration
  qc.py             flagging rules -> per-record flag bitmask (NavBestRes)
  products.py       bestres, sample(interval) (Nav1Min), control (Douglas–Peucker) (NavControl)
  stats.py          navqa statistics (modal interval, completeness, gaps, ranges)
  r2r.py            R2R tests, bounds, ratings, infos; inferred rules documented in the docstring
  qa.py             run_qa(config, fileset) -> NavQAResult
  xml_report.py     R2R QA 2.0 XML with provenance, certificate, manifest
  pdf_report.py     ReportLab PDF embedding the SVGs
  plots.py          one matplotlib Figure per function
  maps.py           cartopy trackline map with Natural Earth; degrades without coastlines
  notebook.py       nbformat notebook that re-runs the assessment
  openvdm.py        CruiseConfig from OpenVDM's API; hook skip/chown/pending-checksum logic
```

`config.py`, `fileset.py`, `xml_report.py`, `pdf_report.py`, `maps.py`, `notebook.py` and
`openvdm.py` mirror their sbe-qa-processing counterparts. Shared code can be factored into a
common package later. It is not a prerequisite.

### Data flow

```mermaid
flowchart LR
  fs[fileset.py<br/>files + manifest] --> rd[formats/ + rawnav.py<br/>RawNav DataFrame]
  rd --> st[stats.py<br/>pre-QC statistics]
  rd --> qc[qc.py<br/>flag bitmask]
  qc --> pr[products.py<br/>BestRes / 1Min / Control]
  st --> r2r[r2r.py<br/>tests + ratings]
  r2r --> res[NavQAResult]
  pr --> res
  res --> w[writers<br/>GeoCSV · r2rnav · XML · PDF · SVG · notebook]
```

`qa.run_qa(config, fileset)` is the only pipeline entry point. Every writer consumes the
`NavQAResult` it returns, which holds:
- the config,
- the file inventory,
- per-file errors,
- the `RawNav` frame,
- the flags,
- the products,
- the statistics,
- the R2R certificate.

### Key design decisions

**Formats are declarative.** Most NavManager formats are "some line framing plus some NMEA
sentences". A `FormatSpec` captures that: an ID, a description, the vessels that use it, the
framing, the sentences that carry position, quality and date, a file glob, and an optional
timestamp source. That one definition drives both "find this file's time range" (navdatalist) and
"parse it" (navcopy), so the two can't drift apart. Formats that resist a spec get a small class
in `formats/custom/` with the same interface. `formats.detect()` tries every spec on a sample of
lines and ranks the matches. The CLI then accepts an explicit `--format` or reports the best
guess. The `formats` subcommand replaces `navformat.php` and prints the spec and the
`doc/fileformat` text for each format.

**One table, not text files, between stages.** `RawNav` is a pandas DataFrame with columns
`time` (datetime64[ns, UTC]), `lon`, `lat`, `quality`, `nsat`, `hdop`, `alt`, plus `source_file`
and `line` for traceability. Missing values are real NaN/`<NA>`. Two things are kept beside it:
- **Precision:** the number of decimals NavManager tracks per value (NROD) is recorded per
  column per file, so `r2rnav.py` can reproduce the receiver's precision on output.
- **Errors:** per-file parse errors are kept in a list, the same pattern as
  `CastData.errors` in sbe-qa-processing.

**QC flags are a bitmask.** `qc.py` gives each record flag bits, e.g. `DATE_RANGE`, `NULL_ISLAND`,
`BAD_FIX`, `ALTITUDE`, `OUT_OF_SEQUENCE`, `STALE_DUPLICATE`, `SPEED`, `ACCEL`. A record with any
bit set is written with `#` in BestRes. The plots, PDF and notebook can then say *why* each fix
was flagged. The rules work on the whole series at once with numpy, not in 3,600-record buffers.
A `navmanager_buffering=True` compatibility mode reproduces the buffer-boundary behavior if parity
tests show it changes ratings. The default follows the parity evidence.

**Geodesy via pyproj.** `pyproj.Geod(ellps="WGS84").inv` replaces the hand-written Vincenty.
Before relying on it, a unit test checks that it agrees with NavManager's Vincenty to well below
the precision of the r2rnav output.

**QA statistics stay on the pre-QC data**, as `navqa.php` does, so that the certificate matches
R2R's. The statistics and the QC rules share `geodesy.py` and the same speed/acceleration
definitions, which removes NavManager's two divergent implementations. The PHP quirks that
affect the published values are reproduced and documented in the `r2r.py` and `stats.py`
docstrings, as sbe-qa-processing does for its inferred CTD rules:
- the modal interval with ties,
- rounding to 2 decimals before rating, which decides ratings at the boundaries,
- completeness above 100 %.

These rules govern the computed values and ratings. How the values are *written* follows the
standard formats below.

**Thresholds live in config.** `config.Thresholds` holds:
- the QC limits: `max_speed`, `max_accel`, `max_gap`, the GGA validity limits and the altitude
  limit;
- the R2R rating bounds.

A cruise TOML or OpenVDM's site TOML can override them. The XML `bounds` elements are written
from the same values, so the thresholds are no longer stated twice.

**Products.** `sample(interval)` keeps the first good fix in each interval and carries its
SOG/COG, as navsample does. `control()` re-implements the recursive Douglas–Peucker of
`navsimplifier.py` with a 0.01° tolerance. That algorithm and tolerance select exactly R2R's
SP2613 control points (see Part 1). Coordinates are written in the standard format below, not
with Python's `repr`. A shapely or antimeridian-aware variant can come later behind an option.

**GeoCSV is the primary product format.** `geocsv.py` writes R2R's header keywords (see Part 1),
including the cruise DOI (`source_event`) and the raw fileset DOI (`source_dataset`), with the
column metadata standardized as below. When R2R hasn't issued DOIs yet, as with OpenVDM at sea,
those lines are left empty or taken from the site config. Flagged bestres records need a GeoCSV
representation. Until R2R's convention is known, the plan is to keep them as `#`-prefixed lines,
as r2rnav does. GeoCSV readers then skip them as comments.

**Standardized quantities.** Every quantity the project writes is defined once in
`quantities.py`, with its name, description, datatype, unit and value format. This covers:
- GeoCSV columns,
- XML test results, bounds, infos and ranges,
- PDF tables and figure axes,
- the notebook.

Every writer formats values and labels units only through this table. No writer contains its
own format strings or unit names. The rules:

- **Units** are CF/UDUNITS strings, spelled the same everywhere: `s`, `m`, `m s-1`, `m s-2`,
  `degree`, `degrees_east`, `degrees_north`, `percent`, and `1` for every dimensionless value
  (counts, codes, HDOP). No `Dimensionless`, `Count`, `(unitless)`, `meter\second`, `m/s^2` or
  `degree_east`. Times are not a unit: their datatype is datetime and their format is ISO 8601,
  and GeoCSV's `field_unit` for them is `ISO_8601`, as the GeoCSV spec prescribes.
- **Datatypes** are one of `datetime`, `integer`, `float`, `string`, the GeoCSV vocabulary. An
  integer is always written without decimals; a float always uses its quantity's fixed decimals.
- **Formats** use fixed decimals and are never trimmed. A quantity has either a fixed precision,
  or "receiver precision": the maximum number of decimals the receiver reported for that column
  across the whole fileset, applied to every value. So a fileset with altitudes `7.76` and `7.887`
  writes `7.760` and `7.887`.
- **Times** are `YYYY-MM-DDThh:mm:ss[.f…]Z` in UTC, at the receiver's time precision.
- **Missing values** are `NAN` in GeoCSV (declared in `field_missing`), and an empty element with
  `xsi:nil="true"` in XML.
- **Descriptions** are written once, in plain grammatical sentences, and reused everywhere.
  R2R's swapped long names and the double space in the overall rating description are not
  carried over.
- **Identifiers are not changed.** Test, bound, info and range names (`PercentCompleteness`,
  `GMinimumThreshold`, `TotalTests`, `HDOP`, …), GeoCSV `field_standard_name`s and XML element
  names keep R2R's spelling, because R2R's tools key on them.

The proposed table follows. Precisions marked "receiver" follow the rule above.

| Quantity | Description | Datatype | Unit | Format |
|---|---|---|---|---|
| `iso_time` | Date and time (UTC) | datetime | ISO 8601 | receiver time precision |
| `ship_longitude` | Longitude of the vessel's GNSS antenna | float | `degrees_east` | receiver |
| `ship_latitude` | Latitude of the vessel's GNSS antenna | float | `degrees_north` | receiver |
| `nmea_quality` | NMEA GGA fix quality indicator | integer | `1` | |
| `nsv` | Number of satellites used in the fix | integer | `1` | |
| `hdop` | Horizontal dilution of precision | float | `1` | receiver |
| `antenna_height` | Antenna height above mean sea level | float | `m` | receiver |
| `speed_made_good` | Speed over ground, from successive positions | float | `m s-1` | 2 decimals |
| `course_made_good` | Course over ground, clockwise from true north | float | `degree` | 3 decimals |
| horizontal acceleration | Horizontal acceleration, from successive speeds | float | `m s-2` | 3 decimals |
| percentage (test results, bounds) | — | float | `percent` | 2 decimals |
| duration (gaps, epoch interval) | — | float | `s` | 3 decimals |
| count (records, tests, infos) | — | integer | `1` | |

Applied to the SP2613 certificate, this would change things as follows:
- `<r2r:bound … uom="Percent">95</r2r:bound>` becomes `uom="percent">95.00`;
- the HDOP maximum `1` (uom `Dimensionless`) becomes `1.0` (uom `1`);
- `m/s^2` becomes `m s-2`.

Comparisons with R2R parse the numbers and map the units.

**Reports and integration** follow sbe-qa-processing:
- **Figures** (`plots.py`):
  - a trackline map with flagged fixes and the cruise extent,
  - SOG and acceleration time series with the limits drawn,
  - a timeline of gaps and flags by reason,
  - histograms of the sampling interval, satellite count, HDOP and quality indicator.
- **PDF:** a certificate page followed by the figures.
- **Notebook:** re-runs `run_qa` step by step.
- **OpenVDM hook:** runs after the GNSS collection-system transfer and writes into an extra
  directory. Its behavior matches sbe-qa-processing's hook: skip, chown and pending-checksum
  handling.

### Validation strategy

1. **Unit tests** use small synthetic inputs:
   - each NMEA sentence and each framing;
   - each QC rule in isolation;
   - the boundary values of each rating;
   - precision round-trips through `r2rnav.py`.
2. **Format fixtures:** a few dozen real lines per format ID, taken from the examples in
   `doc/fileformat` or from public R2R filesets, each checked against an expected `RawNav`.
3. **NavManager parity:** where PHP 7 and Java are available, run NavManager on the same inputs.
   Compare the pre-QC r2rnav, BestRes, 1Min, Control and the certificate. Store the NavManager
   outputs as fixtures, so CI needs neither PHP nor Java.
4. **R2R parity:** for each released cruise, `scripts/fetch_r2r_fileset.py` fetches three
   things:
   - the raw GNSS bag,
   - the r2rnav product bag (located through `api/product/?cruise_id=…` or the fileset's
     `product_info`),
   - the QA 2.0 report (located through `api/qa_info/?fileset_id=…`).

   Tests then compare the certificate (as `test_r2r_comparison.py` does in sbe-qa-processing) and
   all three `.geoCSV` products. These tests skip when the data is absent. The first reference is
   SP2613: raw 169851, products 620146, QA rated G.

### Phased plan

| Phase | Scope | Exit criterion |
|---|---|---|
| 1 | `nmea.py`, `framing.py`, specs for the formats used by OpenVDM ships (nav29 first: SP2613's format), `rawnav.py`, `r2rnav.py` | pre-QC r2rnav identical to `navcopy` on fixtures |
| 2 | `geodesy.py`, `qc.py`, `products.py`, `geocsv.py` | SP2613 bestres/1min/control match R2R's `.geoCSV` by value (`tests/fixtures`, plus the full bestres in `data/`) |
| 3 | `stats.py`, `r2r.py`, `xml_report.py`, `config.py`, `cli.py run` | certificate matches R2R by value on released filesets |
| 4 | `plots.py`, `maps.py`, `pdf_report.py`, `notebook.py` | report and notebook generated for every validation cruise |
| 5 | `openvdm.py`, `cli.py openvdm`, site TOML | hook runs on an OpenVDM test instance |
| 6 | the remaining format IDs, `formats.detect()` | every `doc/fileformat` format has a spec and a fixture |

### Open questions

- **Which other R2R nav filesets should be parity references?** SP2613 (nav29, G) is the first.
  We still need one per format family, and at least one rated Y or R with flagged records.
- **How does R2R write a flagged bestres record in GeoCSV?** A cruise with flagged fixes will
  answer this.
- **Where is the "XML DP report"** that the processing description mentions, and should we produce
  it?
- **Should the port reproduce the flagging differences caused by buffer boundaries,** or report
  them as a documented deviation? Parity tests will show whether any rating depends on them.

Resolved:
- **Wrapper layout:** the nav QA 2.0 wrapper has the same layout as the CTD reports. The
  SP2613 report is the template.
- **NavControl:** produced by `navsimplifier.py` with a 0.01° tolerance (see Part 1).
- **Which collection-system names and file layouts do OpenVDM ships use for GNSS data?** They
  decide how the hook finds the primary GPS feed.
