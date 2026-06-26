from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.config import settings


ARTIFACT_FILENAMES = {
    "trajectory_overlay_png": "trajectory_overlay.png",
    "trajectory_comparison_png": "trajectory_comparison.png",
    "particle_cloud_png": "particle_cloud.png",
    "confidence_timeline_png": "confidence_timeline.png",
    "terrain_profile_match_png": "terrain_profile_match.png",
    "correlation_heatmap_png": "correlation_heatmap.png",
    "profile_comparison_png": "profile_comparison.png",
    "generated_nmea": "generated_flight.nmea",
    "nmea_log": "generated_flight.nmea",
    "result_json": "result.json",
}


class ArtifactPathError(ValueError):
    pass


def normalize_run_id(run_id: str) -> str:
    try:
        return str(UUID(str(run_id)))
    except ValueError as exc:
        raise ArtifactPathError("Invalid run_id") from exc


def run_root(run_id: str) -> Path:
    return settings.output_dir / normalize_run_id(run_id)


def create_run_dirs(run_id: str) -> dict[str, Path]:
    root = run_root(run_id)
    dirs = {
        "root": root,
        "plots": root / "plots",
        "reports": root / "reports",
        "samples": root / "samples",
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def safe_artifact_path(run_id: str, filename: str) -> Path:
    if not filename or "/" in filename or "\\" in filename:
        raise ArtifactPathError("Artifact filename must be a basename")

    filename_path = Path(filename)
    if filename_path.is_absolute() or filename_path.name != filename:
        raise ArtifactPathError("Artifact filename must be a basename")

    if filename in {".", ".."} or ".." in filename_path.parts:
        raise ArtifactPathError("Artifact filename is not allowed")

    root = run_root(run_id).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise ArtifactPathError("Artifact path escapes run directory")
    return candidate


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_artifact_links(run_id: str) -> dict[str, str]:
    normalized_run_id = normalize_run_id(run_id)
    return {
        name: f"/api/artifacts/{normalized_run_id}/{filename}"
        for name, filename in ARTIFACT_FILENAMES.items()
    }
