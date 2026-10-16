# nav-qa-processing

> [!WARNING]
> **Under heavy development.** The core QA, the GeoCSV products and R2R's QA 2.0 XML work; the
> PDF report, figures, notebook and OpenVDM hook are still to come. The command line, config
> format and Python API may change without notice.

A Python port of R2R's [NavManager](https://github.com/rvdata/NavManager), built in the same style
as [sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing). From a research
vessel's raw GNSS files and a small cruise config, it produces:

- the R2R navigation standard products, as GeoCSV 2.0 in R2R's layout:
  - **bestres** (`<CRUISE>_bestres.geoCSV`): every fix, with bad fixes flagged (`#`), and speed
    and course over ground;
  - **1min** (`<CRUISE>_1min.geoCSV`): the good fix nearest each whole minute;
  - **control** (`<CRUISE>_control.geoCSV`): Douglas–Peucker control points of 1min.
- QA statistics and checks: completeness, gaps, records out of sequence, bad quality indicators,
  unreasonable speeds and accelerations, and the satellite, HDOP, altitude, speed and
  acceleration ranges.
- with the [R2R commands](#r2r), an **R2R QA 2.0 XML** report with R2R's six tests, ratings and
  bounds.

Still to come: a **PDF report**, every figure as an **SVG**, a **Jupyter notebook** that
re-runs the assessment, and an [OpenVDM](https://github.com/OceanDataTools/openvdm)
post-transfer hook.

Values are computed soundly rather than by reproducing NavManager's arithmetic, and units,
descriptions and number formats are standardized (CF/UDUNITS); the structure of the products
and the report is R2R's. Every difference from NavManager is listed, with its cause, in
[docs/navmanager-differences.md](docs/navmanager-differences.md).

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
git clone https://github.com/OceanDataTools/nav-qa-processing.git
cd nav-qa-processing
uv sync
uv run pytest
```

## Usage

```bash
uv run nav-qa-processing formats                        # the raw formats it knows
uv run nav-qa-processing run path/to/fileset -f nav29 -o output/MYCRUISE
uv run nav-qa-processing run path/to/fileset -c my_config.toml -o output/MYCRUISE
```

Without `-c`, the products are named after the fileset directory, every fix in it is read, and
the expected-area check is n/a. A [config TOML](#config-toml) sets the cruise's ID, dates and
area, the GNSS files and their format, and the thresholds; every field is optional.

### Command line

`nav-qa-processing --help` lists the commands, and `nav-qa-processing COMMAND --help` (or
`nav-qa-processing r2r COMMAND --help`) documents each one. The core commands are `formats`,
`run` and `fetch-map-data`; the [R2R commands](#r2r-commands) are a group under `r2r`.

Every command follows these conventions:
- `-o/--output` is a directory. Commands that make a cruise TOML write
  `<OUTPUT_DIR>/<CRUISE_ID>_<FILESET_ID>.toml` (default `configs/`), or to stdout with `-o -`.
- Existing outputs aren't overwritten without `--force`. A command checks before it does any
  work or asks R2R anything.
- Fileset IDs are integers.
- A command exits 0 on success and 1 when it refuses (e.g. existing outputs) or fails with an
  expected error (a server that can't be reached, R2R not having what was asked for, a config
  that can't run the QA, a bad theme), with a one-line message on stderr, e.g.
  `nav-qa-processing: couldn't reach R2R (service.rvdata.us): ... Check the network connection and try again`.
- `-v`/`--verbose`, before or after the command, logs more: `-v` is INFO and `-vv` is DEBUG
  (default WARNING). `-vv` also prints the traceback of an expected error.

#### `formats`

```text
nav-qa-processing formats [ID]
```

Without `ID`, lists R2R's raw navigation format IDs (`nav1` … `nav33`, `uhdas`, `siomet`,
`metoc_nav`), whether this version can read each one, and the NMEA sentences it knows. With
`ID`, describes that format (layout, sentences, vessels, R2R format IDs, examples) or sentence.

| Argument | Default | Description |
|---|---|---|
| `ID` | none | A format ID or NMEA sentence, e.g. `nav29`, `GGA` |

#### `run`

```text
nav-qa-processing run [-c CONFIG_TOML] [-f FORMAT_ID] [-o OUTPUT_DIR] [--force]
                      [--theme THEME_TOML] FILESET_DIR
```

Reads the fileset's primary GNSS feed, flags bad fixes, computes the QA statistics and checks,
and writes the three products.

| Argument | Default | Description |
|---|---|---|
| `FILESET_DIR` | required | A directory of GNSS files, or a BagIt bag (`bagit.txt`, `manifest-md5.txt` and the files under `data/`). `.gz` and `.bz2` files are read as they are |
| `-c`, `--config CONFIG_TOML` | none | A [config TOML](#config-toml). Without one, the products are named after `FILESET_DIR` |
| `-f`, `--format FORMAT_ID` | the config's `[nav] format` | The GNSS files' format (see `formats`). Needed when the config doesn't set it |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the products, created if needed |
| `--force` | off | Replace existing products; without it, `run` stops if any of them exists |
| `--theme THEME_TOML` | none | A [theme](#themes) file for the PDF, figures and notebook; the config's `[theme]` applies on top |

It writes `<CRUISE>_bestres.geoCSV`, `<CRUISE>_1min.geoCSV` and `<CRUISE>_control.geoCSV`,
and the figures as SVGs in `<CRUISE>_plots/`, where `<CRUISE>` is the config's `[cruise] id`, or
the fileset directory's name:

| Figure | Shows |
|---|---|
| `track_map.svg` | The good track over coastlines (broken at gaps), flagged fixes by QC flag, the 1min control points, the start and end, QC's position steps and the expected area, with a world inset. Polar stereographic beyond 70°, and whole across the antimeridian |
| `speed_acceleration.svg` | Speed over ground and the acceleration QC tests, at the good fixes, against time, with `max_speed` and QC's acceleration limit |
| `timeline.svg` | When there are fixes, the gaps of `max_gap` or longer, and the flagged fixes by flag |
| `histograms.svg` | The time between fixes, satellites, HDOP and GGA quality (invalid qualities in the secondary color) |

Long cruises are reduced for drawing (a simplified track line, min/max bands per time bin, at
most 3,000 markers per flag); counts in the legends are the full ones. It prints a
summary: the fixes read, QC's flags, the products' sizes, and every check with its status.

#### `fetch-map-data`

```text
nav-qa-processing fetch-map-data
```

Downloads the Natural Earth layers the trackline map uses (1:10m land, lakes and coastlines,
and the 1:110m world for the inset, about 27 MB) into cartopy's data directory, so the map has
coastlines offline, e.g. at sea. Without them, the map is drawn without coastlines and says so.
It takes no arguments.

### Config TOML

A config TOML describes the cruise, the GNSS feed and the thresholds. Every table and key is
optional. A complete example:

```toml
[cruise]
id = "SP2613"                     # the products' file name prefix and GeoCSV cruise_id
name = "MPL Internship Program Day at Sea"
pi = "Pereira, Filipe"
vessel_name = "Robert Gordon Sproul"
depart_date = 2026-07-09          # TOML dates, not strings
arrive_date = 2026-07-09
doi = "doi:10.7284/911454"        # GeoCSV source_event

[cruise.extent]                   # expected area [decimal degrees, east and north positive]
westernmost = -117.38
easternmost = -117.22
southernmost = 32.59
northernmost = 32.71

[fileset]
doi = "doi:10.7284/169851"        # GeoCSV source_dataset
manifest = "MD5_Summary.txt"      # checksums for a plain directory; relative to this file

[nav]                             # the primary GNSS feed
format = "nav29"
files = "data/*bx992*.txt"        # glob within the fileset

[provenance]
contact_institution = "Scripps Institution of Oceanography"
contact_email = "ship-tech@example.org"

[geocsv]
attribution = "Scripps Institution of Oceanography"

[thresholds]                      # defaults below
max_accel = 1.0
```

| Table | Key | Used for |
|---|---|---|
| `[cruise]` | `id` | The products' file names and GeoCSV `cruise_id`. Default: the fileset directory's name |
| | `name`, `pi`, `location`, `vessel_name` | The reports, when given |
| | `depart_date`, `arrive_date` | The cruise's days, from 00:00 UTC on `depart_date` to the end of `arrive_date`: only files with fixes in them are read, and QC flags fixes more than `date_margin_days` outside them |
| | `doi` | GeoCSV `source_event` |
| `[cruise.extent]` | `westernmost`, `easternmost`, `southernmost`, `northernmost` | The "Within the expected area" check (n/a without it). All four are needed. A box across the antimeridian has `westernmost` > `easternmost`, e.g. 178 to -178 |
| `[fileset]` | `doi` | GeoCSV `source_dataset`: the raw fileset |
| | `manifest` | A checksum file for a plain directory: `<md5> <path>` lines, paths relative to the manifest's directory, as in OpenVDM's MD5 summary. A BagIt bag uses its own `manifest-md5.txt` |
| `[nav]` | `format` | The GNSS files' format ID (see `formats`). Required, here or as `run -f` |
| | `files` | A glob of the GNSS files within the fileset. Default: every file in the data directory |
| `[provenance]` | `contact_institution`, `contact_email` | The reports' contact |
| `[geocsv]` | `attribution` | GeoCSV `attribution` |
| `[thresholds]` | see below | QC and the QA checks |
| `[theme]` | see [Themes](#themes) | The PDF, figures and notebook |

`[thresholds]`, with their defaults:

| Key | Default | Used by |
|---|---|---|
| `max_speed` | `8.7` [m s-1] | QA: a speed between successive fixes above this is unreasonable. Not a QC rule |
| `max_gap` | `300.0` [s] | QA: a step between fixes this long or longer is a gap |
| `min_completeness` | `95.0` [%] | Check: completeness passes at or above this |
| `max_longest_gap` | `900.0` [s] | Check: the longest gap passes at or below this |
| `max_out_of_sequence` | `5.0` [%] | Check: records out of sequence |
| `max_bad_quality` | `5.0` [%] | Check: records with a bad GGA quality indicator, of those reporting one |
| `max_unreasonable_speeds` | `5.0` [%] | Check: unreasonable speeds |
| `max_unreasonable_accelerations` | `5.0` [%] | Check: unreasonable accelerations |
| `max_accel` | `1.0` [m s-2] | QC and QA: the horizontal acceleration limit |
| `accel_time_base` | `2.0` [s] | QC and QA: acceleration at a fix uses velocities over at least this long before and after it, so GNSS noise at high sample rates isn't flagged |
| `gps_start`, `gps_end` | `1994-03-01`, `2034-12-31` | QC: valid GPS dates |
| `date_margin_days` | `30.0` [days] | QC: allowed outside the cruise dates |
| `max_time_step_days` | `30.0` [days] | QC: the largest step forward in time |
| `min_quality`, `max_quality` | `1`, `5` | QC and QA: valid GGA fix qualities (0 invalid, 6 dead reckoning, 7 manual, 8 simulator) |
| `min_satellites`, `max_satellites` | `4`, `50` | QC: satellites used in the fix |
| `max_hdop` | `50.0` | QC: HDOP (0 is invalid too) |
| `max_altitude` | `500.0` [m] | QC: largest antenna altitude magnitude |
| `stale_speed` | `0.25` [m s-1] | QC: a repeated position is stale when the median of the preceding speeds is above this |
| `stale_window` | `10` [speeds] | QC: the speeds for that median |

The QC rules are described in `src/nav_qa_processing/qc.py`.

Parsing rules:
- An unknown key in `[thresholds]` or `[theme]` is an error, so a typo doesn't go unnoticed.
- Other tables and keys are ignored, so an [R2R cruise TOML](#r2r-cruise-toml) works as a config
  too.
- `[fileset] manifest` and theme fonts and logo are relative to the TOML file; `[nav] files` to
  the fileset.

### Themes

The PDF, its figures and the notebook can take a house style: page size and margins, fonts,
text sizes, colors, a logo and the footer. Pass a theme file with `--theme` (`run` and `r2r
run`), or set a `[theme]` table in the config, which applies over the file's settings.
`configs/theme.example.toml` shows every setting:

```bash
uv run nav-qa-processing run path/to/fileset -c my_config.toml --theme configs/theme.example.toml
```

```toml
[theme]                     # in a config TOML
page_size = "A4"
logo = "logo.png"           # relative to the TOML file
organization = "Example Oceanographic Institution"
palette = "colorblind"      # Okabe-Ito colors, distinct with common color vision deficiencies

[theme.colors]
water = "#e6f0f7"
```

| Key | Default | Description |
|---|---|---|
| `page_size` | `letter` | `letter` or `A4` |
| `margin` | `0.6` [in] | Page margins |
| `font`, `bold_font` | DejaVu Sans | TrueType (`.ttf`) fonts for the PDF and figures |
| `font_scale`, `figure_font_scale` | `1.0` | Text sizes in the PDF and in the figures |
| `logo` | none | PNG or JPEG beside the report title |
| `organization` | none | At the start of the footer |
| `footer` | the report title | Footer text |
| `palette` | `default` | `default` or `colorblind` (Okabe-Ito); `[colors]` then changes single colors |
| `[colors]` | the palette's | `#rrggbb` colors: `primary` (the good track), `secondary` (flagged fixes, limits, the expected area), `status_pass`, `status_warn`, `status_fail`, `status_info`, `status_na`, `table_header`, `table_grid`, `muted_text`, `footer_text`, `land`, `water`, `coast`, `rating_green`, `rating_yellow`, `rating_red` |

A bad setting (an unknown key, a color that isn't `#rrggbb`, a missing font or logo) stops the
run before it reads anything, with a one-line message. QC flags are drawn in the palette's
distinct colors (matplotlib's tab10, or Okabe–Ito with `colorblind`). The PDF and notebook that
use the theme are still to come (#38, #39).

## R2R

The `r2r` commands add R2R's six navigation tests, its rating and the R2R QA 2.0 XML, and write
the products with R2R's GeoCSV source values. They take a cruise TOML with R2R's IDs, which
comes from R2R's catalog:

```bash
uv run nav-qa-processing r2r fetch-fileset SP2613 169851        # into data/SP2613_169851_gnss/
uv run nav-qa-processing r2r config-from-catalog SP2613 169851  # configs/SP2613_169851.toml
uv run nav-qa-processing r2r run configs/SP2613_169851.toml data/SP2613_169851_gnss -o output/SP2613
```

`r2r run` writes, by R2R's names:

| Output | Contents |
|---|---|
| `<CRUISE>_bestres.geoCSV`, `_1min.geoCSV`, `_control.geoCSV` | The products, as `run` writes them, with R2R's attribution, repository and DOIs |
| `<CRUISE>_<FILESET>_qa.2.0.<TIME>.xml` | R2R QA 2.0 report: R2R's structure, tests, bounds, infos and ranges, with its stylesheet reference. `<TIME>` is the run's, as in the products' `creation_date` |

`run -c` takes the same cruise TOML and ignores the R2R fields.

### R2R commands

#### `r2r run`

```text
nav-qa-processing r2r run [-o OUTPUT_DIR] [--force] [--theme THEME_TOML] CRUISE_TOML FILESET_DIR
```

Runs the QA as `run` does, then R2R's six tests, and writes the products and the QA 2.0 XML.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_TOML` | required | An [R2R cruise TOML](#r2r-cruise-toml), with the cruise ID and dates and `[nav] format` |
| `FILESET_DIR` | required | A BagIt bag or directory of GNSS files, as for `run` |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the products and report |
| `--force` | off | Replace existing products |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the cruise TOML's `[theme]` applies on top |

It prints the R2R rating and each test's rating and result, then the core summary.

#### `r2r config-from-catalog`

```text
nav-qa-processing r2r config-from-catalog [-o OUTPUT_DIR] [--force] [--no-qa-report]
                                          CRUISE_ID FILESET_ID
```

Writes an R2R cruise TOML from R2R's catalog: the cruise's name, chief scientist, vessel,
operator and scheduler, dates, ports, DOIs and navigation bounding box, and the GNSS receiver,
R2R format and nav format. It checks that `FILESET_ID` is one of the cruise's GNSS filesets, and
lists them if it isn't. R2R's API has no port coordinates, country or state, format alias or
catalog dates; those come from R2R's QA report for the fileset, when it has one with fileset
info (R2R's reports from 2024 and earlier don't).

| Argument | Default | Description |
|---|---|---|
| `CRUISE_ID` | required | R2R cruise ID, e.g. `SP2613` |
| `FILESET_ID` | required | R2R's raw GNSS fileset ID, an integer, e.g. `169851` |
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>_<FILESET_ID>.toml`, or `-` for stdout |
| `--force` | off | Replace an existing TOML |
| `--no-qa-report` | off | Use the catalog API only |

It warns when this version has no reader for the fileset's R2R format; set `[nav] format` by
hand then.

#### `r2r config-from-qa`

```text
nav-qa-processing r2r config-from-qa [-o OUTPUT_DIR] [--force] R2R_QA_XML
```

Writes an R2R cruise TOML from the provenance and fileset info of one of R2R's QA 2.0 reports.
Use it to check this tool's results against R2R's.

| Argument | Default | Description |
|---|---|---|
| `R2R_QA_XML` | required | R2R's QA 2.0 XML for the fileset, e.g. the `r2r_qa.2.0.xml` that `r2r fetch-fileset` downloads |
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>_<FILESET_ID>.toml`, or `-` for stdout |
| `--force` | off | Replace an existing TOML |

#### `r2r fetch-fileset`

```text
nav-qa-processing r2r fetch-fileset [-o OUTPUT_DIR] [--force] CRUISE_ID FILESET_ID
```

Downloads a released R2R GNSS fileset (a BagIt bag) into
`<OUTPUT_DIR>/<CRUISE_ID>_<FILESET_ID>_gnss/`, with R2R's QA report beside it as
`r2r_qa.2.0.xml` and R2R's r2rnav product bag (the three `.geoCSV` files) in `products/`.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_ID` | required | R2R cruise ID |
| `FILESET_ID` | required | R2R's raw GNSS fileset ID, an integer |
| `-o`, `--output OUTPUT_DIR` | `data` | Directory for the fileset's directory |
| `--force` | off | Download into an existing fileset directory |

### R2R cruise TOML

An R2R cruise TOML is a [config TOML](#config-toml) with R2R's catalog details added. It must
have `[cruise] id`, `depart_date` and `arrive_date`. The `r2r config-from-*` commands write it;
this is `configs/SP2613_169851.toml`:

```toml
[cruise]
id = "SP2613"
name = "MPL Internship Program Day at Sea"
pi = "Pereira, Filipe"
vessel_id = "32QU"                     # R2R's vessel ID: the ICES ship code
vessel_name = "Robert Gordon Sproul"
operator_id = "edu.ucsd.sio"           # R2R organization IDs
scheduler_id = "org.unols"
date_issued = "2026-07-22"             # R2R's catalog dates, as R2R writes them
date_modified = "2026-07-31T04:28:14Z"
depart_date = 2026-07-09               # required
arrive_date = 2026-07-09               # required
doi = "doi:10.7284/911454"

[cruise.extent]
westernmost = -117.3773103608
easternmost = -117.2262308092
southernmost = 32.5997440998
northernmost = 32.7053695673

[cruise.depart_port]                   # and [cruise.arrive_port]
name = "San Diego"
port_id = "100055"                     # R2R port ID
country_id = "us"
state_id = "ca"
longitude = -117.175
latitude = 32.71667

[cruise.arrive_port]
name = "San Diego"
port_id = "100055"
country_id = "us"
state_id = "ca"
longitude = -117.175
latitude = 32.71667

[fileset]
id = "169851"                          # R2R's raw GNSS fileset ID
doi = "doi:10.7284/169851"
date_created = "2026-07-22"
format_id = "100150"                   # R2R's format ID, and its alias
format_alias = "starc_raw"

[fileset.device]                       # the GNSS receiver, as R2R catalogs it
id = "101253"
make_id = "com.trimble"
model_id = "100542"
model_name = "BX992"

[nav]
format = "nav29"

[provenance]
distro_type = "post-cruise"
contact_institution = "Rolling Deck to Repository (R2R)"
contact_institution_id = "us.rvdata"
contact_email = "info@rvdata.us"

[geocsv]
attribution = "Rolling Deck to Repository (R2R) Program; http://www.rvdata.us/"
```

The keys it adds to a config TOML, all written to the XML:

| Table | Key | Default | Description |
|---|---|---|---|
| `[cruise]` | `id`, `depart_date`, `arrive_date` | required | |
| | `vessel_id` | blank | R2R's vessel ID, the ICES ship code (e.g. `32QU`) |
| | `operator_id`, `scheduler_id` | blank | R2R organization IDs (e.g. `edu.ucsd.sio`, `org.unols`) |
| | `date_issued`, `date_modified` | blank | R2R's catalog dates for the cruise |
| `[cruise.depart_port]`, `[cruise.arrive_port]` | `name`, `port_id`, `country_id`, `state_id`, `longitude`, `latitude` | blank | The ports, as in R2R's catalog |
| `[fileset]` | `id` | blank | R2R's fileset ID. Without it, the report is named `<CRUISE>_gnss` |
| | `date_created` | blank | R2R's catalog date for the fileset |
| | `format_id`, `format_alias` | blank | R2R's format ID and alias for the raw files |
| `[fileset.device]` | `id`, `make_id`, `model_id`, `model_name`, `location_name` | blank | The GNSS receiver, as R2R catalogs it |
| `[provenance]` | `distro_type` | `post-cruise` | R2R's distribution type |
| | `contact_institution_id` | blank | The contact institution's R2R organization ID |

### R2R tests

R2R's six tests, written to the XML with R2R's names and bounds:

| Test | G | Y | otherwise R |
|---|---|---|---|
| `PercentCompleteness` | ≥ 95 % | ≥ 75 % | |
| `LongestGapEpoch` | ≤ 900 s | ≤ 86,400 s | |
| `PercentRecordsOutOfSequence` | ≤ 5 % | ≤ 10 % | |
| `PercentRecordsWithBadQualityIndicator` | ≤ 5 % | ≤ 10 % | |
| `PercentUnreasonableSpeeds` | ≤ 5 % | ≤ 10 % | |
| `PercentUnreasonableAccelerations` | ≤ 5 % | ≤ 10 % | |

The overall rating is G if every test is G, R if any is R, else Y. Each result is rated as
written in the report. How the statistics are computed, and where that differs from NavManager,
is in `src/nav_qa_processing/stats.py`, `src/nav_qa_processing/integrations/r2r/rules.py` and
[docs/navmanager-differences.md](docs/navmanager-differences.md).

### Validation against R2R

The tests compare this tool with released R2R filesets: their pre-QC records, products, QA
statistics and QA 2.0 reports. Where the ratings differ, the cause is in
[docs/navmanager-differences.md](docs/navmanager-differences.md). Fetch them with `r2r fetch-fileset` (into `data/`); the tests
that need one skip without it and name the command.

| Fileset | R2R | This tool |
|---|---|---|
| SP2613 / 169851 (Sproul, one day) | G | G; every result equal; products equal by value |
| SP2503 / 167484 (Sproul) | R: completeness 47.34 % (R), gap 48,625.5 s (Y) | R, the same ratings |
| SP2424 / 161900 (Sproul, 150,925 fixes) | G | G; accelerations differ by definition |
| SP1925 / 134952 (Sproul, flagged fixes) | no QA 2.0 report | QC's flags compared with R2R's |
| RR2406 / 161075 (Revelle) | R on five tests, from sentences its logger split across lines | Y: the sentences rejoined, one 47-minute gap |
| RR2212 / 151339 (Revelle) | R: out of sequence 50.08 %, files processed out of order | G |
| SP2220 / 150139 (Sproul) | R: no fix for most of the cruise | R: bad quality 82.61 %, completeness, gap |

## Development

```bash
uv run pytest                                   # all tests
uv run pytest tests/test_cli.py::test_version   # one test
uv run ruff check . && uv run ruff format --check .
```

## License

[MIT](LICENSE.md)
