"""Particle filter primitives for GNSS-denied terrain navigation."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .dem import DEMData, _sample_dem_array


_MIN_WEIGHT = 1e-300


@dataclass
class ParticleState:
    """Vectorized particle cloud state.

    Coordinates are local DEM meters. Heading convention matches the rest of
    the navigation core: 0 degrees is north/+Y and 90 degrees is east/+X.
    """

    x_m: np.ndarray
    y_m: np.ndarray
    heading_deg: np.ndarray
    speed_mps: np.ndarray
    baro_bias_m: np.ndarray
    radar_bias_m: np.ndarray
    weights: np.ndarray

    def __post_init__(self) -> None:
        arrays = (
            "x_m",
            "y_m",
            "heading_deg",
            "speed_mps",
            "baro_bias_m",
            "radar_bias_m",
            "weights",
        )
        for name in arrays:
            setattr(self, name, np.asarray(getattr(self, name), dtype=float))
        sizes = {getattr(self, name).size for name in arrays}
        if len(sizes) != 1:
            raise ValueError("All particle arrays must have the same length")
        if self.weights.size == 0:
            raise ValueError("Particle cloud must not be empty")
        self.weights = _normalize_weights(self.weights)

    @property
    def size(self) -> int:
        return int(self.weights.size)


def initialize_particles(
    n_particles: int,
    center_x_m: float,
    center_y_m: float,
    radius_m: float,
    heading_deg: float,
    heading_std_deg: float,
    speed_mps: float,
    speed_std_mps: float,
    baro_bias_std_m: float = 10.0,
    radar_bias_std_m: float = 3.0,
    seed: int | None = None,
) -> ParticleState:
    """Create a uniform particle cloud around the initial uncertainty zone."""

    if n_particles <= 0:
        raise ValueError("n_particles must be positive")
    if radius_m < 0:
        raise ValueError("radius_m must be non-negative")

    rng = np.random.default_rng(seed)
    theta = rng.uniform(0.0, 2.0 * np.pi, size=n_particles)
    radius = float(radius_m) * np.sqrt(rng.uniform(0.0, 1.0, size=n_particles))
    x_m = float(center_x_m) + radius * np.sin(theta)
    y_m = float(center_y_m) + radius * np.cos(theta)

    return ParticleState(
        x_m=x_m,
        y_m=y_m,
        heading_deg=(rng.normal(float(heading_deg), float(heading_std_deg), size=n_particles) % 360.0),
        speed_mps=np.maximum(0.1, rng.normal(float(speed_mps), float(speed_std_mps), size=n_particles)),
        baro_bias_m=rng.normal(0.0, float(baro_bias_std_m), size=n_particles),
        radar_bias_m=rng.normal(0.0, float(radar_bias_std_m), size=n_particles),
        weights=np.full(n_particles, 1.0 / float(n_particles), dtype=float),
    )


def predict_particles(
    particles: ParticleState,
    dt_s: float,
    measured_speed_mps: float,
    measured_heading_deg: float,
    speed_noise_std_mps: float = 1.5,
    heading_noise_std_deg: float = 2.0,
    position_noise_std_m: float = 3.0,
    seed: int | None = None,
) -> ParticleState:
    """Move all particles by the inertial/dead-reckoning motion model."""

    if dt_s < 0:
        raise ValueError("dt_s must be non-negative")

    rng = np.random.default_rng(seed)
    n = particles.size
    speed = np.maximum(
        0.1,
        0.35 * particles.speed_mps
        + 0.65 * (float(measured_speed_mps) + rng.normal(0.0, float(speed_noise_std_mps), size=n)),
    )
    heading = (
        0.25 * particles.heading_deg
        + 0.75 * (float(measured_heading_deg) + rng.normal(0.0, float(heading_noise_std_deg), size=n))
    ) % 360.0
    heading_rad = np.deg2rad(heading)
    distance = speed * float(dt_s)

    if position_noise_std_m > 0:
        noise_x = rng.normal(0.0, float(position_noise_std_m), size=n)
        noise_y = rng.normal(0.0, float(position_noise_std_m), size=n)
    else:
        noise_x = 0.0
        noise_y = 0.0

    return replace(
        particles,
        x_m=particles.x_m + distance * np.sin(heading_rad) + noise_x,
        y_m=particles.y_m + distance * np.cos(heading_rad) + noise_y,
        heading_deg=heading,
        speed_mps=speed,
    )


def update_weights_instant_height(
    particles: ParticleState,
    dem: DEMData,
    barometric_altitude_msl: float,
    radar_altitude_agl: float,
    sigma_alt_m: float = 15.0,
) -> ParticleState:
    """Update particle weights by one radar-altimeter measurement."""

    if sigma_alt_m <= 0:
        raise ValueError("sigma_alt_m must be positive")

    dem_height = _sample_dem_array(dem, particles.x_m, particles.y_m)
    predicted_agl = float(barometric_altitude_msl) - particles.baro_bias_m - dem_height + particles.radar_bias_m
    error = float(radar_altitude_agl) - predicted_agl
    likelihood = np.exp(-np.square(error) / (2.0 * float(sigma_alt_m) ** 2))
    likelihood = np.where(np.isfinite(likelihood) & np.isfinite(dem_height), likelihood, _MIN_WEIGHT)
    weights = _normalize_weights(particles.weights * np.maximum(likelihood, _MIN_WEIGHT))
    return replace(particles, weights=weights)


def effective_sample_size(weights: np.ndarray) -> float:
    """Return ESS = 1 / sum(w^2) for normalized or unnormalized weights."""

    normalized = _normalize_weights(np.asarray(weights, dtype=float))
    return float(1.0 / np.sum(np.square(normalized)))


def systematic_resample(particles: ParticleState, seed: int | None = None) -> ParticleState:
    """Systematic resampling with uniform output weights."""

    rng = np.random.default_rng(seed)
    n = particles.size
    weights = _normalize_weights(particles.weights)
    positions = (rng.random() + np.arange(n, dtype=float)) / float(n)
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    indexes = np.searchsorted(cumulative, positions, side="left")

    return ParticleState(
        x_m=particles.x_m[indexes].copy(),
        y_m=particles.y_m[indexes].copy(),
        heading_deg=particles.heading_deg[indexes].copy(),
        speed_mps=particles.speed_mps[indexes].copy(),
        baro_bias_m=particles.baro_bias_m[indexes].copy(),
        radar_bias_m=particles.radar_bias_m[indexes].copy(),
        weights=np.full(n, 1.0 / float(n), dtype=float),
    )


def estimate_state(particles: ParticleState) -> dict:
    """Estimate current navigation state from the weighted particle cloud."""

    weights = _normalize_weights(particles.weights)
    x_m = float(np.sum(weights * particles.x_m))
    y_m = float(np.sum(weights * particles.y_m))
    heading_rad = np.deg2rad(particles.heading_deg)
    sin_mean = float(np.sum(weights * np.sin(heading_rad)))
    cos_mean = float(np.sum(weights * np.cos(heading_rad)))
    heading_deg = float(np.rad2deg(np.arctan2(sin_mean, cos_mean)) % 360.0)
    speed_mps = float(np.sum(weights * particles.speed_mps))
    var_x = float(np.sum(weights * np.square(particles.x_m - x_m)))
    var_y = float(np.sum(weights * np.square(particles.y_m - y_m)))

    return {
        "x_m": x_m,
        "y_m": y_m,
        "heading_deg": heading_deg,
        "speed_mps": speed_mps,
        "error_radius_m": float(np.sqrt(max(0.0, var_x + var_y))),
    }


def _normalize_weights(weights: np.ndarray) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    values = np.where(np.isfinite(values) & (values > 0.0), values, 0.0)
    total = float(np.sum(values))
    if not np.isfinite(total) or total <= 0.0:
        return np.full(values.size, 1.0 / float(values.size), dtype=float)
    return values / total
