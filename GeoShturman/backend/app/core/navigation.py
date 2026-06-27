"""High-level navigation solve pipeline."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

from .confidence import compute_navigation_confidence, terrain_informativeness
from .correlation import CandidateResult, coarse_search, refine_search_around_candidates
from .dem import DEMData, dem_xy_to_geodetic
from .kalman import kalman_smooth_1d
from .metrics import build_quality_report
from .nmea import parse_nmea_text
from .particle_filter import (
    ParticleState,
    effective_sample_size,
    estimate_state,
    initialize_particles,
    predict_particles,
    systematic_resample,
    update_weights_instant_height,
)
from .profile import align_profile_to_reference, clean_profile, radio_agl_to_terrain_msl
from .terrain_matcher import (
    observed_terrain_profile,
    profile_correlation,
    reference_profile_for_state,
    update_weights_profile_match,
)


@dataclass
class NavigationSolution:
    estimated: dict
    quality: dict
    measured_profile: np.ndarray
    reference_profile: np.ndarray
    trajectory: dict
    candidates: list
    heatmap: np.ndarray
    metadata: dict


def solve_navigation(
    dem: DEMData,
    nmea_text: str,
    barometric_altitude_msl: float = 1500.0,
    sample_rate_hz: float = 5.0,
    **kwargs,
):
    """Навигационное решение по NMEA-радиовысоте.

    ЕДИНОЕ ГОРЛЫШКО: все потребители (live-стрим, demo-пайплайн, /solve)
    зовут именно эту функцию. По умолчанию (NAV_ENGINE != "native") расчёт
    делегируется ЯДРУ ТЕАРКОМА через app.core.tercom_bridge, поэтому
    визуализация показывает результат алгоритма tercom_uav без правок
    фронтенда. При любой ошибке моста — прозрачный откат на родной движок.
    Принудительно вернуть родной движок: переменная окружения
    NAV_ENGINE=native.
    """

    engine = os.environ.get("NAV_ENGINE", "tercom").strip().lower()
    if engine != "native":
        try:
            from app.core.tercom_bridge import solve_navigation_via_tercom

            return solve_navigation_via_tercom(
                dem=dem,
                nmea_text=nmea_text,
                barometric_altitude_msl=barometric_altitude_msl,
                sample_rate_hz=sample_rate_hz,
                **kwargs,
            )
        except Exception:
            logger.exception("tercom bridge failed; falling back to native solver")

    return _solve_navigation_native(
        dem=dem,
        nmea_text=nmea_text,
        barometric_altitude_msl=barometric_altitude_msl,
        sample_rate_hz=sample_rate_hz,
        **kwargs,
    )


def _solve_navigation_native(
    dem: DEMData,
    nmea_text: str,
    barometric_altitude_msl: float = 1500.0,
    sample_rate_hz: float = 5.0,
    search_center_x_m: float | None = None,
    search_center_y_m: float | None = None,
    search_radius_m: float = 2000.0,
    coarse_step_m: float = 250.0,
    fine_step_m: float = 50.0,
    azimuth_coarse_step_deg: float = 5.0,
    azimuth_fine_step_deg: float = 1.0,
    speed_min_mps: float = 20.0,
    speed_max_mps: float = 80.0,
    speed_coarse_step_mps: float = 5.0,
    speed_fine_step_mps: float = 1.0,
    enable_kalman: bool = True,
    parallel_jobs: int | None = 1,
    compensate_baro_drift: bool = True,
) -> NavigationSolution:
    """Родной грид-корреляционный решатель (оставлен как fallback и эталон)."""

    parsed = parse_nmea_text(nmea_text)
    valid_altitudes = [item.altitude_m for item in parsed if item.parsed_ok and item.altitude_m is not None]
    if not valid_altitudes:
        raise ValueError("NMEA text contains no valid altitude samples")

    radio_profile = np.asarray(valid_altitudes, dtype=float)
    measured_profile = radio_agl_to_terrain_msl(radio_profile, barometric_altitude_msl)
    measured_profile = clean_profile(
        measured_profile,
        median_window=3,
        max_jump_m=120.0,
        hampel_window=7,
        outlier_sigma=3.5,
    )
    if enable_kalman:
        measured_profile = kalman_smooth_1d(measured_profile, process_variance=2.0, measurement_variance=6.0)

    center_x = float(search_center_x_m) if search_center_x_m is not None else dem.origin_x_m + dem.width_m / 2.0
    center_y = float(search_center_y_m) if search_center_y_m is not None else dem.origin_y_m + dem.height_m / 2.0

    coarse = coarse_search(
        dem=dem,
        measured_terrain_profile=measured_profile,
        sample_rate_hz=sample_rate_hz,
        search_center_x_m=center_x,
        search_center_y_m=center_y,
        search_radius_m=search_radius_m,
        search_step_m=coarse_step_m,
        azimuth_step_deg=azimuth_coarse_step_deg,
        speed_min_mps=speed_min_mps,
        speed_max_mps=speed_max_mps,
        speed_step_mps=speed_coarse_step_mps,
        top_k=12,
        n_jobs=parallel_jobs,
        compensate_drift=compensate_baro_drift,
    )
    refined = refine_search_around_candidates(
        dem=dem,
        measured_terrain_profile=measured_profile,
        sample_rate_hz=sample_rate_hz,
        coarse_result=coarse,
        search_radius_m=max(coarse_step_m, fine_step_m),
        search_step_m=fine_step_m,
        azimuth_window_deg=max(azimuth_coarse_step_deg, azimuth_fine_step_deg * 3.0),
        azimuth_step_deg=azimuth_fine_step_deg,
        speed_window_mps=max(speed_coarse_step_mps, speed_fine_step_mps * 3.0),
        speed_step_mps=speed_fine_step_mps,
        top_n=5,
        top_k=12,
        n_jobs=parallel_jobs,
        compensate_drift=compensate_baro_drift,
    )

    best = refined.best
    quality = build_quality_report(refined.candidates, measured_profile, best)
    corrected_measured, drift_report = align_profile_to_reference(measured_profile, best.reference_profile, degree=1)
    estimated = {
        "start_x_m": best.start_x_m,
        "start_y_m": best.start_y_m,
        "end_x_m": best.end_x_m,
        "end_y_m": best.end_y_m,
        "azimuth_deg": best.azimuth_deg,
        "speed_mps": best.speed_mps,
        "correlation": best.correlation,
        "rmse_m": best.rmse_m,
        "mae_m": best.mae_m,
        "combined_score": best.combined_score,
        "confidence": quality["confidence"],
        "baro_drift_offset_m": best.drift_offset_m,
        "baro_drift_slope_m_per_sample": best.drift_slope_m_per_sample,
    }
    start_geo = dem_xy_to_geodetic(dem, best.start_x_m, best.start_y_m)
    end_geo = dem_xy_to_geodetic(dem, best.end_x_m, best.end_y_m)
    if start_geo is not None and end_geo is not None:
        estimated.update(
            {
                "start_lat_deg": start_geo.lat_deg,
                "start_lon_deg": start_geo.lon_deg,
                "end_lat_deg": end_geo.lat_deg,
                "end_lon_deg": end_geo.lon_deg,
            }
        )
    trajectory = {
        "start": _point_to_dict(dem, best.start_x_m, best.start_y_m),
        "end": _point_to_dict(dem, best.end_x_m, best.end_y_m),
        "duration_s": max(0.0, (measured_profile.size - 1) / float(sample_rate_hz)),
        "sample_count": int(measured_profile.size),
    }

    return NavigationSolution(
        estimated=estimated,
        quality=quality,
        measured_profile=measured_profile,
        reference_profile=best.reference_profile,
        trajectory=trajectory,
        candidates=[_candidate_to_dict(candidate) for candidate in refined.candidates],
        heatmap=refined.heatmap,
        metadata={
            "parsed_samples": len(parsed),
            "valid_samples": int(radio_profile.size),
            "sample_rate_hz": float(sample_rate_hz),
            "barometric_altitude_msl": float(barometric_altitude_msl),
            "enable_kalman": bool(enable_kalman),
            "parallel_jobs": parallel_jobs,
            "compensate_baro_drift": bool(compensate_baro_drift),
            "baro_drift": drift_report,
            "corrected_measured_profile": corrected_measured,
            "coarse": coarse.metadata,
            "coarse_azimuth_values": coarse.azimuth_values,
            "refined": refined.metadata,
            "refined_azimuth_values": refined.azimuth_values,
        },
    )


def run_autonomous_navigation_algorithm(
    dem: DEMData,
    sensor_stream: list[dict],
    initial_x_m: float,
    initial_y_m: float,
    initial_uncertainty_radius_m: float = 500.0,
    n_particles: int = 5000,
    profile_window_s: float = 30.0,
    sample_rate_hz: float = 5.0,
    seed: int | None = 42,
) -> dict:
    """Run Dead-Reckoning + Terrain Lock particle-filter navigation."""

    if not sensor_stream:
        raise ValueError("sensor_stream must not be empty")
    if n_particles <= 0:
        raise ValueError("n_particles must be positive")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if profile_window_s <= 0:
        raise ValueError("profile_window_s must be positive")

    stream = sorted(sensor_stream, key=lambda item: float(item.get("t_s", 0.0)))
    first = stream[0]
    rng_seed = 0 if seed is None else int(seed)
    particles = initialize_particles(
        n_particles=int(n_particles),
        center_x_m=float(initial_x_m),
        center_y_m=float(initial_y_m),
        radius_m=float(initial_uncertainty_radius_m),
        heading_deg=float(first.get("heading_deg", 0.0)),
        heading_std_deg=10.0,
        speed_mps=float(first.get("speed_mps", 0.0)),
        speed_std_mps=max(1.5, abs(float(first.get("speed_mps", 0.0))) * 0.15),
        baro_bias_std_m=12.0,
        radar_bias_std_m=4.0,
        seed=seed,
    )

    window_samples = max(3, int(round(float(profile_window_s) * float(sample_rate_hz))))
    profile_update_interval = max(1, int(round(float(sample_rate_hz) * 5.0)))
    trajectory_estimates: list[dict] = []
    confidence_timeline: list[dict] = []
    particle_snapshots: list[dict] = []
    warnings: list[str] = []
    baro_history: list[float] = []
    radar_history: list[float] = []
    last_profile_correlation: float | None = None
    last_observed_profile: np.ndarray | None = None
    last_reference_profile: np.ndarray | None = None
    previous_t = float(first.get("t_s", 0.0))
    snapshot_indexes = {0, max(0, len(stream) // 2), max(0, len(stream) - 1)}

    for index, measurement in enumerate(stream):
        t_s = float(measurement.get("t_s", previous_t))
        dt_s = max(0.0, t_s - previous_t) if index > 0 else 0.0
        measured_speed = float(measurement.get("speed_mps", 0.0))
        measured_heading = float(measurement.get("heading_deg", 0.0))
        baro_msl = float(measurement["barometric_altitude_msl"])
        radar_agl = float(measurement["radar_altitude_agl"])

        if index > 0:
            particles = predict_particles(
                particles,
                dt_s=dt_s,
                measured_speed_mps=measured_speed,
                measured_heading_deg=measured_heading,
                seed=None if seed is None else rng_seed + index * 17,
            )
        particles = update_weights_instant_height(
            particles,
            dem=dem,
            barometric_altitude_msl=baro_msl,
            radar_altitude_agl=radar_agl,
            sigma_alt_m=18.0,
        )

        baro_history.append(baro_msl)
        radar_history.append(radar_agl)
        if len(baro_history) >= window_samples and (index + 1) % profile_update_interval == 0:
            observed = observed_terrain_profile(
                np.asarray(baro_history[-window_samples:], dtype=float),
                np.asarray(radar_history[-window_samples:], dtype=float),
            )
            observed = clean_profile(observed, median_window=3, max_jump_m=140.0, hampel_window=7, outlier_sigma=3.5)
            particles = update_weights_profile_match(
                particles=particles,
                dem=dem,
                trajectory_history_local={},
                observed_profile=observed,
                sample_rate_hz=sample_rate_hz,
                sigma_profile_m=45.0,
            )
            state_for_profile = estimate_state(particles)
            reference = reference_profile_for_state(
                dem=dem,
                x_m=state_for_profile["x_m"],
                y_m=state_for_profile["y_m"],
                heading_deg=state_for_profile["heading_deg"],
                speed_mps=state_for_profile["speed_mps"],
                n_samples=observed.size,
                sample_rate_hz=sample_rate_hz,
            )
            last_profile_correlation = profile_correlation(observed, reference)
            last_observed_profile = observed
            last_reference_profile = reference

        ess = effective_sample_size(particles.weights)
        ess_ratio = ess / float(particles.size)
        if ess < particles.size / 2.0:
            particles = systematic_resample(particles, seed=None if seed is None else rng_seed + index * 31)
            ess_ratio = 1.0

        state = estimate_state(particles)
        terrain_score = terrain_informativeness(dem, state["x_m"], state["y_m"], radius_m=500.0)
        confidence = compute_navigation_confidence(
            particle_error_radius_m=state["error_radius_m"],
            terrain_score=terrain_score,
            ess_ratio=ess_ratio,
            profile_correlation=last_profile_correlation,
        )
        if confidence.get("warning") and confidence["warning"] not in warnings:
            warnings.append(confidence["warning"])

        trajectory_row = {
            "t_s": t_s,
            "x_m": state["x_m"],
            "y_m": state["y_m"],
            "heading_deg": state["heading_deg"],
            "speed_mps": state["speed_mps"],
            "confidence": confidence["confidence"],
            "mode": confidence["mode"],
            "error_radius_m": state["error_radius_m"],
            "ess_ratio": ess_ratio,
        }
        trajectory_estimates.append(trajectory_row)
        confidence_timeline.append(
            {
                "t_s": t_s,
                "confidence": confidence["confidence"],
                "mode": confidence["mode"],
                "error_radius_m": state["error_radius_m"],
                "ess_ratio": ess_ratio,
            }
        )
        if index in snapshot_indexes:
            particle_snapshots.append(_particle_snapshot(particles, t_s=t_s, max_particles=900))
        previous_t = t_s

    final_estimate = estimate_state(particles)
    final_terrain_score = terrain_informativeness(dem, final_estimate["x_m"], final_estimate["y_m"], radius_m=500.0)
    final_ess_ratio = effective_sample_size(particles.weights) / float(particles.size)
    final_confidence = compute_navigation_confidence(
        particle_error_radius_m=final_estimate["error_radius_m"],
        terrain_score=final_terrain_score,
        ess_ratio=final_ess_ratio,
        profile_correlation=last_profile_correlation,
    )
    if final_confidence.get("warning") and final_confidence["warning"] not in warnings:
        warnings.append(final_confidence["warning"])

    mean_error_radius = float(np.mean([row["error_radius_m"] for row in trajectory_estimates]))
    terrain_lock_ratio = float(
        np.mean([1.0 if row["mode"] == "terrain_lock" else 0.0 for row in trajectory_estimates])
    )
    lost_lock_count = int(sum(1 for row in trajectory_estimates if row["mode"] == "lost"))

    return {
        "status": "ok",
        "algorithm": "BlindFlight Terrain Lock",
        "final_estimate": {
            "x_m": final_estimate["x_m"],
            "y_m": final_estimate["y_m"],
            "heading_deg": final_estimate["heading_deg"],
            "speed_mps": final_estimate["speed_mps"],
            "error_radius_m": final_estimate["error_radius_m"],
        },
        "confidence": {
            "value": final_confidence["confidence"],
            "mode": final_confidence["mode"],
            "warning": final_confidence["warning"],
        },
        "trajectory": trajectory_estimates,
        "confidence_timeline": confidence_timeline,
        "particle_snapshots": particle_snapshots,
        "profile_match": {
            "observed_profile": [] if last_observed_profile is None else last_observed_profile.tolist(),
            "best_dem_profile": [] if last_reference_profile is None else last_reference_profile.tolist(),
            "correlation": last_profile_correlation,
        },
        "quality": {
            "mean_error_radius_m": mean_error_radius,
            "terrain_lock_ratio": terrain_lock_ratio,
            "lost_lock_count": lost_lock_count,
            "terrain_informativeness": final_terrain_score,
            "ess_ratio": final_ess_ratio,
            "profile_correlation": last_profile_correlation,
        },
        "warnings": warnings,
    }


def _particle_snapshot(particles: ParticleState, t_s: float, max_particles: int = 900) -> dict:
    if particles.size <= max_particles:
        indexes = np.arange(particles.size)
    else:
        indexes = np.linspace(0, particles.size - 1, max_particles).astype(int)
    return {
        "t_s": float(t_s),
        "x_m": particles.x_m[indexes].tolist(),
        "y_m": particles.y_m[indexes].tolist(),
        "weights": particles.weights[indexes].tolist(),
    }


def _candidate_to_dict(candidate: CandidateResult) -> dict:
    return {
        "start_x_m": candidate.start_x_m,
        "start_y_m": candidate.start_y_m,
        "end_x_m": candidate.end_x_m,
        "end_y_m": candidate.end_y_m,
        "azimuth_deg": candidate.azimuth_deg,
        "speed_mps": candidate.speed_mps,
        "correlation": candidate.correlation,
        "rmse_m": candidate.rmse_m,
        "mae_m": candidate.mae_m,
        "combined_score": candidate.combined_score,
        "baro_drift_offset_m": candidate.drift_offset_m,
        "baro_drift_slope_m_per_sample": candidate.drift_slope_m_per_sample,
    }


def _point_to_dict(dem: DEMData, x_m: float, y_m: float) -> dict:
    point = {"x_m": float(x_m), "y_m": float(y_m)}
    geo = dem_xy_to_geodetic(dem, x_m, y_m)
    if geo is not None:
        point.update({"lat_deg": geo.lat_deg, "lon_deg": geo.lon_deg})
    return point
