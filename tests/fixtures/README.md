# Test fixtures

R2R reference output for cruise **SP2613** (R/V Sproul, 2026-07-09), fetched 2026-10-05. The raw
GNSS data is fileset 169851 (Trimble BX992, `starc_raw`, nav29-style ISO timestamp + NMEA).
These files are unmodified unless noted. The checksums match R2R's `manifest-md5.txt`.

| File | Source |
|---|---|
| `SP2613_169851_r2r_qa.2.0.xml` | R2R QA 2.0 report for fileset 169851 (rated G), `service.rvdata.us/data/cruise/SP2613/fileset/169851/qa/SP2613_169851_qa.2.0.2026-07-31T20:05:56Z.xml` |
| `SP2613_1min.geoCSV` | R2R product fileset 620146 (`r2rnav_geocsv`), md5 `1b064f07de049292a5c30865cbfa2651` |
| `SP2613_control.geoCSV` | R2R product fileset 620146, md5 `9b9be3d90691cfaa646d8e4ec5b2d300` |
| `SP2613_bestres_head.geoCSV` | The first 40 lines of `SP2613_bestres.geoCSV` (product fileset 620146): the header block and 23 records. The full file (4.6 MB, 56,925 records) is too large to commit |
| `SP1925_bestres_head.geoCSV` | The first 60 lines of `SP1925_bestres.geoCSV` (R2R product fileset 609448, raw GNSS fileset 134952, Furuno GP170, nav29 Unix-time layout, QA rated R), md5 of the full file `f153beb7aa98978eedca25652da7b037`: the header block and 43 records, 3 of them flagged. R2R writes a flagged record as `#` + time through antenna height, without speed and course |

This project standardizes descriptions, unit names and coordinate formats (see
`docs/architecture.md`). Compare these files with our output by parsed values, not byte for byte.

Larger references are fetched into `data/` (gitignored) by the tests' skip messages:

| Cruise | Raw fileset | Why |
|---|---|---|
| SP2613 | 169851 (product 620146) | the files above, in full; one day, 56,925 records, rated G |
| SP1925 | 134952 (product 609448) | 45,723 records, 303 flagged by R2R (stale repeats while barely moving): the first released cruise with flagged fixes found; 1 Hz, 6-decimal positions; no QA 2.0 report |
| SP2503 | 167484 (product 619357) | QA rated R: completeness 47.34 %, longest gap 48,625.5 s (Y); 87,431 fixes over three files, the first of them the previous cruise's; its first fix is at exactly 00:00:00, which R2R's bestres lacks (register R3) |
| SP2424 | 161900 (product 615579) | 150,924 R2R records (we read 150,925: NavManager drops the final fix), past navsogcog's 86,400-record buffer: R2R's first-buffer speeds carry its bug; nav29 with an ISO timestamp and a space; rated G, no flagged fixes |
| RR2406 | 161075 (product 615546) | Revelle's GP170, 2024: the logger split 40 % of GGAs across lines (register R5, R6). R2R: R on five tests with impossible values; here Y (one 47-minute gap). Fails completeness (Y), gap, out of sequence, speeds and accelerations in R2R's report |
| RR2212 | 151339 (product 613418) | R2R processed its files out of date order (R7): R on out of sequence (50.08 %); here G. R2R's report has no fileset info |
| SP2220 | 150139 (product 613340) | Ashtech ABX-Two without a fix for most of the cruise (empty and quality-0 GGAs): R on bad quality (82.61 % here), completeness and gap, as R2R rates it |
| FKt230417 | 157454 (product 614737) | Falkor (too)'s Applanix POS/MV, nav33 (ISO timestamp + GGA), an INS fileset: G for both. R2R lacks the GGAs with checksum 0A–0F (2.9 %, R1), every 86,400th fix (R8) and the last (R9); QC flags 283 fixes 650 m off for 4.7 min (Q8) |
| RR1802 | 132277 (product 608479) | Revelle's Furuno GP-150, nav01 (bare NMEA): 28,527 records equal to R2R's, record for record; R2R flags 292 stale repeats at the dock (Q7). No QA 2.0 report |
| EN735 | 165419 (product 616264) | Endeavor's NOAA SCS, nav02 (logger date and time + GGA): G for both. R2R lacks every 86,400th fix (R8: 3), the last (R9) and the earlier of 5 repeated times (R10) |
| SKQ202516S | 165743 (product 616278) | Sikuliaq's Seapath 380+, nav24 (logger ID, ISO timestamp, NMEA; R2R format 100103): G for both, the same results and 62 flags; R2R lacks the final fix (R2) |
| HLY14TF | 114739 (product 603305) | Healy's Seapath 330+, nav25 (logger ID, ISO timestamp, NMEA): 349,199 records equal to R2R's, record for record, none flagged. No QA 2.0 report |
| KN220-02 | 114491 (product 603657) | Knorr's C-Nav 2000, nav20: 227,436 records equal to R2R's; R2R flags 2 more (Q3). Alongside on Charleston's Cooper River for 5 h. No QA 2.0 report |
| HLY08TD | 110937 (product 603255) | Healy's POS/MV, nav12 with ZDA, gzipped files: 363,486 records equal to R2R's but 2 empty HDOPs R2R writes as 0 (R11). No QA 2.0 report |
| MGL1509 | 122372 (product 603725) | Langseth's C-Nav 3050, nav12 without ZDA (dated by the logger's year:day-of-year): 601,027 records equal to R2R's; Y (a 54-minute gap). No QA 2.0 report |
| W1108A | 105073 (product 604751) | Wecoma's Ashtech ADU5, nav15 (DATA, ISO timestamp, quoted GGA): 50,678 records equal to R2R's within R2R's window, the Pacific day (08:00Z, from its 2012 QA report). No QA 2.0 report |
| W1109B | 105076 (product 604754) | nav15 with unquoted sentences (September 2011): the same 68,302 records as R2R's, but R2R dates 1,017 of them a day early (R12). No QA 2.0 report |
| NH1209 | 107219 (product 608147) | New Horizon's Ashtech ADU2, nav22 (no date but the file name's): R2R made no products from it, but its fixes lie within meters of R2R's primary-GPS bestres at the same times; one file named 09-24 holds 09-23's fixes (R13) |

Between them, every one of R2R's six tests has a reference that R2R rates Y or R
(`tests/test_r2r_references.py`, #26).
