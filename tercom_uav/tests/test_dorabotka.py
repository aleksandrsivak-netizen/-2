import csv
import json
from pathlib import Path

import numpy as np
import pytest

from tercom_uav.dorabotka import (
    DorabotkaError,
    DorabotkaSearchConfig,
    GeoTiffContext,
    build_trajectory_points,
    parse_heights_text,
    run_dorabotka,
)


rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin


def _terrain(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 100.0 + 0.03 * x + 0.02 * y + 8.0 * np.sin(x / 37.0) + 6.0 * np.cos(y / 29.0)


def _write_geotiff(path: Path, width: int = 100, height: int = 100, resolution: float = 10.0) -> Path:
    transform = from_origin(5000.0, 7000.0, resolution, resolution)
    cols = np.arange(width, dtype=float)
    rows = np.arange(height, dtype=float)
    xs = 5000.0 + (cols + 0.5) * resolution
    ys_desc = 7000.0 - (rows + 0.5) * resolution
    xx, yy = np.meshgrid(xs, ys_desc)
    data = _terrain(xx, yy).astype("float32")
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
        nodata=-9999.0,
    ) as dataset:
        dataset.write(data, 1)
    return path


def _write_heights(path: Path, values: np.ndarray) -> Path:
    path.write_text("\n".join(f"{value:.6f}" for value in values), encoding="utf-8")
    return path


def test_parse_heights_text_ignores_blanks_and_spaces() -> None:
    heights = parse_heights_text(" 120.5\n\n121\n  122.25  \n123\n124\n")
    assert heights.tolist() == pytest.approx([120.5, 121.0, 122.25, 123.0, 124.0])


def test_parse_heights_text_reports_invalid_line() -> None:
    with pytest.raises(DorabotkaError) as exc:
        parse_heights_text("1\n2\nbad\n4\n5\n")
    assert exc.value.code == "invalid_heights_file"
    assert exc.value.extra["line"] == 3


def test_heading_convention_north_and_east() -> None:
    distances = np.asarray([0.0, 10.0])
    north_x, north_y = build_trajectory_points(0.0, 0.0, 0.0, distances)
    east_x, east_y = build_trajectory_points(0.0, 0.0, 90.0, distances)
    assert north_x.tolist() == pytest.approx([0.0, 0.0])
    assert north_y.tolist() == pytest.approx([0.0, 10.0])
    assert east_x.tolist() == pytest.approx([0.0, 10.0])
    assert east_y.tolist() == pytest.approx([0.0, 0.0])


def test_dorabotka_pixel_and_map_start_outputs_artifacts(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    context = GeoTiffContext.from_path(geotiff)
    true_start_x, true_start_y = context.pixel_to_local(32.0, 58.0)
    heading = 35.0
    sample_step = 10.0
    distances = np.arange(24, dtype=float) * sample_step
    heights = context.dem.sample_along(true_start_x, true_start_y, heading, distances)
    heights_path = _write_heights(tmp_path / "heights.txt", heights)

    result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff,
        start_x=32.0,
        start_y=58.0,
        heading_deg=heading,
        output_dir=tmp_path / "out_pixel",
        config=DorabotkaSearchConfig(
            start_coord_type="pixel",
            sample_step_m=sample_step,
            search_radius_m=20.0,
            search_step_m=10.0,
            heading_search_deg=2.0,
            heading_step_deg=1.0,
            coarse_to_fine=False,
        ),
    )
    assert result["mode"] == "dorabotka"
    assert result["result"]["correlation"] > 0.99
    assert abs(result["result"]["best_offset_x_m"]) <= 10.0
    assert abs(result["result"]["best_offset_y_m"]) <= 10.0
    assert len(result["trajectory"]["local"]) == heights.size
    assert result["trajectory"]["global"][0]["lat"] is not None
    assert (tmp_path / "out_pixel" / "trajectory_local.csv").exists()
    assert (tmp_path / "out_pixel" / "trajectory_global.csv").exists()
    assert (tmp_path / "out_pixel" / "trajectory.geojson").exists()
    assert (tmp_path / "out_pixel" / "trajectory_plot.png").exists()
    assert (tmp_path / "out_pixel" / "result.json").exists()

    map_result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff,
        start_x=true_start_x,
        start_y=true_start_y,
        heading_deg=heading,
        output_dir=tmp_path / "out_map",
        config=DorabotkaSearchConfig(
            start_coord_type="map",
            sample_step_m=sample_step,
            search_radius_m=0.0,
            search_step_m=10.0,
            heading_search_deg=0.0,
            heading_step_deg=1.0,
            coarse_to_fine=False,
        ),
    )
    assert map_result["result"]["correlation"] > 0.99


def test_dorabotka_sample_step_affects_distance(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    context = GeoTiffContext.from_path(geotiff)
    start_x, start_y = context.pixel_to_local(40.0, 40.0)
    distances = np.arange(8, dtype=float) * 20.0
    heights = context.dem.sample_along(start_x, start_y, 90.0, distances)
    heights_path = _write_heights(tmp_path / "heights.txt", heights)
    result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff,
        start_x=start_x,
        start_y=start_y,
        heading_deg=90.0,
        output_dir=tmp_path / "out",
        config=DorabotkaSearchConfig(start_coord_type="map", sample_step_m=20.0, search_radius_m=0.0, heading_search_deg=0.0),
    )
    local = result["trajectory"]["local"]
    assert local[1]["distance_m"] == pytest.approx(20.0)
    assert local[1]["x"] - local[0]["x"] == pytest.approx(20.0)


def test_dorabotka_allows_nonfinite_tail_outside_geotiff(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    context = GeoTiffContext.from_path(geotiff)
    start_x, start_y = context.pixel_to_local(20.0, 20.0)
    distances = np.arange(18, dtype=float) * 20.0
    heights = context.dem.sample_along(start_x, start_y, 0.0, distances)
    assert np.isnan(heights).any()
    heights_path = _write_heights(tmp_path / "heights.txt", heights)

    result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff,
        start_x=start_x,
        start_y=start_y,
        heading_deg=0.0,
        output_dir=tmp_path / "out",
        config=DorabotkaSearchConfig(
            start_coord_type="map",
            sample_step_m=20.0,
            search_radius_m=0.0,
            search_step_m=10.0,
            heading_search_deg=0.0,
            coarse_to_fine=False,
        ),
    )

    assert result["result"]["correlation"] > 0.99
    assert len(result["trajectory"]["local"]) == heights.size
    assert np.isnan(result["trajectory"]["local"][-1]["map_height_m"])
    assert any("non-finite" in warning for warning in result["warnings"])


def test_dorabotka_reference_metrics(tmp_path: Path) -> None:
    geotiff = _write_geotiff(tmp_path / "map.tif")
    context = GeoTiffContext.from_path(geotiff)
    start_x, start_y = context.pixel_to_local(30.0, 50.0)
    distances = np.arange(12, dtype=float) * 10.0
    heights = context.dem.sample_along(start_x, start_y, 0.0, distances)
    heights_path = _write_heights(tmp_path / "heights.txt", heights)
    ref_path = tmp_path / "reference.csv"
    xs, ys = build_trajectory_points(start_x, start_y, 0.0, distances)
    with ref_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["x", "y"])
        writer.writeheader()
        for x, y in zip(xs, ys):
            writer.writerow({"x": x, "y": y})
    result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff,
        start_x=start_x,
        start_y=start_y,
        heading_deg=0.0,
        output_dir=tmp_path / "out",
        reference_trajectory=ref_path,
        config=DorabotkaSearchConfig(start_coord_type="map", sample_step_m=10.0, search_radius_m=0.0, heading_search_deg=0.0),
    )
    assert result["reference_metrics"]["mean_horizontal_error_m"] == pytest.approx(0.0, abs=1e-6)
    geojson = json.loads((tmp_path / "out" / "trajectory.geojson").read_text(encoding="utf-8"))
    assert geojson["features"][0]["geometry"]["type"] == "LineString"
