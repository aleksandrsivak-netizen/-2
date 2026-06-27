"""TERCOM-style correlation search over azimuth and along-track shift."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from tercom_uav.confidence import confidence_from_scores, terrain_roughness_score
from tercom_uav.config import CorrelationConfig
from tercom_uav.dem import DEMGrid
from tercom_uav.types import CorrelationResult


def pearson_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Compute Pearson correlation with flat-profile protection."""

    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    mask = np.isfinite(a_arr) & np.isfinite(b_arr)
    if int(mask.sum()) < 3:
        return float("nan")
    a_valid = a_arr[mask] - np.mean(a_arr[mask])
    b_valid = b_arr[mask] - np.mean(b_arr[mask])
    denom = float(np.linalg.norm(a_valid) * np.linalg.norm(b_valid))
    if denom <= 1e-12:
        return 0.0
    return float(np.dot(a_valid, b_valid) / denom)


def normalized_cross_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Alias for normalized zero-mean cross-correlation at zero lag."""

    return pearson_correlation(a, b)


def mse(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if int(mask.sum()) == 0:
        return float("nan")
    diff = np.asarray(a)[mask] - np.asarray(b)[mask]
    return float(np.mean(diff**2))


def mad(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if int(mask.sum()) == 0:
        return float("nan")
    diff = np.asarray(a)[mask] - np.asarray(b)[mask]
    return float(np.mean(np.abs(diff)))


def _make_grid(start: float, stop: float, step: float) -> np.ndarray:
    count = int(np.floor((stop - start) / step)) + 1
    return start + np.arange(count, dtype=float) * step


@lru_cache(maxsize=256)
def _azimuth_grid(step_deg: float) -> np.ndarray:
    count = int(np.floor(360.0 / step_deg))
    return np.arange(count, dtype=float) * step_deg


def _score_references(references: np.ndarray, observed: np.ndarray) -> np.ndarray:
    observed_centered = observed - np.mean(observed)
    observed_std = np.std(observed_centered)
    scores = np.full(references.shape[0], np.nan, dtype=float)
    if observed_std <= 1e-12:
        return np.nan_to_num(scores, nan=0.0)

    valid_rows = np.isfinite(references).all(axis=1)
    if not np.any(valid_rows):
        return scores

    refs = references[valid_rows]
    refs_centered = refs - np.mean(refs, axis=1, keepdims=True)
    refs_std = np.std(refs_centered, axis=1)
    nonflat = refs_std > 1e-12
    row_scores = np.full(refs.shape[0], np.nan, dtype=float)
    row_scores[nonflat] = np.mean(
        (refs_centered[nonflat] / refs_std[nonflat, None])
        * (observed_centered / observed_std),
        axis=1,
    )
    scores[np.flatnonzero(valid_rows)] = row_scores
    return scores


def _search_grid(
    dem: DEMGrid,
    observed_profile_m: np.ndarray,
    distances_m: np.ndarray,
    azimuths_deg: np.ndarray,
    shifts_m: np.ndarray,
    center_x_m: float,
    center_y_m: float,
) -> np.ndarray:
    heatmap = np.full((azimuths_deg.size, shifts_m.size), np.nan, dtype=float)
    for azimuth_idx, azimuth_deg in enumerate(azimuths_deg):
        shifted_distances = shifts_m[:, None] + distances_m[None, :]
        azimuth_rad = np.deg2rad(azimuth_deg)
        x = center_x_m + np.sin(azimuth_rad) * shifted_distances
        y = center_y_m + np.cos(azimuth_rad) * shifted_distances
        references = np.asarray(dem.sample(x, y), dtype=float)
        heatmap[azimuth_idx, :] = _score_references(references, observed_profile_m)
    return heatmap


def _circular_angle_delta_deg(values_deg: np.ndarray, reference_deg: float) -> np.ndarray:
    return np.abs((values_deg - reference_deg + 180.0) % 360.0 - 180.0)


def _best_second_scores(
    heatmap: np.ndarray,
    azimuths_deg: np.ndarray,
    shifts_m: np.ndarray,
    best_index: tuple[int, int],
    exclude_azimuth_deg: float = 3.0,
    exclude_shift_m: float = 90.0,
) -> tuple[float, float]:
    best_score = float(heatmap[best_index])
    masked = np.array(heatmap, copy=True)
    az_idx, shift_idx = best_index
    az_mask = _circular_angle_delta_deg(azimuths_deg, float(azimuths_deg[az_idx])) <= exclude_azimuth_deg
    shift_mask = np.abs(shifts_m - float(shifts_m[shift_idx])) <= exclude_shift_m
    masked[np.ix_(az_mask, shift_mask)] = np.nan
    if np.all(~np.isfinite(masked)):
        return best_score, float("-inf")
    return best_score, float(np.nanmax(masked))


def correlate_profile(
    dem: DEMGrid,
    observed_profile_m: np.ndarray,
    distances_m: np.ndarray,
    config: CorrelationConfig | None = None,
    center_x_m: float | None = None,
    center_y_m: float | None = None,
) -> CorrelationResult:
    """Search DEM profiles and return the best azimuth/shift match."""

    cfg = config or CorrelationConfig()
    cfg.validate()
    observed = np.asarray(observed_profile_m, dtype=float)
    distances = np.asarray(distances_m, dtype=float)
    valid = np.isfinite(observed) & np.isfinite(distances)
    observed = observed[valid]
    distances = distances[valid]
    if observed.size < 5:
        raise ValueError("At least five observed terrain samples are required.")
    if center_x_m is None or center_y_m is None:
        center_x_m, center_y_m = dem.center_m

    if cfg.coarse_to_fine:
        coarse_az = _azimuth_grid(cfg.coarse_azimuth_step_deg)
        coarse_shifts = _make_grid(cfg.shift_min_m, cfg.shift_max_m, cfg.coarse_shift_step_m)
        coarse_heatmap = _search_grid(dem, observed, distances, coarse_az, coarse_shifts, center_x_m, center_y_m)
        coarse_best = np.unravel_index(np.nanargmax(coarse_heatmap), coarse_heatmap.shape)
        coarse_best_az = coarse_az[coarse_best[0]]
        coarse_best_shift = coarse_shifts[coarse_best[1]]
        azimuths = np.mod(
            _make_grid(
                coarse_best_az - cfg.fine_azimuth_radius_deg,
                coarse_best_az + cfg.fine_azimuth_radius_deg,
                cfg.azimuth_step_deg,
            ),
            360.0,
        )
        shifts = _make_grid(
            max(cfg.shift_min_m, coarse_best_shift - cfg.fine_shift_radius_m),
            min(cfg.shift_max_m, coarse_best_shift + cfg.fine_shift_radius_m),
            cfg.shift_step_m,
        )
    else:
        azimuths = _azimuth_grid(cfg.azimuth_step_deg)
        shifts = _make_grid(cfg.shift_min_m, cfg.shift_max_m, cfg.shift_step_m)

    heatmap = _search_grid(dem, observed, distances, azimuths, shifts, center_x_m, center_y_m)
    roughness = terrain_roughness_score(observed)
    if np.all(~np.isfinite(heatmap)):
        # Degenerate case: either the search grid left the DEM bounds, or the
        # terrain under every candidate reference is flat (zero std), which
        # makes normalized correlation undefined everywhere. Both are real
        # operating conditions over taiga/tundra/steppe, not programming
        # errors, so we report a "no fix possible" result instead of raising.
        nan_profile = np.full_like(distances, np.nan, dtype=float)
        return CorrelationResult(
            best_azimuth_deg=float(azimuths[0] % 360.0),
            best_shift_m=float(shifts[0]),
            best_score=float("nan"),
            second_best_score=float("nan"),
            discrimination_ratio=0.0,
            roughness_score=roughness,
            observability_score=0.0,
            confidence_score=0.0,
            ambiguous_match=True,
            mse_m2=float("nan"),
            mad_m=float("nan"),
            ncc=float("nan"),
            azimuths_deg=azimuths,
            shifts_m=shifts,
            heatmap=heatmap,
            best_reference_profile_m=nan_profile,
            observed_profile_m=observed,
            distances_m=distances,
        )

    best_index = np.unravel_index(np.nanargmax(heatmap), heatmap.shape)
    best_score, second_best_score = _best_second_scores(heatmap, azimuths, shifts, best_index)
    best_azimuth = float(azimuths[best_index[0]] % 360.0)
    best_shift = float(shifts[best_index[1]])
    reference = dem.sample_along(center_x_m, center_y_m, best_azimuth, best_shift + distances)
    heatmap_std = float(np.nanstd(heatmap)) if np.any(np.isfinite(heatmap)) else 0.0
    confidence, ambiguous, observability, score_gap = confidence_from_scores(
        best_score=best_score,
        second_best_score=second_best_score,
        roughness_m=roughness,
        min_correlation=cfg.min_correlation,
        min_score_gap=cfg.min_score_gap,
        min_observability=cfg.min_observability,
        heatmap_std=heatmap_std,
        min_relative_gap=cfg.min_relative_gap,
    )
    discrimination_ratio = float(score_gap / max(abs(best_score), 1e-9))

    return CorrelationResult(
        best_azimuth_deg=best_azimuth,
        best_shift_m=best_shift,
        best_score=best_score,
        second_best_score=second_best_score,
        discrimination_ratio=discrimination_ratio,
        roughness_score=roughness,
        observability_score=observability,
        confidence_score=confidence,
        ambiguous_match=ambiguous,
        mse_m2=mse(observed, reference),
        mad_m=mad(observed, reference),
        ncc=normalized_cross_correlation(observed, reference),
        azimuths_deg=azimuths,
        shifts_m=shifts,
        heatmap=heatmap,
        best_reference_profile_m=np.asarray(reference, dtype=float),
        observed_profile_m=observed,
        distances_m=distances,
    )
