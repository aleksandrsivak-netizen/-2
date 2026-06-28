from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_valid_nmea_line_is_parsed() -> None:
    response = client.post(
        "/api/nmea/parse",
        json={"nmea_text": "$GPGGA,123519.111,,,,,,,,545.4,M,46.9,M,,*47"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["count"] == 1
    assert data["valid_count"] == 1
    assert data["measurements"][0]["radio_altitude_agl_m"] == 545.4
    assert data["measurements"][0]["checksum_valid"] is False
    assert "checksum mismatch" in data["measurements"][0]["warning"]


def test_invalid_nmea_line_does_not_break_endpoint() -> None:
    response = client.post("/api/nmea/parse", json={"nmea_text": "not-a-sentence"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["count"] == 1
    assert data["valid_count"] == 0
    assert data["invalid_count"] == 1


def test_malformed_checksum_is_invalid() -> None:
    response = client.post(
        "/api/nmea/parse",
        json={"nmea_text": "$GPGGA,123519.111,,,,,,,,545.4,M,46.9,M,,*XX"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid_count"] == 0
    assert data["measurements"][0]["error"] == "malformed checksum"


def test_navigation_solve_uses_valid_nmea_measurements() -> None:
    response = client.post(
        "/api/navigation/solve",
        json={"nmea_text": "$GPGGA,123519.111,,,,,,,,545.4,M,46.9,M,,*47"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["valid_measurement_count"] == 1
    assert data["terrain_summary"]["mean_msl_m"] == 954.6


def test_nmea_parse_returns_gps_quality_diagnostics() -> None:
    response = client.post(
        "/api/nmea/parse",
        json={"nmea_text": "$GPGGA,123519.000,4515.0000,N,03930.0000,E,1,10,0.8,545.4,M,0.0,M,,*00"},
    )

    assert response.status_code == 200
    data = response.json()
    item = data["measurements"][0]
    assert item["gps_enabled"] is True
    assert item["lat_deg"] == 45.25
    assert item["lon_deg"] == 39.5
    assert item["satellites"] == 10
    assert item["hdop"] == 0.8
    assert item["input_diagnostic"]["source"] == "gps"
    assert item["input_diagnostic"]["accepted"] is True
