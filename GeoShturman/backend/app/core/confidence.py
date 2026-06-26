"""Confidence scoring and navigation modes for Terrain Lock."""

from __future__ import annotations

import numpy as np

from .dem import DEMData, _sample_dem_array


def terrain_informativeness(dem: DEMData, x_m: float, y_m: float, radius_m: float = 500.0) -> float:
    """Score local DEM relief diversity from 0.0 to 1.0."""

    radius = max(float(radius_m), float(dem.resolution_m) * 2.0)
    step = max(float(dem.resolution_m), radius / 12.0)
    offsets = np.arange(-radius, radius + step, step, dtype=float)
    xx, yy = np.meshgrid(float(x_m) + offsets, float(y_m) + offsets)
    mask = np.square(xx - float(x_m)) + np.square(yy - float(y_m)) <= radius**2
    heights = _sample_dem_array(dem, xx[mask], yy[mask])
    heights = heights[np.isfinite(heights)]
    if heights.size < 6:
        return 0.0

    std_score = np.clip(float(np.std(heights)) / 45.0, 0.0, 1.0)
    range_score = np.clip(float(np.ptp(heights)) / 160.0, 0.0, 1.0)
    if heights.size >= 9:
        side = int(np.sqrt(heights.size))
        grid = heights[: side * side].reshape(side, side)
        gy, gx = np.gradient(grid)
        gradient_energy = float(np.nanmean(np.sqrt(np.square(gx) + np.square(gy))))
    else:
        gradient_energy = 0.0
    gradient_score = np.clip(gradient_energy / 12.0, 0.0, 1.0)

    return float(np.clip(0.45 * std_score + 0.35 * range_score + 0.20 * gradient_score, 0.0, 1.0))


def compute_navigation_confidence(
    particle_error_radius_m: float,
    terrain_score: float,
    ess_ratio: float,
    profile_correlation: float | None = None,
) -> dict:
    """Combine particle spread, DEM informativeness and matching quality."""

    radius = max(0.0, float(particle_error_radius_m))
    error_score = float(np.exp(-radius / 650.0))
    terrain = float(np.clip(terrain_score, 0.0, 1.0))
    ess = float(np.clip(ess_ratio, 0.0, 1.0))
    corr = 0.65 if profile_correlation is None else float(np.clip((profile_correlation + 1.0) / 2.0, 0.0, 1.0))

    confidence = 0.46 * error_score + 0.22 * terrain + 0.14 * ess + 0.18 * corr
    if terrain < 0.20:
        confidence *= 0.72
    if profile_correlation is not None and profile_correlation < 0.35:
        confidence *= 0.82
    confidence = float(np.clip(confidence, 0.0, 1.0))

    if confidence > 0.75:
        mode = "terrain_lock"
    elif confidence >= 0.45:
        mode = "degraded"
    elif confidence >= 0.20:
        mode = "low_confidence"
    else:
        mode = "lost"

    warning = None
    if terrain < 0.20:
        warning = "flat terrain: terrain profile is weakly informative"
    elif radius > 700.0:
        warning = "particle cloud diverged"
    elif profile_correlation is not None and profile_correlation < 0.35:
        warning = "low terrain match correlation"
    elif mode == "lost":
        warning = "navigation lock lost"

    return {"confidence": confidence, "mode": mode, "warning": warning}
