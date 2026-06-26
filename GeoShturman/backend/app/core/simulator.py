"""Synthetic flight and radio altimeter data generators."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dem import DEMData, sample_dem
from .nmea import build_gpgga_sentence


@dataclass
class Trajectory:
    x_m: np.ndarray
    y_m: np.ndarray
    t_s: np.ndarray
    azimuth_deg: float
    speed_mps: float


def generate_truth_trajectory(
    start_x_m: float,
    start_y_m: float,
    azimuth_deg: float,
    speed_mps: float,
    duration_s: float,
    sample_rate_hz: float,
) -> Trajectory:
    """Generate a straight ground track in the local DEM coordinate frame."""

    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    n_samples = max(1, int(round(duration_s * sample_rate_hz)))
    t_s = np.arange(n_samples, dtype=float) / float(sample_rate_hz)
    distances = float(speed_mps) * t_s
    azimuth_rad = np.deg2rad(float(azimuth_deg) % 360.0)
    x_m = float(start_x_m) + distances * np.sin(azimuth_rad)
    y_m = float(start_y_m) + distances * np.cos(azimuth_rad)
    return Trajectory(
        x_m=x_m,
        y_m=y_m,
        t_s=t_s,
        azimuth_deg=float(azimuth_deg) % 360.0,
        speed_mps=float(speed_mps),
    )


def generate_radio_altimeter_profile(
    dem: DEMData,
    trajectory: Trajectory,
    barometric_altitude_msl: float,
    noise_std_m: float = 2.0,
    outlier_probability: float = 0.0,
    outlier_scale_m: float | None = None,
    dropout_probability: float = 0.0,
    barometric_drift_m: float = 0.0,
    seed: int | None = None,
) -> np.ndarray:
    """Generate AGL radio altitude measurements from a DEM and truth path."""

    rng = np.random.default_rng(seed)
    terrain = np.asarray([sample_dem(dem, x, y) for x, y in zip(trajectory.x_m, trajectory.y_m)], dtype=float)
    if trajectory.t_s.size > 1:
        drift = np.linspace(0.0, float(barometric_drift_m), trajectory.t_s.size)
    else:
        drift = np.zeros_like(terrain)
    aircraft_msl = float(barometric_altitude_msl) + drift
    radio_altitude = aircraft_msl - terrain

    if noise_std_m > 0:
        radio_altitude = radio_altitude + rng.normal(0.0, float(noise_std_m), size=radio_altitude.shape)
    if outlier_probability > 0:
        mask = rng.random(radio_altitude.shape) < float(outlier_probability)
        scale = float(outlier_scale_m) if outlier_scale_m is not None else max(25.0, noise_std_m * 10.0)
        outliers = rng.normal(0.0, scale, size=radio_altitude.shape)
        radio_altitude = np.where(mask, radio_altitude + outliers, radio_altitude)
    if dropout_probability > 0:
        dropout_mask = rng.random(radio_altitude.shape) < float(dropout_probability)
        radio_altitude = np.where(dropout_mask, np.nan, radio_altitude)

    return np.where(np.isfinite(radio_altitude), np.maximum(radio_altitude, 0.0), np.nan)


def generate_nmea_from_radio_profile(profile_agl: np.ndarray, sample_rate_hz: float) -> str:
    """Encode a radio-altimeter profile as multiline GPGGA-like NMEA text."""

    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    values = np.asarray(profile_agl, dtype=float)
    lines = []
    for index, altitude in enumerate(values):
        safe_altitude = float(altitude) if np.isfinite(altitude) else 0.0
        lines.append(build_gpgga_sentence(index / float(sample_rate_hz), safe_altitude))
    return "\n".join(lines)


def generate_sensor_stream(
    dem: DEMData,
    truth_trajectory: Trajectory,
    barometric_altitude_msl: float = 1500.0,
    radar_noise_std_m: float = 2.0,
    baro_noise_std_m: float = 3.0,
    baro_drift_m_per_s: float = 0.02,
    speed_noise_std_mps: float = 0.8,
    heading_noise_std_deg: float = 1.5,
    speed_bias_mps: float = 0.0,
    heading_bias_deg: float = 0.0,
    seed: int | None = 42,
) -> list[dict]:
    """Generate onboard sensor measurements for autonomous navigation demos."""

    rng = np.random.default_rng(seed)
    stream: list[dict] = []
    for x_m, y_m, t_s in zip(truth_trajectory.x_m, truth_trajectory.y_m, truth_trajectory.t_s):
        terrain_h = sample_dem(dem, float(x_m), float(y_m))
        true_baro_msl = float(barometric_altitude_msl)
        true_radar_agl = max(0.0, true_baro_msl - terrain_h)
        stream.append(
            {
                "t_s": float(t_s),
                "barometric_altitude_msl": float(
                    true_baro_msl
                    + rng.normal(0.0, float(baro_noise_std_m))
                    + float(baro_drift_m_per_s) * float(t_s)
                ),
                "radar_altitude_agl": float(true_radar_agl + rng.normal(0.0, float(radar_noise_std_m))),
                "speed_mps": float(
                    truth_trajectory.speed_mps + float(speed_bias_mps) + rng.normal(0.0, float(speed_noise_std_mps))
                ),
                "heading_deg": float(
                    (truth_trajectory.azimuth_deg + float(heading_bias_deg) + rng.normal(0.0, float(heading_noise_std_deg)))
                    % 360.0
                ),
            }
        )
    return stream
