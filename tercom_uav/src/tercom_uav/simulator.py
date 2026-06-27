"""Flight and radio-altimeter simulator over a DEM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from tercom_uav.config import SimulationConfig
from tercom_uav.dem import DEMGrid
from tercom_uav.nmea import generate_gpgga


@dataclass(slots=True)
class SimulationOutput:
    """Truth trajectory, telemetry table and generated NMEA lines."""

    truth: pd.DataFrame
    telemetry: pd.DataFrame
    nmea_lines: list[str]

    def export_nmea(self, path: str | Path) -> None:
        Path(path).write_text("\n".join(self.nmea_lines) + "\n", encoding="utf-8")

    def export_truth(self, path: str | Path) -> None:
        self.truth.to_csv(path, index=False)

    def export_telemetry(self, path: str | Path) -> None:
        self.telemetry.to_csv(path, index=False)


def _default_start(dem: DEMGrid, heading_deg: float, track_length_m: float) -> tuple[float, float]:
    center_x, center_y = dem.center_m
    az = np.deg2rad(heading_deg)
    return (
        float(center_x - np.sin(az) * track_length_m * 0.5),
        float(center_y - np.cos(az) * track_length_m * 0.5),
    )


def simulate_flight(dem: DEMGrid, config: SimulationConfig) -> SimulationOutput:
    """Simulate constant-heading flight and GPGGA radio-altimeter telemetry."""

    config.validate()
    rng = np.random.default_rng(config.random_seed)
    dt = 1.0 / config.hz
    times = np.arange(0.0, config.duration_s + dt * 0.5, dt)
    track_length = config.speed_mps * float(times[-1])
    start_x, start_y = (
        (config.start_x_m, config.start_y_m)
        if config.start_x_m is not None and config.start_y_m is not None
        else _default_start(dem, config.heading_deg, track_length)
    )
    if start_x is None or start_y is None:
        raise ValueError("Both start_x_m and start_y_m must be provided together.")

    azimuth_rad = np.deg2rad(config.heading_deg)
    distances = config.speed_mps * times
    x = float(start_x) + np.sin(azimuth_rad) * distances
    y = float(start_y) + np.cos(azimuth_rad) * distances
    terrain = np.asarray(dem.sample(x, y), dtype=float)
    if np.any(~np.isfinite(terrain)):
        valid = np.isfinite(terrain)
        if not np.any(valid):
            raise ValueError("Simulated flight path is outside DEM bounds.")
        terrain = np.interp(times, times[valid], terrain[valid])

    baro_alt = config.baro_alt_msl + config.drift_mps * times
    true_radio = baro_alt - terrain
    measured_radio = true_radio + rng.normal(0.0, config.noise_std_m, size=times.size)

    if config.outlier_prob > 0:
        outlier_mask = rng.random(times.size) < config.outlier_prob
        measured_radio[outlier_mask] += rng.normal(0.0, config.outlier_std_m, size=int(outlier_mask.sum()))
    measured_radio = np.clip(measured_radio, config.sensor_min_m, config.sensor_max_m)

    dropout_mask = rng.random(times.size) < config.dropout_prob if config.dropout_prob > 0 else np.zeros(times.size, dtype=bool)
    nmea_lines: list[str] = []
    for time_s, radio_m, dropped in zip(times, measured_radio, dropout_mask, strict=True):
        if dropped or not np.isfinite(radio_m):
            continue
        nmea_lines.append(generate_gpgga(float(radio_m), float(time_s)))

    truth = pd.DataFrame(
        {
            "time_s": times,
            "x_m": x,
            "y_m": y,
            "terrain_msl_m": terrain,
            "baro_alt_msl_m": baro_alt,
            "true_radio_alt_agl_m": true_radio,
            "heading_deg": np.full(times.size, config.heading_deg),
            "speed_mps": np.full(times.size, config.speed_mps),
            "traveled_distance_m": distances,
        }
    )
    telemetry = pd.DataFrame(
        {
            "time_s": times,
            "radio_alt_agl_m": measured_radio,
            "dropout": dropout_mask,
            "baro_alt_msl_m": baro_alt,
        }
    )
    return SimulationOutput(truth=truth, telemetry=telemetry, nmea_lines=nmea_lines)

