"""The nav22 reader: GGA dated from the file name's YYYYMMDDhhmmss (#90)."""

from datetime import date

from nav_qa_processing import formats, rawnav
from nav_qa_processing.formats import file_stamp
from nav_qa_processing.formats.nmea import checksum


def gga(hhmmss: str) -> str:
    body = f"GPGGA,{hhmmss},3408.8635,N,11912.5066,W,1,08,01.2,-00003,M,,M,,"
    return f"${body}*{checksum(body):02X}"


def dated(name: str, lines: list[str]):
    result = formats.get("nav22").reader.read([(name, lines)])
    return [(f.day, f.gga.time.seconds) for f in result.fixes], result


def test_name_time():
    assert file_stamp.name_time("data/ashtech_20120918225733.raw") == (
        date(2012, 9, 18),
        (22 * 3600 + 57 * 60 + 33) * 10**9,
    )
    assert file_stamp.name_time("dir_2099/gps_20120918T225733.raw")[0] == date(2012, 9, 18)
    assert file_stamp.name_time("ashtech.raw") is None
    assert file_stamp.name_time("ashtech_20121318225733.raw") is None  # no month 13


def test_a_file_runs_past_midnight():
    lines = ["$PASHR,ATT,255472.0,091.94,+001.50,-001.77,0.0032,0.0412,0*2A"]
    lines += [gga("235959.00"), gga("000000.00"), gga("000001.00")]
    fixes, result = dated("ashtech_20120918225819.raw", lines)
    assert fixes == [
        (date(2012, 9, 18), 86399.0),
        (date(2012, 9, 19), 0.0),
        (date(2012, 9, 19), 1.0),
    ]
    assert result.errors == [] and result.rejected_checksums == {}


def test_data_before_the_name_time_takes_the_nearer_date():
    """NH1209's ashtech_20120924000000.raw holds 09-23 18:36 to 21:00, which R2R's primary GPS
    confirms; NavManager gives it the name's date, 09-24 (R13)
    """
    fixes, _ = dated("ashtech_20120924000000.raw", [gga("183603.00"), gga("210006.00")])
    assert fixes == [(date(2012, 9, 23), 66963.0), (date(2012, 9, 23), 75606.0)]


def test_a_file_name_without_a_date(tmp_path):
    path = tmp_path / "ashtech.raw"
    path.write_text(gga("120000.00") + "\n")
    nav = rawnav.read_fileset([path], "nav22")
    assert nav.unread_files == {"ashtech.raw": "no date in the file name"}
    fixes, result = dated("ashtech.raw", [gga("120000.00")])
    assert fixes == [] and result.skipped == {"ashtech.raw": {file_stamp.NO_NAME_STAMP: 1}}
