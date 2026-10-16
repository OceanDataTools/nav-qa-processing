import pytest

from nav_qa_processing.formats.nmea import GGA, ZDA, checksum, has_checksum, parse_sentence

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


def test_checksum_0a_to_0f_is_accepted():
    """NavManager compares unpadded hex loosely and rejects these; they're correct (#13, R1)"""
    body = next(b for c in range(10_000) if 0x0A <= checksum(b := f"GPTXT,{c},A") < 0x10)
    assert parse_sentence(f"${body}*{checksum(body):02X}") is not None
    assert parse_sentence(f"${body}*{checksum(body):02x}") is not None  # lowercase too
    assert parse_sentence(f"${body}*{checksum(body):X}") is not None  # one digit, nonstandard
    assert parse_sentence(f"${body}*{checksum(body) + 1:02X}") is None
    assert parse_sentence(f"${body}*zz") is None


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


def test_a_sentence_cut_short_without_a_checksum_is_rejected():
    """RR2406's logger splits sentences across lines; the first part isn't a sentence"""
    assert parse_sentence("$GPGGA,235959.00,2558.9137,N,08312.6472,W,1,08,0.8,11.8") is None
    assert parse_sentence("$GPZDA,023928.00,31,05,2") is None
    assert parse_sentence("$GPZDA,023928.00,31,05,2024,00,00") is not None  # complete
    assert parse_sentence(SP_GGA.split("*")[0] + "*") is None  # cut inside the checksum


def test_has_checksum():
    assert has_checksum("$GPZDA,000000.00,04,11,2022,00,00*60")
    assert not has_checksum("$GPZDA,000000.00,04,11,2022,00,00*6")
    assert not has_checksum("$GPZDA,000000.00,04,11,2022,00,00")


def test_coordinates_need_a_hemisphere():
    """NavManager reads a missing hemisphere as N or E: RR2406's truncated fixes at 83°E"""
    g = GGA.from_fields(["$GPGGA", "123519", "2558.9137", "", "08312.6472", "", "1", "08"])
    assert g.lat is None and g.lon is None
