"""Shared dataclasses used across the TERCOM prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True)
class GGARecord:
    """Parsed GPGGA message.

    In this project `radio_alt_m` is read from the GPGGA altitude field by
    task definition. Latitude and longitude are intentionally ignored.
    """

    raw: str
    utc_seconds: float | None
    radio_alt_m: float | None
    checksum: str | None
    checksum_valid: bool
    quality: int | None = None
    satellites: int | None = None


@dataclass(slots=True)
class TerrainProfile:
    """Observed absolute terrain profile reconstructed from radio altitude."""

    times_s: np.ndarray
    radio_alt_m: np.ndarray
    terrain_msl_m: np.ndarray

    @property
    def duration_s(self) -> float:
        if self.times_s.size < 2:
            return 0.0
        return float(self.times_s[-1] - self.times_s[0])

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time_s": self.times_s,
                "radio_alt_m": self.radio_alt_m,
                "terrain_msl_m": self.terrain_msl_m,
            }
        )


@dataclass(slots=True)
class CorrelationResult:
    """Result of a TERCOM grid search."""

    best_azimuth_deg: float
    best_shift_m: float
    best_score: float
    second_best_score: float
    discrimination_ratio: float
    roughness_score: float
    observability_score: float
    confidence_score: float
    ambiguous_match: bool
    mse_m2: float
    mad_m: float
    ncc: float
    azimuths_deg: np.ndarray
    shifts_m: np.ndarray
    heatmap: np.ndarray
    best_reference_profile_m: np.ndarray
    observed_profile_m: np.ndarray
    distances_m: np.ndarray

    def to_summary(self) -> dict[str, Any]:
        return {
            "best_azimuth_deg": self.best_azimuth_deg,
            "best_shift_m": self.best_shift_m,
            "best_score": self.best_score,
            "second_best_score": self.second_best_score,
            "discrimination_ratio": self.discrimination_ratio,
            "roughness_score": self.roughness_score,
            "observability_score": self.observability_score,
            "confidence_score": self.confidence_score,
            "ambiguous_match": self.ambiguous_match,
            "mse_m2": self.mse_m2,
            "mad_m": self.mad_m,
            "ncc": self.ncc,
        }


@dataclass(slots=True)
class NavigationEstimate:
    """Estimated UAV state in a local metric ENU-like map frame."""

    time_s: float
    x_m: float
    y_m: float
    azimuth_deg: float
    speed_mps: float
    vx_mps: float
    vy_mps: float
    traveled_distance_m: float
    confidence_score: float
    ambiguous_match: bool
    dead_reckoning: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_s": self.time_s,
            "x_m": self.x_m,
            "y_m": self.y_m,
            "azimuth_deg": self.azimuth_deg,
            "speed_mps": self.speed_mps,
            "vx_mps": self.vx_mps,
            "vy_mps": self.vy_mps,
            "traveled_distance_m": self.traveled_distance_m,
            "confidence_score": self.confidence_score,
            "ambiguous_match": self.ambiguous_match,
            "dead_reckoning": self.dead_reckoning,
        }


@dataclass(slots=True)
class AccuracyMetrics:
    """Self-assessment metrics against truth when available."""

    horizontal_error_m: float | None = None
    heading_error_deg: float | None = None
    speed_error_mps: float | None = None
    position_rmse_m: float | None = None
    position_mae_m: float | None = None
    confidence_score: float | None = None
    ambiguity_flag: bool | None = None
    terrain_roughness_score: float | None = None
    observability_score: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizontal_error_m": self.horizontal_error_m,
            "heading_error_deg": self.heading_error_deg,
            "speed_error_mps": self.speed_error_mps,
            "position_rmse_m": self.position_rmse_m,
            "position_mae_m": self.position_mae_m,
            "confidence_score": self.confidence_score,
            "ambiguity_flag": self.ambiguity_flag,
            "terrain_roughness_score": self.terrain_roughness_score,
            "observability_score": self.observability_score,
            **self.extra,
        }

