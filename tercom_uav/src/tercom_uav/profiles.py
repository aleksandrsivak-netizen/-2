"""Conversion from radio-altimeter NMEA records to terrain profiles."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np

from tercom_uav.types import GGARecord, TerrainProfile


def build_terrain_profile(records: list[GGARecord], baro_alt_msl_m: float) -> TerrainProfile:
    """Build `terrain_msl = baro_alt_msl - radio_alt_agl` time series."""

    times: list[float] = []
    radio_altitudes: list[float] = []
    fallback_time = 0.0
    rollover_offset = 0.0
    previous_utc: float | None = None

    for record in records:
        if record.radio_alt_m is None or not np.isfinite(record.radio_alt_m):
            continue
        if record.utc_seconds is None:
            time_s = fallback_time
            fallback_time += 1.0
        else:
            utc = record.utc_seconds
            if previous_utc is not None and utc + rollover_offset < previous_utc - 43200.0:
                rollover_offset += 86400.0
            time_s = utc + rollover_offset
            previous_utc = time_s
        times.append(float(time_s))
        radio_altitudes.append(float(record.radio_alt_m))

    if not times:
        raise ValueError("No valid radio-altimeter samples found in NMEA records.")

    times_arr = np.asarray(times, dtype=float)
    times_arr = times_arr - times_arr[0]
    radio_arr = np.asarray(radio_altitudes, dtype=float)
    terrain_arr = float(baro_alt_msl_m) - radio_arr
    return TerrainProfile(times_s=times_arr, radio_alt_m=radio_arr, terrain_msl_m=terrain_arr)


def resample_by_time(profile: TerrainProfile, hz: float) -> TerrainProfile:
    """Resample a terrain profile to a uniform time grid."""

    if hz <= 0:
        raise ValueError("hz must be positive.")
    if profile.times_s.size < 2:
        return profile
    step = 1.0 / hz
    new_times = np.arange(profile.times_s[0], profile.times_s[-1] + step * 0.5, step)
    radio = np.interp(new_times, profile.times_s, profile.radio_alt_m)
    terrain = np.interp(new_times, profile.times_s, profile.terrain_msl_m)
    return TerrainProfile(times_s=new_times, radio_alt_m=radio, terrain_msl_m=terrain)


def distances_from_time(profile: TerrainProfile, speed_mps: float) -> np.ndarray:
    """Convert profile timestamps to distance using a speed hypothesis."""

    if speed_mps <= 0:
        raise ValueError("speed_mps must be positive.")
    return (profile.times_s - profile.times_s[0]) * speed_mps


def resample_by_distance(
    profile: TerrainProfile,
    speed_mps: float,
    spacing_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Resample terrain profile by traveled distance.

    Distance is not observable from radio altitude alone, so this function uses
    an explicit speed hypothesis or simulator truth.
    """

    if spacing_m <= 0:
        raise ValueError("spacing_m must be positive.")
    distances = distances_from_time(profile, speed_mps)
    if distances[-1] <= 0:
        return distances, profile.terrain_msl_m
    new_distances = np.arange(0.0, distances[-1] + spacing_m * 0.5, spacing_m)
    terrain = np.interp(new_distances, distances, profile.terrain_msl_m)
    return new_distances, terrain


def sliding_time_windows(
    profile: TerrainProfile,
    window_duration_s: float,
    step_s: float,
    min_samples: int = 8,
) -> Iterator[TerrainProfile]:
    """Yield fixed-duration windows from a profile."""

    if window_duration_s <= 0 or step_s <= 0:
        raise ValueError("window_duration_s and step_s must be positive.")
    if profile.times_s.size < min_samples:
        return
    start = float(profile.times_s[0])
    end = float(profile.times_s[-1])
    while start + window_duration_s <= end + 1e-9:
        mask = (profile.times_s >= start) & (profile.times_s <= start + window_duration_s)
        if int(mask.sum()) >= min_samples:
            base_time = profile.times_s[mask][0]
            yield TerrainProfile(
                times_s=profile.times_s[mask] - base_time,
                radio_alt_m=profile.radio_alt_m[mask],
                terrain_msl_m=profile.terrain_msl_m[mask],
            )
        start += step_s


def save_profile_csv(profile: TerrainProfile, path: str | Path) -> None:
    """Save profile as CSV."""

    profile.to_frame().to_csv(path, index=False)

