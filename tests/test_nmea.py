import pytest

from nav_qa_processing.formats.nmea import GGA, ZDA, checksum, parse_sentence

SP_GGA = "$INGGA,192045.68,4437.541871,N,12402.686139,W,2,07,1.3,-4.31,M,-22.07,M,2.0,0135*4B"


def test_checksum_and_prefix():
    s = parse_sentence("1531596045.718 " + SP_GGA)
    assert s.kind == "GGA" and s.checksum == "4b"
    assert parse_sentence(SP_GGA.replace("*4B", "*4C")) is None
    assert parse_sentence("no sentence here") is None


def test_partial_sentence_uses_last():
    s = parse_sentence("$INGGA,1920" + SP_GGA)
    assert s.fields[1] == "192045.68"


def test_no_checksum_accepted():
    assert parse_sentence(SP_GGA.split("*")[0]).checksum is None


def test_navmanager_rejects_checksum_below_0x10():
    body = next(b for c in range(10_000) if checksum(b := f"GPTXT,{c},A") < 0x10)
    line = f"${body}*{checksum(body):02X}"
    assert parse_sentence(line) is None
    assert parse_sentence(line, navmanager_checksum=False) is not None


def test_gga():
    g = GGA.from_fields(parse_sentence(SP_GGA).fields)
    assert g.time.seconds == pytest.approx(19 * 3600 + 20 * 60 + 45.68)
    assert g.time.decimals == 2
    assert g.lat == pytest.approx(44 + 37.541871 / 60)
    assert g.lon == pytest.approx(-(124 + 2.686139 / 60))
    assert (g.lat_decimals, g.lon_decimals) == (8, 8)
    assert (g.quality, g.nsat, g.hdop, g.alt, g.alt_decimals) == (2, 7, 1.3, -4.31, 2)


def test_gga_empty_fields_are_missing():
    g = GGA.from_fields(["$GPGGA", "123519", "", "", "", "", "0", "00", "", "", "M"])
    assert g.lat is None and g.lon is None and g.hdop is None and g.quality == 0


def test_zda():
    z = ZDA.from_fields(parse_sentence("$INZDA,192045.68,14,07,2018,,*7A").fields)
    assert (z.year, z.month, z.day) == (2018, 7, 14)
    assert z.time.decimals == 2
