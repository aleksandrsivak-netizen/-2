from __future__ import annotations

from html import escape
import json
import logging
import mimetypes
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.api.schemas import (
    AutonomousDemoRequest,
    DemoRunRequest,
    DemoRunResponse,
    NMEAParseRequest,
    NMEAParseResponse,
    NavigationSolveRequest,
)
from app.config import settings
from app.services.artifact_service import ARTIFACT_FILENAMES, ArtifactPathError, normalize_run_id, safe_artifact_path
from app.services.dorabotka_service import DorabotkaError, run_dorabotka_from_uploads
from app.services.pipeline import (
    parse_nmea_text,
    run_autonomous_demo_pipeline,
    run_demo_pipeline,
    solve_navigation_from_nmea,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DORABOTKA_ARTIFACT_FILENAMES = {
    "trajectory_local_csv": "trajectory_local.csv",
    "trajectory_global_csv": "trajectory_global.csv",
    "trajectory_geojson": "trajectory.geojson",
    "trajectory_plot_png": "trajectory_plot.png",
    "result_json": "result.json",
    "heights": "heights.txt",
    "geotiff": "map.tif",
}
VIEW_ARTIFACT_FILENAMES = {**ARTIFACT_FILENAMES, **DORABOTKA_ARTIFACT_FILENAMES}


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
    }


@router.post("/api/demo/run", response_model=DemoRunResponse)
def run_demo(request: DemoRunRequest) -> DemoRunResponse:
    try:
        return run_demo_pipeline(request)
    except Exception as exc:
        logger.exception("Demo pipeline failed")
        raise HTTPException(status_code=500, detail="Demo pipeline failed") from exc


@router.post("/api/navigation/autonomous-demo")
def run_autonomous_demo(request: AutonomousDemoRequest) -> dict:
    try:
        return run_autonomous_demo_pipeline(request)
    except Exception as exc:
        logger.exception("Autonomous navigation demo failed")
        raise HTTPException(status_code=500, detail="Autonomous navigation demo failed") from exc


@router.post("/api/nmea/parse", response_model=NMEAParseResponse)
def parse_nmea(request: NMEAParseRequest) -> NMEAParseResponse:
    if not request.nmea_text.strip():
        raise HTTPException(status_code=400, detail="nmea_text must not be empty")

    try:
        measurements = parse_nmea_text(request.nmea_text)
    except Exception as exc:
        logger.exception("NMEA parsing failed")
        raise HTTPException(status_code=500, detail="NMEA parsing failed") from exc

    valid_count = sum(1 for item in measurements if item.get("valid"))
    invalid_count = len(measurements) - valid_count
    return NMEAParseResponse(
        status="ok",
        count=len(measurements),
        valid_count=valid_count,
        invalid_count=invalid_count,
        measurements=measurements,
    )


@router.post("/api/navigation/solve")
def solve_navigation(request: NavigationSolveRequest) -> dict:
    if not request.nmea_text.strip():
        raise HTTPException(status_code=400, detail="nmea_text must not be empty")

    try:
        result = solve_navigation_from_nmea(request)
    except Exception as exc:
        logger.exception("Navigation solve failed")
        raise HTTPException(status_code=500, detail="Navigation solve failed") from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/api/dorabotka/run")
async def run_dorabotka(
    heights_file: UploadFile = File(...),
    geotiff_file: UploadFile = File(...),
    start_x: float = Form(...),
    start_y: float = Form(...),
    heading_deg: float = Form(...),
    start_coord_type: str = Form("auto"),
    sample_step_m: float = Form(1.0),
    search_radius_m: float = Form(200.0),
    search_step_m: float = Form(5.0),
    heading_search_deg: float = Form(5.0),
    heading_step_deg: float = Form(1.0),
    normalize_profile: bool = Form(True),
    coarse_to_fine: bool = Form(True),
    max_candidates: int = Form(8),
    max_hypotheses: int = Form(250_000),
    reference_trajectory: UploadFile | None = File(None),
) -> dict:
    try:
        return await run_dorabotka_from_uploads(
            heights_file=heights_file,
            geotiff_file=geotiff_file,
            start_x=start_x,
            start_y=start_y,
            heading_deg=heading_deg,
            start_coord_type=start_coord_type,
            sample_step_m=sample_step_m,
            search_radius_m=search_radius_m,
            search_step_m=search_step_m,
            heading_search_deg=heading_search_deg,
            heading_step_deg=heading_step_deg,
            normalize_profile=normalize_profile,
            coarse_to_fine=coarse_to_fine,
            max_candidates=max_candidates,
            max_hypotheses=max_hypotheses,
            reference_trajectory=reference_trajectory,
        )
    except DorabotkaError as exc:
        raise HTTPException(status_code=400, detail=exc.to_dict()) from exc
    except Exception as exc:
        logger.exception("Dorabotka pipeline failed")
        raise HTTPException(status_code=500, detail="Dorabotka pipeline failed") from exc


@router.get("/api/artifacts/{run_id}/{filename}")
def get_artifact(run_id: str, filename: str):
    path = _artifact_path_or_404(run_id, filename)

    suffix = path.suffix.lower()
    if suffix in {".csv", ".json", ".geojson", ".txt", ".nmea"}:
        media_type = {
            ".json": "application/json; charset=utf-8",
            ".geojson": "application/geo+json; charset=utf-8",
        }.get(suffix, "text/plain; charset=utf-8")
        return Response(
            content=path.read_text(encoding="utf-8", errors="replace"),
            media_type=media_type,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type="inline")


@router.get("/api/artifact-view/{run_id}/{artifact_key}")
def view_artifact(run_id: str, artifact_key: str):
    filename = VIEW_ARTIFACT_FILENAMES.get(artifact_key)
    if filename is None:
        raise HTTPException(status_code=404, detail="Artifact key not found")

    path = _artifact_path_or_404(run_id, filename)
    normalized_run_id = normalize_run_id(run_id)
    title = f"Просмотр артефакта: {path.name}"
    suffix = path.suffix.lower()

    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        image_url = f"/api/artifacts/{normalized_run_id}/{quote(path.name)}"
        return _artifact_view_page(
            title,
            f'<img class="artifact-image" src="{image_url}" alt="{escape(path.name)}">',
        )

    if suffix in {".csv", ".json", ".geojson", ".txt", ".nmea"}:
        text = escape(path.read_text(encoding="utf-8", errors="replace"))
        return _artifact_view_page(title, f"<pre>{text}</pre>")

    download_url = f"/api/artifacts/{normalized_run_id}/{quote(path.name)}"
    return _artifact_view_page(title, f'<a class="download-link" href="{download_url}">Открыть файл</a>')


def _artifact_path_or_404(run_id: str, filename: str):
    try:
        path = safe_artifact_path(run_id, filename)
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return path


def _artifact_view_page(title: str, body: str) -> HTMLResponse:
    safe_title = escape(title)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #07111f;
      color: #d8e3f2;
      font: 14px/1.5 ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
    }}
    h1 {{
      margin: 0 0 16px;
      color: #f1f5f9;
      font: 700 18px/1.25 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    pre {{
      box-sizing: border-box;
      min-height: calc(100vh - 96px);
      margin: 0;
      padding: 16px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid #20344d;
      border-radius: 8px;
      background: #0b1624;
    }}
    .artifact-image {{
      display: block;
      max-width: 100%;
      height: auto;
      border: 1px solid #20344d;
      border-radius: 8px;
      background: #0b1624;
    }}
    .download-link {{
      color: #22d3ee;
    }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  {body}
</body>
</html>"""
    )


@router.get("/api/runs/{run_id}/result")
def get_run_result(run_id: str) -> dict:
    try:
        path = safe_artifact_path(run_id, "result.json")
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Run result not found")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.exception("Stored result JSON is invalid for run_id=%s", run_id)
        raise HTTPException(status_code=500, detail="Stored result JSON is invalid") from exc
