"""Navigation state estimation from reconstructed terrain profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tercom_uav.config import CorrelationConfig, GPSFusionConfig, KalmanConfig
from tercom_uav.correlation import correlate_profile
from tercom_uav.dem import DEMGrid
from tercom_uav.gps import (
    GPSFix,
    GPSFusionState,
    first_usable_gps_anchor,
    fuse_navigation_estimate,
    tercom_only_diagnostics,
)
from tercom_uav.kalman import smooth_estimates
from tercom_uav.profiles import resample_by_distance
from tercom_uav.types import AccuracyMetrics, CorrelationResult, NavigationEstimate, TerrainProfile


@dataclass(slots=True)
class LocalizationResult:
    """Complete localization output for one NMEA stream."""

    correlation: CorrelationResult
    estimate: NavigationEstimate
    estimates: pd.DataFrame
    metrics: AccuracyMetrics
    diagnostics: dict[str, Any] = field(default_factory=dict)


def angle_error_deg(estimated_deg: float, truth_deg: float) -> float:
    """Smallest absolute angular difference in degrees."""

    return float(abs((estimated_deg - truth_deg + 180.0) % 360.0 - 180.0))


def track_angle_from_velocity_deg(vx_mps: float, vy_mps: float) -> float:
    """Return track angle clockwise from north for an x-east/y-north velocity."""

    return float((np.degrees(np.arctan2(vx_mps, vy_mps)) + 360.0) % 360.0)


def _estimate_from_correlation(
    dem: DEMGrid,
    result: CorrelationResult,
    speed_mps: float,
    time_s: float,
    center_x_m: float | None = None,
    center_y_m: float | None = None,
) -> NavigationEstimate:
    center_x, center_y = dem.center_m if center_x_m is None or center_y_m is None else (center_x_m, center_y_m)
    azimuth_rad = np.deg2rad(result.best_azimuth_deg)
    end_distance = float(result.distances_m[-1])
    along_track = result.best_shift_m + end_distance
    x = center_x + np.sin(azimuth_rad) * along_track
    y = center_y + np.cos(azimuth_rad) * along_track
    vx = np.sin(azimuth_rad) * speed_mps
    vy = np.cos(azimuth_rad) * speed_mps
    return NavigationEstimate(
        time_s=float(time_s),
        x_m=float(x),
        y_m=float(y),
        azimuth_deg=float(result.best_azimuth_deg),
        speed_mps=float(speed_mps),
        vx_mps=float(vx),
        vy_mps=float(vy),
        traveled_distance_m=end_distance,
        confidence_score=float(result.confidence_score),
        ambiguous_match=bool(result.ambiguous_match),
    )


def _dynamic_shift_config(dem: DEMGrid, config: CorrelationConfig) -> CorrelationConfig:
    x_min, y_min, x_max, y_max = dem.bounds_m
    half_span = min(x_max - x_min, y_max - y_min) * 0.5
    if config.shift_min_m != -6000.0 or config.shift_max_m != 6000.0:
        return config
    return CorrelationConfig(
        azimuth_step_deg=config.azimuth_step_deg,
        shift_min_m=-half_span,
        shift_max_m=half_span,
        shift_step_m=config.shift_step_m,
        sample_spacing_m=config.sample_spacing_m,
        coarse_to_fine=config.coarse_to_fine,
        coarse_azimuth_step_deg=config.coarse_azimuth_step_deg,
        coarse_shift_step_m=config.coarse_shift_step_m,
        fine_azimuth_radius_deg=config.fine_azimuth_radius_deg,
        fine_shift_radius_m=config.fine_shift_radius_m,
        min_correlation=config.min_correlation,
        min_score_gap=config.min_score_gap,
        min_relative_gap=config.min_relative_gap,
        min_observability=config.min_observability,
        speed_search_enabled=config.speed_search_enabled,
        speed_scale_min=config.speed_scale_min,
        speed_scale_max=config.speed_scale_max,
        speed_scale_step=config.speed_scale_step,
        speed_search_azimuth_step_deg=config.speed_search_azimuth_step_deg,
        speed_search_shift_step_m=config.speed_search_shift_step_m,
        speed_search_use_coarse_azimuth=config.speed_search_use_coarse_azimuth,
        tercom_quality_mode=config.tercom_quality_mode,
        max_search_radius_m=config.max_search_radius_m,
        max_candidates=config.max_candidates,
    )


def _gps_assisted_shift_config(config: CorrelationConfig, gps_config: GPSFusionConfig) -> CorrelationConfig:
    radius = float(gps_config.reacquire_window_radius_m)
    return CorrelationConfig(
        azimuth_step_deg=config.azimuth_step_deg,
        shift_min_m=max(config.shift_min_m, -radius),
        shift_max_m=min(config.shift_max_m, radius),
        shift_step_m=config.shift_step_m,
        sample_spacing_m=config.sample_spacing_m,
        coarse_to_fine=config.coarse_to_fine,
        coarse_azimuth_step_deg=config.coarse_azimuth_step_deg,
        coarse_shift_step_m=config.coarse_shift_step_m,
        fine_azimuth_radius_deg=config.fine_azimuth_radius_deg,
        fine_shift_radius_m=min(config.fine_shift_radius_m, radius),
        min_correlation=config.min_correlation,
        min_score_gap=config.min_score_gap,
        min_relative_gap=config.min_relative_gap,
        min_observability=config.min_observability,
        speed_search_enabled=config.speed_search_enabled,
        speed_scale_min=config.speed_scale_min,
        speed_scale_max=config.speed_scale_max,
        speed_scale_step=config.speed_scale_step,
        speed_search_azimuth_step_deg=config.speed_search_azimuth_step_deg,
        speed_search_shift_step_m=config.speed_search_shift_step_m,
        speed_search_use_coarse_azimuth=config.speed_search_use_coarse_azimuth,
        tercom_quality_mode=config.tercom_quality_mode,
        max_search_radius_m=min(
            radius,
            config.max_search_radius_m if config.max_search_radius_m is not None else radius,
        ),
        max_candidates=config.max_candidates,
    )


def _speed_candidates(speed_hint_mps: float, cfg: CorrelationConfig) -> np.ndarray:
    scales = np.arange(cfg.speed_scale_min, cfg.speed_scale_max + 1e-9, cfg.speed_scale_step)
    return speed_hint_mps * scales


def _cheap_screening_config(cfg: CorrelationConfig) -> CorrelationConfig:
    """A faster variant of `cfg` used only to rank speed hypotheses against
    each other. Only the shift step is coarsened (`speed_search_shift_step_m`):
    coarsening azimuth as well was tried first and rejected, because on
    periodic/low-frequency terrain it reintroduces the same wrong-local-optimum
    failure mode found for `coarse_to_fine` - a coarse azimuth grid combined
    with a wrong speed hypothesis can alias onto a wrong azimuth that
    correlates deceptively well. Azimuth resolution is therefore kept at the
    caller's exact `azimuth_step_deg`; only the (much larger, ~12 km) shift
    range is coarsened, which is the dimension that actually dominates the
    per-candidate cost. coarse_to_fine stays disabled here too: the goal is a
    quick, *consistent* relative ranking across candidate speeds, not a
    precise fix (that refinement happens once, after the winning speed is
    selected, using the caller's exact `cfg`)."""

    return CorrelationConfig(
        azimuth_step_deg=cfg.speed_search_azimuth_step_deg if cfg.speed_search_use_coarse_azimuth else cfg.azimuth_step_deg,
        shift_min_m=cfg.shift_min_m,
        shift_max_m=cfg.shift_max_m,
        shift_step_m=cfg.speed_search_shift_step_m,
        sample_spacing_m=cfg.sample_spacing_m,
        coarse_to_fine=False,
        min_correlation=cfg.min_correlation,
        min_score_gap=cfg.min_score_gap,
        min_relative_gap=cfg.min_relative_gap,
        min_observability=cfg.min_observability,
        speed_search_enabled=False,
        tercom_quality_mode="accurate",
        max_search_radius_m=cfg.max_search_radius_m,
        max_candidates=cfg.max_candidates,
    )


def correlate_with_speed_search(
    dem: DEMGrid,
    profile: TerrainProfile,
    speed_hint_mps: float,
    cfg: CorrelationConfig,
    center_x_m: float | None = None,
    center_y_m: float | None = None,
) -> tuple[float, CorrelationResult]:
    """Resolve the speed<->distance-scale circularity by trying several
    speed hypotheses and keeping the one whose resampled terrain profile
    correlates best with the DEM.

    Radio altitude alone carries no metric distance scale, so the original
    implementation always resampled time-to-distance using a single,
    externally supplied `speed_hint_mps`. If that hint is off, the resampled
    profile is stretched or compressed relative to the true ground track and
    TERCOM can lock onto a wrong, but locally well-correlated, location -
    exactly the failure mode behind the kinematic-gate fix in this module.
    Searching a small range of speed scales around the hint and picking the
    one that maximizes correlation makes speed an *output* of the matching
    process, as required by the task, instead of an unverified input.

    A cheap, coarser correlation config is used to rank candidates (this
    only needs to compare scores against each other, not deliver a precise
    fix), then the winning speed gets one precise pass with the caller's
    exact `cfg` for the final azimuth/shift.
    """

    if not cfg.speed_search_enabled:
        distances, terrain = resample_by_distance(profile, speed_hint_mps, cfg.sample_spacing_m)
        return speed_hint_mps, correlate_profile(dem, terrain, distances, cfg, center_x_m, center_y_m)

    screening_cfg = _cheap_screening_config(cfg)
    best_speed = speed_hint_mps
    best_score = -float("inf")
    for candidate_speed in _speed_candidates(speed_hint_mps, cfg):
        if candidate_speed <= 0:
            continue
        distances, terrain = resample_by_distance(profile, float(candidate_speed), cfg.sample_spacing_m)
        try:
            result = correlate_profile(dem, terrain, distances, screening_cfg, center_x_m, center_y_m)
        except ValueError:
            continue
        if np.isfinite(result.best_score) and result.best_score > best_score:
            best_score = result.best_score
            best_speed = float(candidate_speed)

    distances, terrain = resample_by_distance(profile, best_speed, cfg.sample_spacing_m)
    final_result = correlate_profile(dem, terrain, distances, cfg, center_x_m, center_y_m)
    return best_speed, final_result


def estimate_single_window(
    dem: DEMGrid,
    profile: TerrainProfile,
    speed_hint_mps: float,
    correlation_config: CorrelationConfig | None = None,
    search_center_x_m: float | None = None,
    search_center_y_m: float | None = None,
) -> tuple[CorrelationResult, NavigationEstimate]:
    """Run TERCOM over the full profile and return the final state estimate."""

    cfg = _dynamic_shift_config(dem, correlation_config or CorrelationConfig())
    estimated_speed, correlation = correlate_with_speed_search(
        dem,
        profile,
        speed_hint_mps,
        cfg,
        center_x_m=search_center_x_m,
        center_y_m=search_center_y_m,
    )
    estimate = _estimate_from_correlation(
        dem,
        correlation,
        estimated_speed,
        float(profile.times_s[-1]),
        center_x_m=search_center_x_m,
        center_y_m=search_center_y_m,
    )
    return correlation, estimate


def _is_kinematically_plausible(
    dx_m: float,
    dy_m: float,
    dt_s: float,
    max_speed_mps: float,
) -> bool:
    """Reject window-to-window jumps that no light UAV could fly.

    A raw TERCOM fix on smooth, low-frequency terrain (taiga/tundra/steppe)
    can lock onto a wrong but locally well-correlated location far from the
    true track. Such a fix implies an instantaneous speed of hundreds of
    m/s between consecutive 15s windows, which is physically impossible for
    the cargo UAVs in scope and is the cheapest, most reliable rejection
    test available without truth data.
    """

    implied_speed = float(np.hypot(dx_m, dy_m) / max(dt_s, 1e-6))
    return np.isfinite(implied_speed) and implied_speed <= max_speed_mps


def estimate_window_series(
    dem: DEMGrid,
    profile: TerrainProfile,
    speed_hint_mps: float,
    correlation_config: CorrelationConfig,
    window_duration_s: float = 45.0,
    step_s: float = 15.0,
    max_speed_mps: float = 120.0,
) -> pd.DataFrame:
    """Estimate state over sliding windows.

    Speed is refined from movement between consecutive window-end positions.
    The initial window uses `speed_hint_mps`, because radio altitude alone does
    not provide metric time-to-distance scale before map matching.

    Each new window's raw TERCOM fix is checked against `max_speed_mps`
    before being accepted: a fix that implies an impossible kinematic jump
    from the last accepted fix is rejected and replaced by a dead-reckoning
    extrapolation (last known velocity), rather than being passed on to
    accuracy metrics or the smoothing filter as if it were a real position.

    Each window's speed is itself re-derived by `correlate_with_speed_search`
    instead of being trusted from `speed_hint_mps` (see that function for
    why). A window's winning speed becomes the search center for the next
    window, since true cruise speed changes slowly - this narrows the search
    range over time instead of re-searching the same wide range from
    scratch, and lets the estimate track genuine speed changes.
    """

    if profile.duration_s < window_duration_s:
        _, estimate = estimate_single_window(dem, profile, speed_hint_mps, correlation_config)
        return pd.DataFrame([estimate.to_dict()])

    estimates: list[NavigationEstimate] = []
    cfg = _dynamic_shift_config(dem, correlation_config)
    current_speed_hint = speed_hint_mps
    start = float(profile.times_s[0])
    end = float(profile.times_s[-1])
    while start + window_duration_s <= end + 1e-9:
        mask = (profile.times_s >= start) & (profile.times_s <= start + window_duration_s)
        if int(mask.sum()) >= 8:
            window_times = profile.times_s[mask]
            window_profile = TerrainProfile(
                times_s=window_times - window_times[0],
                radio_alt_m=profile.radio_alt_m[mask],
                terrain_msl_m=profile.terrain_msl_m[mask],
            )
            estimated_speed, correlation = correlate_with_speed_search(dem, window_profile, current_speed_hint, cfg)
            estimate = _estimate_from_correlation(
                dem,
                correlation,
                estimated_speed,
                time_s=float(window_times[-1]),
            )

            if estimates:
                previous = estimates[-1]
                dt = max(estimate.time_s - previous.time_s, 1e-6)
                dx = estimate.x_m - previous.x_m
                dy = estimate.y_m - previous.y_m
                if not _is_kinematically_plausible(dx, dy, dt, max_speed_mps):
                    estimate = NavigationEstimate(
                        time_s=estimate.time_s,
                        x_m=previous.x_m + previous.vx_mps * dt,
                        y_m=previous.y_m + previous.vy_mps * dt,
                        azimuth_deg=previous.azimuth_deg,
                        speed_mps=previous.speed_mps,
                        vx_mps=previous.vx_mps,
                        vy_mps=previous.vy_mps,
                        traveled_distance_m=previous.traveled_distance_m
                        + previous.speed_mps * dt,
                        confidence_score=0.0,
                        ambiguous_match=True,
                        dead_reckoning=True,
                    )
            if not estimate.dead_reckoning:
                current_speed_hint = estimate.speed_mps
            estimates.append(estimate)
        start += step_s

    if not estimates:
        _, estimate = estimate_single_window(dem, profile, speed_hint_mps, correlation_config)
        return pd.DataFrame([estimate.to_dict()])

    frame = pd.DataFrame([estimate.to_dict() for estimate in estimates])
    for idx in range(1, len(frame)):
        if bool(frame.loc[idx, "dead_reckoning"]):
            continue
        dt = max(float(frame.loc[idx, "time_s"] - frame.loc[idx - 1, "time_s"]), 1e-6)
        dx = float(frame.loc[idx, "x_m"] - frame.loc[idx - 1, "x_m"])
        dy = float(frame.loc[idx, "y_m"] - frame.loc[idx - 1, "y_m"])
        speed = float(np.hypot(dx, dy) / dt)
        if np.isfinite(speed) and speed > 0:
            frame.loc[idx, "speed_mps"] = speed
            frame.loc[idx, "vx_mps"] = dx / dt
            frame.loc[idx, "vy_mps"] = dy / dt
            frame.loc[idx, "azimuth_deg"] = track_angle_from_velocity_deg(dx / dt, dy / dt)
    return frame


def compute_accuracy_metrics(
    estimates: pd.DataFrame,
    truth: pd.DataFrame | None,
    correlation: CorrelationResult,
) -> AccuracyMetrics:
    """Compute internal or truth-based accuracy metrics."""

    last = estimates.iloc[-1]
    metrics = AccuracyMetrics(
        confidence_score=float(last["confidence_score"]),
        ambiguity_flag=bool(last["ambiguous_match"]),
        terrain_roughness_score=float(correlation.roughness_score),
        observability_score=float(correlation.observability_score),
    )
    if truth is None or truth.empty:
        return metrics

    truth_sorted = truth.sort_values("time_s")
    estimate_times = estimates["time_s"].to_numpy(dtype=float)
    truth_x = np.interp(estimate_times, truth_sorted["time_s"], truth_sorted["x_m"])
    truth_y = np.interp(estimate_times, truth_sorted["time_s"], truth_sorted["y_m"])
    errors = np.hypot(estimates["x_m"].to_numpy(dtype=float) - truth_x, estimates["y_m"].to_numpy(dtype=float) - truth_y)
    final_truth_x = float(truth_x[-1])
    final_truth_y = float(truth_y[-1])
    final_truth_heading = float(np.interp(estimate_times[-1], truth_sorted["time_s"], truth_sorted["heading_deg"]))
    final_truth_speed = float(np.interp(estimate_times[-1], truth_sorted["time_s"], truth_sorted["speed_mps"]))

    metrics.horizontal_error_m = float(np.hypot(float(last["x_m"]) - final_truth_x, float(last["y_m"]) - final_truth_y))
    metrics.heading_error_deg = angle_error_deg(float(last["azimuth_deg"]), final_truth_heading)
    metrics.speed_error_mps = float(abs(float(last["speed_mps"]) - final_truth_speed))
    metrics.position_rmse_m = float(np.sqrt(np.mean(errors**2)))
    metrics.position_mae_m = float(np.mean(errors))
    return metrics


def localize_profile(
    dem: DEMGrid,
    profile: TerrainProfile,
    speed_hint_mps: float = 55.0,
    correlation_config: CorrelationConfig | None = None,
    kalman_config: KalmanConfig | None = None,
    truth: pd.DataFrame | None = None,
    window_duration_s: float = 45.0,
    step_s: float = 15.0,
    max_speed_mps: float = 120.0,
    gps_fixes: list[GPSFix] | None = None,
    gps_config: GPSFusionConfig | None = None,
    gps_state: GPSFusionState | None = None,
) -> LocalizationResult:
    """Run full localization and optional smoothing for a terrain profile."""

    cfg = correlation_config or CorrelationConfig()
    gps_cfg = gps_config or GPSFusionConfig(max_uav_speed_mps=max_speed_mps)
    fixes = list(gps_fixes or [])
    gps_available = any(fix.has_position for fix in fixes)
    anchor = first_usable_gps_anchor(fixes, gps_cfg) if gps_available else None
    search_center_x_m = anchor.x_m if anchor is not None else None
    search_center_y_m = anchor.y_m if anchor is not None else None
    single_cfg = _gps_assisted_shift_config(cfg, gps_cfg) if anchor is not None else cfg
    correlation, estimate = estimate_single_window(
        dem,
        profile,
        speed_hint_mps,
        single_cfg,
        search_center_x_m=search_center_x_m,
        search_center_y_m=search_center_y_m,
    )

    if gps_available:
        state = gps_state or GPSFusionState()
        diagnostics = tercom_only_diagnostics(estimate, search_window_m=gps_cfg.reacquire_window_radius_m)
        position_fixes = [fix for fix in fixes if fix.has_position]
        for fix in position_fixes[:-1]:
            state.evaluate(fix, gps_cfg)
        fusion = fuse_navigation_estimate(
            estimate,
            position_fixes[-1],
            config=gps_cfg,
            state=state,
            search_window_m=gps_cfg.reacquire_window_radius_m if anchor is not None else None,
        )
        estimate = fusion.estimate
        diagnostics = fusion.diagnostics
        estimates = pd.DataFrame([estimate.to_dict()])
        estimates["navigation_mode"] = diagnostics["mode"]
    else:
        estimates = estimate_window_series(
            dem, profile, speed_hint_mps, cfg, window_duration_s, step_s, max_speed_mps
        )
        if kalman_config and kalman_config.enabled:
            estimates = smooth_estimates(estimates, kalman_config)
        if not estimates.empty:
            last = estimates.iloc[-1]
            estimate = NavigationEstimate(
                time_s=float(last["time_s"]),
                x_m=float(last["x_m"]),
                y_m=float(last["y_m"]),
                azimuth_deg=float(last["azimuth_deg"]),
                speed_mps=float(last["speed_mps"]),
                vx_mps=float(last["vx_mps"]),
                vy_mps=float(last["vy_mps"]),
                traveled_distance_m=float(last["traveled_distance_m"]),
                confidence_score=float(last["confidence_score"]),
                ambiguous_match=bool(last["ambiguous_match"]),
            )
        diagnostics = tercom_only_diagnostics(estimate)
    metrics = compute_accuracy_metrics(estimates, truth, correlation)
    metrics.confidence_score = estimate.confidence_score
    metrics.extra["navigation_diagnostics"] = diagnostics
    return LocalizationResult(
        correlation=correlation,
        estimate=estimate,
        estimates=estimates,
        metrics=metrics,
        diagnostics=diagnostics,
    )
