# Performance against NavManager

How long nav-qa-processing and NavManager take to assess the same filesets, and how much memory
they need, measured 2026-10-30 on one machine. In short, nav-qa-processing takes 1.6 to 2.5 times
less time and 4.5 to 37 times more memory. NavManager streams through its files in fixed buffers,
so its memory stays at tens of megabytes whatever the cruise's length. nav-qa-processing holds
the whole cruise in arrays, about 300 bytes a fix, and a raw file's lines while it reads it.
What it does with that memory is in [docs/navmanager-differences.md](navmanager-differences.md):
QC over the whole series rather than in buffers, and every fix kept.

| Fileset | Fixes | NavManager | nav-qa-processing, the same work | nav-qa-processing, `run` |
|---|---:|---:|---:|---:|
| SP2613 (nav29, 2 Hz, 8 hours) | 56,925 | 9.7 s, 48 MB | 4.9 s, 219 MB (2.0× faster) | 8.6 s, 264 MB |
| MGL1509 (nav12, 1 Hz, 7 days) | 601,027 | 87.7 s, 66 MB | 39.6 s, 388 MB (2.2×) | 49.3 s, 535 MB |
| SR2612 (nav29, 5 Hz, 2 days) | 880,236 | 160.9 s, 66 MB | 99.3 s, 2,445 MB (1.6×) | 108.0 s, 2,495 MB |
| Synthetic (nav33, 2 Hz, 20 days) | 3,456,000 | 530.1 s, 279 MB | 211.8 s, 1,243 MB (2.5×) | 227.1 s, 1,341 MB |

Wall time and peak resident memory (MB of 2^20 bytes), medians of three runs. NavManager's time
is its six steps' added, and its memory the largest of their peaks.

## What was measured

**NavManager** (master, f8e4158): its steps from the raw files to the products and the QA
statistics, each a PHP process reading the previous one's r2rnav file:

| Step | Command | Writes |
|---|---|---|
| navcopy | `navcopy.php -d RAW_DIR -f FORMAT -o raw.r2rnav -t START/END` | the raw fixes |
| navqc | `navqc.php -i raw.r2rnav -o qc.r2rnav` | flagged (`#`) fixes |
| navsogcog | `navsogcog.php -i bestres.r2rnav -o …` | bestres: speed and course added |
| navsample 1min | `navsample.php -i bestres.r2rnav -o 1min.r2rnav -t 60` | 1min |
| navsample control | `navsample.php -i 1min.r2rnav -o control.r2rnav -c` | control (`navsimplifier`, Java) |
| navqa | `navqa.php -i raw.r2rnav` | the QA statistics |

The window `-t` is the cruise's first day 00:00Z to the day after its last. Not included:
navdatalist, decompressor and navbounds (no compressed files, the extent not needed here), and
writing the QA 2.0 XML (xmltools2).

**nav-qa-processing** (ab59071, before 1.0.0), twice:
- **The same work:** [`performance/same_work.py`](performance/same_work.py): `qa.run_qa` on the
  raw files (read, QC, bestres, 1min, control and the QA statistics and checks) and the three
  GeoCSV products written. No map data, so no over-water check, which NavManager has no
  equivalent of. One Python process, its start-up and imports included.
- **`run`:** `nav-qa-processing run RAW_DIR -c config.toml` with Natural Earth fetched: the same,
  plus the over-water check, the figures, the PDF report and the notebook (not executed). This
  is what a user or the OpenVDM hook runs. Not `r2r run`: R2R's tests and XML are left out on
  both sides.

Both assessed the same records, and their products have the same number of records but for
three known differences:
- SR2612's last fix, which follows its last ZDA and which NavManager's nav29 reader drops
  ([R2](navmanager-differences.md#r2-the-last-gga-after-the-final-zda-is-dropped)).
- The 1min of MGL1509 and the synthetic cruise: NavManager lacks their last minute,
  2015-05-28T23:59 and 2026-07-20T23:59 ([P1](navmanager-differences.md#p1-1min-sampling)).
- The synthetic cruise's 40 fixes, two a day, which NavManager's nav33 reader drops
  ([R8](navmanager-differences.md#r8-nav33-and-nav02-the-86400th-fix-of-each-buffer-is-dropped)).

QC flagged the same number of records: 960 in SR2612, none in the others.

## The filesets

| Fileset | R2R fileset | Format | Files | Size | Fixes | Notes |
|---|---|---|---:|---:|---:|---|
| SP2613 | 169851 | nav29 | 2 (1 in the window) | 23 MB | 56,925 | Sproul's Trimble BX992; the first parity reference |
| MGL1509 | 122372 | nav12 | 8 | 115 MB | 601,027 | Langseth's C-Nav 3050, no ZDA (dated by the logger's year:day-of-year) |
| SR2612 | 169918 | nav29 | 3 | 769 MB | 880,236 | Sally Ride's Seapath 380 at 5 Hz, 13 lines a fix (GGA, ZDA, GLL, VTG, RMC, GSA, PSXN, …), the largest file 396 MB |
| Synthetic | — | nav33 | 20 | 356 MB | 3,456,000 | [`performance/make_cruise.py`](performance/make_cruise.py): OpenRVDAS's GGA at 2 Hz for 20 days; the size of the cruise that ran a 4 GB OpenVDM server out of memory before #157 |

## Environment

- An Apple M1 Pro (8 cores: 6 performance, 2 efficiency; 16 GB) running Docker 29.8.2 (Linux
  7.0.14-linuxkit, arm64), the container given 8 CPUs and 7.8 GB.
- The `php:7.4-cli` image (Debian 11) with PHP 7.4.33, OpenJDK 17.0.20.1 (for navsimplifier) and
  GNU time for the measurements.
- nav-qa-processing installed by `uv sync --no-dev --python 3.12` (Python 3.12.16).

PHP 7, because NavManager's navqc doesn't run on PHP 8: with no speeds in a buffer,
`count($speedHori)` (`include/navqc.inc.php`, line 676) is `count(null)`, a warning in PHP 7 and a
`TypeError` that stops navqc in PHP 8 (8.2 here, with SP2613).

Neither tool uses more than one core: their CPU time is their wall time, within a few percent.
The three runs of each NavManager step over 10 s were within 5 % of each other; ours varied
more, up to 11 % (SR2612's same work, 11 s in 99 s) and 21 % (SP2613's `run`, 2 s in 9 s).

## The steps

Wall time [s], CPU time [s] and peak memory [MB], medians of three runs.

| Step | SP2613 | MGL1509 | SR2612 | Synthetic |
|---|---:|---:|---:|---:|
| navcopy | 1.41, 1.40, 24 | 6.36, 6.24, 24 | 37.40, 37.34, 23 | 44.20, 44.08, 279 |
| navqc | 1.99, 1.97, 24 | 19.49, 19.47, 24 | 29.73, 29.69, 24 | 118.34, 118.25, 24 |
| navsogcog | 0.22, 0.21, 48 | 1.55, 1.55, 66 | 3.32, 3.30, 66 | 11.61, 11.59, 66 |
| navsample 1min | 0.17, 0.17, 24 | 1.59, 1.59, 24 | 2.58, 2.58, 24 | 10.54, 10.52, 23 |
| navsample control | 0.11, 0.15, 45 | 0.13, 0.23, 53 | 0.13, 0.20, 49 | 0.16, 0.31, 68 |
| navqa | 5.76, 5.74, 24 | 58.32, 58.28, 26 | 87.57, 87.52, 24 | 345.89, 345.81, 24 |
| **NavManager** | **9.66, 9.63, 48** | **87.70, 87.64, 66** | **160.85, 160.73, 66** | **530.12, 529.91, 279** |
| **Same work** | **4.88, 5.45, 219** | **39.56, 40.03, 388** | **99.26, 98.48, 2,445** | **211.76, 211.68, 1,243** |
| `run` | 8.60, 9.19, 264 | 49.26, 49.72, 535 | 107.95, 107.62, 2,495 | 227.09, 227.03, 1,341 |

## Why

**Time.** NavManager spends most of its time in navqa (55 to 65 %) and navqc (about 20 %), PHP
loops over every record. nav-qa-processing's whole-series steps are numpy: the geodesics
(pyproj), the accelerations, the sampling and Douglas–Peucker (shapely). It still has three
loops in Python, which take most of its time (MGL1509, by cProfile):
- **Reading** the raw files, about half: each line split, its NMEA checksum computed a
  character at a time, each GGA parsed. A fileset with many sentences a fix is slow to read
  for both tools: SR2612's 13 lines a fix are why its navcopy and our reading take longest per
  fix.
- **QC's stale-repeat check** (`qc._stale_repeats`), about a quarter: a loop over the good fixes
  with a median and a geodesic for each.
- **Writing the GeoCSV text**, about a fifth: every value formatted through `quantities.py`.

These are where nav-qa-processing would get faster; none changes a result.

**Memory.** NavManager reads and writes a record at a time, holding at most a buffer: 86,400
records in navcopy and navsogcog, 3,600 in navqc. Its memory is constant, about 25 MB a step,
but for nav33's navcopy, whose buffer holds up to 86,400 parsed GGA objects (279 MB).
nav-qa-processing holds the whole cruise: the fixes as numpy columns, the QC flags, the products and the
statistics, for QC and statistics over the whole series. That is about 300 bytes a fix on top
of about 200 MB for Python and its libraries (SP2613's 219 MB and the synthetic cruise's
1,243 MB; `run` about 320 bytes a fix on 250 MB). While it reads a file
it also holds the file and its lines, one file at a time (#157): SR2612's 396 MB file of 5.6 M
lines is most of its 2.4 GB. [INSTALL.md](../INSTALL.md) gives the memory to plan for a cruise.

## Reproduce

With Docker, from nav-qa-processing's checkout:

```bash
# The filesets: three from R2R, one made
uv run nav-qa-processing r2r fetch-fileset SP2613 169851
uv run nav-qa-processing r2r fetch-fileset MGL1509 122372
uv run nav-qa-processing r2r fetch-fileset SR2612 169918
uv run python docs/performance/make_cruise.py data/synthetic 20 2

# PHP 7.4, Java and GNU time; Debian 11's packages are in Debian's archive
NAVMANAGER=$(cd ../../NavManager && pwd) # NavManager's checkout (master)
docker run -d --name navperf -v "$PWD:/src:ro" -v "$NAVMANAGER:/navmanager:ro" \
    php:7.4-cli sleep infinity
docker exec navperf bash -c '
    printf "deb http://archive.debian.org/debian bullseye main\n" >/etc/apt/sources.list
    rm -f /etc/apt/sources.list.d/*
    apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
        time openjdk-17-jre-headless curl
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    mkdir /work && tar -C /src -cf - --exclude=./data --exclude=./output --exclude=./.venv . |
        tar -C /work -xf - && ln -s /src/data /work/data # a copy of the checkout, its data read
    cd /work && uv sync --no-dev --python 3.12
    .venv/bin/nav-qa-processing fetch-map-data
'

# Three runs of each, then their medians
docker exec -w /work -e NAVMANAGER=/navmanager navperf bash -c '
    mkdir -p results
    while read -r name format raw depart arrive; do
        for n in 1 2 3; do
            bash docs/performance/benchmark.sh "$name" "$format" "$raw" "$depart" "$arrive" \
                >"results/$name.$n.tsv"
        done
        .venv/bin/python docs/performance/medians.py results/"$name".*.tsv
    done <<EOF
SP2613 nav29 data/SP2613_169851_gnss/data 2026-07-09 2026-07-09
MGL1509 nav12 data/MGL1509_122372_gnss/data 2015-05-22 2015-05-28
SR2612 nav29 data/SR2612_169918_gnss/data 2026-06-19 2026-06-21
synthetic nav33 data/synthetic 2026-07-01 2026-07-20
EOF'
```

[`performance/benchmark.sh`](performance/benchmark.sh) runs each step under GNU time and prints
a line for it (wall time, CPU time, peak memory), and the records each tool's bestres has; each
step's output is in `/tmp/benchmark/NAME/`. SP2613's window is its last day, 2026-07-09, as
R2R's run had it. The three take about 70 minutes on the machine above, the synthetic cruise
most of it.
