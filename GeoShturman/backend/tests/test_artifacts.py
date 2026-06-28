from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _run_demo() -> dict:
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
    return response.json()


def test_artifact_can_be_downloaded() -> None:
    data = _run_demo()

    response = client.get(data["artifacts"]["trajectory_overlay_png"])

    assert response.status_code == 200
    assert response.content
    assert response.headers["content-type"].startswith("image/png")


def test_artifact_view_opens_inline() -> None:
    data = _run_demo()

    response = client.get(f"/api/artifact-view/{data['run_id']}/result_json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in response.headers
    assert "run_id" in response.text


def test_artifact_path_traversal_is_blocked() -> None:
    data = _run_demo()
    run_id = data["run_id"]

    response = client.get(f"/api/artifacts/{run_id}/%2E%2E%2Fresult.json")

    assert response.status_code in {400, 404}


def test_missing_run_result_returns_404() -> None:
    response = client.get("/api/runs/11111111-1111-4111-8111-111111111111/result")

    assert response.status_code == 404


def test_invalid_run_id_returns_400() -> None:
    response = client.get("/api/artifacts/not-a-uuid/result.json")

    assert response.status_code == 400
