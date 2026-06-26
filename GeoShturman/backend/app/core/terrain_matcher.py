"""Terrain profile matching helpers for particle-filter navigation."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .dem import DEMData, _sample_dem_array
from .particle_filter import ParticleState


def observed_terrain_profile(
    barometric_altitudes_msl: np.ndarray,
    radar_altitudes_agl: np.ndarray,
) -> np.ndarray:
    """Convert barometric MSL and radar AGL measurements to terrain MSL."""

    return np.asarray(barometric_altitudes_msl, dtype=float) - np.asarray(radar_altitudes_agl, dtype=float)


def normalize_profile(profile: np.ndarray) -> np.ndarray:
    """Return a zero-mean, unit-variance profile over finite samples."""

    values = np.asarray(profile, dtype=float)
    out = np.zeros_like(values, dtype=float)
    finite = np.isfinite(values)
    if not np.any(finite):
        return out
    centered = values[finite] - float(np.mean(values[finite]))
    scale = float(np.std(centered))
    if scale <= 1e-9:
        out[finite] = centered
    else:
        out[finite] = centered / scale
    return out


def profile_rmse(a: np.ndarray, b: np.ndarray) -> float:
    """RMSE over paired finite profile samples."""

    a_arr, b_arr = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    mask = np.isfinite(a_arr) & np.isfinite(b_arr)
    if not np.any(mask):
        return float("inf")
    return float(np.sqrt(np.mean(np.square(a_arr[mask] - b_arr[mask]))))


def profile_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation over paired finite profile samples."""

    a_arr, b_arr = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    mask = np.isfinite(a_arr) & np.isfinite(b_arr)
    if np.count_nonzero(mask) < 2:
        return 0.0
    a_norm = normalize_profile(a_arr[mask])
    b_norm = normalize_profile(b_arr[mask])
    denom = float(np.linalg.norm(a_norm) * np.linalg.norm(b_norm))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(np.dot(a_norm, b_norm) / denom, -1.0, 1.0))


def update_weights_profile_match(
    particles: ParticleState,
    dem: DEMData,
    trajectory_history_local: dict,
    observed_profile: np.ndarray,
    sample_rate_hz: float,
    sigma_profile_m: float = 30.0,
) -> ParticleState:
    """Update particle weights by matching a recent terrain profile window."""

    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if sigma_profile_m <= 0:
        raise ValueError("sigma_profile_m must be positive")

    observed = np.asarray(observed_profile, dtype=float)
    finite_obs = np.isfinite(observed)
    if np.count_nonzero(finite_obs) < 3:
        return particles

    dem_profiles = _profiles_for_particles(particles, dem, trajectory_history_local, observed.size, sample_rate_hz)
    if dem_profiles.size == 0:
        return particles

    observed_mean = float(np.nanmean(observed))
    obs_centered = observed - observed_mean
    obs_std = float(np.nanstd(obs_centered))
    likelihood = np.full(particles.size, 1e-300, dtype=float)

    valid = np.isfinite(dem_profiles) & finite_obs[:, None]
    enough = np.sum(valid, axis=0) >= max(3, int(0.65 * observed.size))
    if np.any(enough):
        dem_mean = np.nanmean(np.where(valid, dem_profiles, np.nan), axis=0)
        aligned = dem_profiles - dem_mean[None, :] + observed_mean
        diff = np.where(valid, observed[:, None] - aligned, np.nan)
        rmse = np.sqrt(np.nanmean(np.square(diff), axis=0))

        dem_centered = dem_profiles - dem_mean[None, :]
        numerator = np.nansum(np.where(valid, obs_centered[:, None] * dem_centered, 0.0), axis=0)
        dem_std = np.sqrt(np.nanmean(np.square(np.where(valid, dem_centered, np.nan)), axis=0))
        denom = obs_std * dem_std * np.sum(valid, axis=0)
        corr = np.divide(numerator, denom, out=np.zeros_like(numerator), where=denom > 1e-12)
        corr = np.clip(corr, -1.0, 1.0)

        profile_weight = np.exp(-np.square(rmse) / (2.0 * float(sigma_profile_m) ** 2))
        profile_weight *= np.maximum(corr, 0.01)
        likelihood = np.where(enough & np.isfinite(profile_weight), np.maximum(profile_weight, 1e-300), likelihood)

    weights = _normalize_weights(particles.weights * likelihood)
    return replace(particles, weights=weights)


def reference_profile_for_state(
    dem: DEMData,
    x_m: float,
    y_m: float,
    heading_deg: float,
    speed_mps: float,
    n_samples: int,
    sample_rate_hz: float,
) -> np.ndarray:
    """Reconstruct a DEM profile ending at the supplied state."""

    if n_samples <= 0:
        return np.asarray([], dtype=float)
    offsets_s = (np.arange(n_samples - 1, -1, -1, dtype=float) / float(sample_rate_hz))
    heading_rad = np.deg2rad(float(heading_deg) % 360.0)
    x = float(x_m) - float(speed_mps) * offsets_s * np.sin(heading_rad)
    y = float(y_m) - float(speed_mps) * offsets_s * np.cos(heading_rad)
    return _sample_dem_array(dem, x, y)


def _profiles_for_particles(
    particles: ParticleState,
    dem: DEMData,
    trajectory_history_local: dict,
    n_samples: int,
    sample_rate_hz: float,
) -> np.ndarray:
    x_history = trajectory_history_local.get("x_history")
    y_history = trajectory_history_local.get("y_history")
    if x_history is not None and y_history is not None:
        x_arr = np.asarray(x_history, dtype=float)[-n_samples:]
        y_arr = np.asarray(y_history, dtype=float)[-n_samples:]
        if x_arr.shape == y_arr.shape and x_arr.shape == (n_samples, particles.size):
            return _sample_dem_array(dem, x_arr, y_arr)

    offsets_s = (np.arange(n_samples - 1, -1, -1, dtype=float) / float(sample_rate_hz))[:, None]
    heading_rad = np.deg2rad(particles.heading_deg % 360.0)[None, :]
    x = particles.x_m[None, :] - particles.speed_mps[None, :] * offsets_s * np.sin(heading_rad)
    y = particles.y_m[None, :] - particles.speed_mps[None, :] * offsets_s * np.cos(heading_rad)
    return _sample_dem_array(dem, x, y)


def _normalize_weights(weights: np.ndarray) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    values = np.where(np.isfinite(values) & (values > 0.0), values, 0.0)
    total = float(np.sum(values))
    if not np.isfinite(total) or total <= 0.0:
        return np.full(values.size, 1.0 / float(values.size), dtype=float)
    return values / total
