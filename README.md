# nav-qa-processing

> [!WARNING]
> **Under heavy development.** The command line, config format and Python API may change without
> notice.

A Python port of R2R's [NavManager](https://github.com/rvdata/NavManager), built in the same style
as [sbe-qa-processing](https://github.com/OceanDataTools/sbe-qa-processing). From a research
vessel's raw GNSS files and a small cruise config, it produces:

- QA statistics and checks: completeness, gaps, records out of sequence, bad quality indicators,
  unreasonable speeds and accelerations, and the satellite, HDOP, altitude, speed and
  acceleration ranges.
- a **PDF report**, every figure as an **SVG**, and a **Jupyter notebook** that re-runs the
  assessment.
- by default, the standard R2R GeoCSV 2.0 navigation products (`--no-products` leaves them out):
  - **bestres** (`<CRUISE>_bestres.geoCSV`): every fix, with bad fixes flagged (`#`), and speed
    and course over ground;
  - **1min** (`<CRUISE>_1min.geoCSV`): the good fix nearest each whole minute;
  - **control** (`<CRUISE>_control.geoCSV`): Douglas–Peucker control points of 1min.
- optionally with the [R2R commands](#r2r), an **R2R QA 2.0 XML** report with R2R's six tests, ratings and
  bounds.

It runs from the command line and can be integrated with [OpenVDM](https://github.com/OceanDataTools/openvdm)
to run as a post-transfer hook and end-of-cruise task.

**[docs/examples](docs/examples/)** contain example products from a real cruise
(SP2613): the PDF report, with and without R2R's certificate, products, a figure and the R2R
QA 2.0 XML.

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and git. **[INSTALL.md](INSTALL.md)** has
the commands for macOS, Debian and Ubuntu, RHEL, AlmaLinux and Rocky Linux, and Windows, the
install for the OpenVDM hook, the map data, and installing for a ship without internet. In
short:

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

**[COOKBOOK.md](COOKBOOK.md)** walks through whole workflows with real output: a directory of a
ship's raw nav files (finding the format, a config TOML, several sensors, the notebook, Python),
and an R2R fileset (downloading it, its cruise TOML, R2R's assessment, comparing with R2R's
products).

### Command line

`nav-qa-processing --help` lists the commands, and `nav-qa-processing COMMAND --help` (or
`nav-qa-processing r2r COMMAND --help`) documents each one. The core commands are `formats`,
`run` and `fetch-map-data`; the [R2R commands](#r2r-commands) are a group under `r2r`; the
[OpenVDM commands](#openvdm-commands) are `openvdm`, `config-from-openvdm` and `site-config`.

Every command follows these conventions:
- `-o/--output` is a directory; only `site-config` writes a single file. Commands that make a cruise TOML write
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

Without `ID`, lists R2R's raw navigation format IDs (`nav01` … `nav33`, `uhdas`, `siomet`,
`metoc_nav`), whether this version can read each one, and the NMEA sentences it knows. With
`ID`, describes that format (layout, sentences, vessels, R2R format IDs, examples) or sentence.
IDs have two digits, so they sort in order: `nav02`, not NavManager's `nav2`. NavManager's
spelling is still accepted wherever a format ID is given (`-f`, `[nav] format`, `formats ID`).

| Argument | Default | Description |
|---|---|---|
| `ID` | none | A format ID or NMEA sentence, e.g. `nav29`, `GGA` |

#### `run`

```text
nav-qa-processing run [-c CONFIG_TOML] [-f FORMAT_ID] [--nav NAME] [-o OUTPUT_DIR] [--force]
                      [--theme THEME_TOML] [--execute-notebook] [--no-products] FILESET_DIR
```

Reads the fileset's primary GNSS feed, flags bad fixes, computes the QA statistics and checks,
and writes the three products, the figures and the PDF report.

| Argument | Default | Description |
|---|---|---|
| `FILESET_DIR` | required | A directory of GNSS files, or a BagIt bag (`bagit.txt`, `manifest-md5.txt` and the files under `data/`). `.gz` and `.bz2` files are read as they are |
| `-c`, `--config CONFIG_TOML` | none | A [config TOML](#config-toml). Without one, the products are named after `FILESET_DIR` |
| `-f`, `--format FORMAT_ID` | the config's `[nav] format` | The GNSS files' format (see `formats`). Needed when the config doesn't set it. With several feeds, only with `--nav` |
| `--nav NAME` | every feed | With a config listing several position sensors (`[[nav]]`), run only the one of this name |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the products, created if needed |
| `--force` | off | Replace existing products and reports; without it, `run` stops if any of them exists |
| `--theme THEME_TOML` | none | A [theme](#themes) file for the PDF, figures and notebook; the config's `[theme]` applies on top |
| `--execute-notebook` | off | Run the notebook (needs `nbclient` and `ipykernel`, in the dev group) and save it with its outputs. Its last cell, which writes the reports again, isn't run |
| `--no-products` | off | Don't write the GeoCSV products (bestres, 1min, control): only the figures, PDF and notebook. The PDF still gives each product's record count, its file marked "not written", and the notebook rewrites the reports without them |

It writes `<CRUISE>_bestres.geoCSV`, `<CRUISE>_1min.geoCSV` and `<CRUISE>_control.geoCSV`,
and the figures as SVGs in `<CRUISE>_plots/`, where `<CRUISE>` is the config's `[cruise] id`, or
the fileset directory's name. With several position sensors, every output of a feed other than
the primary also carries the feed's name, e.g. `<CRUISE>_seapath_bestres.geoCSV`:

| Figure | Shows |
|---|---|
| `track_map.svg` | The good track over coastlines, broken at gaps of `max_gap` or longer between good fixes, which are drawn faint and dashed (a stretch too small to show as a line at the map's scale is a dot), flagged fixes by QC flag (each under its first flag; the legend gives the QC table's count and how many are drawn), the 1min control points, the start and end, QC's position steps (smaller when there are many), the fixes the "Over water" check counts over land (ringed) and the expected area, with a world inset. The legend and inset never cover the track: with no clear place on the map, the legend goes below it and the inset is left out. Polar stereographic beyond 70°, and whole across the antimeridian |
| `speed_acceleration.svg` | Speed over ground and the acceleration QC tests, at the good fixes, against time, with `max_speed` and QC's acceleration limit |
| `timeline.svg` | When there are fixes, the gaps of `max_gap` or longer, and the flagged fixes by flag. Each record is marked once, under its first flag; a row gives the QC table's count and how many of its records are in the rows above |
| `histograms.svg` | The time between fixes, satellites, HDOP and GGA quality (invalid qualities in the secondary color); a field the receiver doesn't report (GLL: satellites, HDOP) is marked so |

Long cruises are reduced for drawing (a simplified track line, min/max bands per time bin, at
most 3,000 markers per flag); counts in the legends are the full ones.

It also writes `<CRUISE>_qa_report.pdf`, which embeds the SVGs as vector drawings. Its sections
follow one another without page breaks; a section starts a new page only when it doesn't fit
on the current one:

| Sections | Contents |
|---|---|
| Summary | The title block (cruise, vessel, dates, expected area, contact, the fileset, its nav files and format, versions) and the checks in a line on the worst status's color (e.g. "2 of 8 checks warn: Completeness 47.34 %; Longest gap 13:30:25.500 (48625.500 s)."; with no usable fixes, a warning that no check could be made), then any warnings about the run, e.g. nav files outside the cruise window with their time span (the console summary and the hook's log say the same), every check with its status, the checks by file (what each check counted in each nav file read and the sentences the reader couldn't use, the files it couldn't read, those skipped as outside the cruise window, and the total), the statistics over the fixes that pass QC (their number, first and last fix, satellite, HDOP, altitude, speed and acceleration ranges) with the most common and longest times between fixes (the longest as hh:mm:ss) over every usable record, and the products |
| Quality control | Records flagged by flag, QC's notes (raised limits, position steps), reader and manifest errors (the first 12, then a count; with none, a line saying so and whether a manifest was checked), the gaps of `max_gap` or longer (the 10 longest, with their start, end, length as hh:mm:ss and the files either side, then a count), and the coverage timeline |
| Figures | The trackline map; speed and acceleration with sampling and fix quality, sharing a page |

Integrations put their pages after the title block: `r2r run` adds [R2R's certificate](#r2r),
with R2R's ranges over every record before QC.

And it writes `<CRUISE>_qa.ipynb`, a notebook that re-runs the assessment step by step: the
config (the config file, or the run's config written into it as TOML), `run_qa`, the checks,
statistics, QC flags and errors, every figure, the products, and writing the reports again
(all but the notebook itself). Edit its paths or the config's `[thresholds]` and re-run it to
change the assessment; see [Opening the notebook](#opening-the-notebook). It prints a
summary: the fixes read, QC's flags, the products' sizes, and every check with its status.

#### `fetch-map-data`

```text
nav-qa-processing fetch-map-data
```

Downloads the Natural Earth layers the trackline map uses (1:10m land, lakes and coastlines,
and the 1:110m world for the inset, about 27 MB) into cartopy's data directory, so the map has
coastlines offline, e.g. at sea. Without them, the map is drawn without coastlines and says so,
and the "Over water" check, which uses the same land and lakes, is n/a. Setting the environment
variable `NAV_QA_NO_MAP_DATA` has the same effect without touching the network; the tests set
it, so they never download map data (tests marked `map_data` use it when it's already here).
It takes no arguments.

### Opening the notebook

The notebook imports this package, so it has to run on the project's Python (`.venv`), not on
the Python of whichever Jupyter opens it. A Jupyter installed with Homebrew, Anaconda or pip
starts its own Python by default, which doesn't have this project's packages, so the first cell
fails with `ModuleNotFoundError: No module named 'nav_qa_processing'`. Either:

**Run JupyterLab from the project.** Nothing to set up, and nothing is added to `.venv` or
`uv.lock`:

```bash
uv run --with jupyterlab jupyter lab output/SP2613/SP2613_qa.ipynb
```

**Or use the Jupyter you already have.** Register the project's Python as a kernel, once per
checkout:

```bash
uv run python -m ipykernel install --user --name nav-qa-processing --display-name "Python (nav-qa-processing)"
```

Then, in each notebook, choose **Kernel → Change Kernel… → Python (nav-qa-processing)**. The
generated notebooks ask for the default `python3` kernel, so do this for each new one. It needs
`ipykernel` from the dev group; if you move the checkout or delete `.venv`, register it again;
`jupyter kernelspec uninstall nav-qa-processing` removes it.

The notebook's paths to the fileset, the config, the theme and the output directory are
absolute, so it doesn't matter where Jupyter starts. `--execute-notebook` runs the notebook
with the project's Python and needs neither setup. It runs every cell but the last, which
would rewrite the reports just written; run that one yourself to write them again.

### Config TOML

A config TOML describes the cruise, the GNSS feed and the thresholds. Every table and key is
optional, but a key that isn't one of them is an error, which says where a misplaced or
misspelled key belongs (`cruise_id (top level): did you mean [cruise] id?`), as is a value
where a table belongs or a table where a value does (`[cruise] extent must be a table`). The keys of an
[R2R cruise TOML](#r2r-cruise-toml) are accepted too. `configs/config.example.toml` shows every
key, commented where it's optional or at its default. An example:

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

[nav.device]                      # the receiver or INS, for the reports; every key optional
make = "Trimble"
model = "BX992"
location = "main mast"

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
| `[cruise]` | `id` | The products' file names and GeoCSV `cruise_id`. Default: the fileset directory's name. In file names, `/`, `\`, `<>:"\|?*` and control characters become `_`, so every output stays in `-o` and Windows can write it; the reports show the ID as given |
| | `name`, `pi`, `location`, `vessel_name` | The reports, when given |
| | `depart_date`, `arrive_date` | The cruise's days, from 00:00 UTC on `depart_date` to the end of `arrive_date`: only files with fixes in them are read, and QC flags fixes more than `date_margin_days` outside them |
| | `doi` | GeoCSV `source_event` |
| `[cruise.extent]` | `westernmost`, `easternmost`, `southernmost`, `northernmost` | The "Within the expected area" check (n/a without it). All four are needed. A box across the antimeridian has `westernmost` > `easternmost`, e.g. 178 to -178 |
| `[fileset]` | `doi` | GeoCSV `source_dataset`: the raw fileset |
| | `manifest` | A checksum file for a plain directory: `<md5> <path>` lines, paths relative to the manifest's directory, as in OpenVDM's MD5 summary. A BagIt bag uses its own `manifest-md5.txt` |
| `[nav]` | `format` | The GNSS files' format ID (see `formats`). Required, here or as `run -f` |
| | `files` | A glob of the GNSS files within the fileset. Default: every file in the data directory |
| `[nav.device]` | `make`, `model`, `serial_number`, `location` | The receiver or INS that logged the feed, shown in the reports' title block with the GGA talker IDs found in the data (e.g. GN, a multi-constellation receiver; IN, an INS). All optional. In an R2R cruise TOML it defaults to R2R's `[fileset.device]` (make ID, model name, location), and the `r2r config-from-*` commands write it out, so `run -c` with the cruise TOML reports the receiver too |
| `[[nav]]` | `name`, `primary`, and the keys above | Several position sensors, e.g. a POS MV and a Seapath: one `[[nav]]` table each, with its own `format`, `files` and `[nav.device]`. Each needs a `name` (letters, digits, `_`, `-`); one has `primary = true`, else the first is. `run` assesses each; the others' outputs are named `<CRUISE>_<NAME>_…`. R2R's cruise TOMLs have one feed |
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
| `land_tolerance` | `3000.0` [m] | Check "Over water": a fix on land farther than this from Natural Earth's 1:10m coasts and river centerlines is reported in the check's detail. Harbors the coastline draws as land (RR2212's Oakland inner harbor: up to 1,438 m) and rivers whose generalized centerline runs off a ship under way (RR1802 on the Willamette: up to 2,630 m) lie within it, counted as ports |
| `max_inland` | `10000.0` [m] | Check "Over water": a fix on land farther than this from water warns. Between `land_tolerance` and this, fixes are reported, not warned of: waterways the map lacks, e.g. Seattle's ship canal (RC0014: up to 3,929 m). A fix plainly ashore is kilometres to hundreds of kilometres inland |
| `max_accel` | `1.0` [m s-2] | QC and QA: the horizontal acceleration limit |
| `accel_time_base` | `2.0` [s] | QC and QA: acceleration at a fix uses velocities over at least this long before and after it, so GNSS noise at high sample rates isn't flagged |
| `max_reach_speed` | `25.0` [m s-1] | QC: a fix the ship couldn't have reached from the track at this speed (plus `reach_tolerance`) is flagged unreachable, e.g. a jump to near 0, 0 or minutes at a wrong position. Far above any ship's speed: not a speed limit |
| `reach_tolerance` | `250.0` [m] | QC: slack for the reachability rule; closer jumps are left to the acceleration rule |
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
- An unknown key, in any table, is an error that says where a misplaced key or table belongs, or
  the closest key to a misspelled one (`[thresholds] max_gpa: did you mean max_gap?`). So is a
  value of the wrong shape: a value where a table belongs, or a table where a value does.
- The keys of an [R2R cruise TOML](#r2r-cruise-toml) are accepted, so it works as a config too,
  as is the site TOML's `[openvdm] output_extra_directory`.
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
distinct colors (matplotlib's tab10, or Okabe–Ito with `colorblind`). Fonts must be TrueType
(`.ttf`). The notebook re-creates the same theme, so figures re-run there match the PDF.

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
| `<CRUISE>_<FILESET>_qa.2.0.<TIME>.xml` | R2R QA 2.0 report: R2R's structure, tests, bounds, infos and ranges, with its stylesheet reference. `<TIME>` is the run's, as in the products' `creation_date`, without its colons (`2026-07-31T200556Z`; R2R's names have them, which Windows doesn't allow) |
| `<CRUISE>_plots/`, `<CRUISE>_qa_report.pdf` | The figures and PDF, as `run` writes them, with R2R's certificate after the title block: R2R's fileset, vessel, ports, receiver and format, the rating as a G/Y/R banner in the theme's rating colors, each test's rating, result, G and Y bounds and detail, the infos, and R2R's ranges (NSV, HDOP, altitude, speed and acceleration over every record before QC, as the XML gives them). The PDF's subject is the rating, e.g. `R2R rating G` |
| `<CRUISE>_qa.ipynb` | The notebook, as `run` writes it, loading the cruise TOML (or, for a config built in Python, the cruise TOML written into it), then running R2R's tests, showing the certificate's tables, and writing the reports with the certificate and a new QA 2.0 XML, named by the notebook's run time |

`run -c` takes the same cruise TOML and ignores the R2R fields.

### R2R commands

#### `r2r run`

```text
nav-qa-processing r2r run [-o OUTPUT_DIR] [--force] [--theme THEME_TOML] [--execute-notebook]
                          [--no-products] [--r2rnav] CRUISE_TOML FILESET_DIR
```

Runs the QA as `run` does, then R2R's six tests, and writes the products, figures, PDF with
R2R's certificate, notebook and QA 2.0 XML.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_TOML` | required | An [R2R cruise TOML](#r2r-cruise-toml), with the cruise ID and dates and `[nav] format` |
| `FILESET_DIR` | required | A BagIt bag or directory of GNSS files, as for `run` |
| `-o`, `--output OUTPUT_DIR` | `output` | Directory for the products and report |
| `--force` | off | Replace existing products |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the cruise TOML's `[theme]` applies on top |
| `--execute-notebook` | off | Run the notebook and save its outputs |
| `--no-products` | off | Don't write the GeoCSV products, as for `run`; the QA 2.0 XML, and with `--r2rnav` the r2rnav products, are written still |
| `--r2rnav` | off | Also write the products as legacy r2rnav, `<CRUISE>_bestres\|1min\|control.r2rnav`, the format NavManager wrote them in (`format-r2rnav.txt`: tab-separated, three `//` header lines, a flagged bestres record as `#` and time through antenna height). R2R itself now publishes GeoCSV |

It prints the R2R rating and each test's rating and result, then the core summary.

#### `r2r config-from-catalog`

```text
nav-qa-processing r2r config-from-catalog [-o OUTPUT_DIR] [--force] [--no-qa-report]
                                          CRUISE_ID FILESET_ID
```

Writes an R2R cruise TOML from R2R's catalog: the cruise's name, chief scientist, vessel,
operator and scheduler, dates, ports, DOIs and navigation bounding box, and the GNSS receiver,
R2R format and nav format. It checks that `FILESET_ID` is one of the cruise's navigation filesets (GNSS, or an INS such as
Falkor (too)'s POS/MV), and
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
When R2R answers with an HTTP error (e.g. 503 while its service is down), the command says
which download failed in one line and exits 1. If the raw files arrived but the QA report or
products didn't, it says so: run it again with `--force` to fetch them.

| Argument | Default | Description |
|---|---|---|
| `CRUISE_ID` | required | R2R cruise ID |
| `FILESET_ID` | required | R2R's raw GNSS fileset ID, an integer |
| `-o`, `--output OUTPUT_DIR` | `data` | Directory for the fileset's directory |
| `--force` | off | Download into an existing fileset directory |

### R2R cruise TOML

An R2R cruise TOML is a [config TOML](#config-toml) with R2R's catalog details added. It must
have `[cruise] id`, `depart_date` and `arrive_date`. The `r2r config-from-*` commands write it;
this is `tests/configs/SP2613_169851.toml`:

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

[nav.device]                           # the receiver for the reports: R2R's device, written out
make = "com.trimble"                   # so `run -c` reports it too
model = "BX992"

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
| `[cruise]` | `id`, `depart_date`, `arrive_date` | required | `id` is made a valid file name as in a [config TOML](#config-toml) |
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
[docs/navmanager-differences.md](docs/navmanager-differences.md). SP2613, SP2503, BH19-30 and
RC0014 are committed in `tests/data/` and unpacked into `data/` by the tests, so CI downloads
nothing; fetch the others with `r2r fetch-fileset` (into `data/`). The tests that need one skip
without it and name the command.

| Fileset | R2R | This tool |
|---|---|---|
| SP2613 / 169851 (Sproul, one day) | G | G; every result equal; products equal by value |
| SP2503 / 167484 (Sproul) | R: completeness 47.34 % (R), gap 48,625.5 s (Y) | R, the same ratings |
| SP2424 / 161900 (Sproul, 150,925 fixes) | G | G; accelerations differ by definition |
| SP1925 / 134952 (Sproul, flagged fixes) | no QA 2.0 report | QC's flags compared with R2R's |
| RR2406 / 161075 (Revelle) | R on five tests, from sentences its logger split across lines | Y: the sentences rejoined, one 47-minute gap |
| RR2212 / 151339 (Revelle) | R: out of sequence 50.08 %, files processed out of order | G |
| SP2220 / 150139 (Sproul) | R: no fix for most of the cruise | R: bad quality 82.61 %, completeness, gap |

### Performance against NavManager

On the same filesets and machine, this tool takes 1.6 to 2.5 times less time than NavManager and
4.5 to 37 times more memory: NavManager streams through fixed buffers, and this tool holds the
whole cruise, about 300 bytes a fix. A 20-day cruise at 2 Hz (3.5 M fixes) takes NavManager
8.8 minutes in 279 MB, and this tool 3.5 minutes in 1.2 GB. The measurements, why, and how to
repeat them are in [docs/performance.md](docs/performance.md).

## OpenVDM

On a ship running [OpenVDM](https://github.com/OceanDataTools/openvdm), the QA runs from
OpenVDM's hooks through the cruise:
- **During the cruise,** after each transfer of the collection system that logs the GNSS
  (`postCollectionSystemTransfer`), at most every `--min-interval`: a failing receiver or a
  missing feed shows while there's time to fix it.
- **When the cruise is finalized** (`preFinalizeCurrentCruise`): the final reports, on the whole
  cruise. OpenVDM waits for them, so they're in the finalized cruise.
- **Afterwards, by hand:** any earlier cruise, from the `cruise_config.json` in its directory
  ([A past cruise](#a-past-cruise)).

Each run writes the core products and reports (GeoCSV, SVG plots, PDF, notebook), not R2R's,
into an OpenVDM extra directory. **Setting it up** (the install, the extra directory, the ship's
site TOML and the hooks in `openvdm.yaml`) is in
[INSTALL.md](INSTALL.md#install-for-the-openvdm-hook).

### Where its values come from

- **The cruise,** from OpenVDM's API (`api/warehouse/getCruiseConfig`): its ID, name, PI,
  location, dates and extent, the warehouse directory, the transfer's destination, the extra
  directories and the MD5 summary. For a past cruise, from its `cruise_config.json` instead. The
  current cruise comes from the API rather than its `cruise_config.json` for two reasons: the hook
  sets the cruise extent through the API, after asking it which cruise is current, and OpenVDM
  rewrites the file only at setup, when the cruise is edited, on an export and at finalize, so
  an extra directory or transfer added mid-cruise isn't in it yet.
- **The vessel's name and contact,** from the site TOML, or where it leaves them blank, from
  `openvdm.yaml`'s `vessel` block (OpenVDM 2.17). For a past cruise, from its `cruise_config.json`,
  the vessel it was on.
- **The GNSS feed:** OpenVDM doesn't know which of a collection system's files are the GNSS
  feed, or their format, so the ship's [site TOML](#site-toml) says (`[nav] format` and
  `files`). A ship logging several position sensors lists each as a `[[nav]]` table.

### What the hook does

- **Assesses each feed of the transfer:** the site TOML's `[nav]`, or each `[[nav]]` whose
  `transfer` is this one or has none, the primary first. A non-primary feed's outputs carry its
  name (`<CRUISE>_seapath_…`).
- **Skips** a transfer without new or updated files matching a feed's `files`
  (`--changed-files`), and with `--min-interval` a feed whose reports are newer than that.
- **Reads** the cruise's nav files from the transfer's directory, within the cruise's dates (an
  open-ended cruise ends today, UTC), and **checks their checksums** against OpenVDM's MD5
  summary. Files changed after the summary was written count as pending, not failures.
- **Writes into the extra directory,** replacing earlier ones: the GeoCSV products, the plots,
  the PDF, the notebook, and `<CRUISE>_config.toml`, a snapshot of what OpenVDM and the site TOML
  supplied, which the notebook re-runs from. Run as root, it gives them to the warehouse user.
- **Adds those files to the cruise's MD5 summary,** with OpenVDM's `utils/update_md5_summary.py`
  (OpenVDM 2.16.1 or later).
- **Sets OpenVDM's cruise extent** (`cruiseExtent`, OpenVDM 2.17) to the box around the primary
  feed's fixes that pass QC, so a jump to 0, 0 never stretches it. Its "Within the expected
  area" check is then n/a, and other feeds are checked against the primary's track. With `--no-set-extent`, without a `workerApiKey`, for a past cruise or
  before OpenVDM 2.17, it checks the fixes against OpenVDM's extent instead, else the site TOML's
  `[extent]`. [INSTALL.md](INSTALL.md#4-add-the-hooks-to-openvdmyaml) covers the setup it needs.
- **Fails in one line,** which OpenVDM shows as the hook's message. One feed's failure doesn't
  stop the others. Updating the MD5 summary or the extent can fail without failing the hook: it
  logs a warning.

### Troubleshooting

OpenVDM logs the hook's output in `/var/log/openvdm/post_hooks.log`. To see it directly, run the
hook's command by hand, as root, without `--changed-files` or `--min-interval`
([INSTALL.md step 5](INSTALL.md#5-try-it)). Each run prints the cruise and feed it found, what it
assumed (`note:` lines), the QA summary and the files it wrote.

| Message | Meaning |
|---|---|
| `no new or updated nav files; nothing to do` | None of the transfer's files match a feed's `files` glob. By hand, leave `--changed-files` out |
| `its reports, from ..., are newer than --min-interval 6h; nothing to do` | The last run was within the interval, so its reports stand. By hand, leave `--min-interval` out |
| `... doesn't exist yet; nothing to do` | The transfer hasn't brought any files yet |
| `0 fixes ... from 0 files` | The `files` glob matches nothing in the transfer's directory: check it with `ls` there |
| `every nav file is outside the cruise window` | The files' dates aren't within the cruise's start and end in OpenVDM. OpenVDM's [sample data](https://github.com/OceanDataTools/openvdm_sample_data) is from 2012-04-28 and 29 |
| `no nav format ...: set [nav] format in the site config` | The site TOML (`--site-config`) has no feed format |
| `no collection system transfer named ...` | `TRANSFER` isn't a transfer's name in OpenVDM (Configuration > Collection System Transfers) |
| `no extra directory named 'NAV_QA'` | Add it in OpenVDM (Configuration > Extra Directories), or set the site TOML's `[openvdm] output_extra_directory` |
| `no openvdm.yaml at ...` | OpenVDM is installed elsewhere: give `--openvdm-config` |
| `the reports aren't in OpenVDM's MD5 summary: ...` | OpenVDM's `update_md5_summary.py` is missing (OpenVDM before 2.16.1) or failed; for a past cruise, OpenVDM's warehouse doesn't have it. The reports are written all the same |
| `OpenVDM's cruise extent wasn't set: ...` | OpenVDM refused it (its `WORKER_API_KEY` doesn't match `workerApiKey`), is older than 2.17, or moved on to another cruise during the run |
| `note: openvdm.yaml has no workerApiKey ...` | The hook can't set the extent, so it checks the fixes against OpenVDM's instead |

### OpenVDM commands

#### `openvdm`

```text
nav-qa-processing openvdm [--openvdm-config OPENVDM_YAML] [--cruise-config CRUISE_CONFIG_JSON]
                          [--site-config SITE_TOML] [--no-set-extent]
                          [--changed-files FILES] [--theme THEME_TOML]
                          [--execute-notebook] [--no-products] [--min-interval DURATION]
                          TRANSFER
```

The hook: assesses the collection system transfer's nav files for OpenVDM's current cruise, or
with `--cruise-config` any cruise, as [above](#what-the-hook-does). Installed `openvdm.yaml`
files call it, so its arguments keep their names; they're named as in sbe-qa-processing's hook.

| Argument | Default | Description |
|---|---|---|
| `TRANSFER` | required | The collection system transfer's name; in a hook, `{collectionSystemTransferName}` |
| `--openvdm-config OPENVDM_YAML` | `/opt/openvdm/server/etc/openvdm.yaml` | OpenVDM's settings: its web root (`siteRoot`), the `workerApiKey` and, from OpenVDM 2.17, the `vessel` block. Needs the `openvdm` extra. With `--cruise-config`, only where OpenVDM is installed, for the MD5 summary |
| `--cruise-config CRUISE_CONFIG_JSON` | none | A cruise's `cruise_config.json`, which OpenVDM writes into each cruise's directory: assess that cruise, past or current, without OpenVDM's API. [A past cruise](#a-past-cruise) says how |
| `--site-config SITE_TOML` | none | The ship's [site TOML](#site-toml); the hook needs its `[nav] format` |
| `--no-set-extent` | off | Don't set OpenVDM's `cruiseExtent` from the fixes that pass QC; check the fixes against it (else the site TOML's `[extent]`) instead |
| `--changed-files FILES` | none | OpenVDM's `{newFiles}` and `{updatedFiles}`; repeatable. When given, the QA runs only if they include nav files (`[nav] files`, or any file without it) |
| `--theme THEME_TOML` | none | A [theme](#themes) file; the site TOML's `[theme]` applies on top |
| `--execute-notebook` | off | Run the notebook and save its outputs |
| `--no-products` | off | Don't write the GeoCSV products into the extra directory, as for `run`. bestres has a line per fix and grows through the cruise (about 300 MB for 20 days at 2 Hz), and the hook rewrites it after every transfer |
| `--min-interval DURATION` | none | Skip a feed whose reports are newer than this: a number and `s`, `m`, `h` or `d`, e.g. `6h`. Each run assesses the whole cruise so far, so a long cruise's take longer than OpenVDM's transfer interval. Leave it out of a `preFinalizeCurrentCruise` hook, which writes the final reports. [Choosing it](INSTALL.md#4-add-the-hooks-to-openvdmyaml) |

#### `config-from-openvdm`

```text
nav-qa-processing config-from-openvdm [--openvdm-config OPENVDM_YAML]
                                      [--cruise-config CRUISE_CONFIG_JSON]
                                      [--site-config SITE_TOML] [--no-set-extent]
                                      [-o OUTPUT_DIR] [--force] TRANSFER
```

Builds the config TOML the hook would use, from OpenVDM's current cruise (or with
`--cruise-config` any cruise) and the site TOML, without running the QA, and reports what's
missing or assumed. `TRANSFER`, `--openvdm-config`, `--cruise-config`, `--site-config` and
`--no-set-extent` are as for `openvdm`: when the hook would
set the cruise extent, the config has no expected area. To review it, or run the QA by hand:

```bash
uv run nav-qa-processing config-from-openvdm OpenRVDAS --site-config configs/site.toml  # configs/<CRUISE>.toml
uv run nav-qa-processing run /path/to/cruise/OpenRVDAS -c configs/<CRUISE>.toml -o output/<CRUISE>
```

| Argument | Default | Description |
|---|---|---|
| `-o`, `--output OUTPUT_DIR` | `configs` | Directory for `<CRUISE_ID>.toml` (and `<CRUISE_ID>_<NAME>.toml` for each other feed of the transfer), or `-` for stdout with one feed |
| `--force` | off | Replace an existing TOML |

#### A past cruise

OpenVDM's API knows only the current cruise, but OpenVDM writes each cruise's config into its
directory as `cruise_config.json` (its default name, set by `CRUISE_CONFIG_FN` in OpenVDM's
`www/app/Core/Config.php`): its ID, name, PI, location, dates and extent, the transfers' and
extra directories' destinations, the MD5 summary's name, and the vessel it was on. Give it with
`--cruise-config`, and the hook or `config-from-openvdm` take the cruise from it instead of the
API, with the site TOML as for the current cruise:

```bash
# Rebuild a past cruise's reports in its NAV_QA extra directory, as the hook would have
sudo /opt/nav-qa-processing/.venv/bin/nav-qa-processing openvdm OpenRVDAS \
    --cruise-config /data/CruiseData/SP2613/cruise_config.json \
    --site-config /opt/nav-qa-processing/configs/site.toml

# Or keep it as a config TOML, and run the QA anywhere
nav-qa-processing config-from-openvdm OpenRVDAS --site-config configs/site.toml \
    --cruise-config /data/CruiseData/SP2613/cruise_config.json -o configs   # configs/SP2613.toml
nav-qa-processing run /data/CruiseData/SP2613/OpenRVDAS -c configs/SP2613.toml -o output/SP2613
```

- **The cruise's directory** is the JSON's, wherever the cruise now is: an archived or copied
  cruise works, as long as its directory keeps the cruise's ID as its name, as OpenVDM names it.
- **OpenVDM's cruise extent isn't set:** it's the current cruise's. The fixes are checked
  against the JSON's extent.
- **The reports go into the cruise's MD5 summary** where OpenVDM is installed and still has the
  cruise in its warehouse: they're given to OpenVDM by their full paths, so it refuses files
  outside its directory for the cruise. Otherwise, or without OpenVDM, the hook warns that the
  reports aren't in the summary and still succeeds.
- **The reports belong** to the owner of the cruise's directory, run as root.
- A cruise the hook ran on also has `<CRUISE>_config.toml` beside its reports, the config as of
  that run, which `run -c` takes as it is.

#### `site-config`

```text
nav-qa-processing site-config [-o SITE_TOML] [--openvdm-config OPENVDM_YAML] [--force]
```

Writes the [site TOML](#site-toml) by prompting, on stderr, for each field OpenVDM doesn't
store: the vessel name and report contact (their defaults the site TOML's, else those in
`openvdm.yaml`'s `vessel` block), the GNSS feed's format (one with a reader; the
prompt lists them), files glob, device (make, model, serial number, location) and transfer, any
the vessel's top speed in m s-1 (`[thresholds] max_speed`, default 8.7), other position
sensors (a name each, then the same; `-` drops one), the GeoCSV attribution, the extra directory and an expected
cruise area. Enter keeps the `[default]` and `-` clears it. It works offline. Over an existing
file (with `--force`) its values are the defaults, and at the end of input every remaining
field keeps its default, so with no input the file is written back unchanged. The other
`[thresholds]` and `[theme]` are kept as they are.

| Argument | Default | Description |
|---|---|---|
| `-o`, `--output SITE_TOML` | `configs/site.toml` | The site TOML to write, or `-` for stdout |
| `--openvdm-config OPENVDM_YAML` | `/opt/openvdm/server/etc/openvdm.yaml`, when it exists | From OpenVDM 2.17, its `vessel` block's name and contact are the prompts' defaults where the site TOML has none, and are written into it. Without the file, the defaults are the site TOML's, or blank |
| `--force` | off | Replace an existing site TOML, starting from its values |

### Site TOML

The per-ship settings OpenVDM doesn't store. Only `[nav] format` is required;
`configs/site.example.toml` shows every key. Any other key is an error, as in a config TOML.

A site TOML describes the ship, so it has a `[vessel]` table and no `[cruise]`: the hook gets
each cruise's ID, name, chief scientist, location and dates from OpenVDM, and writes them with
the site's settings into a config TOML for the cruise, `<CRUISE>_config.toml`, where the
vessel's name is `[cruise] vessel_name`. A `[cruise]` table here, or a `[vessel]` one in a config
TOML, is an error that says so.

| Table | Key | Default | Description |
|---|---|---|---|
| `[vessel]` | `name` | blank | The vessel's name, for the report header. Wins over `openvdm.yaml`'s `vessel.name` (OpenVDM 2.17), which fills it when blank |
| `[nav]` | `format` | required | The GNSS feed's format ID (`nav-qa-processing formats`), e.g. `nav33` for OpenRVDAS's ISO timestamp + GGA |
| `[nav]` | `files` | every file | A glob of the GNSS feed's files within the collection system's directory, e.g. `raw/POSMV_GGA-*.txt`. Also what `--changed-files` is matched against |
| `[nav.device]` | `make`, `model`, `serial_number`, `location` | blank | The receiver or INS, for the reports, as in a [config TOML](#config-toml) |
| `[[nav]]` | `name`, `primary`, `transfer`, and the keys above | one feed | Several position sensors, as in a [config TOML](#config-toml). `transfer` names the collection system transfer a feed is in; the hook runs the feeds of its `TRANSFER` and those without one |
| `[extent]` | `westernmost`, `easternmost`, `southernmost`, `northernmost` | none | The expected cruise area, used when the hook doesn't set OpenVDM's `cruiseExtent` and OpenVDM has none. westernmost > easternmost crosses the antimeridian |
| `[openvdm]` | `output_extra_directory` | `NAV_QA` | The OpenVDM extra directory the reports go into |
| `[provenance]` | `contact_institution`, `contact_email` | blank | The report contact. Wins over `openvdm.yaml`'s `vessel.contact` (OpenVDM 2.17), which fills them when blank |
| `[geocsv]` | `attribution` | blank | The GeoCSV products' attribution |
| `[thresholds]` | as in a [config TOML](#config-toml) | defaults | QC and QA limits. `max_speed`, the vessel's top speed [m s-1] (default 8.7), is always written: a step faster than it counts as an unreasonable speed |
| `[theme]` | as in a [config TOML](#config-toml) | none | The reports' look; paths relative to the site TOML |

## Development

```bash
uv run pytest -n auto --dist loadgroup          # all tests, on every core
uv run pytest tests/test_cli.py::test_version   # one test
uv run ruff check . && uv run ruff format --check .
```

CI (`.github/workflows/ci.yml`) runs Ruff and the tests on Linux (Python 3.11 to 3.14), macOS and
Windows on every pull request and push to `main`. It downloads nothing: the tests unpack the
reference filesets committed in `tests/data/` and draw on Natural Earth clipped to their areas.
The tests never download map data either; with `fetch-map-data` run, the tests marked `map_data`
use the full layers.

`tests/test_openvdm_contract.py` checks the OpenVDM hook against OpenVDM's own code rather than
stand-ins: how OpenVDM runs the hook commands INSTALL.md documents, its
`update_md5_summary.py`, and the cruise config file it writes. It needs a checkout of OpenVDM's
`2.17` branch with a `venv` of its requirements, at `OPENVDM_SOURCE` (default `../openvdm`), and
skips without one; CI's "OpenVDM contract" job clones it:

```bash
git clone --branch 2.17 https://github.com/OceanDataTools/openvdm.git ../openvdm
uv venv --python 3.12 ../openvdm/venv && VIRTUAL_ENV=../openvdm/venv uv pip install -r ../openvdm/requirements.txt
uv run pytest tests/test_openvdm_contract.py
```

## License

[MIT](LICENSE.md)
