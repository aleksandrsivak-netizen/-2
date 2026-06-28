"""Configuration models for simulation, TERCOM matching and smoothing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SimulationConfig:
    """Flight and sensor scenario parameters.

    Units are meters, seconds, hertz and degrees. The barometric altitude is
    absolute MSL, while GPGGA altitude fields are used as radio altitude AGL.
    """

    dem_path: str | None = None
    baro_alt_msl: float = 1500.0
    speed_mps: float = 55.0
    heading_deg: float = 73.0
    duration_s: float = 180.0
    hz: float = 5.0
    noise_std_m: float = 2.5
    outlier_prob: float = 0.0
    outlier_std_m: float = 35.0
    dropout_prob: float = 0.0
    drift_mps: float = 0.0
    sensor_min_m: float = 0.0
    sensor_max_m: float = 5000.0
    start_x_m: float | None = None
    start_y_m: float | None = None
    random_seed: int = 42

    def validate(self) -> None:
        if not 1.0 <= self.hz <= 10.0:
            raise ValueError("NMEA frequency must be in range 1..10 Hz.")
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive.")
        if self.speed_mps <= 0:
            raise ValueError("speed_mps must be positive.")
        if self.sensor_min_m >= self.sensor_max_m:
            raise ValueError("sensor_min_m must be lower than sensor_max_m.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CorrelationConfig:
    """Search-grid parameters for TERCOM correlation.

    `coarse_to_fine` defaults to False (exhaustive full-grid search) on
    purpose: on smooth, low-frequency terrain (taiga/tundra/steppe) the
    correlation surface often has several local maxima with near-identical
    scores. A single coarse-grid pass followed by local refinement can lock
    onto the wrong basin before refinement ever sees the true optimum -
    verified experimentally to misjudge azimuth by 3-130+ degrees on this
    project's synthetic DEM even with a widened refinement radius. Only
    enable it after validating, on your real DEM and noise levels, that its
    azimuth/shift output matches the full-grid result within tolerance.
    """

    azimuth_step_deg: float = 1.0
    shift_min_m: float = -6000.0
    shift_max_m: float = 6000.0
    shift_step_m: float = 30.0
    sample_spacing_m: float = 30.0
    coarse_to_fine: bool = False
    coarse_azimuth_step_deg: float = 5.0
    coarse_shift_step_m: float = 150.0
    fine_azimuth_radius_deg: float = 6.0
    fine_shift_radius_m: float = 180.0
    min_correlation: float = 0.55
    min_score_gap: float = 0.05
    min_relative_gap: float = 0.75
    min_observability: float = 0.2

    # Speed-hypothesis search: resolves the speed<->distance-scale
    # circularity (radio altitude alone gives no metric distance scale, but
    # the along-track resampling needs one). Several speed hypotheses around
    # `speed_hint_mps` are tried; the one whose resampled profile correlates
    # best with the DEM is treated as the data-derived speed estimate.
    speed_search_enabled: bool = True
    speed_scale_min: float = 0.7
    speed_scale_max: float = 1.3
    speed_scale_step: float = 0.1
    speed_search_azimuth_step_deg: float = 5.0
    speed_search_shift_step_m: float = 90.0
    speed_search_use_coarse_azimuth: bool = False
    tercom_quality_mode: str = "accurate"
    max_search_radius_m: float | None = None
    max_candidates: int = 1

    def validate(self) -> None:
        if self.azimuth_step_deg <= 0 or self.azimuth_step_deg > 90:
            raise ValueError("azimuth_step_deg must be in (0, 90].")
        if self.shift_step_m <= 0:
            raise ValueError("shift_step_m must be positive.")
        if self.shift_min_m >= self.shift_max_m:
            raise ValueError("shift_min_m must be lower than shift_max_m.")
        if self.sample_spacing_m <= 0:
            raise ValueError("sample_spacing_m must be positive.")
        if self.speed_scale_min <= 0 or self.speed_scale_min > self.speed_scale_max:
            raise ValueError("speed_scale_min must be positive and <= speed_scale_max.")
        if self.speed_scale_step <= 0:
            raise ValueError("speed_scale_step must be positive.")
        if self.speed_search_azimuth_step_deg <= 0 or self.speed_search_azimuth_step_deg > 90:
            raise ValueError("speed_search_azimuth_step_deg must be in (0, 90].")
        if self.speed_search_shift_step_m <= 0:
            raise ValueError("speed_search_shift_step_m must be positive.")
        if self.tercom_quality_mode not in {"fast", "balanced", "accurate"}:
            raise ValueError("tercom_quality_mode must be one of: fast, balanced, accurate.")
        if self.max_search_radius_m is not None and self.max_search_radius_m <= 0:
            raise ValueError("max_search_radius_m must be positive when set.")
        if self.max_candidates <= 0:
            raise ValueError("max_candidates must be positive.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def correlation_config_for_quality(
    base: CorrelationConfig | None = None,
    mode: str | None = None,
) -> CorrelationConfig:
    """Return a correlation config tuned for fast/balanced/accurate modes."""

    cfg = base or CorrelationConfig()
    selected = (mode or cfg.tercom_quality_mode).strip().lower()
    if selected == "accurate":
        return replace(cfg, tercom_quality_mode="accurate")
    if selected == "balanced":
        return replace(
            cfg,
            tercom_quality_mode="balanced",
            coarse_to_fine=True,
            coarse_azimuth_step_deg=max(cfg.coarse_azimuth_step_deg, 8.0),
            coarse_shift_step_m=max(cfg.coarse_shift_step_m, 240.0),
            fine_azimuth_radius_deg=max(cfg.fine_azimuth_radius_deg, 8.0),
            fine_shift_radius_m=max(cfg.fine_shift_radius_m, 300.0),
            speed_scale_step=max(cfg.speed_scale_step, 0.15),
            speed_search_use_coarse_azimuth=True,
            max_candidates=max(cfg.max_candidates, 3),
        )
    if selected == "fast":
        return replace(
            cfg,
            tercom_quality_mode="fast",
            azimuth_step_deg=max(cfg.azimuth_step_deg, 2.0),
            shift_step_m=max(cfg.shift_step_m, 60.0),
            coarse_to_fine=True,
            coarse_azimuth_step_deg=max(cfg.coarse_azimuth_step_deg, 12.0),
            coarse_shift_step_m=max(cfg.coarse_shift_step_m, 360.0),
            fine_azimuth_radius_deg=max(cfg.fine_azimuth_radius_deg, 10.0),
            fine_shift_radius_m=max(cfg.fine_shift_radius_m, 420.0),
            speed_scale_step=max(cfg.speed_scale_step, 0.2),
            speed_search_use_coarse_azimuth=True,
            max_candidates=max(cfg.max_candidates, 2),
        )
    raise ValueError("tercom_quality_mode must be one of: fast, balanced, accurate.")


@dataclass(slots=True)
class KalmanConfig:
    """Constant-velocity alpha-beta smoothing parameters."""

    enabled: bool = False
    alpha: float = 0.65
    beta: float = 0.18
    min_confidence_weight: float = 0.1

    def validate(self) -> None:
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1].")
        if not 0.0 <= self.beta <= 1.0:
            raise ValueError("beta must be in [0, 1].")
        if not 0.0 <= self.min_confidence_weight <= 1.0:
            raise ValueError("min_confidence_weight must be in [0, 1].")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class GPSFusionConfig:
    """Thresholds for GPS-assisted TERCOM and degraded-GPS handling."""

    gps_max_age_ms: float = 1000.0
    gps_max_hdop: float = 2.5
    gps_min_satellites: int = 5
    gps_max_position_jump_m: float = 350.0
    gps_tercom_max_disagreement_m: float = 250.0
    max_uav_speed_mps: float = 120.0
    stale_data_timeout_ms: float = 2500.0
    reacquire_window_radius_m: float = 1500.0
    gps_good_required_count: int = 3
    gps_bad_required_count: int = 2
    gps_tercom_high_confidence: float = 0.65
    gps_degraded_max_weight: float = 0.35
    gps_healthy_weight_min: float = 0.35
    gps_healthy_weight_max: float = 0.75

    def validate(self) -> None:
        if self.gps_max_age_ms <= 0:
            raise ValueError("gps_max_age_ms must be positive.")
        if self.stale_data_timeout_ms < self.gps_max_age_ms:
            raise ValueError("stale_data_timeout_ms must be >= gps_max_age_ms.")
        if self.gps_max_hdop <= 0:
            raise ValueError("gps_max_hdop must be positive.")
        if self.gps_min_satellites < 0:
            raise ValueError("gps_min_satellites must be non-negative.")
        if self.gps_max_position_jump_m <= 0:
            raise ValueError("gps_max_position_jump_m must be positive.")
        if self.gps_tercom_max_disagreement_m <= 0:
            raise ValueError("gps_tercom_max_disagreement_m must be positive.")
        if self.max_uav_speed_mps <= 0:
            raise ValueError("max_uav_speed_mps must be positive.")
        if self.reacquire_window_radius_m <= 0:
            raise ValueError("reacquire_window_radius_m must be positive.")
        if self.gps_good_required_count <= 0 or self.gps_bad_required_count <= 0:
            raise ValueError("GPS hysteresis counters must be positive.")
        if not 0.0 <= self.gps_tercom_high_confidence <= 1.0:
            raise ValueError("gps_tercom_high_confidence must be in [0, 1].")
        if not 0.0 <= self.gps_degraded_max_weight <= 1.0:
            raise ValueError("gps_degraded_max_weight must be in [0, 1].")
        if not 0.0 <= self.gps_healthy_weight_min <= self.gps_healthy_weight_max <= 1.0:
            raise ValueError("GPS healthy weight bounds must be ordered in [0, 1].")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_output_dir(path: str | Path) -> Path:
    """Create and return an output directory."""

    output = Path(path)
    output.mkdir(parents=True, exist_ok=True)
    return output
