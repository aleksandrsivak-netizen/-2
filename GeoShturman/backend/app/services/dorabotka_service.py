from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile

from app.services.artifact_service import create_run_dirs, make_json_safe, normalize_run_id, save_json


_REPO = Path(__file__).resolve().parents[4]
_TERCOM_SRC = _REPO / "tercom_uav" / "src"
if str(_TERCOM_SRC) not in sys.path:
    sys.path.insert(0, str(_TERCOM_SRC))

from tercom_uav.dorabotka import DorabotkaError, DorabotkaSearchConfig, run_dorabotka  # noqa: E402


async def _save_upload(upload: UploadFile, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as fh:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    await upload.seek(0)
    return destination


def _artifact_links(run_id: str) -> dict[str, str]:
    normalized = normalize_run_id(run_id)
    filenames = {
        "trajectory_local_csv": "trajectory_local.csv",
        "trajectory_global_csv": "trajectory_global.csv",
        "trajectory_geojson": "trajectory.geojson",
        "trajectory_plot_png": "trajectory_plot.png",
        "result_json": "result.json",
        "heights": "heights.txt",
        "geotiff": "map.tif",
    }
    return {key: f"/api/artifacts/{normalized}/{filename}" for key, filename in filenames.items()}


def _artifact_view_links(run_id: str) -> dict[str, str]:
    normalized = normalize_run_id(run_id)
    keys = (
        "trajectory_local_csv",
        "trajectory_global_csv",
        "trajectory_geojson",
        "trajectory_plot_png",
        "result_json",
        "heights",
        "geotiff",
    )
    return {key: f"/api/artifact-view/{normalized}/{key}" for key in keys}


async def run_dorabotka_from_uploads(
    *,
    heights_file: UploadFile,
    geotiff_file: UploadFile,
    start_x: float,
    start_y: float,
    heading_deg: float,
    start_coord_type: str = "auto",
    sample_step_m: float = 1.0,
    search_radius_m: float = 200.0,
    search_step_m: float = 5.0,
    heading_search_deg: float = 5.0,
    heading_step_deg: float = 1.0,
    normalize_profile: bool = True,
    coarse_to_fine: bool = True,
    max_candidates: int = 8,
    max_hypotheses: int = 250_000,
    reference_trajectory: UploadFile | None = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    dirs = create_run_dirs(run_id)
    root = dirs["root"]
    heights_path = root / "heights.txt"
    geotiff_suffix = Path(geotiff_file.filename or "map.tif").suffix or ".tif"
    geotiff_path = root / f"map{geotiff_suffix}"
    reference_path: Path | None = None

    await _save_upload(heights_file, heights_path)
    await _save_upload(geotiff_file, geotiff_path)
    if geotiff_path.name != "map.tif":
        shutil.copyfile(geotiff_path, root / "map.tif")
    if reference_trajectory is not None and reference_trajectory.filename:
        suffix = Path(reference_trajectory.filename).suffix or ".csv"
        reference_path = root / f"reference{suffix}"
        await _save_upload(reference_trajectory, reference_path)

    config = DorabotkaSearchConfig(
        sample_step_m=sample_step_m,
        search_radius_m=search_radius_m,
        search_step_m=search_step_m,
        heading_search_deg=heading_search_deg,
        heading_step_deg=heading_step_deg,
        start_coord_type=start_coord_type,
        normalize_profile=normalize_profile,
        coarse_to_fine=coarse_to_fine,
        max_candidates=max_candidates,
        max_hypotheses=max_hypotheses,
    )
    result = run_dorabotka(
        heights_path=heights_path,
        geotiff_path=geotiff_path,
        start_x=start_x,
        start_y=start_y,
        heading_deg=heading_deg,
        output_dir=root,
        config=config,
        reference_trajectory=reference_path,
    )
    result["run_id"] = run_id
    result["artifact_links"] = _artifact_links(run_id)
    result["artifact_view_links"] = _artifact_view_links(run_id)
    response = make_json_safe(result)
    save_json(root / "result.json", response)
    return response


__all__ = [
    "DorabotkaError",
    "run_dorabotka_from_uploads",
]
