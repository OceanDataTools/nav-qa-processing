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

This project standardizes descriptions, unit names and coordinate formats (see
`docs/architecture.md`). Compare these files with our output by parsed values, not byte for byte.

Larger references are fetched into `data/` (gitignored) by the tests' skip messages:

| Cruise | Raw fileset | Why |
|---|---|---|
| SP2613 | 169851 (product 620146) | the files above, in full; one day, 56,925 records, rated G |
| SP2424 | 161900 (product 615579) | 150,924 R2R records (we read 150,925: NavManager drops the final fix), past navsogcog's 86,400-record buffer: R2R's first-buffer speeds carry its bug; nav29 with an ISO timestamp and a space; rated G, no flagged fixes |
