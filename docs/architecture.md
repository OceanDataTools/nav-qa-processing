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
   - more than 30 days outside the cruise dates (or outside the valid GPS dates);
   - `00:00:00` at 0,0;
   - invalid lon or lat;
   - an invalid GGA fix: quality outside 1–5, fewer than 4 or more than 50 satellites, HDOP of 0
     or over 50 (checked only when quality and satellite count are present);
   - \|altitude\| ≥ 500 m;
   - a timestamp that isn't later than the last in-sequence one, or is more than 30 days after it;
   - identical positions while the median of the preceding 10 speeds is above 0.25 m/s, which
     catches stale repeats from loggers with uncalibrated clocks;
   - acceleration over the limit, between successive speeds (Vincenty on WGS-84): the fix
     nearest in time to the excessive acceleration is flagged. These checks run over each
     3,600-record buffer, in at most 2 iterations (the loop runs while `iter < 3` from 1).

   **Speed never flags a fix:** the speed rule (flag the fix between two speeds over the limit)
   is commented out. **The acceleration check rarely fires:** in the main buffer loop a
   "Flipped check" (2016) computes speeds only from fixes already flagged, so on clean data it
   finds nothing; only the end-of-file loop checks every pair. The acceleration limit is raised to
   3× the position resolution [m] when that is 1.11 m or more, judged from one record: a
   workaround for one vessel (Endeavor).
5. **navsogcog** appends instantaneous SOG and COG, computed from each fix to the next.
6. **navsample** decimates without interpolating: for each whole minute it takes the good record
   at that time, or else the nearer of the records either side if within half the interval. With
   `-c`, it shells out to Java `navsimplifier`, which runs Douglas–Peucker with a planar
   tolerance of 0.01° on lon/lat.

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
- The nav29 parser dates GGAs buffer by buffer: it drops the last GGA after the final ZDA (R2R's
  SP2424 bestres lacks its final fix), drops a GGA repeating the last dated time or at 00:00:00
  before anything is dated, and moves a GGA with an equal time of day to the next day. The port
  dates each GGA from the nearest ZDA and keeps every one (#14, #15).
- navqc's speed rule is commented out, and its main-loop acceleration check is inverted (a 2016
  "Flipped check" computes speeds only from flagged fixes), so on clean data almost nothing is
  flagged; only the end-of-file loop checks every pair.
- navsogcog's full-buffer loop (86,400 records) computes each speed before calling Vincenty for
  the current pair, so it divides the previous segment's distance by the current elapsed time.
  Only the end-of-file loop is right. R2R's published SOG carries this (SP2424).
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
- 1min takes the first valid record of each minute. (navsample actually takes the record
  nearest each minute, within half a minute.)
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

### Upstream branches

https://github.com/rvdata/NavManager has branches besides `master` (checked 2026-10-13; `master`
is the porting baseline, and none of these is merged into it):

| Branch | Last commit | What it changes |
|---|---|---|
| `geocsv` | 2019-08-19 | Formatting only: an `R2R_DELIMITER` of `,` replaces tabs, `HEADER` becomes `#` (the same as `QCFLAG`), NavControl takes the delimiter and lon/lat columns as arguments. No QC, sampling or SOG/COG change, and no GeoCSV keyword header. `GPS_END_DATE` is still 2024-12-31 there |
| `qac2` | 2019-04-09 | `navqa.php` writes the QA 2.0 XML through `xmltools2` |
| `docs` | 2020-08-06 | Notes for nav24 (Sikuliaq: logger tag, ISO timestamp, NMEA), nav25 (Healy: Seapath + timestamp + ZDA/GGA/VTG), nav26 (generic CSV, Endeavor `.elg`), nav27 (Sproul ZDA + GLL without GGA), nav29 ("some STARC format", 2018 Sproul and Healy) |
| `blue_heron_mod`, `uhdas_parser` | 2019–2020 | navdatalist/navcopy edits for Blue Heron cut-off data and UHDAS |
| `pyrtm_parser` | 2020-08-07 | Already in `master` (nav31, Blue Heron UHDAS) |
| `rcrv_nrt` | 2021-10-03 | A near-real-time experiment: QA on the last 10 minutes of data |

## Part 2: Proposed Python architecture

### Goals

1. **Format parity with R2R's published products** (decided 2026-10-13). The three `.geoCSV`
   products and the QA 2.0 report have R2R's **structure**: file names, GeoCSV keyword lines and
   their order, columns and their `field_standard_name`s and datatypes, delimiter, missing value,
   the form of flagged records, and the report's XML elements and test, bound, info and range
   names.

   - **Text is standardized,** not copied: datatypes, number formats, unit names and
     descriptions follow "Standardized quantities" below, so files never match R2R byte for
     byte.
   - **Values are computed the sound way,** not by reproducing NavManager's arithmetic. Parity
     tests compare records, flags, values and ratings with R2R's on released filesets, and
     document each difference and its cause with tolerances measured on real data. A rating may
     differ from R2R's where NavManager's arithmetic is wrong or fragile; the test and this
     document say why.
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
    __init__.py     registry: FormatSpec (definition + reader) by ID, detect(path) -> ranked candidates
    definitions/    one TOML definition per format ID, plus sentences.toml (done for all 35 IDs)
    catalog.py      loads and prints the definitions
    nmea.py         sentence parsing (GGA, ZDA so far; GLL, RMC, VTG, HDT, GST, GSA to come), checksum
    gga_zda.py      GGA fixes dated by the nearest ZDA (nav29 and the other GGA + ZDA formats)
    framing.py      line framings that carry the time (SCS/UNIX-time prefixes), when a format needs one
                    (the FormatSpec registry, one per R2R format ID, lives in formats/__init__.py)
    custom/         the few formats that need code rather than a spec
  rawnav.py         read_fileset -> RawNav (DataFrame + per-file errors + per-column precision)
  r2rnav.py         read/write legacy .r2rnav files, precision-preserving value formatting
  geocsv.py         read/write GeoCSV 2.0 nav products (header block, DOIs; column metadata from quantities.py)
  geodesy.py        distance/azimuth on WGS-84 (pyproj.Geod), speed, acceleration, SOG/COG
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

**QC flags are a bitmask.** `qc.py` gives each record flag bits: `DATE_RANGE`, `NULL_ISLAND`,
`POSITION`, `BAD_FIX`, `ALTITUDE`, `TIME_SEQUENCE`, `STALE_REPEAT`, `ACCELERATION`. A record with
any bit set is written with `#` in BestRes, and the plots, PDF and notebook can say *why*. The
rules work on the whole series, not in 3,600-record buffers. Decided 2026-10-13/14:
- **No speed rule.** A vessel's top speed varies and currents change speed over ground;
  acceleration is the better indicator of a bad fix. `max_speed` is only for the QA test.
- **Acceleration over a time base, with velocity vectors.** The acceleration at each fix comes
  from the velocities over at least `accel_time_base` (2 s) before and after it, so GNSS noise
  at high sample rates doesn't trip it (on SP2424 at 2 Hz, checking every pair at 1 m s-2 flags
  hundreds of fixes), and vectors catch sideways jumps that speed magnitudes miss.
- **Excursions are flagged; steps are reported.** Around each peak over the limit, the chord
  across it is compared with the track on either side: if the track comes back, the fixes more
  than `max_accel · time_base² / 4` (1 m) off the chord are flagged; if it shifts and stays (a
  DGPS correction), no fix is wrong, nothing is flagged, and the step is reported.
- **Resolution allowance in the open.** The limit is raised only when coordinate quantization
  alone would exceed it over the time base, and the QC notes say so.

On SP2613 nothing is flagged, as in R2R's bestres. On SP2424 (150,925 fixes) R2R flags nothing;
`qc.py` flags 33 fixes in six 2–4 s excursions of 1–2 m and reports three steps, one of them a
3.7 m jump at the dock (`tests/test_qc.py`). SP1925 (45,723 fixes at 1 Hz, 6-decimal positions) is
the first released cruise found with flagged fixes: R2R flags 303, `qc.py` 219, 209 of them the
same. Every difference is a stale-repeat decision on a repeated position while the ship barely
moves (median of the preceding speeds near 0.25 m s-1). NavManager's lagged median and its
buffers don't explain them; its inverted acceleration loop, working only from fixes already
flagged, likely flags more repeats next to them (`tests/test_geocsv.py`).

**Geodesy via pyproj.** `pyproj.Geod(ellps="WGS84").inv` (Karney's algorithm) replaces
NavManager's hand-written Vincenty. On SP2613 and SP2424 the two differ by at most 5.4 µm per step,
1.1e-5 m s-1 in speed and 0.001° in course while under way. The differences show only where a value
sits on a rounding boundary: 8 of SP2613's 56,924 speeds and 12 of SP2424's 150,923 move by
0.01 m s-1. Course differs more on short steps, because course is as sensitive to position as
1/step; under a millimetre (at the dock) it is noise.

We could reproduce R2R's last digits by porting NavManager's Vincenty, which has a rounded
semi-minor axis and takes the azimuth from λ one iteration early. That port was built, matched
every R2R speed, and was dropped (2026-10-13) in favor of the maintained library. Speed and course
therefore match R2R within a documented tolerance (see `tests/test_geodesy.py`): a speed may round
0.01 m s-1 the other way at a rounding half; a course is within 0.001° + 0.005°·m / step, because
course is as sensitive to position as 1/step. pyproj is also slightly slower (36 ms against 21 ms
for SP2424's 150,923 segments), which doesn't matter next to the 3 s it takes to read the raw
files.

**Speed and course follow navsogcog's definition, not its quirks.** Each good fix takes the
segment to the next good fix, and the last good fix repeats the previous one. Coordinates are used
at full precision; NavManager reads them back rounded to the receiver's precision. navsogcog also
reads 86,400 records at a time, and in every full buffer it divides the *previous* segment's
distance by the current elapsed time. R2R's products carry this: all 86,399 first-buffer speeds of
SP2424 (150,924 records) match the bug, and the rest match the correct formula. The bug lags
speed one fix behind the ship (and, where the time step changes, divides one step's distance by
another's time). Our speeds are correct; the SP2424 parity test checks R2R's first buffer is ours
one fix late, and compares the rest. Not yet reported upstream.

**QA statistics stay on the pre-QC data**, as `navqa.php` does: that's the definition of the
tests, not a quirk. The statistics and the QC rules share `geodesy.py` and the same
speed/acceleration definitions, which removes NavManager's two divergent implementations. Where
NavManager's arithmetic is fragile, `stats.py` and `r2r.py` choose a sound rule and document the
difference from R2R:
- the modal interval when there are ties;
- rounding to 2 decimals before rating, which decides ratings at the boundaries;
- completeness above 100 % when the sampling interval changes.

How the values are *written* follows the standard formats below.

**Thresholds live in config.** `config.Thresholds` holds:
- the QC limits: `max_speed`, `max_accel`, `max_gap`, the GGA validity limits and the altitude
  limit;
- the R2R rating bounds.

A cruise TOML or OpenVDM's site TOML can override them. The XML `bounds` elements are written
from the same values, so the thresholds are no longer stated twice.

**Products** (`products.py`, #8):
- `bestres` is every pre-QC record with its QC flags and SOG/COG (good fixes only).
- `sample(interval)` takes, for each whole multiple of the interval, the good record nearest to
  it within half an interval, never interpolating, and keeps its SOG/COG. That is navsample's
  rule (not "the first valid record of each minute", as R2R's processing description says),
  without its quirks: whole-minute nodes whatever the interval, a 29 s/30 s asymmetric window,
  and a ring buffer that loses a node across each 3,600-record wrap.
- `control()` is Douglas–Peucker on 1min with a 0.01° planar tolerance, by shapely (GEOS), on
  unwrapped longitudes so the antimeridian isn't a 360° jump. It selects exactly the points of
  R2R's control (NavManager's recursive `navsimplifier.py` at 0.01°): 5 for SP2613, and SP2424's
  10 but for the last.

SP2613's 1min (475 records) and control equal R2R's. SP2424's equal R2R's except at the end:
we keep its final fix (#14), which is also the record nearest 03:17:00, a node NavManager's
navsample never fills because no record follows it. Coordinates are written in the standard
format below, not with Python's `repr`.

**GeoCSV is the primary product format** (`geocsv.py`, #9). The writer produces R2R's
structure: the same 16 `#keyword: value` lines in the same order, the same title, field types,
`field_standard_name`s and column order, then comma-separated records. Units, long names and
number formats come from `quantities.py` (descriptions therefore contain no commas: GeoCSV writes
them as one comma-separated list). The values that identify the source (`attribution`,
`source_repository`, the cruise DOI in `source_event`, the raw fileset DOI in `source_dataset`)
are passed in as a `Source`: R2R's for R2R's processing, the operator's at sea, empty when
unknown; R2R's identity is never a default. Flagged bestres records are written as R2R writes
them (SP1925, #10): `#` followed by time through antenna height only, without speed and course,
so GeoCSV readers skip them as comments. `geocsv.read` parses ours and R2R's files alike;
SP2613's three products written here equal R2R's by parsed value (speed and course within the
geodesy tolerance).

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
| `speed_made_good` | Speed over ground from successive positions | float | `m s-1` | 2 decimals |
| `course_made_good` | Course over ground clockwise from true north | float | `degree` | 3 decimals |
| horizontal acceleration | Horizontal acceleration | float | `m s-2` | 3 decimals |
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
| 1 | `nmea.py`, `framing.py`, specs for the formats used by OpenVDM ships (nav29 first: SP2613's format), `rawnav.py`, `r2rnav.py` | pre-QC r2rnav identical to `navcopy` on fixtures. **nav29 done:** SP2613's pre-QC records equal R2R's bestres by value (no PHP run yet) |
| 2 | `geodesy.py`, `qc.py`, `products.py`, `geocsv.py` | SP2613's three products have R2R's GeoCSV structure; records, flags and values compared with R2R's (`tests/fixtures`, plus the full bestres in `data/`) and every difference documented. **geodesy done (#6):** SP2613 and SP2424 SOG/COG match within the documented tolerance. **QC done (#7):** SP2613 unflagged like R2R; SP2424's differences documented. **Products done (#8):** SP2613's 1min and control equal R2R's; SP2424's but for its final fix. **GeoCSV done (#9):** R2R's header structure; SP2613's products equal R2R's by parsed value. **Flagged reference done (#10):** SP1925 settles R2R's flagged-record form; flag differences documented |
| 3 | `stats.py`, `r2r.py`, `xml_report.py`, `config.py`, `cli.py run` | the QA 2.0 report has R2R's structure; results and ratings compared with R2R's on released filesets, differences documented |
| 4 | `plots.py`, `maps.py`, `pdf_report.py`, `notebook.py` | report and notebook generated for every validation cruise |
| 5 | `openvdm.py`, `cli.py openvdm`, site TOML | hook runs on an OpenVDM test instance |
| 6 | the remaining format IDs, `formats.detect()` | every `doc/fileformat` format has a spec and a fixture. **Definitions done** for all 35 IDs (`formats/definitions/`); readers and fixtures remain |

### Open questions

- **Which other R2R nav filesets should be parity references?** SP2613 (nav29, G) is the first.
  We still need one per format family, and at least one rated Y or R with flagged records.
- **Where is the "XML DP report"** that the processing description mentions, and should we produce
  it?

Resolved:
- **Format definitions** are written for this project, not copied: NavManager has no LICENSE
  file and its format docs carry no license notice. They correct errors in the originals: the
  nav23 doc labeled nav14, UNIXD/PYRTM "decimal year" (it is a day of year), wrong checksums in
  the generic GLL and ZDA examples, a shell prompt inside the nav10 sample, and nav29 described
  only in its Unix-time layout. NavManager has no nav28.
- **NavManager's checksum test** compares unpadded lowercase hex, so it rejects every correct
  checksum below `0x10`. In SP2613 that discards 39 % of VTG and 29 % of RMC sentences but no
  GGA or ZDA. It isn't reproduced: `nmea.parse_sentence` checks two-digit hex (#13).
- **SP2613's time window** is the cruise day (depart and arrive 2026-07-09). Files are selected
  by their first to last ZDA, so the 2026-07-07 file is left out, as in R2R's bestres.
- **How does R2R write a flagged bestres record in GeoCSV?** As `#` followed by the record's
  measured fields only, time through antenna height, without speed and course (SP1925, product
  fileset 609448: 303 flagged records; #10). `geocsv.py` does the same.
- **Reproduce NavManager's numeric quirks?** No (2026-10-13): parity is about the products'
  format. Flags, speeds and ratings are computed soundly and compared with R2R's in tests, with
  the differences documented. This settles the buffer-boundary question.
- **Wrapper layout:** the nav QA 2.0 wrapper has the same layout as the CTD reports. The
  SP2613 report is the template.
- **NavControl:** produced by `navsimplifier.py` with a 0.01° tolerance (see Part 1).
- **Which collection-system names and file layouts do OpenVDM ships use for GNSS data?** They
  decide how the hook finds the primary GPS feed.
