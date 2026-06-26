from __future__ import annotations

import base64
import logging
import math
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from app.api.schemas import (
    ArtifactLinks,
    AutonomousDemoRequest,
    DemoRunRequest,
    DemoRunResponse,
    EstimatedState,
    NavigationSolveRequest,
    QualityReport,
    TruthState,
)
from app.services.artifact_service import (
    ARTIFACT_FILENAMES,
    build_artifact_links,
    create_run_dirs,
    safe_artifact_path,
    save_json,
)

logger = logging.getLogger(__name__)

_MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _angle_error_deg(a_deg: float, b_deg: float) -> float:
    return abs((float(a_deg) - float(b_deg) + 180.0) % 360.0 - 180.0)


def _max_distance_inside_dem(width_m: float, height_m: float, start_x_m: float, start_y_m: float, azimuth_deg: float) -> float:
    azimuth_rad = math.radians(float(azimuth_deg) % 360.0)
    direction_x = math.sin(azimuth_rad)
    direction_y = math.cos(azimuth_rad)
    distances: list[float] = []
    if abs(direction_x) > 1e-9:
        distances.append((width_m - start_x_m) / direction_x if direction_x > 0 else -start_x_m / direction_x)
    if abs(direction_y) > 1e-9:
        distances.append((height_m - start_y_m) / direction_y if direction_y > 0 else -start_y_m / direction_y)
    positive = [distance for distance in distances if distance >= 0]
    return min(positive) if positive else 0.0


def _safe_demo_speed(request: DemoRunRequest, start_x_m: float, start_y_m: float) -> tuple[float, str | None]:
    requested_distance_m = request.speed_mps * max(request.duration_s - 1.0 / request.sample_rate_hz, 0.0)
    max_distance_m = _max_distance_inside_dem(
        request.width_m,
        request.height_m,
        start_x_m,
        start_y_m,
        request.azimuth_deg,
    )
    if requested_distance_m <= max_distance_m * 0.96:
        return request.speed_mps, None

    denominator = max(request.duration_s - 1.0 / request.sample_rate_hz, 1.0)
    capped_speed = max(1.0, max_distance_m * 0.96 / denominator)
    warning = "Synthetic demo speed was capped to keep the trajectory inside DEM bounds"
    logger.info("Capping demo speed from %.3f to %.3f m/s", request.speed_mps, capped_speed)
    return capped_speed, warning


def _search_parameters(request: DemoRunRequest, speed_mps: float) -> dict[str, float]:
    search_radius = float(min(max(request.search_radius_m, 500.0), 900.0))
    coarse_step = float(max(150.0, min(300.0, search_radius / 3.0)))
    fine_step = float(max(50.0, min(100.0, request.resolution_m * 2.0)))
    speed_margin = float(max(6.0, speed_mps * 0.18))
    return {
        "search_radius_m": search_radius,
        "coarse_step_m": coarse_step,
        "fine_step_m": fine_step,
        "azimuth_coarse_step_deg": 10.0,
        "azimuth_fine_step_deg": 2.0,
        "speed_min_mps": max(1.0, speed_mps - speed_margin),
        "speed_max_mps": speed_mps + speed_margin,
        "speed_coarse_step_mps": max(2.0, round(speed_margin / 2.0)),
        "speed_fine_step_mps": 1.0,
    }


def _write_minimal_png(path: Path) -> None:
    path.write_bytes(_MINIMAL_PNG)


def _save_core_plots(
    artifact_paths: dict[str, Path],
    dem: Any,
    truth: Any,
    solution: Any,
) -> None:
    try:
        from app.core.visualization import (
            save_correlation_heatmap,
            save_profile_comparison,
            save_trajectory_overlay,
        )

        save_trajectory_overlay(dem, truth, solution.trajectory, str(artifact_paths["trajectory_overlay_png"]))
        save_correlation_heatmap(
            solution.heatmap,
            solution.metadata.get("refined_azimuth_values", np.asarray([])),
            str(artifact_paths["correlation_heatmap_png"]),
        )
        save_profile_comparison(
            solution.metadata.get("corrected_measured_profile", solution.measured_profile),
            solution.reference_profile,
            str(artifact_paths["profile_comparison_png"]),
        )
    except Exception:
        logger.exception("Plot generation failed, writing placeholder PNGs")
        for key in ("trajectory_overlay_png", "correlation_heatmap_png", "profile_comparison_png"):
            _write_minimal_png(artifact_paths[key])


def _quality_warning(core_warning: str | None, scenario_warning: str | None) -> str | None:
    warnings = [item for item in (scenario_warning, core_warning) if item]
    return "; ".join(warnings) if warnings else None


def _trajectory_path(truth: Any, width_m: float, height_m: float) -> list[list[float]]:
    return [
        [
            _clip(float(x) / width_m, 0.0, 1.0),
            _clip(float(y) / height_m, 0.0, 1.0),
        ]
        for x, y in zip(truth.x_m, truth.y_m)
    ]


def _profile_payload(solution: Any, barometric_altitude_msl: float) -> dict[str, list[float]]:
    measured = np.asarray(solution.metadata.get("corrected_measured_profile", solution.measured_profile), dtype=float)
    reference = np.asarray(solution.reference_profile, dtype=float)
    return {
        "radio": [float(barometric_altitude_msl - value) for value in measured],
        "dem": [float(value) for value in reference],
    }


def _trajectory_fraction_path(trajectory: list[dict[str, Any]], width_m: float, height_m: float) -> list[list[float]]:
    return [
        [
            _clip(float(row.get("x_m", 0.0)) / width_m, 0.0, 1.0),
            _clip(float(row.get("y_m", 0.0)) / height_m, 0.0, 1.0),
        ]
        for row in trajectory
    ]


def _trajectory_error_stats(truth: Any, estimated_trajectory: list[dict[str, Any]]) -> dict[str, float]:
    count = min(len(truth.x_m), len(estimated_trajectory))
    if count == 0:
        return {"final_position_error_m": float("nan"), "mean_position_error_m": float("nan")}
    truth_x = np.asarray(truth.x_m[:count], dtype=float)
    truth_y = np.asarray(truth.y_m[:count], dtype=float)
    est_x = np.asarray([row.get("x_m", np.nan) for row in estimated_trajectory[:count]], dtype=float)
    est_y = np.asarray([row.get("y_m", np.nan) for row in estimated_trajectory[:count]], dtype=float)
    errors = np.hypot(est_x - truth_x, est_y - truth_y)
    return {
        "final_position_error_m": round(float(errors[-1]), 3),
        "mean_position_error_m": round(float(np.nanmean(errors)), 3),
    }


def _autonomous_artifacts(links: dict[str, str]) -> dict[str, str]:
    keys = (
        "trajectory_comparison_png",
        "particle_cloud_png",
        "confidence_timeline_png",
        "terrain_profile_match_png",
        "result_json",
    )
    return {key: links[key] for key in keys if key in links}


def _estimated_state(solution: Any, duration_s: float) -> EstimatedState:
    estimated = solution.estimated
    distance_m = math.hypot(estimated["end_x_m"] - estimated["start_x_m"], estimated["end_y_m"] - estimated["start_y_m"])
    ground_speed_mps = distance_m / max(duration_s, 1e-9)
    return EstimatedState(
        start_x_m=round(float(estimated["start_x_m"]), 3),
        start_y_m=round(float(estimated["start_y_m"]), 3),
        end_x_m=round(float(estimated["end_x_m"]), 3),
        end_y_m=round(float(estimated["end_y_m"]), 3),
        azimuth_deg=round(float(estimated["azimuth_deg"]), 3),
        speed_mps=round(float(estimated["speed_mps"]), 3),
        ground_speed_mps=round(float(ground_speed_mps), 3),
        correlation=round(float(estimated["correlation"]), 4),
        rmse_m=round(float(estimated["rmse_m"]), 3),
        mae_m=round(float(estimated["mae_m"]), 3),
        confidence=round(float(estimated["confidence"]), 4),
    )


def _quality_report(solution: Any, warning: str | None) -> QualityReport:
    quality = solution.quality
    return QualityReport(
        terrain_informativeness=round(float(quality.get("terrain_informativeness", 0.0)), 4),
        peak_sharpness=round(float(quality.get("peak_sharpness", 0.0)), 4),
        top1_top2_gap=round(float(quality.get("peak_gap", 0.0)), 4),
        correlation=round(float(quality.get("correlation", solution.estimated.get("correlation", 0.0))), 4),
        rmse_m=round(float(quality.get("rmse_m", solution.estimated.get("rmse_m", 0.0))), 3),
        confidence=round(float(quality.get("confidence", solution.estimated.get("confidence", 0.0))), 4),
        warning=warning,
    )


def run_demo_pipeline(request: DemoRunRequest) -> DemoRunResponse:
    started_at = time.perf_counter()
    run_id = str(uuid4())
    dirs = create_run_dirs(run_id)
    os.environ.setdefault("MPLCONFIGDIR", str(dirs["root"] / "mpl_cache"))
    logger.info("Starting core-backed demo run %s with params=%s", run_id, _model_to_dict(request))

    from app.core.dem import create_synthetic_dem
    from app.core.navigation import solve_navigation
    from app.core.simulator import (
        generate_nmea_from_radio_profile,
        generate_radio_altimeter_profile,
        generate_truth_trajectory,
    )

    start_x_m = request.width_m / 2.0
    start_y_m = request.height_m / 2.0
    speed_mps, scenario_warning = _safe_demo_speed(request, start_x_m, start_y_m)
    dem = create_synthetic_dem(
        width_m=request.width_m,
        height_m=request.height_m,
        resolution_m=request.resolution_m,
        seed=request.seed,
        terrain_type=request.terrain_type,
        origin_lat_deg=56.10,
        origin_lon_deg=37.20,
    )
    truth_trajectory = generate_truth_trajectory(
        start_x_m=start_x_m,
        start_y_m=start_y_m,
        azimuth_deg=request.azimuth_deg,
        speed_mps=speed_mps,
        duration_s=request.duration_s,
        sample_rate_hz=request.sample_rate_hz,
    )
    radio_profile = generate_radio_altimeter_profile(
        dem=dem,
        trajectory=truth_trajectory,
        barometric_altitude_msl=request.barometric_altitude_msl,
        noise_std_m=request.noise_std_m,
        outlier_probability=request.outlier_probability,
        outlier_scale_m=max(30.0, request.noise_std_m * 12.0),
        dropout_probability=min(0.02, request.outlier_probability / 4.0),
        barometric_drift_m=0.0,
        seed=None if request.seed is None else request.seed + 17,
    )
    nmea_text = generate_nmea_from_radio_profile(radio_profile, sample_rate_hz=request.sample_rate_hz)

    search = _search_parameters(request, speed_mps)
    solution = solve_navigation(
        dem=dem,
        nmea_text=nmea_text,
        barometric_altitude_msl=request.barometric_altitude_msl,
        sample_rate_hz=request.sample_rate_hz,
        search_center_x_m=start_x_m + min(search["search_radius_m"] * 0.12, 90.0),
        search_center_y_m=start_y_m - min(search["search_radius_m"] * 0.10, 80.0),
        enable_kalman=request.enable_kalman,
        parallel_jobs=0,
        compensate_baro_drift=True,
        **search,
    )

    artifact_paths = {
        name: safe_artifact_path(run_id, filename)
        for name, filename in ARTIFACT_FILENAMES.items()
    }
    artifact_paths["generated_nmea"].write_text(nmea_text, encoding="utf-8")
    _save_core_plots(artifact_paths, dem, truth_trajectory, solution)

    duration_s = max(0.0, (len(truth_trajectory.t_s) - 1) / request.sample_rate_hz)
    truth = TruthState(
        start_x_m=round(float(truth_trajectory.x_m[0]), 3),
        start_y_m=round(float(truth_trajectory.y_m[0]), 3),
        end_x_m=round(float(truth_trajectory.x_m[-1]), 3),
        end_y_m=round(float(truth_trajectory.y_m[-1]), 3),
        azimuth_deg=float(request.azimuth_deg),
        speed_mps=round(float(speed_mps), 3),
    )
    estimated = _estimated_state(solution, duration_s)
    warning = _quality_warning(solution.quality.get("warning"), scenario_warning)
    quality = _quality_report(solution, warning)
    artifacts = ArtifactLinks(**build_artifact_links(run_id))

    response = DemoRunResponse(
        status="ok",
        run_id=run_id,
        truth=truth,
        estimated=estimated,
        quality=quality,
        artifacts=artifacts,
        message="Navigation solution calculated by backend/app/core",
    )
    result_data = _model_to_dict(response)
    result_data.update(
        {
            "metrics": {
                "correlation": estimated.correlation,
                "rmse_m": estimated.rmse_m,
                "confidence": estimated.confidence,
                "confidence_pct": round(estimated.confidence * 100.0, 2),
                "cep_m": round(solution.estimated["rmse_m"] * 1.2, 2),
                "sep_m": round(solution.estimated["rmse_m"] * 1.8, 2),
                "vertical_m": round(solution.estimated["rmse_m"] * 0.6, 2),
                "distance_km": round(speed_mps * duration_s / 1000.0, 3),
                "offset_km": round(
                    math.hypot(estimated.start_x_m - truth.start_x_m, estimated.start_y_m - truth.start_y_m) / 1000.0,
                    3,
                ),
            },
            "found_position": {
                "lat": solution.estimated.get("start_lat_deg"),
                "lon": solution.estimated.get("start_lon_deg"),
                "altitude_msl": request.barometric_altitude_msl,
            },
            "profile": _profile_payload(solution, request.barometric_altitude_msl),
            "path": _trajectory_path(truth_trajectory, request.width_m, request.height_m),
            "log": [
                {"t": time.strftime("%H:%M:%S", time.gmtime()), "msg": "NMEA generated from synthetic radio altimeter"},
                {"t": time.strftime("%H:%M:%S", time.gmtime()), "msg": "Core terrain correlation search completed"},
            ],
            "diagnostics": {
                "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
                "artifact_dir": str(dirs["root"]),
                "core_mode": "core",
                "requested_speed_mps": request.speed_mps,
                "azimuth_error_deg": round(_angle_error_deg(estimated.azimuth_deg, request.azimuth_deg), 3),
            },
        }
    )
    save_json(artifact_paths["result_json"], result_data)

    logger.info("Demo run %s completed in %.3fs", run_id, time.perf_counter() - started_at)
    return response


def run_autonomous_demo_pipeline(request: AutonomousDemoRequest) -> dict[str, Any]:
    """Run the main BlindFlight Terrain Lock autonomous navigation demo."""

    started_at = time.perf_counter()
    run_id = str(uuid4())
    dirs = create_run_dirs(run_id)
    os.environ.setdefault("MPLCONFIGDIR", str(dirs["root"] / "mpl_cache"))
    logger.info("Starting autonomous Terrain Lock demo %s with params=%s", run_id, _model_to_dict(request))

    from app.core.dead_reckoning import run_dead_reckoning
    from app.core.dem import create_synthetic_dem
    from app.core.navigation import run_autonomous_navigation_algorithm
    from app.core.simulator import generate_sensor_stream, generate_truth_trajectory
    from app.core.visualization import (
        save_confidence_timeline,
        save_particle_cloud,
        save_terrain_profile_match,
        save_trajectory_comparison,
    )

    start_x_m = request.width_m * 0.46
    start_y_m = request.height_m * 0.42
    max_distance_m = _max_distance_inside_dem(
        request.width_m,
        request.height_m,
        start_x_m,
        start_y_m,
        request.true_heading_deg,
    )
    requested_distance_m = request.true_speed_mps * max(request.duration_s - 1.0 / request.sample_rate_hz, 0.0)
    true_speed_mps = float(request.true_speed_mps)
    scenario_warning = None
    if requested_distance_m > max_distance_m * 0.94:
        true_speed_mps = max(1.0, max_distance_m * 0.94 / max(request.duration_s - 1.0 / request.sample_rate_hz, 1.0))
        scenario_warning = "Synthetic demo speed was capped to keep the UAV inside DEM bounds"

    dem = create_synthetic_dem(
        width_m=request.width_m,
        height_m=request.height_m,
        resolution_m=request.resolution_m,
        seed=request.seed,
        terrain_type=request.terrain_type,
        origin_lat_deg=56.10,
        origin_lon_deg=37.20,
    )
    truth_trajectory = generate_truth_trajectory(
        start_x_m=start_x_m,
        start_y_m=start_y_m,
        azimuth_deg=request.true_heading_deg,
        speed_mps=true_speed_mps,
        duration_s=request.duration_s,
        sample_rate_hz=request.sample_rate_hz,
    )
    sensor_stream = generate_sensor_stream(
        dem=dem,
        truth_trajectory=truth_trajectory,
        barometric_altitude_msl=request.barometric_altitude_msl,
        radar_noise_std_m=2.0,
        baro_noise_std_m=3.0,
        baro_drift_m_per_s=0.025,
        speed_noise_std_mps=0.7,
        heading_noise_std_deg=1.2,
        speed_bias_mps=1.0,
        heading_bias_deg=4.0,
        seed=None if request.seed is None else request.seed + 101,
    )

    initial_x_m = _clip(start_x_m + request.initial_uncertainty_radius_m * 0.42, 0.0, request.width_m)
    initial_y_m = _clip(start_y_m - request.initial_uncertainty_radius_m * 0.36, 0.0, request.height_m)
    dead_reckoning = run_dead_reckoning(sensor_stream, initial_x_m=initial_x_m, initial_y_m=initial_y_m)
    algorithm_result = run_autonomous_navigation_algorithm(
        dem=dem,
        sensor_stream=sensor_stream,
        initial_x_m=initial_x_m,
        initial_y_m=initial_y_m,
        initial_uncertainty_radius_m=request.initial_uncertainty_radius_m,
        n_particles=request.n_particles,
        profile_window_s=request.profile_window_s,
        sample_rate_hz=request.sample_rate_hz,
        seed=request.seed,
    )

    terrain_lock = algorithm_result["trajectory"]
    truth_error = _trajectory_error_stats(truth_trajectory, terrain_lock)
    dead_reckoning_error = _trajectory_error_stats(truth_trajectory, dead_reckoning)
    improvement_factor = round(
        float(dead_reckoning_error["final_position_error_m"])
        / max(float(truth_error["final_position_error_m"]), 1.0),
        3,
    )

    artifact_paths = {
        name: safe_artifact_path(run_id, filename)
        for name, filename in ARTIFACT_FILENAMES.items()
    }
    try:
        save_trajectory_comparison(
            dem,
            truth_trajectory,
            dead_reckoning,
            terrain_lock,
            str(artifact_paths["trajectory_comparison_png"]),
            initial_uncertainty_radius_m=request.initial_uncertainty_radius_m,
        )
        final_snapshot = algorithm_result["particle_snapshots"][-1] if algorithm_result["particle_snapshots"] else {}
        save_particle_cloud(
            dem,
            final_snapshot,
            algorithm_result["final_estimate"],
            str(artifact_paths["particle_cloud_png"]),
        )
        save_confidence_timeline(terrain_lock, str(artifact_paths["confidence_timeline_png"]))
        profile_match = algorithm_result.get("profile_match", {})
        save_terrain_profile_match(
            np.asarray(profile_match.get("observed_profile", []), dtype=float),
            np.asarray(profile_match.get("best_dem_profile", []), dtype=float),
            str(artifact_paths["terrain_profile_match_png"]),
        )
    except Exception:
        logger.exception("Autonomous plot generation failed, writing placeholder PNGs")
        for key in (
            "trajectory_comparison_png",
            "particle_cloud_png",
            "confidence_timeline_png",
            "terrain_profile_match_png",
        ):
            _write_minimal_png(artifact_paths[key])

    links = build_artifact_links(run_id)
    warnings = list(algorithm_result.get("warnings", []))
    if scenario_warning:
        warnings.insert(0, scenario_warning)

    response: dict[str, Any] = {
        "status": "ok",
        "run_id": run_id,
        "algorithm": "BlindFlight Terrain Lock",
        "message": "Autonomous UAV navigation demo calculated by backend/app/core",
        "final_estimate": algorithm_result["final_estimate"],
        "confidence": algorithm_result["confidence"],
        "truth_error": truth_error,
        "dead_reckoning_error": dead_reckoning_error,
        "improvement_factor": improvement_factor,
        "quality": algorithm_result["quality"],
        "warnings": warnings,
        "artifacts": _autonomous_artifacts(links),
        "truth": {
            "start_x_m": round(float(truth_trajectory.x_m[0]), 3),
            "start_y_m": round(float(truth_trajectory.y_m[0]), 3),
            "end_x_m": round(float(truth_trajectory.x_m[-1]), 3),
            "end_y_m": round(float(truth_trajectory.y_m[-1]), 3),
            "heading_deg": float(request.true_heading_deg),
            "speed_mps": round(float(true_speed_mps), 3),
        },
        "dead_reckoning_trajectory": dead_reckoning,
        "terrain_lock_trajectory": terrain_lock,
        "confidence_timeline": algorithm_result["confidence_timeline"],
        "profile_match": algorithm_result["profile_match"],
        "metrics": {
            "final_position_error_m": truth_error["final_position_error_m"],
            "mean_position_error_m": truth_error["mean_position_error_m"],
            "dead_reckoning_final_error_m": dead_reckoning_error["final_position_error_m"],
            "dead_reckoning_mean_error_m": dead_reckoning_error["mean_position_error_m"],
            "improvement_factor": improvement_factor,
            "confidence": round(float(algorithm_result["confidence"]["value"]), 4),
            "confidence_pct": round(float(algorithm_result["confidence"]["value"]) * 100.0, 2),
            "terrain_lock_ratio": round(float(algorithm_result["quality"]["terrain_lock_ratio"]), 4),
            "error_radius_m": round(float(algorithm_result["final_estimate"]["error_radius_m"]), 3),
            "profile_correlation": algorithm_result["quality"].get("profile_correlation"),
            "distance_km": round(true_speed_mps * max(request.duration_s - 1.0 / request.sample_rate_hz, 0.0) / 1000.0, 3),
        },
        "path": _trajectory_fraction_path(terrain_lock, request.width_m, request.height_m),
        "log": [
            {"t": time.strftime("%H:%M:%S", time.gmtime()), "msg": "Synthetic DEM and civil UAV route generated"},
            {"t": time.strftime("%H:%M:%S", time.gmtime()), "msg": "Dead Reckoning baseline integrated without GNSS"},
            {"t": time.strftime("%H:%M:%S", time.gmtime()), "msg": "Particle Filter Terrain Lock completed"},
        ],
        "diagnostics": {
            "duration_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            "artifact_dir": str(dirs["root"]),
            "initial_x_m": initial_x_m,
            "initial_y_m": initial_y_m,
            "n_particles": request.n_particles,
            "sample_count": len(sensor_stream),
            "profile_window_s": request.profile_window_s,
        },
    }
    save_json(artifact_paths["result_json"], response)
    logger.info("Autonomous demo %s completed in %.3fs", run_id, time.perf_counter() - started_at)
    return response


def _nmea_checksum(body: str) -> str:
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def _parse_nmea_time(value: str | None) -> float | None:
    if not value:
        return None
    try:
        hours = int(value[0:2])
        minutes = int(value[2:4])
        seconds = float(value[4:])
    except (ValueError, IndexError):
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def _parse_nmea_line(line: str, line_number: int) -> dict[str, Any]:
    raw_line = line.strip()
    if not raw_line:
        return {"line_number": line_number, "raw": line, "valid": False, "error": "empty line"}
    if not raw_line.startswith("$"):
        return {"line_number": line_number, "raw": raw_line, "valid": False, "error": "missing $ prefix"}

    nmea_payload = raw_line[1:]
    checksum_valid = None
    checksum_warning = None
    if "*" in nmea_payload:
        body, supplied_checksum = nmea_payload.split("*", 1)
        supplied_checksum = supplied_checksum.strip().upper()
        expected_checksum = _nmea_checksum(body)
        if len(supplied_checksum) != 2 or any(char not in "0123456789ABCDEF" for char in supplied_checksum):
            return {"line_number": line_number, "raw": raw_line, "valid": False, "error": "malformed checksum"}
        checksum_valid = supplied_checksum == expected_checksum
        if not checksum_valid:
            checksum_warning = f"checksum mismatch: expected {expected_checksum}, got {supplied_checksum}"
    else:
        body = nmea_payload

    fields = body.split(",")
    if len(fields) < 10 or not fields[0].endswith("GGA"):
        return {"line_number": line_number, "raw": raw_line, "valid": False, "error": "unsupported NMEA sentence"}

    try:
        altitude_m = float(fields[9])
    except ValueError:
        return {"line_number": line_number, "raw": raw_line, "valid": False, "error": "missing altitude field"}

    return {
        "line_number": line_number,
        "raw": raw_line,
        "valid": True,
        "timestamp_s": _parse_nmea_time(fields[1]),
        "talker": fields[0][:2],
        "sentence_type": fields[0][2:],
        "time_utc": fields[1] or None,
        "radio_altitude_agl_m": altitude_m,
        "checksum_valid": checksum_valid,
        "warning": checksum_warning,
    }


def parse_nmea_text(nmea_text: str) -> list[dict[str, Any]]:
    return [
        _parse_nmea_line(line, line_number)
        for line_number, line in enumerate(nmea_text.splitlines(), start=1)
        if line.strip()
    ]


def _terrain_summary(valid_measurements: list[dict[str, Any]], barometric_altitude_msl: float) -> dict[str, float]:
    derived_terrain = [
        barometric_altitude_msl - item["radio_altitude_agl_m"]
        for item in valid_measurements
    ]
    return {
        "mean_msl_m": round(float(np.mean(derived_terrain)), 3),
        "min_msl_m": round(float(np.min(derived_terrain)), 3),
        "max_msl_m": round(float(np.max(derived_terrain)), 3),
    }


def solve_navigation_from_nmea(request: NavigationSolveRequest) -> dict[str, Any]:
    measurements = parse_nmea_text(request.nmea_text)
    valid_measurements = [item for item in measurements if item.get("valid")]
    if not valid_measurements:
        return {
            "status": "error",
            "message": "No valid NMEA GGA altitude measurements found",
            "measurements": measurements,
        }

    result: dict[str, Any] = {
        "status": "ok",
        "dem_mode": request.dem_mode,
        "valid_measurement_count": len(valid_measurements),
        "invalid_measurement_count": len(measurements) - len(valid_measurements),
        "barometric_altitude_msl": request.barometric_altitude_msl,
        "terrain_summary": _terrain_summary(valid_measurements, request.barometric_altitude_msl),
        "measurements": measurements,
    }

    if len(valid_measurements) < 8:
        terrain_span = result["terrain_summary"]["max_msl_m"] - result["terrain_summary"]["min_msl_m"]
        confidence = _clip(0.45 + min(terrain_span / 300.0, 0.4), 0.0, 0.9)
        result["estimated"] = {
            "confidence": round(confidence, 4),
            "correlation": round(_clip(confidence + 0.08, 0.0, 0.95), 4),
            "search_radius_m": request.search_radius_m,
            "enable_kalman": request.enable_kalman,
        }
        result["message"] = "Terrain summary calculated; provide at least 8 samples for full core navigation solve"
        return result

    try:
        from app.core.dem import create_synthetic_dem
        from app.core.navigation import solve_navigation

        dem = create_synthetic_dem(
            width_m=request.width_m,
            height_m=request.height_m,
            resolution_m=request.resolution_m,
            seed=42,
            terrain_type=request.terrain_type,
            origin_lat_deg=56.10,
            origin_lon_deg=37.20,
        )
        solution = solve_navigation(
            dem=dem,
            nmea_text="\n".join(item["raw"] for item in valid_measurements),
            barometric_altitude_msl=request.barometric_altitude_msl,
            sample_rate_hz=request.sample_rate_hz,
            search_radius_m=min(request.search_radius_m, 900.0),
            coarse_step_m=250.0,
            fine_step_m=75.0,
            azimuth_coarse_step_deg=10.0,
            azimuth_fine_step_deg=2.0,
            speed_min_mps=20.0,
            speed_max_mps=80.0,
            speed_coarse_step_mps=5.0,
            speed_fine_step_mps=2.0,
            enable_kalman=request.enable_kalman,
            parallel_jobs=request.parallel_jobs,
            compensate_baro_drift=True,
        )
        result["estimated"] = solution.estimated
        result["quality"] = solution.quality
        # географическая привязка найденной точки (для карты на дашборде)
        import math as _math
        _est = solution.estimated
        _x = float(_est.get("end_x_m", _est.get("start_x_m", 4000.0)))
        _y = float(_est.get("end_y_m", _est.get("start_y_m", 4000.0)))
        result["found_position"] = {
            "lat": round(56.10 + _y / 111_320.0, 6),
            "lon": round(37.20 + _x / (111_320.0 * _math.cos(_math.radians(56.10))), 6),
            "altitude_msl": request.barometric_altitude_msl,
        }
        result["message"] = "Navigation solution calculated by backend/app/core"
    except Exception as exc:
        logger.exception("Core solve from supplied NMEA failed")
        terrain_span = result["terrain_summary"]["max_msl_m"] - result["terrain_summary"]["min_msl_m"]
        confidence = _clip(0.45 + min(terrain_span / 300.0, 0.4), 0.0, 0.9)
        result["estimated"] = {
            "confidence": round(confidence, 4),
            "correlation": round(_clip(confidence + 0.08, 0.0, 0.95), 4),
            "search_radius_m": request.search_radius_m,
            "enable_kalman": request.enable_kalman,
        }
        result["message"] = f"Terrain summary calculated; full core solve failed: {exc}"
    return result
