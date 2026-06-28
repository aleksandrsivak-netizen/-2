import pytest

from tercom_uav.nmea import NMEAError, compute_checksum, generate_gpgga, parse_gpgga


def test_checksum_nmea() -> None:
    payload = "GPGGA,120000.00,,,,,1,08,1.0,123.45,M,0.0,M,,"
    assert compute_checksum(payload) == generate_gpgga(123.45, 0.0).split("*")[1]


def test_parse_gpgga_radio_altitude() -> None:
    line = generate_gpgga(987.65, 3.5)
    record = parse_gpgga(line)
    assert record.checksum_valid is True
    assert record.radio_alt_m == pytest.approx(987.65)
    assert record.utc_seconds == pytest.approx(12 * 3600 + 3.5)
    assert record.quality == 1
    assert record.satellites == 8


def test_parse_gpgga_gps_quality_fields() -> None:
    line = generate_gpgga(
        987.65,
        3.5,
        lat_deg=45.25,
        lon_deg=39.5,
        satellites=11,
        hdop=0.8,
    )
    record = parse_gpgga(line)

    assert record.lat_deg == pytest.approx(45.25)
    assert record.lon_deg == pytest.approx(39.5)
    assert record.hdop == pytest.approx(0.8)
    assert record.satellites == 11


def test_invalid_checksum_raises() -> None:
    line = generate_gpgga(100.0, 0.0)
    bad = line[:-2] + "00"
    with pytest.raises(NMEAError):
        parse_gpgga(bad)
