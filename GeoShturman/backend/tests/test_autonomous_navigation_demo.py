from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_autonomous_demo_improves_over_dead_reckoning() -> None:
    response = client.post(
        "/api/navigation/autonomous-demo",
        json={
            "width_m": 3000,
            "height_m": 3000,
            "resolution_m": 50,
            "duration_s": 35,
            "sample_rate_hz": 2,
            "true_speed_mps": 18,
            "true_heading_deg": 73,
            "initial_uncertainty_radius_m": 300,
            "n_particles": 500,
            "profile_window_s": 10,
            "seed": 42,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["algorithm"] == "BlindFlight Terrain Lock"
    assert data["improvement_factor"] >= 1.5
    assert data["truth_error"]["final_position_error_m"] < data["dead_reckoning_error"]["final_position_error_m"]
    assert data["artifacts"]["trajectory_comparison_png"]
    assert data["artifacts"]["particle_cloud_png"]
    assert data["artifacts"]["confidence_timeline_png"]
    assert data["artifacts"]["terrain_profile_match_png"]
