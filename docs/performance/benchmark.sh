#!/usr/bin/env bash
# Times NavManager's pipeline and nav-qa-processing's core QA on one fileset, for
# docs/performance.md: wall time [s], CPU time [s] and peak memory [MB] for each step, as
# tab-separated lines. Run on Linux (GNU time), from nav-qa-processing's checkout after `uv sync`,
# with NavManager at $NAVMANAGER, PHP 7 (navqc stops with a TypeError on PHP 8) and a Java
# runtime installed, and Natural Earth fetched (`nav-qa-processing fetch-map-data`) for the full
# run. Each step's output is in $OUT/NAME/ (default /tmp/benchmark), replaced on every run.
#
# usage: benchmark.sh NAME FORMAT RAW_DIR DEPART ARRIVE   (FORMAT both tools' ID, e.g. nav29;
#        dates YYYY-MM-DD: the cruise's UTC days, as NavManager's -t and our config take them)
#        Run it three times per fileset, each to its own file, and take medians.py of them.
set -euo pipefail

name=$1 format=$2 raw=$(cd "$3" && pwd) depart=$4 arrive=$5
repo=$(cd "$(dirname "$0")/../.." && pwd)
navmanager=${NAVMANAGER:?set NAVMANAGER to NavManager\'s checkout}
out=${OUT:-/tmp/benchmark}/$name
window="${depart}T00:00:00Z/$(date -u -d "$arrive + 1 day" +%F)T00:00:00Z"
rm -rf "$out" && mkdir -p "$out" && cd "$out"

# label, then the command: one line of label, wall, CPU, peak memory; its output in label.log
measure() {
    local label=$1
    shift
    /usr/bin/time -f "%e %U %S %M" -o time.txt "$@" >"${label// /_}.log" 2>&1
    read -r wall user system kilobytes <time.txt
    awk -v l="$label" -v w="$wall" -v u="$user" -v s="$system" -v k="$kilobytes" \
        'BEGIN { printf "%s\t%s\t%.2f\t%.0f\n", l, w, u + s, k / 1024 }'
}

# NavManager: raw r2rnav, flagged, speed and course (bestres), 1min, control, the QA statistics
measure "navcopy" php "$navmanager/bin/navcopy.php" -d "$raw" -f "$format" -o raw.r2rnav -t "$window"
measure "navqc" php "$navmanager/bin/navqc.php" -i raw.r2rnav -o qc.r2rnav
cp qc.r2rnav bestres.r2rnav # navsogcog writes to -o, then moves that over its input
measure "navsogcog" php "$navmanager/bin/navsogcog.php" -i bestres.r2rnav -o sogcog.tmp.r2rnav
measure "navsample 1min" php "$navmanager/bin/navsample.php" -i bestres.r2rnav -o 1min.r2rnav -t 60
measure "navsample control" php "$navmanager/bin/navsample.php" -i 1min.r2rnav -o control.r2rnav -c
measure "navqa" php "$navmanager/bin/navqa.php" -i raw.r2rnav

# nav-qa-processing: the same work, then a full `run` (PDF, figures, notebook, over-water check)
measure "same work" "$repo/.venv/bin/python" "$repo/docs/performance/same_work.py" \
    "$raw" "$format" "$depart" "$arrive" same_work
printf '[cruise]\nid = "%s"\ndepart_date = %s\narrive_date = %s\n\n[nav]\nformat = "%s"\n' \
    "$name" "$depart" "$arrive" "$format" >config.toml
measure "run" "$repo/.venv/bin/nav-qa-processing" run "$raw" -c config.toml -o run

# What each produced, to check they assessed the same records
# (r2rnav: every line but the // header; GeoCSV: lines starting with a time, flagged with # or not)
echo "records	$(grep -vc '^//' bestres.r2rnav)	$(grep -c '^#\?[0-9]\{4\}-' same_work/BENCHMARK_bestres.geoCSV)"
