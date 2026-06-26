from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_demo_run_returns_navigation_result() -> None:
    response = client.post(
        "/api/demo/run",
        json={
            "width_m": 2000,
            "height_m": 2000,
            "resolution_m": 50,
            "duration_s": 30,
            "sample_rate_hz": 2,
            "speed_mps": 30,
            "azimuth_deg": 45,
            "search_radius_m": 500,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["run_id"]
    assert data["estimated"]["confidence"] >= 0
    assert data["artifacts"]["trajectory_overlay_png"]
    assert data["artifacts"]["result_json"]


def test_default_demo_run_stays_inside_synthetic_dem_bounds() -> None:
    response = client.post("/api/demo/run", json={})

    assert response.status_code == 200
    data = response.json()
    assert 0 <= data["truth"]["end_x_m"] <= 8000
    assert 0 <= data["truth"]["end_y_m"] <= 8000
    assert 0 <= data["estimated"]["end_x_m"] <= 8000
    assert 0 <= data["estimated"]["end_y_m"] <= 8000
    assert data["truth"]["speed_mps"] <= 45
    assert "capped" in data["quality"]["warning"]
