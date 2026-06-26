from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DemoRunRequest(BaseModel):
    width_m: float = Field(default=8000, ge=1000, le=20000)
    height_m: float = Field(default=8000, ge=1000, le=20000)
    resolution_m: float = Field(default=30, ge=10, le=100)
    duration_s: float = Field(default=120, ge=10, le=600)
    sample_rate_hz: float = Field(default=5, ge=1, le=10)
    speed_mps: float = Field(default=45, ge=10, le=100)
    azimuth_deg: float = Field(default=73, ge=0, le=359)
    barometric_altitude_msl: float = Field(default=1500, ge=300, le=5000)
    noise_std_m: float = Field(default=2, ge=0, le=100)
    outlier_probability: float = Field(default=0.01, ge=0, le=0.2)
    search_radius_m: float = Field(default=2000, ge=500, le=5000)
    enable_kalman: bool = True
    seed: int | None = 42
    terrain_type: str = Field(default="mixed")


class TruthState(BaseModel):
    start_x_m: float
    start_y_m: float
    end_x_m: float | None
    end_y_m: float | None
    azimuth_deg: float
    speed_mps: float


class EstimatedState(BaseModel):
    start_x_m: float
    start_y_m: float
    end_x_m: float
    end_y_m: float
    azimuth_deg: float
    speed_mps: float
    ground_speed_mps: float
    correlation: float
    rmse_m: float
    mae_m: float
    confidence: float


class QualityReport(BaseModel):
    terrain_informativeness: float
    peak_sharpness: float
    top1_top2_gap: float
    correlation: float | None = None
    rmse_m: float | None = None
    confidence: float | None = None
    warning: str | None


class ArtifactLinks(BaseModel):
    trajectory_overlay_png: str
    correlation_heatmap_png: str
    profile_comparison_png: str
    generated_nmea: str
    nmea_log: str
    result_json: str


class DemoRunResponse(BaseModel):
    status: str
    run_id: str
    truth: TruthState
    estimated: EstimatedState
    quality: QualityReport
    artifacts: ArtifactLinks
    message: str


class NMEAParseRequest(BaseModel):
    nmea_text: str


class NMEAParseResponse(BaseModel):
    status: str
    count: int
    valid_count: int
    invalid_count: int
    measurements: list[dict[str, Any]]


class NavigationSolveRequest(BaseModel):
    nmea_text: str
    barometric_altitude_msl: float = Field(default=1500, ge=300, le=5000)
    sample_rate_hz: float = Field(default=5, ge=1, le=10)
    dem_mode: str = "synthetic"
    width_m: float = Field(default=8000, ge=1000, le=20000)
    height_m: float = Field(default=8000, ge=1000, le=20000)
    resolution_m: float = Field(default=30, ge=10, le=100)
    search_radius_m: float = Field(default=2000, ge=500, le=5000)
    enable_kalman: bool = True
    terrain_type: str = Field(default="mixed")
    parallel_jobs: int | None = 1


class AutonomousDemoRequest(BaseModel):
    width_m: float = Field(default=8000, ge=1000, le=20000)
    height_m: float = Field(default=8000, ge=1000, le=20000)
    resolution_m: float = Field(default=30, ge=10, le=120)
    duration_s: float = Field(default=180, ge=20, le=600)
    sample_rate_hz: float = Field(default=5, ge=1, le=10)
    true_speed_mps: float = Field(default=18, ge=5, le=80)
    true_heading_deg: float = Field(default=73, ge=0, le=359)
    barometric_altitude_msl: float = Field(default=1500, ge=300, le=5000)
    initial_uncertainty_radius_m: float = Field(default=500, ge=50, le=2500)
    n_particles: int = Field(default=5000, ge=100, le=10000)
    profile_window_s: float = Field(default=30, ge=5, le=120)
    terrain_type: str = Field(default="mixed")
    seed: int | None = 42
