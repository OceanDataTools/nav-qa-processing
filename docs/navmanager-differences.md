# Differences from NavManager

A register of every place where nav-qa-processing deliberately behaves differently from R2R's
NavManager (https://github.com/rvdata/NavManager, `master` at `f8e4158`, 2025-07-02), or where
R2R's published products differ from what NavManager's own code or documentation says. It is
kept up to date as differences are found, so that it can be presented as a report.

The project's parity policy (decided 2026-10-13, `CLAUDE.md`): match the **format** of R2R's
published products exactly, compute values soundly, and don't reproduce NavManager's quirks;
document each difference instead. Each entry gives:

- **NavManager:** what it does, and where (file and approximate line in `NavManager/`).
- **Here:** what this package does instead.
- **Evidence:** the R2R filesets and counts that show it, and the test that checks it.
- **Why:** the mechanism behind NavManager's behavior. *Confirmed* means read in the code and
  reproduced; *suspected* means inferred from the data and the code, not proven.
- **Effect:** on R2R's published products or ratings.

Reference filesets (see `tests/fixtures/README.md`): SP2613 (raw GNSS 169851, one day, rated G),
SP2424 (161900, 150,925 fixes, rated G), SP2503 (167484, rated R on completeness), SP1925 (134952, 303 flagged fixes, rated R), RR2406, RR2212 and SP2220 (#26), and FKt230417 (157454, Falkor (too)'s POS/MV, nav33, rated G).

## Summary

| ID | Stage | Difference | Effect on R2R's products | Status here |
|---|---|---|---|---|
| R1 | reader | Correct checksums 0x0A–0x0F rejected | Sentences dropped (14 % of VTG, 11 % of RMC in SP2613; 48,427 GGAs, 2.9 %, in FKt230417) | Fixed (#13) |
| R2 | reader | Last GGA after the final ZDA dropped | One record lost per cruise (SP2424) | Fixed (#14) |
| R3 | reader | GGA repeating the last dated time, or at 00:00:00, dropped | Records lost (SP2503: its first fix, at 00:00:00) | Fixed (#14) |
| R4 | reader | An equal time of day advances the date | A repeated fix dated 24 h later (none in the references) | Fixed (#15) |
| R5 | reader | Sentences split across logger lines read as short sentences, missing fields as 0 | RR2406: 191,414 of 266,841 records flagged; R on five tests from absurd values | Rejoined; a truncated sentence is rejected (#26) |
| R6 | reader | A coordinate without its hemisphere letter read as N or E | RR2406: 45,243 fixes at 83°E | Missing (#26) |
| R8 | navcopy nav33 | The 86,400th fix of each buffer dropped | FKt230417: 18 fixes | Every fix kept (#48) |
| R9 | navcopy nav33 | The last fix of the cruise dropped | FKt230417: its final fix | Every fix kept (#48) |
| R7 | R2R's run | RR2212's files processed out of date order | 121,321 records flagged out of sequence (50.08 %, R); the catalog extent misses two days | Files in time order (suspected R2R pipeline, not NavManager) |
| G1 | geodesy | Hand-written Vincenty: rounded semi-minor axis, early-exit azimuth, no iteration limit | Last digit of 8 speeds and ~4,300 courses (SP2613) | pyproj (#6) |
| G2 | geodesy | SOG/COG from coordinates rounded as written | Last digit of some speeds | Full precision (#12) |
| G3 | navsogcog | Full-buffer speeds use the previous segment's distance | SOG lags one fix in the first 86,399 records of long cruises (SP2424) | Not reproduced (#12) |
| G4 | navsogcog | Division by zero for equal times | None after QC | NaN |
| Q1 | navqc | Speed rule commented out | No speed flags | No speed rule, by decision (#7) |
| Q2 | navqc | Main-loop acceleration check inverted ("Flipped check") | Acceleration flags almost never fire (SP2424: 0) | Sound check (#7) |
| Q3 | navqc | Speed magnitudes, nearest-fix flagging, at most 2 iterations | Sideways jumps missed; neighbours flagged | Velocity vectors, step/excursion test (#7) |
| Q4 | navqc | 3,600-record buffers | Flags depend on buffer boundaries | Whole series (#7) |
| Q5 | navqc | Acceleration limit set to 3× position resolution [m], from one record | Silent, dimensionally odd limit | Derived from resolution, reported (#7) |
| Q6 | navqc | Null island only at 00:00:00 | 0,0 fixes at other times kept | Any time (#7) |
| Q7 | navqc | Stale-repeat decisions near the 0.25 m/s threshold | SP1925: 94 flags R2R-only, 10 ours-only | Documented (#10) |
| Q8 | navqc | No rule for fixes the ship couldn't reach (only an exact 0,0 at midnight, Q6) | A long stretch at a wrong position stays in the products (FKt230417: 283 fixes 650 m off for 4.7 min) | Reachability rule at 25 m s-1, longest reachable chain (#47) |
| P1 | navsample | Nearest record to each whole minute, with a skewed window and ring-buffer gaps | 1min misses nodes at buffer wraps and after the last fix | Nearest within half an interval (#8) |
| P2 | NavControl | Committed tolerance 0.1°, R2R uses 0.01° | None (R2R runs 0.01°) | shapely at 0.01° (#8) |
| F1 | R2R GeoCSV | Inconsistent units and long names, trimmed numbers | Header text and number formats | Standardized text (#9) |
| F2 | R2R GeoCSV | Flagged records without speed and course | — | Same structure (#10) |
| D1 | docs | Format and processing documents that disagree with the code | Misleading documentation | Our own definitions |
| A1 | QA | navqa issues found in review (PHP 8 error, port distances, thresholds twice) | Ratings and infos | Bounds stated once (#27) |
| A2 | navqa | Modal interval truncated to ms, ties to whichever comes first | None in the references | Rounded; ties to the shorter step (#24) |
| A3 | navqa | Completeness from accumulated "countable epochs"; the end-of-file loop compares ms with s | Completeness can exceed 100 % | Occupied interval slots (#24) |
| A4 | navqa | Quality 3 (PPS) counted as bad, though navqc accepts 1–5 | Bad-quality percentage | QC's valid range (#24) |
| A5 | navqa | Speeds and accelerations between every pair of records, scalar | SP2424: 0.16 % unreasonable accelerations, ±14 m s-2 (GNSS noise) | QC's acceleration over 2 s (#24) |
| A6 | navqa | Percentages over "actual epochs" (in-sequence steps), −1 and −2 for speeds and accelerations | Negligible | Over records, speeds, accelerations (#24) |
| A7 | xmltools2 | Ratings of values as written; gaps at the receiver's time precision; uncomputable results | A gap just over a bound; division by zero | Rated as written through `quantities.py`; no result is R (#27) |
| X1 | R2R QA XML | Duplicate `<r2r:id>` in `cruise`, PHP number printing, mixed unit names, a double space | Text only | R2R's structure, standardized text (#27) |

## Reader (navdatalist, navcopy, nmeatools)

### R1. Correct NMEA checksums 0x0A–0x0F are rejected

- **NavManager:** `NMEA0183Message::init` (`include/nmeatools.inc.php`, ~line 54) computes the checksum with `dechex()` and compares it with the supplied one, lowercased, with PHP's loose `==`.
- **Here:** checksums are compared by value, so `*0A`, `*0a` and a nonstandard one-digit `*A` are all 0x0A (`formats/nmea.py`).
- **Evidence:** FKt230417 (Falkor (too) POS/MV, fileset 157454, nav33): R2R's bestres lacks every GGA with checksum `0A`–`0F`, 48,427 of 1,652,627 fixes (2.9 %), and keeps every one with `00`–`09` (80,371); its completeness is 96.94 %, ours 99.86 % (`tests/test_r2r_references.py`). SP2613: 14.4 % of VTG and 11.0 % of RMC sentences are rejected, all with correct checksums; no GGA or ZDA there, or in SP2424, has a checksum below 0x10 (`tests/test_nmea.py`). This entry first said every checksum below 0x10 was rejected (39 % of VTG and 29 % of RMC); FKt230417 showed `00`–`09` pass.
- **Why (confirmed):** PHP's `dechex()` returns unpadded hex (`a`, not `0a`), while receivers write two digits. `==` compares two numeric strings as numbers, so `"5" == "05"` holds and `00`–`09` pass; `"a" == "0a"` is a text comparison and fails.
- **Effect:** 6 checksum values in 256, about 1 in 43 sentences of any type, are dropped. In SP2613 none was a GGA or ZDA; in FKt230417, 2.9 % of the fixes are missing from R2R's products, with its completeness rated on what's left.

### R2. The last GGA after the final ZDA is dropped

- **NavManager:** `parse_nav29` (`include/parsers/nav29.php`, end-of-input block): the loop `for ($inx=1; $inx < $binx - 1; $inx++)` prints one record fewer than the buffer holds.
- **Here:** every valid GGA is kept (`formats/gga_zda.py`).
- **Evidence:** SP2424 has 150,925 valid GGAs; R2R's bestres has 150,924, missing the final fix, 2024-11-04T03:16:44.50Z (`tests/test_rawnav.py`).
- **Why (confirmed):** an off-by-one. `$binx` is the next free (1-based) index, so the buffer holds `$binx - 1` records, but the loop stops before the last one.
- **Effect:** one fix lost at the end of every cruise whose last GGA follows its last ZDA. Here SP2424's 1min and control also end on that fix (P1).

### R3. A GGA repeating the last dated time, or at 00:00:00, is dropped

- **NavManager:** `parse_nav29` skips a GGA whose `hhmmss` equals `$dateBufferLast->hhmmss`.
- **Here:** kept; duplicates are QC's decision (`TIME_SEQUENCE`).
- **Evidence:** SP2503 (raw GNSS fileset 167484): its raw data starts at exactly 2025-02-13T00:00:00.00Z, and R2R's bestres lacks that fix; every other record matches (`tests/test_stats.py` data). Synthetic tests in `tests/test_gga_zda.py`.
- **Why (confirmed in code):** the check was meant to drop a duplicate across a buffer flush, but it only compares with the single last dated record. Before anything is dated, and after a ZDA arrives with nothing buffered, `$dateBufferLast->hhmmss` is unset; PHP's loose `==` treats null as equal to `0.0`, so a GGA at exactly 00:00:00 is dropped.
- **Effect:** occasional fixes lost, including any fix at exactly midnight after an empty buffer.

### R4. An equal GGA time of day advances the date

- **NavManager:** `parse_nav29`, ZDA flush: after the leading GGAs dated by the ZDA, a GGA keeps its predecessor's date only if its time is *later* (`>`), so an equal time moves to the next day. The end-of-input block uses `<` for "earlier", so there an equal time stays.
- **Here:** each GGA is dated from the ZDA either side of it in the stream that is closer in time of day, moving a day only across midnight; an equal time keeps its date. (A first version used the nearest ZDA by position in the stream; at a 13.5 h gap between files it misdated SP2503's first fix of its second file by a day, which the QA comparison caught.)
- **Evidence:** none in the reference filesets; synthetic tests.
- **Why (suspected):** an inconsistent comparison between two near-duplicate copies of the dating loop; the day-rollover test should have been "time decreased", not "time didn't increase".
- **Effect:** a receiver repeating a fix would put the repeat 24 h later, where QC sees it as out of sequence.

### R5. Sentences split across logger lines

- **NavManager:** `parse_nav29` (`include/parsers/nav29.php`, ~line 73) accepts a sentence without a checksum (`is_null($nmea->suppliedCheckSum)`) and takes the text from the line's last `$`; the rest of a split sentence, on the next line, has no `$` and is skipped.
- **Here:** a sentence without a complete checksum is joined with the following lines' text, after their logger timestamps, until it has one, and kept only if the checksum matches (`formats/gga_zda.py`). A sentence without a checksum must have every field of its kind (GGA 15, ZDA 7, `nmea.FIELDS`), so a truncated one is never read as a short sentence. A ZDA year outside 1980–2100 is skipped.
- **Evidence:** RR2406 (raw GNSS fileset 161075, Furuno GP170 on Revelle, 2024): the logger split 40 % of GGAs and 46 % of ZDAs across two timestamped lines, anywhere up to the checksum's last digit; rejoining recovers 99.97 %. We read 385,320 fixes; R2R's bestres has 266,841 records, 191,414 flagged: 120,694 with quality 0 (the missing fields) and 45,243 at 83°E (R6). R2R rates it R on five tests with impossible values (out of sequence 653,911 %, speeds to 14,456,754 m s-1); here it is Y, on one 47-minute gap (`tests/test_r2r_references.py`). Revelle's 2022–2024 GP170 reports (RR2404, RR2405, RR2407) show the same impossible values. Before this, a ZDA cut to `…,31,05,2` dated fixes in year 2 and overflowed the reader.
- **Why (confirmed):** the serial logger writes each read from the port as a line with its own timestamp, and a read can end mid-sentence. NavManager has no notion of a continuation line, and accepting checksum-less sentences, which some receivers send, lets the first half through.
- **Effect:** on affected cruises, R2R's products lose or misplace most fixes and its ratings measure the parsing, not the navigation.

### R6. A coordinate without its hemisphere letter

- **NavManager:** `NMEA0183_GGA::init` (`include/nmeatools.inc.php`, ~line 465) negates latitude only for `S` and longitude only for `W`; anything else, including an empty field, is north or east.
- **Here:** a coordinate needs `N`/`S` or `E`/`W`; without one it's missing, and QC flags the fix (`POSITION`).
- **Evidence:** RR2406: 45,243 of R2R's flagged records are at 83°E, mirror images of the ship's 83°W, from truncated GGAs (R5). Jumps between them give R2R's speeds of millions of m s-1.
- **Effect:** with R5, fixes on the wrong side of the globe.

### R7. RR2212's files out of date order

- **R2R:** R2R's bestres for RR2212 (fileset 151339) runs Nov 5, then Nov 4 and Nov 3, then Nov 6 and 7, so every Nov 3 and 4 record comes after Nov 5 and is flagged out of sequence: 121,321 records, rated R (50.08 %). R2R's catalog extent (32.6–33.8°N) leaves out those two days' transit from Moss Landing (36.4–37.8°N).
- **Here:** files are read in time order (`rawnav.read_fileset`): G on every test.
- **Why (suspected):** not NavManager master: `navdatalist` sorts the files by their first ZDA, and each RR2212 file's first ZDA is correct, which would give the right order. R2R's run must have used a file list made another way.
- **Evidence:** `tests/test_r2r_references.py`.
- **Effect:** R2R's out-of-sequence rating and its catalog extent are wrong for this cruise; the extent check here finds 100,828 fixes "outside" R2R's extent.

### R8. nav33: the 86,400th fix of each buffer is dropped

- **NavManager:** `parse_nav33()` (`include/parsers/nav33.php`, ~line 231) collects valid GGAs in a buffer of `$maxBuffer = 86400`. When it's full, it dates and prints entries 1 to `$maxBuffer - 1` and discards the buffer, so the last entry is never written.
- **Here:** the reader keeps every valid GGA (`formats/timestamped_gga.py`).
- **Evidence:** FKt230417: one fix missing after every 86,399 that R2R kept, 18 in all (`tests/test_r2r_references.py`).
- **Why (confirmed in code):** an off-by-one in the buffer's print loop; the other parsers of `navcopy.inc.php` with the same buffer may share it.
- **Effect:** one fix a day at 1 Hz; too few to change a rating.

### R9. nav33: the cruise's last fix is dropped

- **NavManager:** the flush after the last file (`include/parsers/nav33.php`, ~line 312) loops `for ($inx=1; $inx < $binx - 1; ...)`, which stops one short of the buffer's last entry.
- **Here:** every fix is kept.
- **Evidence:** FKt230417: R2R's bestres ends at 13:51:28.29Z; the raw files' last fix is 13:51:29.29Z.
- **Why (confirmed in code):** as R2 for the GGA + ZDA reader: the final loop stops one short.
- **Effect:** one record per cruise.

## Geodesy and speed/course (navtools, navsogcog)

### G1. Hand-written Vincenty

- **NavManager:** `vincenty()` (`include/navtools.inc.php`, ~line 61).
- **Here:** `pyproj.Geod` (Karney's algorithm), decided 2026-10-13 (`geodesy.py`).
- **Evidence:** on SP2613 the two differ by at most 5.4 µm per step, 1.1e-5 m s-1 in speed and 0.001° in course while under way; 8 of 56,924 speeds and ~4,300 courses (mostly at the dock) round differently. A vectorized port of NavManager's version matched every R2R speed, confirming the details below (`tests/test_geodesy.py`).
- **Why (confirmed):**
  - The semi-minor axis is rounded to 6356752.314 m while the flattening is exact, so the two constants disagree slightly.
  - The azimuth is computed after the loop from `$sinLambda`/`$cosLambda`, which were set at the *start* of the last iteration, before λ's final update. On steps of a few millimetres the loop stops after one pass, so the azimuth uses the uncorrected longitude difference: up to ~0.07° off on 2 mm steps.
  - `$iterLimit` is never decremented, so nearly antipodal points (where Vincenty doesn't converge) loop forever.
- **Effect:** last-digit differences only; physically negligible.

### G2. Speed and course from coordinates as written

- **NavManager:** navsogcog reads navqc's r2rnav text, so it computes from coordinates rounded to the receiver's precision.
- **Here:** full-precision coordinates.
- **Evidence:** SP2613: 41 speeds round differently with full precision, 8 with rounded coordinates (G1 accounts for those 8).
- **Why (confirmed):** the pipeline passes text files between stages.
- **Effect:** last-digit differences only.

### G3. navsogcog: full-buffer speeds use the previous segment's distance

- **NavManager:** `navsogcog()` (`include/navsogcog.inc.php`, ~line 189): in the full-buffer branch (86,400 records), `$sog = $distance / $delt` runs before `vincenty()` for the current pair (~line 194). The end-of-file branch (~lines 341–346) calls `vincenty()` first and is correct.
- **Here:** every speed is the current segment's distance over its elapsed time.
- **Evidence:** SP2424 (150,925 fixes): all 86,399 first-buffer speeds in R2R's bestres equal the previous segment's distance over the current time step; every later speed is correct. In the first buffer, 15,175 speeds differ from the correct value by more than 0.005 m s-1, 7,114 by more than 0.1, the largest by 7.38 m s-1 (`tests/test_geodesy.py`).
- **Why (confirmed):** two statements in the wrong order in one of two copies of the loop; the stale `$distance` is also undefined (treated as 0) for the very first segment. The full-buffer copy only runs on files over 86,400 records, so short test files would never show it.
- **Effect:** SOG lags the ship by one fix in the first 86,399 records (about a day at 1 Hz) of most multi-day cruises. A position spike's speed lands on the following fix. Where the time step changes, one step's distance is divided by another's time. Course is unaffected. Not yet reported upstream.

### G4. Division by zero for equal times

- **NavManager:** navsogcog's end-of-file branch divides by `$delt` without checking for 0; the full-buffer branch sets 0.
- **Here:** NaN.
- **Why (confirmed):** missing guard; in practice navqc flags equal times first.
- **Effect:** none on published products.

## Quality control (navqc)

### Q1. The speed rule is commented out

- **NavManager:** `navqc()` (`include/navqc.inc.php`, ~line 565): the rule "two successive speeds over the limit: flag the fix between" is commented out with `//`.
- **Here:** no speed rule, by decision (2026-10-13): a vessel's top speed varies and currents change speed over ground, so acceleration is the better indicator. `max_speed` is used only by the QA test.
- **Why (suspected):** a fixed speed limit flagged good fixes on fast vessels or with GNSS noise, and was disabled rather than tuned.
- **Effect:** R2R never flags a fix for speed.

### Q2. The main-loop acceleration check is inverted

- **NavManager:** `navqc()`, main buffer loop (~line 548): `if (!$epochOK[$inx])`, commented "Flipped check - Webb Pinner" and "Skip flagged positions": speeds are computed only *from* fixes already flagged. The end-of-file loop (~line 1014) uses `if ($epochOK[$inx])`.
- **Here:** every good fix is checked (`qc.py`, Q3).
- **Evidence:** SP2424: R2R flags nothing; a sound check finds 33 fixes in six 2–4 s excursions of 1–2 m (sideways swings no ship can make, two of them at 3.6 m s-1), plus three steps (`tests/test_qc.py`).
- **Why (suspected):** the 2016 change likely stopped the check from flagging GNSS noise and DGPS steps on high-rate receivers, which a pair-by-pair check at 1 m s-2 does; as a side effect it disabled the check on clean data. The end-of-file copy wasn't changed, so the last part of each file is checked differently.
- **Effect:** acceleration flags are rare and depend on other flags; position errors of a few metres stay in bestres, 1min and control.

### Q3. Speed magnitudes, nearest-fix flagging, at most two iterations

- **NavManager:** acceleration between successive scalar speeds; the fix nearest in time to an excessive acceleration is flagged; the loop runs while `$iter < $maxIter` from 1 with `$maxIter = 3`.
- **Here:** acceleration at each fix from velocity *vectors* over a 2 s time base; each peak is resolved against the track either side: an excursion that comes back is flagged, a step that stays is reported; repeated until nothing new is flagged.
- **Why (confirmed for the mechanics):** scalar speeds barely change when a fix jumps sideways, so lateral errors are missed; the nearest fix to the acceleration's time is often a good neighbour of the bad one; `$iter < 3` from 1 allows two passes, though the comment says up to 3.
- **Effect:** missed sideways errors; good neighbours flagged instead of the bad fix.

### Q4. 3,600-record buffers

- **NavManager:** navqc reads 3,600 records at a time; speed and acceleration aren't evaluated across buffer boundaries, and the end of the file goes through a near-duplicate copy of the loop.
- **Here:** whole series.
- **Why (suspected):** memory limits of the PHP 5 era.
- **Effect:** whether a fix is flagged can depend on where a buffer boundary falls.

### Q5. Acceleration limit from position resolution

- **NavManager:** `navqc()`, ~line 245: if one record's position resolution is 1.11 m or coarser, the acceleration limit becomes 3 × that resolution, a length in metres used as m s-2.
- **Here:** the limit is raised only when quantization alone would exceed it over the time base (4 × resolution / time base²), and the QC notes say so.
- **Why (confirmed in code comments):** a workaround for one vessel (Endeavor) with low-precision coordinates.
- **Effect:** silent; depends on one record.

### Q6. Null island only at midnight

- **NavManager:** a fix is rejected as 0,0 only if it is also at 00:00:00.
- **Here:** 0,0 at any time.
- **Why (suspected):** the case seen was a receiver's empty default record, which has both.
- **Effect:** 0,0 fixes at other times would stay in the products (none in the references).

### Q7. Stale-repeat decisions near the threshold

- **NavManager and here:** a repeated position is flagged while the median of the 10 preceding speeds exceeds 0.25 m s-1.
- **Evidence:** SP1925 (45,723 fixes, 1 Hz, 6-decimal positions): R2R flags 303, this package 219, 209 the same. Every difference is a repeated position while the ship barely moves (median of the preceding speeds 0.16–0.32 m s-1). NavManager's median skips the latest speed (`array_slice($speedHori, $vnx-11, 10)`) and restarts per buffer; neither changes the count (`tests/test_geocsv.py`).
- **Why (suspected):** NavManager's inverted acceleration loop (Q2) computes speeds from the flagged repeats and flags further repeats next to them.
- **Effect:** R2R flags more repeated positions at low speed.

### Q8. Nothing catches a fix the ship couldn't have reached

- **NavManager:** apart from 0,0 at midnight (Q6), only the speed rule (commented out, Q1) and the acceleration check (inverted, Q2) look at where a fix is relative to the others. A receiver that holds a wrong position for a while, or jumps to near 0,0, passes navqc.
- **Here:** `UNREACHABLE` (`qc.py`): the good fixes are split into segments wherever a fix can't be reached from the one before at `max_reach_speed` (25 m s-1) plus `reach_tolerance` (250 m). The track is the chain of segments, each reachable in the same way from the end of the one before, with the most fixes (a longest path, by dynamic programming); the segments off it are flagged whole. The acceleration rule alone would call the start and end of such a stretch two steps, flag nothing and report them.
- **Evidence:** FKt230417 (Falkor (too)'s Applanix POS/MV, fileset 157454, rated G): at 2023-04-27 08:57:19.5 the longitude jumps about 650 m west (latitude unbroken), drifts there for 4.7 min, and comes back at 09:02:01.5–02.5. Those 283 fixes are flagged here; R2R flags none of them. The first version of the rule walked from the largest segment and trusted each next one within reach: the fix at 09:02:01.5 is within reach of the track before the jump (2 km in 283 s) but not of the track after it, so the walk rejected the rest of the cruise, 794,663 fixes. Hence the longest chain (`tests/test_qc.py`, with FKt230417's pattern). None of the seven earlier references has such a fix: the largest speed between successive good fixes is 4.8–8.1 m s-1, except in SP2220, whose degraded fixes jump 12–77 m in 0.5 s (up to 154 m s-1); the tolerance leaves those to the acceleration rule, so they flag nothing new. Synthetic tracks show the rule: a 3-minute stretch 50 km off, a jump near 0,0, a bad first fix.
- **Why (suspected):** NavManager produced post-cruise products, where an analyst could review the track; the OpenVDM hook sets OpenVDM's cruise extent from the fixes that pass QC, unattended at sea (#1).
- **Effect:** such fixes would be in R2R's bestres, 1min and control as good, and stretch any extent computed from them.

## Products (navsample, NavControl)

### P1. 1min sampling

- **NavManager:** `navsample()` (`include/navsample.inc.php`): for each whole minute, the record exactly on it, else the nearer of the records either side, if within half the interval.
- **Here:** for each whole multiple of the interval, the nearest good record within half an interval (`products.py`).
- **Evidence:** SP2613's 1min (475 records) equals R2R's; SP2424's equals R2R's but for its final fix (R2), which is the record nearest 03:17:00 (`tests/test_products.py`).
- **Why (confirmed in code):**
  - Nodes are always whole minutes (`gmmktime($hour, $minute, 0, …)`), whatever the interval.
  - The window is ≤ `intval($interval/2) - 1` after a node but ≤ `intval($interval/2)` before it.
  - The buffer is a 3,600-record ring (`$binx = 1` on wrap), so at each wrap the previous record is lost and a node bracketed across the wrap gets no record.
  - At the start, the undefined previous time counts as 0, so a node just before the first fix is filled; at the end no record follows, so a node just after the last fix never is.
- **Effect:** occasional missing 1min records; the interval option only works for minutes.

### P2. Control-point tolerance

- **NavManager:** `include/NavControl/navsimplifier.py` (recursive Douglas–Peucker, planar on lon/lat) is committed with a 0.1° tolerance; the Java version uses 0.01°.
- **Here:** shapely's `simplify` at 0.01°, on unwrapped longitudes.
- **Evidence:** R2R's SP2613 control equals the Python simplifier at 0.01° (5 points; 0.1° gives 3) and shapely's result.
- **Why (suspected):** R2R's production copy of the script has a locally changed tolerance.
- **Effect:** none on R2R's products; the repository's script doesn't reproduce them.

## R2R's published GeoCSV (R2R's pipeline, not in NavManager)

### F1. Header text and number formats

- **R2R:** bestres swaps the speed and course long names; the SOG unit is `meter\second` in bestres and `meter/second` in 1min; units say `degree_east` where R2R's other GeoCSV and the GeoCSV spec use `degrees_east`; numbers are printed PHP-style (`7.76` for `7.760`, `5` for `5.0`); control coordinates use Python's `repr` (`32.702744442`).
- **Here:** the same structure with standardized CF/UDUNITS units, descriptions and fixed decimals (`quantities.py`, #9).
- **Why (suspected):** the GeoCSV writer was added in R2R's catalog pipeline on top of NavManager's r2rnav output; NavManager has no GeoCSV writer (its `geocsv` branch only changes delimiters).
- **Effect:** text only; values parse the same.

### F2. Flagged records

- **R2R:** a flagged bestres record is `#` + time through antenna height, without speed and course (SP1925).
- **Here:** the same (#10).

## Documentation

### D1. Documents that disagree with the code or data

- `bin/navformat.php` labels `metoc_nav` "USAP Navigation (OpenRVDAS)"; it was added for Atlantic Explorer's METOC system (commit 776878e, 2020-09-01; R2R format 100232).
- `doc/fileformat/format-nav23.txt` is headed "nav14" and reuses nav14's unsigned sample.
- `format-UNIXD.txt` and `format-PYRTM.txt` call a decimal day of year a "decimal year".
- The generic GLL and ZDA examples have wrong checksums; the nav10 sample contains a shell prompt.
- `format-nav29.txt` describes only the Unix-time layout; R2R's nav29 files are also ISO-timestamped, with a comma or a space.
- R2R's processing description says 1min takes "the first valid record of each minute"; navsample takes the nearest record (P1).
- The GeoCSV format document `r2rnav_geocsv.txt` describes tab-separated r2rnav, not GeoCSV.
- **Why (suspected):** documents written once and not updated as the code and vessels changed.
- **Here:** the format definitions in `formats/definitions/` are written from the code and real data (#2, #4, #5).

## QA statistics (navqa)

Compared with R2R's QA 2.0 reports for SP2613, SP2424 and SP2503 (`tests/test_stats.py`):
completeness, longest gap, out-of-sequence, bad-quality and unreasonable-speed percentages, and
the NSV, HDOP, altitude and speed ranges equal R2R's at R2R's precision on all three. Only the
acceleration results differ (A5).

### A1. Issues found reading navqa

- `count()` is called on a scalar mode value (`navqa.inc.php`, ~line 375), a `TypeError` on PHP ≥ 8.
- `navqa.php` takes the "ports" from the first and last records of the file it assesses, so distance from port is always about 0, and it prints the start distance as the end distance.
- The rating thresholds are written twice: in the rating `switch` and as literal bound values (`xmltools2.inc.php`).
- **Status (#27):** the G/Y bounds are stated once (`integrations/r2r/rules.py`) and written to the XML from the same values. There's no distance-from-port statistic here; the ports in the report come from R2R's catalog (the cruise TOML).

### A2. Modal interval

- **NavManager:** `navqa()` first pass (~line 240): steps between consecutive records (forward, at most 30 days), truncated to milliseconds with `intval(1e3 * …)`; the mode by `array_mode`, taking the first value when several tie.
- **Here:** steps rounded to the millisecond; a tie goes to the shorter step (`stats.modal_interval`).
- **Why (confirmed):** `intval` truncates, so a step of 0.4999 s counts as 499 ms; which tied value comes first depends on the order values were first seen.
- **Effect:** none in the references (0.5 s on all three).

### A3. Completeness

- **NavManager:** completeness = "countable epochs" / ⌊1 + duration / modal interval⌋. Countable epochs walk the steps, counting each step at least one interval long and accumulating shorter ones until they reach it. In the full-buffer loop the comparison is `intval($delt)/1e3 >= $epochInterval` (milliseconds over 1,000 against seconds); in the end-of-file loop it is `intval($delt[$inx]) >= $stats->epochInterval` (milliseconds against seconds), so there every step counts, however short.
- **Here:** the share of interval slots, from the first in-sequence record to the last, that hold a record; at most 100 %.
- **Evidence:** equal to R2R's on SP2613 (99.98 %), SP2424 (100.00 %) and SP2503 (47.34 %, rated R). SP2220 (150139): 6.02 % here, 33.99 % in R2R's report, both R: its receiver logged quality-0 GGAs without a position for most of the cruise, and R2R's count includes them (338,581 records in 978,000 half-second epochs is 34.6 %); here only fixes count. R2R's reports with misparsed data (R5) give completeness above 100 % (RR2404 276 %, RR2212 149.93 %).
- **Why (confirmed in code):** the two copies of the loop scale the step differently; the end-of-file copy is missing `/1e3`.
- **Effect:** where the last buffer has records closer together than the interval, completeness counts too many epochs and can exceed 100 % (which the R2R cookbook says to discard, but the XML still rates).

### A4. Bad quality indicator

- **NavManager:** a record is bad unless its quality is 1, 2, 4 or 5 (`navqa.inc.php`, ~line 700); a missing quality (`"NAN"`) also counts as bad.
- **Here:** quality outside QC's valid range, 1 to 5, over the records within the dates that report one, with or without a position (a receiver without a fix reports quality 0 and no position); no result when none does. (Until #26, records without a position were left out, which hid exactly these: SP2220 was 0.01 %.)
- **Evidence:** SP2220: 82.61 % here (279,696 of 338,581), rated R; R2R also R, at an impossible 164,557.65 %.
- **Why (suspected):** written separately from navqc's `valid_gga_fix`, which accepts 1–5; quality 3 (PPS, a valid fix) was left out.
- **Missing indicators:** navqa counts a missing quality (`"NAN"`) as bad, but drops the count (reported as 0.00 %, G) when every bad record is a missing one (~line 1521). So a fileset without any quality indicator is G both here and in NavManager; one where some records lack it counts them as bad there, and leaves them out here.
- **Effect:** PPS fixes, rare on research vessels, would count as bad (none in the references).

### A5. Speeds and accelerations

- **NavManager:** speeds between every pair of consecutive records (scalar, its Vincenty, G1), accelerations as the change of speed between consecutive pairs over the time between their midpoints, within 3,600-record buffers; unreasonable above `MAX_SPEED` and `MAX_ACCEL` (the absolute value, so slowing counts too).
- **Here:** speeds the same way, which match R2R's percentages and range; accelerations are QC's (velocity vectors over a 2 s time base, #7), so QA and QC agree; the range is the signed change of speed over the same time base.
- **Evidence:** SP2424: R2R 0.16 % unreasonable accelerations and −14.257 to 14.766 m s-2; ours 0.01 % and about −0.9 to 1.0. SP2503: 0.04 % against 0.01 %. SP2613: 0.00 % both, R2R's range −0.898 to 0.872, ours about ±0.5.
- **Why (confirmed):** at 2 Hz, centimetres of GNSS noise between 0.5 s fixes are metres per second squared of apparent acceleration; navqc's own check is inverted (Q2), so the noise never shows as flags, only in the QA percentage.
- **Effect:** R2R's acceleration test and range measure GNSS noise on high-rate receivers.

### A6. Denominators

- **NavManager:** out-of-sequence and bad-quality percentages are over "actual epochs" (the in-sequence steps, records − 1); unreasonable speeds over actual epochs − 1; accelerations over actual epochs − 2 (`xmltools2.inc.php`).
- **Here:** out of sequence over the usable records; bad quality over the records reporting a quality; speeds over the speeds; accelerations over the accelerations.
- **Effect:** negligible for clean filesets. With misparsed data (R5) R2R's percentages exceed 100 % by orders of magnitude (RR2406: out of sequence 653,911.11 %, speeds 117,790.57 %), so the counts and denominators must come from different record sets there.

### A7. Ratings (xmltools2)

- **NavManager:** `xml_write2` rates each value with the bounds written as literals in the rating `switch` and again as bound elements. Percentages are `sprintf("%0.2f")` before rating; the longest gap is written at the receiver's time precision (`timeNROD` decimals, ~line 1222) and rated as written. Percentages divide by "actual epochs", which is zero for an empty fileset.
- **Here:** every result is rounded to its quantity's decimals (percentages 2, durations 3) and rated as written, so the report's value and its rating always agree. A result that can't be computed (no usable fixes) has no value (`xsi:nil="true"`) and is rated R.
- **Evidence:** SP2613 (G), SP2503 (R: completeness 47.34 % R, gap 48,625.50 s Y) and SP2424 (G): every rating, the overall rating and the infos equal R2R's (`tests/test_r2r_xml.py`).
- **Why (confirmed in code):** the thresholds were written twice by hand; the gap keeps the receiver's precision because it's a time.
- **Effect:** a gap between a bound and the next centisecond (900.004 s) is G in NavManager, Y here.

## R2R's QA 2.0 XML

### X1. Report text and structure

- **R2R:** the nav report (`<CRUISE>_<FILESET>_qa.2.0.<timestamp>.xml`) has `<r2r:id>` twice in `filesetinfo/cruise` (before `cname` and after `operator_id`); bounds are PHP integers (`95`, `86400`); units are `Percent`, `Count`, `Dimensionless`, `m/s`, `m/s^2`; ranges are trimmed (`1` for an HDOP of 1.0); the overall rating's description has a double space ("at least  one test RED"). Test descriptions describe navqa's definitions. SP2424's report (2024) has an empty `cruise` and `fileset`; SP2613's and SP2503's (2026) are filled in.
- **Here:** the same elements in the same order, with `<r2r:id>` once; values and units through `quantities.py` (`95.00` `percent`, `900.000` `s`, `1` for counts and HDOP, `m s-1`, `m s-2`); missing values `xsi:nil="true"`; our own test descriptions, saying what is measured here; the provenance names nav-qa-processing and its version; the log lists the run's errors (manifest, reader). `filesetinfo` comes from the cruise TOML (`configs/`).
- **Evidence:** `tests/test_r2r_xml.py`: SP2613's and SP2503's element paths equal R2R's but for the second `<r2r:id>`; SP2613's fileset, manifest, references and cruise elements equal R2R's.
- **Why (suspected):** the report is assembled from a template filled from R2R's database (`r2r_catalog_qa_bootstrap.php`, not in NavManager), with the certificate from `xml_write2`; the duplicate looks like a template slip.
- **Effect:** text only; values parse the same.

## Not NavManager: R2R's catalog and the data

- R2R files Healy's 2016–2025 GNSS logs under format 100010 ("raw NMEA: GGA + ZDA", nav1), though most have nav29's timestamped layouts.
- NBP2410's receiver dates are 1024 weeks early (GPS week-number rollover): its ZDA and RMC say 2005-03-13 on 2024-10-27. A reader that dates fixes from RMC (NavManager's nav30/nav32 path) would put the cruise in 2005; not yet checked against R2R's products.
- Point Sur's UDAS files drop the Ashtech differential column from 2011, shifting later columns.
- R2R's catalog API (`api/cruise`) gives the cruise extent rounded, to 10 decimals (SP2613) or 6 (RR2406), so the fixes on its edges can fall just outside it (SP2613: 3 fixes, by 3e-11°; RR2406: 1, by 1.7e-7°); its QA reports write it to 7. The core's "Within the expected area" check allows 1e-6° (about 11 cm, `stats.EXTENT_TOLERANCE`).
- The catalog API has no port coordinates, country or state, no format alias (`starc_raw`) and no catalog dates (`date_issued`, `date_modified`, the fileset's `date_created`); R2R's QA reports do. `r2r config-from-catalog` takes them from the report (#28). R2R's 2024 reports (SP2424) have an empty `filesetinfo`, so for those cruises they stay empty.
- `info/vessel/<ICES>` gives a vessel's `max_speed` (Sproul: 5.4 m/s); NavManager uses one `MAX_SPEED` (8.7 m/s) for every vessel.
