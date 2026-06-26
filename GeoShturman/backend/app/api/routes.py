from __future__ import annotations

import json
import logging
import mimetypes

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.schemas import (
    AutonomousDemoRequest,
    DemoRunRequest,
    DemoRunResponse,
    NMEAParseRequest,
    NMEAParseResponse,
    NavigationSolveRequest,
)
from app.config import settings
from app.services.artifact_service import ArtifactPathError, safe_artifact_path
from app.services.pipeline import (
    parse_nmea_text,
    run_autonomous_demo_pipeline,
    run_demo_pipeline,
    solve_navigation_from_nmea,
)

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.get("/api/artifacts/{run_id}/{filename}")
def get_artifact(run_id: str, filename: str):
    try:
        path = safe_artifact_path(run_id, filename)
    except ArtifactPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


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
