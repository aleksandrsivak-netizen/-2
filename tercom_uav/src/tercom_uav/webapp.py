"""Local web dashboard for running and inspecting TERCOM scenarios."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np
import pandas as pd

from tercom_uav.cli import _correlation_config, _json_default, _load_dem, _save_run_artifacts, _write_json
from tercom_uav.config import KalmanConfig, SimulationConfig, ensure_output_dir
from tercom_uav.estimator import localize_profile
from tercom_uav.nmea import read_gpgga_file
from tercom_uav.profiles import build_terrain_profile
from tercom_uav.route_planning import (
    build_automatic_route,
    build_simple_route,
    build_waypoint_route,
    generate_nmea_from_truth,
    plot_route_plan,
    save_route_artifacts,
)
from tercom_uav.simulator import simulate_flight


LOGGER = logging.getLogger("tercom_uav.webapp")
PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "web" / "static"
DEM_EXTENSIONS = {".tif", ".tiff", ".vrt"}
SKIP_SCAN_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "site-packages"}


def _project_root() -> Path:
    return Path.cwd().resolve()


def _web_outputs_root() -> Path:
    return ensure_output_dir(_project_root() / "outputs" / "web")


def _to_optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser().resolve()


def _resolve_map_selection(payload: dict[str, Any]) -> tuple[Path | None, bool, str]:
    """Resolve dashboard map selector fields to a DEM path or synthetic mode."""

    map_id = str(payload.get("mapId") or "").strip()
    if map_id == "synthetic-flat":
        return None, True, "synthetic-flat"
    if map_id == "synthetic" or (not map_id and not payload.get("demPath")):
        return None, False, "synthetic"
    if map_id.startswith("file:"):
        return Path(map_id.removeprefix("file:")).expanduser().resolve(), False, map_id
    dem_path = _to_optional_path(payload.get("demPath"))
    synthetic_flat = bool(payload.get("syntheticFlat", False))
    return dem_path, synthetic_flat, "manual" if dem_path else ("synthetic-flat" if synthetic_flat else "synthetic")


def _format_file_size(path: Path) -> str:
    size = path.stat().st_size
    if size >= 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def list_available_maps(max_results: int = 80) -> list[dict[str, Any]]:
    """Return built-in DEM options and local GeoTIFF/VRT files under the workspace."""

    maps = [
        {
            "id": "synthetic",
            "label": "Синтетический рельеф",
            "kind": "builtin",
            "path": None,
            "description": "Демонстрационная DEM с холмами и долинами, не требует файлов.",
        },
        {
            "id": "synthetic-flat",
            "label": "Плоская синтетическая карта",
            "kind": "builtin",
            "path": None,
            "description": "Контрольный случай: рельеф почти не наблюдаем, confidence должен падать.",
        },
    ]
    root = _project_root()
    found: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in SKIP_SCAN_DIRS and not dirname.startswith(".")
        ]
        for filename in filenames:
            path = Path(current_root) / filename
            if path.suffix.lower() in DEM_EXTENSIONS:
                found.append(path.resolve())
                if len(found) >= max_results:
                    break
        if len(found) >= max_results:
            break

    for path in sorted(found):
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path
        maps.append(
            {
                "id": f"file:{path}",
                "label": str(relative),
                "kind": "geotiff",
                "path": str(path),
                "description": f"Локальный DEM-файл, {_format_file_size(path)}.",
            }
        )
    return maps


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    payload = handler.rfile.read(length)
    if not payload:
        return {}
    return json.loads(payload.decode("utf-8"))


def _summary_for_frontend(run_dir: Path) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_name = run_dir.name
    summary["run_name"] = run_name
    summary["run_dir"] = str(run_dir)
    artifact_names = [
        "correlation_heatmap.png",
        "dem_tracks.png",
        "terrain_profile.png",
        "speed.png",
        "confidence.png",
        "observed_profile.csv",
        "estimates.csv",
        "truth.csv",
        "route.json",
        "route.csv",
        "route_plan.png",
        "telemetry.nmea",
        "telemetry.csv",
        "summary.json",
        "report.html",
    ]
    summary["artifact_urls"] = {
        name: f"/outputs/web/{run_name}/{name}"
        for name in artifact_names
        if (run_dir / name).exists()
    }
    return summary


def _timestamped_run_dir(prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ensure_output_dir(_web_outputs_root() / f"{prefix}_{stamp}")


def _checked_web_run_dir(run_name: str) -> Path:
    run_dir = (_web_outputs_root() / run_name).resolve()
    if _web_outputs_root() not in run_dir.parents:
        raise ValueError("Invalid run name.")
    if not run_dir.exists():
        raise FileNotFoundError(f"Run not found: {run_name}")
    return run_dir


def _load_route_payload(run_dir: Path) -> dict[str, Any]:
    route_path = run_dir / "route.json"
    if not route_path.exists():
        raise FileNotFoundError(f"route.json not found in {run_dir}")
    return json.loads(route_path.read_text(encoding="utf-8"))


def _load_dem_from_route_payload(route_payload: dict[str, Any]) -> DEMGrid:
    config = route_payload.get("config", {})
    dem_path = _to_optional_path(config.get("dem_path"))
    synthetic_flat = bool(config.get("synthetic_flat", False))
    return _load_dem(dem_path, synthetic_flat=synthetic_flat)


def _route_number(payload: dict[str, Any], key: str, fallback: float) -> float:
    value = payload.get(key)
    if value is None or str(value).strip() == "":
        return fallback
    return float(value)


def _route_base_config(payload: dict[str, Any], dem_path: Path | None, synthetic_flat: bool, map_id: str) -> dict[str, Any]:
    return {
        "map_id": map_id,
        "dem_path": str(dem_path) if dem_path else None,
        "synthetic_flat": synthetic_flat,
        "baro_alt_msl": _route_number(payload, "routeBaroAlt", _route_number(payload, "baroAlt", 1500.0)),
        "random_seed": int(_route_number(payload, "randomSeed", 42)),
    }


def _write_route_summary(run_dir: Path, dem: DEMGrid, route_payload: dict[str, Any], artifacts: list[Path]) -> dict[str, Any]:
    summary = {
        "config": {"mode": "route", **route_payload["config"]},
        "dem": {
            "source_path": dem.source_path,
            "bounds_m": dem.bounds_m,
            "resolution_m": dem.resolution_m,
            "metadata": dem.metadata,
        },
        "route": route_payload["route"],
        "route_warnings": route_payload.get("warnings", []),
        "artifacts": [str(path) for path in artifacts],
    }
    _write_json(run_dir / "summary.json", summary)
    return _summary_for_frontend(run_dir)


def _merge_route_summary(run_dir: Path) -> dict[str, Any]:
    route_payload = _load_route_payload(run_dir)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["route"] = route_payload.get("route", {})
    summary["route_warnings"] = route_payload.get("warnings", [])
    summary.setdefault("config", {})["route_config"] = route_payload.get("config", {})
    route_artifacts = [run_dir / "route.json", run_dir / "route.csv", run_dir / "route_plan.png"]
    existing = set(summary.get("artifacts", []))
    for artifact in route_artifacts:
        if artifact.exists() and str(artifact) not in existing:
            summary.setdefault("artifacts", []).append(str(artifact))
    _write_json(summary_path, summary)
    return _summary_for_frontend(run_dir)


def run_demo_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run simulation plus localization from dashboard JSON input."""

    dem_path, synthetic_flat, map_id = _resolve_map_selection(payload)
    dem_grid = _load_dem(dem_path, synthetic_flat=synthetic_flat)
    out_dir = _timestamped_run_dir("demo")
    sim_config = SimulationConfig(
        dem_path=str(dem_path) if dem_path else None,
        baro_alt_msl=float(payload.get("baroAlt", 1500.0)),
        speed_mps=float(payload.get("speed", 55.0)),
        heading_deg=float(payload.get("heading", 73.0)),
        duration_s=float(payload.get("duration", 180.0)),
        hz=float(payload.get("hz", 5.0)),
        noise_std_m=float(payload.get("noiseStd", 2.5)),
        outlier_prob=float(payload.get("outlierProb", 0.0)),
        dropout_prob=float(payload.get("dropoutProb", 0.0)),
        drift_mps=float(payload.get("driftMps", 0.0)),
        random_seed=int(payload.get("randomSeed", 42)),
    )
    strict_mode = bool(payload.get("strictMode", True))
    correlation_config = _correlation_config(
        dem_grid,
        shift_step_m=float(payload.get("shiftStep", 30.0)),
        coarse_to_fine=False if strict_mode else bool(payload.get("coarseToFine", False)),
        quality=str(payload.get("qualityMode", "balanced")),
    )
    kalman_config = KalmanConfig(enabled=bool(payload.get("useKalman", False)))

    simulation = simulate_flight(dem_grid, sim_config)
    simulation.export_nmea(out_dir / "telemetry.nmea")
    simulation.export_truth(out_dir / "truth.csv")
    simulation.export_telemetry(out_dir / "telemetry.csv")
    records = read_gpgga_file(out_dir / "telemetry.nmea")
    profile = build_terrain_profile(records, sim_config.baro_alt_msl)
    localization = localize_profile(
        dem=dem_grid,
        profile=profile,
        speed_hint_mps=sim_config.speed_mps,
        correlation_config=correlation_config,
        kalman_config=kalman_config,
        truth=simulation.truth,
    )
    config_payload = {
        "mode": "demo",
        "map_id": map_id,
        "strict_mode": strict_mode,
        "simulation": sim_config.to_dict(),
        "correlation": correlation_config.to_dict(),
        "kalman": kalman_config.to_dict(),
    }
    _write_json(out_dir / "config.json", config_payload)
    _save_run_artifacts(out_dir, dem_grid, profile, localization, simulation.truth, config_payload)
    return _summary_for_frontend(out_dir)


def run_localize_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run localization for existing NMEA and optional truth files."""

    nmea_path = _to_optional_path(payload.get("nmeaPath"))
    if nmea_path is None:
        raise ValueError("nmeaPath is required for localization mode.")
    if not nmea_path.exists():
        raise FileNotFoundError(f"NMEA file does not exist: {nmea_path}")

    dem_path, synthetic_flat, map_id = _resolve_map_selection(payload)
    truth_path = _to_optional_path(payload.get("truthPath"))
    dem_grid = _load_dem(dem_path, synthetic_flat=synthetic_flat)
    out_dir = _timestamped_run_dir("localize")
    records = read_gpgga_file(nmea_path)
    baro_alt = float(payload.get("baroAlt", 1500.0))
    speed_hint = float(payload.get("speedHint", payload.get("speed", 55.0)))
    profile = build_terrain_profile(records, baro_alt)
    truth_frame = pd.read_csv(truth_path) if truth_path and truth_path.exists() else None
    strict_mode = bool(payload.get("strictMode", True))
    correlation_config = _correlation_config(
        dem_grid,
        shift_step_m=float(payload.get("shiftStep", 30.0)),
        coarse_to_fine=False if strict_mode else bool(payload.get("coarseToFine", False)),
        quality=str(payload.get("qualityMode", "balanced")),
    )
    kalman_config = KalmanConfig(enabled=bool(payload.get("useKalman", False)))
    localization = localize_profile(
        dem=dem_grid,
        profile=profile,
        speed_hint_mps=speed_hint,
        correlation_config=correlation_config,
        kalman_config=kalman_config,
        truth=truth_frame,
    )
    config_payload = {
        "mode": "localize",
        "map_id": map_id,
        "strict_mode": strict_mode,
        "input": {
            "nmea": str(nmea_path),
            "dem": str(dem_path) if dem_path else None,
            "truth": str(truth_path) if truth_path else None,
        },
        "baro_alt_msl": baro_alt,
        "speed_hint_mps": speed_hint,
        "correlation": correlation_config.to_dict(),
        "kalman": kalman_config.to_dict(),
    }
    _write_json(out_dir / "config.json", config_payload)
    _save_run_artifacts(out_dir, dem_grid, profile, localization, truth_frame, config_payload)
    return _summary_for_frontend(out_dir)


def build_route_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build and persist a civil UAV route from dashboard input."""

    dem_path, synthetic_flat, map_id = _resolve_map_selection(payload)
    dem_grid = _load_dem(dem_path, synthetic_flat=synthetic_flat)
    out_dir = _timestamped_run_dir("route")
    mode = str(payload.get("routeMode", "simple"))
    base_config = _route_base_config(payload, dem_path, synthetic_flat, map_id)
    baro_alt = float(base_config["baro_alt_msl"])

    if mode == "simple":
        result = build_simple_route(
            dem=dem_grid,
            x0_m=_route_number(payload, "routeSimpleX0", -2500.0),
            y0_m=_route_number(payload, "routeSimpleY0", -2500.0),
            heading_deg=_route_number(payload, "routeSimpleHeading", _route_number(payload, "heading", 73.0)),
            speed_mps=_route_number(payload, "routeSimpleSpeed", _route_number(payload, "speed", 55.0)),
            duration_s=_route_number(payload, "routeSimpleDuration", _route_number(payload, "duration", 180.0)),
            hz=_route_number(payload, "routeSimpleHz", _route_number(payload, "hz", 5.0)),
            baro_alt_msl=baro_alt,
        )
    elif mode == "waypoints":
        result = build_waypoint_route(
            dem=dem_grid,
            waypoint_text=str(payload.get("routeWaypoints", "")),
            speed_mps=_route_number(payload, "routeWaypointSpeed", _route_number(payload, "speed", 55.0)),
            hz=_route_number(payload, "routeWaypointHz", _route_number(payload, "hz", 5.0)),
            baro_alt_msl=baro_alt,
        )
    elif mode == "automatic":
        desired_length = payload.get("routeAutoDesiredLength")
        desired_duration = payload.get("routeAutoDesiredDuration")
        result = build_automatic_route(
            dem=dem_grid,
            start_x_m=_route_number(payload, "routeAutoStartX", -3500.0),
            start_y_m=_route_number(payload, "routeAutoStartY", -3500.0),
            end_x_m=_route_number(payload, "routeAutoEndX", 3500.0),
            end_y_m=_route_number(payload, "routeAutoEndY", 2500.0),
            speed_mps=_route_number(payload, "routeAutoSpeed", _route_number(payload, "speed", 55.0)),
            hz=_route_number(payload, "routeAutoHz", _route_number(payload, "hz", 5.0)),
            baro_alt_msl=baro_alt,
            desired_length_m=float(desired_length) if desired_length not in (None, "") else None,
            desired_duration_s=float(desired_duration) if desired_duration not in (None, "") else None,
            min_observability=_route_number(payload, "routeAutoMinObservability", 0.25),
        )
    else:
        raise ValueError(f"Unsupported route mode: {mode}")

    route_config = {
        **base_config,
        "route_mode": mode,
        "request": payload,
    }
    save_route_artifacts(result, out_dir, route_config)
    route_plan_path = plot_route_plan(dem_grid, result, out_dir)
    route_payload = _load_route_payload(out_dir)
    return _write_route_summary(out_dir, dem_grid, route_payload, [route_plan_path, out_dir / "route.json", out_dir / "route.csv", out_dir / "truth.csv"])


def generate_route_nmea_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate telemetry.nmea for an already built route."""

    run_name = str(payload.get("runName") or "").strip()
    if not run_name:
        raise ValueError("runName is required.")
    run_dir = _checked_web_run_dir(run_name)
    route_payload = _load_route_payload(run_dir)
    truth = pd.read_csv(run_dir / "truth.csv")
    lines, telemetry = generate_nmea_from_truth(
        truth,
        noise_std_m=_route_number(payload, "routeNmeaNoiseStd", 0.0),
        outlier_prob=_route_number(payload, "routeNmeaOutlierProb", 0.0),
        dropout_prob=_route_number(payload, "routeNmeaDropoutProb", 0.0),
        drift_mps=_route_number(payload, "routeNmeaDriftMps", 0.0),
        random_seed=int(route_payload.get("config", {}).get("random_seed", 42)),
        target_hz=_route_number(payload, "routeNmeaHz", float(route_payload.get("config", {}).get("request", {}).get("hz", 5.0))),
    )
    (run_dir / "telemetry.nmea").write_text("\n".join(lines) + "\n", encoding="utf-8")
    telemetry.to_csv(run_dir / "telemetry.csv", index=False)
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["route_nmea"] = {
        "noise_std_m": _route_number(payload, "routeNmeaNoiseStd", 0.0),
        "outlier_prob": _route_number(payload, "routeNmeaOutlierProb", 0.0),
        "dropout_prob": _route_number(payload, "routeNmeaDropoutProb", 0.0),
        "drift_mps": _route_number(payload, "routeNmeaDriftMps", 0.0),
        "hz": _route_number(payload, "routeNmeaHz", float(route_payload.get("config", {}).get("request", {}).get("hz", 5.0))),
        "message_count": len(lines),
    }
    _write_json(summary_path, summary)
    return _summary_for_frontend(run_dir)


def run_route_localization_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run TERCOM localization using route-generated truth and NMEA."""

    run_name = str(payload.get("runName") or "").strip()
    if not run_name:
        raise ValueError("runName is required.")
    run_dir = _checked_web_run_dir(run_name)
    if not (run_dir / "telemetry.nmea").exists():
        generate_route_nmea_from_payload({**payload, "runName": run_name})
    route_payload = _load_route_payload(run_dir)
    previous_summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    route_nmea_summary = previous_summary.get("route_nmea")
    dem_grid = _load_dem_from_route_payload(route_payload)
    truth = pd.read_csv(run_dir / "truth.csv")
    records = read_gpgga_file(run_dir / "telemetry.nmea")
    baro_alt = float(route_payload.get("config", {}).get("baro_alt_msl", 1500.0))
    profile = build_terrain_profile(records, baro_alt)
    speed_hint = float(route_payload.get("route", {}).get("mean_speed_mps", payload.get("speedHint", 55.0)))
    strict_mode = bool(payload.get("strictMode", True))
    correlation_config = _correlation_config(
        dem_grid,
        shift_step_m=_route_number(payload, "shiftStep", 30.0),
        coarse_to_fine=False if strict_mode else bool(payload.get("coarseToFine", False)),
        quality=str(payload.get("qualityMode", "balanced")),
    )
    kalman_config = KalmanConfig(enabled=bool(payload.get("useKalman", False)))
    localization = localize_profile(
        dem=dem_grid,
        profile=profile,
        speed_hint_mps=speed_hint,
        correlation_config=correlation_config,
        kalman_config=kalman_config,
        truth=truth,
    )
    config_payload = {
        "mode": "route-localization",
        "route_run": run_name,
        "strict_mode": strict_mode,
        "baro_alt_msl": baro_alt,
        "speed_hint_mps": speed_hint,
        "correlation": correlation_config.to_dict(),
        "kalman": kalman_config.to_dict(),
    }
    _save_run_artifacts(run_dir, dem_grid, profile, localization, truth, config_payload)
    if route_nmea_summary:
        summary_path = run_dir / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["route_nmea"] = route_nmea_summary
        _write_json(summary_path, summary)
    return _merge_route_summary(run_dir)


def route_result(run_name: str) -> dict[str, Any]:
    return _summary_for_frontend(_checked_web_run_dir(run_name))


def list_runs() -> list[dict[str, Any]]:
    """List web runs with compact metrics."""

    root = _web_outputs_root()
    runs: list[dict[str, Any]] = []
    for summary_path in sorted(root.glob("*/summary.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        run_dir = summary_path.parent
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        metrics = summary.get("metrics", {})
        estimate = summary.get("estimate", {})
        runs.append(
            {
                "run_name": run_dir.name,
                "run_dir": str(run_dir),
                "created_at": datetime.fromtimestamp(summary_path.stat().st_mtime).isoformat(timespec="seconds"),
                "confidence_score": metrics.get("confidence_score"),
                "ambiguity_flag": metrics.get("ambiguity_flag"),
                "heading_error_deg": metrics.get("heading_error_deg"),
                "horizontal_error_m": metrics.get("horizontal_error_m"),
                "azimuth_deg": estimate.get("azimuth_deg"),
                "speed_mps": estimate.get("speed_mps"),
            }
        )
    return runs


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for static dashboard files and JSON API."""

    server_version = "TercomUavDashboard/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/runs":
            self._send_json({"runs": list_runs()})
            return
        if path == "/api/maps":
            self._send_json({"maps": list_available_maps()})
            return
        if path.startswith("/api/runs/") and path.endswith("/summary"):
            run_name = path.split("/")[3]
            run_dir = (_web_outputs_root() / run_name).resolve()
            if _web_outputs_root() not in run_dir.parents:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Invalid run name.")
                return
            if not (run_dir / "summary.json").exists():
                self._send_error_json(HTTPStatus.NOT_FOUND, "Run summary not found.")
                return
            self._send_json(_summary_for_frontend(run_dir))
            return
        if path.startswith("/api/route/result/"):
            run_name = path.removeprefix("/api/route/result/").strip("/")
            self._send_json(route_result(run_name))
            return
        if path.startswith("/outputs/web/"):
            rel = path.removeprefix("/outputs/web/")
            file_path = (_web_outputs_root() / rel).resolve()
            if _web_outputs_root() not in file_path.parents:
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid output path")
                return
            self._send_file(file_path)
            return
        if path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        static_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR in static_path.parents:
            self._send_file(static_path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = _read_json_body(self)
            if parsed.path == "/api/demo":
                self._send_json(run_demo_from_payload(payload))
                return
            if parsed.path == "/api/localize":
                self._send_json(run_localize_from_payload(payload))
                return
            if parsed.path == "/api/route/build":
                self._send_json(build_route_from_payload(payload))
                return
            if parsed.path == "/api/route/generate-nmea":
                self._send_json(generate_route_nmea_from_payload(payload))
                return
            if parsed.path == "/api/route/run-localization":
                self._send_json(run_route_localization_from_payload(payload))
                return
            self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint.")
        except Exception as exc:
            LOGGER.exception("Dashboard request failed")
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local dashboard server."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    LOGGER.info("TERCOM dashboard running at http://%s:%s", host, port)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local TERCOM UAV dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
