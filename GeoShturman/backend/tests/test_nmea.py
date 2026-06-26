from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.nmea import build_gpgga_sentence, compute_nmea_checksum, parse_gpgga_sentence


def test_nmea_checksum():
    body = "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,"
    assert compute_nmea_checksum(body) == "47"


def test_nmea_parse_valid_gga():
    sentence = build_gpgga_sentence(12 * 3600 + 35 * 60 + 19.111, 545.4)
    parsed = parse_gpgga_sentence(sentence)

    assert parsed.parsed_ok
    assert parsed.checksum_ok
    assert parsed.altitude_m == 545.4
    assert parsed.timestamp_s == 12 * 3600 + 35 * 60 + 19.111


def test_nmea_parse_broken_line():
    parsed = parse_gpgga_sentence("not-nmea")

    assert not parsed.parsed_ok
    assert parsed.error is not None
