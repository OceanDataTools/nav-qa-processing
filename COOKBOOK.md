# Cookbook

Worked examples, end to end: assessing **a directory of raw nav files** (a ship's own logs), and
assessing **an R2R fileset** (downloaded from R2R, with R2R's tests and QA 2.0 XML). Every
command and output here comes from a real run. [INSTALL.md](INSTALL.md) covers installing; the
[README](README.md) lists every command, argument and TOML key.

The examples call `nav-qa-processing`. From a clone, that's `uv run nav-qa-processing`; from an
install like the OpenVDM hook's, `/opt/nav-qa-processing/.venv/bin/nav-qa-processing`.

- [A directory of raw nav files](#a-directory-of-raw-nav-files)
  - [Find the format](#find-the-format)
  - [A quick look](#a-quick-look)
  - [A proper run, with a config TOML](#a-proper-run-with-a-config-toml)
  - [Several position sensors](#several-position-sensors)
  - [Change a threshold and run again](#change-a-threshold-and-run-again)
  - [The notebook](#the-notebook)
  - [From Python](#from-python)
- [An R2R fileset](#an-r2r-fileset)
  - [Download it](#download-it)
  - [Make its cruise TOML](#make-its-cruise-toml)
  - [Run R2R's assessment](#run-r2rs-assessment)
  - [Compare with R2R's products](#compare-with-r2rs-products)
  - [Without R2R's tests](#without-r2rs-tests)
- [An OpenVDM cruise, by hand](#an-openvdm-cruise-by-hand)

## A directory of raw nav files

The example is the OpenRVDAS logs in OpenVDM's sample data
([openvdm_sample_data](https://github.com/OceanDataTools/openvdm_sample_data),
`sample_data/ssh_source/OpenRVDAS/raw/`), copied to `openrvdas_raw/`. Like most logger
directories, it mixes the GNSS feed with other instruments:

```text
openrvdas_raw/
  MET-2012-04-28-000000.txt        POSMV_GGA-2012-04-28-000000.txt
  MET-2012-04-29-000000.txt        POSMV_GGA-2012-04-29-000000.txt
  SVP-2012-04-28-000000.txt        TSG_Raw-2012-04-28-000000.txt
  SVP-2012-04-29-000000.txt        TSG_Raw-2012-04-29-000000.txt
```

### Find the format

Look at a GNSS file, then find the format whose lines look the same:

```bash
head -2 openrvdas_raw/POSMV_GGA-2012-04-28-000000.txt
```

```text
2012-04-28T00:00:01.109000Z,$GPGGA,000000.987,2635.03241,N,09331.21859,W,2,09,1.1,1.89,M,,,9,0025*16
2012-04-28T00:00:01.609000Z,$GPGGA,000001.487,2635.03382,N,09331.21899,W,2,09,1.1,1.90,M,,,10,0025*28
```

```bash
nav-qa-processing formats          # every format ID, and whether it can be read
nav-qa-processing formats nav33    # one format: its layout, example lines, how it's read
```

An ISO timestamp, a comma and a GGA sentence is `nav33`. (With a space instead of the comma,
it's `nav30`; a logger timestamp then NMEA with ZDA sentences is usually `nav29`.)

### A quick look

With only the format, `run` reads every file in the directory and names the outputs after it:

```bash
nav-qa-processing run openrvdas_raw -f nav33 -o out_quick
```

```text
openrvdas_raw: 281168 fixes (nav33) from 8 files in openrvdas_raw
  receiver: not in the config; talker IDs: GP (GPS, 281168 fixes)
  flagged: 0
  products: bestres 281168, 1min 2344, control 35
  QA statistics: 7 pass, 1 info, 1 n/a
    PASS  Completeness: 100.00 % (281168 of 281170 0.5 s epochs)
    PASS  Longest gap: 00:00:01.000 (1.000 s) (0 gaps of 300 s or longer)
    ...
    N/A   Within the expected area: no extent configured
```

It read all 8 files, the MET, SVP and TSG ones too. They hold no GGA, so they add no fixes (the
PDF's "Checks by file" lists each with 0), only reading time; and the outputs are named after the
directory, with no cruise dates to keep stray files out. For a real assessment, give the cruise
a config.

### A proper run, with a config TOML

A config TOML names the cruise and its dates, which files are the GNSS feed, the receiver, and
any limits that differ from R2R's. Every key is optional:

```toml
# ODT1204.toml
[cruise]
id = "ODT1204"                    # the products' prefix: ODT1204_bestres.geoCSV, ...
name = "OpenVDM sample cruise"
vessel_name = "Sample Vessel"
depart_date = 2012-04-28          # fixes outside these dates are left out (UTC days)
arrive_date = 2012-04-29

[nav]
format = "nav33"
files = "POSMV_GGA-*.txt"         # only the GNSS files; a glob, relative to the directory

[nav.device]
make = "Applanix"
model = "POS MV"

[thresholds]
max_speed = 6.0                   # the vessel's top speed [m s-1]
```

```bash
nav-qa-processing run openrvdas_raw -c ODT1204.toml -o output/ODT1204
```

```text
ODT1204: 281168 fixes (nav33) from 2 files in openrvdas_raw
  receiver: Applanix POS MV; talker IDs: GP (GPS, 281168 fixes)
  ...
wrote output/ODT1204/ODT1204_bestres.geoCSV
wrote output/ODT1204/ODT1204_1min.geoCSV
wrote output/ODT1204/ODT1204_control.geoCSV
wrote output/ODT1204/ODT1204_plots/track_map.svg
...
wrote output/ODT1204/ODT1204_qa_report.pdf
```

`output/ODT1204/` then holds the three GeoCSV products, the figures as SVGs in
`ODT1204_plots/`, the PDF report and `ODT1204_qa.ipynb`.

- **Files in subdirectories:** the glob is relative to the directory and can reach into it,
  e.g. `files = "raw/POSMV_GGA-*.txt"` or `"**/*GGA*.txt"`. Without `files`, every file in the
  directory and its subdirectories is read.
- **An expected area:** add a `[cruise.extent]` table (`westernmost`, `easternmost`,
  `southernmost`, `northernmost`, decimal degrees) and the "Within the expected area" check
  counts the fixes outside it.
- **Wrong dates:** if the cruise's dates don't cover the files, the summary says so: "every nav
  file is outside the cruise window (...): 2 files with fixes from ...; check the cruise's
  dates".
- **A typo in the TOML** is an error that says where the key belongs, e.g. `cruise_id (top
  level): did you mean [cruise] id?`.

### Several position sensors

A ship logging a POS MV and a Seapath gives each a `[[nav]]` table, with a name:

```toml
[[nav]]
name = "posmv"
primary = true
format = "nav33"
files = "POSMV_GGA-*.txt"

[[nav]]
name = "seapath"
format = "nav33"
files = "SEAPATH_GGA-*.txt"
```

`run` assesses each: the primary's outputs are `<CRUISE>_...`, the others'
`<CRUISE>_seapath_...`. `--nav seapath` runs only that one.

### Change a threshold and run again

`run` won't replace existing reports:

```text
output/ODT1204/ODT1204_bestres.geoCSV exists; use --force to overwrite the reports
```

Edit the config's `[thresholds]` (the README's [Config TOML](README.md#config-toml) lists every
limit, e.g. `max_gap`, `min_completeness`, `max_accel`), then run with `--force`, or into a new
`-o` directory to keep both.

### The notebook

`<CRUISE>_qa.ipynb` re-runs the whole assessment, step by step: the config, the checks, the
statistics, QC's flags, every figure and the products. Open it in Jupyter or VS Code and edit a
threshold to see its effect. `run --execute-notebook` saves it with its outputs already in it
(needs the dev dependencies: an install without `--no-dev`).

### From Python

```python
from nav_qa_processing.qa import run_qa
from nav_qa_processing.reports import write_reports

result = run_qa("openrvdas_raw", "ODT1204.toml")    # or a QAConfig built in Python
print(result.stats.verdict[1])                       # 7 of 8 checks pass. n/a: Within the expected area.
print([c.name for c in result.stats.checks if c.status == "warn"])
written = write_reports(result, "output/ODT1204", notebook=False)
```

`result.bestres`, `result.one_minute` and `result.control` are pandas DataFrames;
`result.stats` has every statistic and check, and `result.qc` the flags.

## An R2R fileset

R2R's raw GNSS filesets are BagIt bags, and R2R has already published products and, for recent
cruises, a QA 2.0 report for each. The R2R commands download them, make a cruise TOML from R2R's
catalog, and run the assessment with R2R's six tests, writing a QA 2.0 XML beside the core
reports. The example is Blue Heron's BH19-30, raw GNSS fileset 136141.

### Download it

```bash
nav-qa-processing r2r fetch-fileset BH19-30 136141 -o data
```

```text
R2R has no QA 2.0 report for this fileset
7 raw files from https://.../BH19-30/BH19-30_136141_gnss.tar.gz -> data/BH19-30_136141_gnss
8 product files (fileset 609730) -> data/BH19-30_136141_gnss/products
```

```text
data/BH19-30_136141_gnss/
  bagit.txt, bag-info.txt, manifest-md5.txt, file-info.txt   the bag, with R2R's checksums
  data/                                                      the raw GNSS files
  products/data/BH19-30_bestres|1min|control.geoCSV          R2R's published products
  r2r_qa.2.0.xml                                             R2R's QA report, when it has one
```

### Make its cruise TOML

```bash
nav-qa-processing r2r config-from-catalog BH19-30 136141 -o configs
```

writes `configs/BH19-30_136141.toml` from R2R's catalog: the cruise's dates, IDs, ports, extent,
DOIs, the receiver and the raw format (here `nav31`, UHDAS's GPS feed). An excerpt:

```toml
# From R2R's catalog, 2026-10-26
[cruise]
id = "BH19-30"
pi = "Savage, Jessica"
vessel_id = "33B1"
vessel_name = "Blue Heron"
operator_id = "edu.umn.d"
scheduler_id = "org.unols"
depart_date = 2019-10-03
arrive_date = 2019-10-03
doi = "doi:10.7284/908765"

[cruise.extent]
westernmost = -92.09525
easternmost = -91.980398
southernmost = 46.774652
northernmost = 46.820732

[fileset.device]
make_id = "com.furuno"
model_name = "GP-90"

[nav]
format = "nav31"
```

When the fileset has a QA 2.0 report, `r2r config-from-qa data/<...>/r2r_qa.2.0.xml` makes the
TOML from the report instead, with no request to R2R.

### Run R2R's assessment

```bash
nav-qa-processing r2r run configs/BH19-30_136141.toml data/BH19-30_136141_gnss -o output/BH19-30
```

```text
R2R rating G
  G  PercentCompleteness: 100.00 percent (9097 of 9097 intervals of 1.0 s)
  G  LongestGapEpoch: 1.000 s (0 gaps of the gap threshold or longer)
  G  PercentRecordsOutOfSequence: 0.00 percent (0 of 9097)
  G  PercentRecordsWithBadQualityIndicator: 0.00 percent (0 of 9097 reporting one)
  G  PercentUnreasonableSpeeds: 0.10 percent (9 of 9096)
  G  PercentUnreasonableAccelerations: 0.43 percent (39 of 9093)
BH19-30_136141: 9097 fixes (nav31) from 2 files in data/BH19-30_136141_gnss
  receiver: com.furuno GP-90; talker IDs: GP (GPS, 9097 fixes)
  flagged: 115 (stale_repeat 112, acceleration 3)
  ...
```

`output/BH19-30/` gets the core outputs, with R2R's certificate in the PDF and R2R's tests in
the notebook, plus `BH19-30_136141_qa.2.0.<time>.xml`, the QA 2.0 report. Add `--r2rnav` to
write the products in R2R's legacy r2rnav format too (`BH19-30_bestres.r2rnav`, ...).

### Compare with R2R's products

The downloaded `products/` are what R2R published, so they can be read beside ours:

```python
from nav_qa_processing import geocsv

ours = geocsv.read("output/BH19-30/BH19-30_bestres.geoCSV")
r2rs = geocsv.read("data/BH19-30_136141_gnss/products/data/BH19-30_bestres.geoCSV")
print(len(ours.records), len(r2rs.records))                            # 9097 9097
print(ours.records["flagged"].sum(), r2rs.records["flagged"].sum())    # 115 162
```

The same 9,097 records. Both flag 106; R2R flags 56 that we don't, 51 of them a position
repeating the one before (stale repeats, which NavManager judges differently near its speed
threshold), and we flag 9 that R2R doesn't. Every known difference from R2R's processing, with
its cause and the filesets that show it, is in
[docs/navmanager-differences.md](docs/navmanager-differences.md) (the stale repeats are Q7). With a QA 2.0
report in the bag, compare its test results with our XML's the same way.

### Without R2R's tests

The core `run` reads an R2R bag and an R2R cruise TOML too, and writes the core reports without
R2R's tests, certificate or XML:

```bash
nav-qa-processing run data/BH19-30_136141_gnss -c configs/BH19-30_136141.toml -o output/BH19-30-core
```

## An OpenVDM cruise, by hand

On a ship running OpenVDM, the hook runs the QA after each transfer ([INSTALL.md](INSTALL.md#install-for-the-openvdm-hook)).
To run it yourself, either give the hook's command without `--changed-files`:

```bash
sudo /opt/nav-qa-processing/.venv/bin/nav-qa-processing openvdm OpenRVDAS \
    --site-config=/opt/nav-qa-processing/configs/site.toml
```

or write out the config the hook would use and run it anywhere, e.g. to try other thresholds
without touching OpenVDM's extra directory:

```bash
nav-qa-processing config-from-openvdm OpenRVDAS --site-config configs/site.toml   # configs/<CRUISE>.toml
nav-qa-processing run /path/to/cruise/OpenRVDAS -c configs/<CRUISE>.toml -o output/<CRUISE>
```
