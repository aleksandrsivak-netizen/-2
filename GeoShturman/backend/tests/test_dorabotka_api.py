from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app


rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin


client = TestClient(app)


def _write_geotiff(path: Path) -> Path:
    width = height = 80
    resolution = 10.0
    transform = from_origin(4000.0, 6000.0, resolution, resolution)
    cols = np.arange(width, dtype=float)
    rows = np.arange(height, dtype=float)
    xs = 4000.0 + (cols + 0.5) * resolution
    ys = 6000.0 - (rows + 0.5) * resolution
    xx, yy = np.meshgrid(xs, ys)
    data = (300.0 + 0.04 * xx - 0.02 * yy + 4.0 * np.sin(xx / 25.0)).astype("float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="float32",
        crs="EPSG:32637",
        transform=transform,
    ) as dataset:
        dataset.write(data, 1)
    return path


def test_dorabotka_run_endpoint_accepts_files(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    from tercom_uav.dorabotka import GeoTiffContext

    context = GeoTiffContext.from_path(geotiff)
    start_x, start_y = context.pixel_to_local(25.0, 40.0)
    distances = np.arange(16, dtype=float) * 10.0
    heights = context.dem.sample_along(start_x, start_y, 90.0, distances)
    heights_path = tmp_path / "heights.txt"
    heights_path.write_text("\n".join(f"{value:.6f}" for value in heights), encoding="utf-8")

    with heights_path.open("rb") as heights_fh, geotiff.open("rb") as geotiff_fh:
        response = client.post(
            "/api/dorabotka/run",
            data={
                "start_x": str(start_x),
                "start_y": str(start_y),
                "heading_deg": "90",
                "start_coord_type": "map",
                "sample_step_m": "10",
                "search_radius_m": "0",
                "heading_search_deg": "0",
            },
            files={
                "heights_file": ("heights.txt", heights_fh, "text/plain"),
                "geotiff_file": ("map.tif", geotiff_fh, "image/tiff"),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "dorabotka"
    assert data["run_id"]
    assert data["result"]["correlation"] > 0.99
    assert data["artifact_links"]["trajectory_plot_png"].endswith("trajectory_plot.png")
    assert len(data["trajectory"]["local"]) == heights.size


def test_dorabotka_run_endpoint_sanitizes_non_finite_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    heights_path = tmp_path / "heights.txt"
    heights_path.write_text("1\n2\n3\n", encoding="utf-8")

    from app.services import dorabotka_service

    def fake_run_dorabotka(**_: object) -> dict:
        return {
            "mode": "dorabotka",
            "result": {"confidence": float("nan"), "rmse_m": float("inf")},
            "trajectory": {
                "local": [{"i": 0, "height_error_m": float("nan")}],
                "global": [],
            },
            "warnings": [],
        }

    monkeypatch.setattr(dorabotka_service, "run_dorabotka", fake_run_dorabotka)

    with heights_path.open("rb") as heights_fh, geotiff.open("rb") as geotiff_fh:
        response = client.post(
            "/api/dorabotka/run",
            data={"start_x": "10", "start_y": "10", "heading_deg": "0"},
            files={
                "heights_file": ("heights.txt", heights_fh, "text/plain"),
                "geotiff_file": ("map.tif", geotiff_fh, "image/tiff"),
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["result"]["confidence"] is None
    assert data["result"]["rmse_m"] is None
    assert data["trajectory"]["local"][0]["height_error_m"] is None


def test_dorabotka_run_endpoint_reports_bad_heights(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    heights_path = tmp_path / "heights.txt"
    heights_path.write_text("1\n2\nbad\n4\n5\n", encoding="utf-8")

    with heights_path.open("rb") as heights_fh, geotiff.open("rb") as geotiff_fh:
        response = client.post(
            "/api/dorabotka/run",
            data={"start_x": "10", "start_y": "10", "heading_deg": "0"},
            files={
                "heights_file": ("heights.txt", heights_fh, "text/plain"),
                "geotiff_file": ("map.tif", geotiff_fh, "image/tiff"),
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_heights_file"


def test_dem_grid_endpoint_reads_local_geotiff(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")

    response = client.get(
        "/api/dem/grid",
        params={
            "geotiff_path": str(geotiff),
            "center_x_m": "4400",
            "center_y_m": "5600",
            "width_m": "400",
            "height_m": "400",
            "side": "24",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"].startswith("GeoTIFF")
    assert data["rows"] == 24
    assert data["cols"] == 24
    assert data["span_m"] > 0
