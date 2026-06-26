"""Terrain profile matching by grid search and correlation scoring."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from typing import Iterable

import numpy as np

from .dem import DEMData, is_inside_dem, sample_profile
from .profile import align_profile_to_reference, normalize_profile


@dataclass
class CandidateResult:
    start_x_m: float
    start_y_m: float
    end_x_m: float
    end_y_m: float
    azimuth_deg: float
    speed_mps: float
    correlation: float
    rmse_m: float
    mae_m: float
    combined_score: float
    reference_profile: np.ndarray
    drift_offset_m: float = 0.0
    drift_slope_m_per_sample: float = 0.0


@dataclass
class SearchResult:
    best: CandidateResult
    candidates: list[CandidateResult]
    heatmap: np.ndarray
    azimuth_values: np.ndarray
    metadata: dict


@dataclass
class _StartCellResult:
    x_index: int
    y_index: int
    cell_best: float
    candidates: list[CandidateResult]
    evaluated: int
    skipped: int


@dataclass
class _CandidateChunkResult:
    candidates: list[CandidateResult]
    cell_scores: dict[tuple[float, float], float]
    evaluated: int
    skipped: int


def normalized_cross_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Compute Pearson correlation for two profiles, ignoring non-finite values."""

    a_values = np.asarray(a, dtype=float)
    b_values = np.asarray(b, dtype=float)
    if a_values.shape != b_values.shape:
        raise ValueError("profiles must have the same shape")
    mask = np.isfinite(a_values) & np.isfinite(b_values)
    if np.count_nonzero(mask) < 2:
        return 0.0
    a_norm = normalize_profile(a_values[mask])
    b_norm = normalize_profile(b_values[mask])
    denom = float(np.linalg.norm(a_norm) * np.linalg.norm(b_norm))
    if denom < 1e-12:
        return 0.0
    return float(np.clip(np.dot(a_norm, b_norm) / denom, -1.0, 1.0))


def rmse(a: np.ndarray, b: np.ndarray) -> float:
    """Root mean square error over finite pairs."""

    diff = _finite_diff(a, b)
    if diff.size == 0:
        return float("inf")
    return float(np.sqrt(np.mean(diff**2)))


def mae(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute error over finite pairs."""

    diff = _finite_diff(a, b)
    if diff.size == 0:
        return float("inf")
    return float(np.mean(np.abs(diff)))


def search_best_match(
    dem: DEMData,
    measured_terrain_profile: np.ndarray,
    sample_rate_hz: float,
    search_center_x_m: float,
    search_center_y_m: float,
    search_radius_m: float,
    search_step_m: float,
    azimuth_step_deg: float,
    speed_min_mps: float,
    speed_max_mps: float,
    speed_step_mps: float,
    top_k: int = 10,
    n_jobs: int | None = 1,
    compensate_drift: bool = True,
) -> SearchResult:
    """Search candidate starts, azimuths, and speeds for the best DEM profile.

    ``n_jobs=1`` is deterministic sequential execution. ``n_jobs=0`` uses the
    available CPU count minus one. Threads are used to avoid repeatedly
    serializing DEM arrays in a process pool.
    """

    measured = np.asarray(measured_terrain_profile, dtype=float)
    if measured.size == 0:
        raise ValueError("measured profile must not be empty")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if search_step_m <= 0 or azimuth_step_deg <= 0 or speed_step_mps <= 0:
        raise ValueError("search steps must be positive")
    if speed_min_mps > speed_max_mps:
        raise ValueError("speed_min_mps must be <= speed_max_mps")

    x_values = _range_inclusive(search_center_x_m - search_radius_m, search_center_x_m + search_radius_m, search_step_m)
    y_values = _range_inclusive(search_center_y_m - search_radius_m, search_center_y_m + search_radius_m, search_step_m)
    azimuth_values = np.arange(0.0, 360.0, float(azimuth_step_deg), dtype=float)
    speed_values = _range_inclusive(speed_min_mps, speed_max_mps, speed_step_mps)
    heatmap = np.full((len(y_values), len(x_values)), np.nan, dtype=float)

    start_tasks: list[tuple[int, int, float, float]] = []
    skipped = 0
    combinations_per_start = len(azimuth_values) * len(speed_values)
    for y_index, start_y in enumerate(y_values):
        for x_index, start_x in enumerate(x_values):
            outside_radius = np.hypot(start_x - search_center_x_m, start_y - search_center_y_m)
            if outside_radius > search_radius_m + 1e-9:
                skipped += combinations_per_start
                continue
            if not is_inside_dem(dem, float(start_x), float(start_y)):
                skipped += combinations_per_start
                continue
            start_tasks.append((x_index, y_index, float(start_x), float(start_y)))

    jobs = _resolve_n_jobs(n_jobs)
    worker = lambda task: _evaluate_start_cell(
        task,
        dem,
        measured,
        sample_rate_hz,
        azimuth_values,
        speed_values,
        compensate_drift,
        max(25, top_k * 10),
    )
    if jobs == 1 or len(start_tasks) <= 1:
        results = [worker(task) for task in start_tasks]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            results = list(executor.map(worker, start_tasks))

    candidates: list[CandidateResult] = []
    evaluated = 0
    for result in results:
        evaluated += result.evaluated
        skipped += result.skipped
        if np.isfinite(result.cell_best):
            heatmap[result.y_index, result.x_index] = result.cell_best
        candidates.extend(result.candidates)
        _trim_candidates(candidates, max(50, top_k * 20), max(25, top_k * 10))

    if not candidates:
        raise ValueError("no valid candidates found inside DEM")

    candidates.sort(key=lambda item: item.combined_score, reverse=True)
    top_candidates = candidates[: max(1, top_k)]
    return SearchResult(
        best=top_candidates[0],
        candidates=top_candidates,
        heatmap=heatmap,
        azimuth_values=azimuth_values,
        metadata={
            "x_values": x_values,
            "y_values": y_values,
            "speed_values": speed_values,
            "search_center_x_m": float(search_center_x_m),
            "search_center_y_m": float(search_center_y_m),
            "search_radius_m": float(search_radius_m),
            "search_step_m": float(search_step_m),
            "azimuth_step_deg": float(azimuth_step_deg),
            "speed_step_mps": float(speed_step_mps),
            "evaluated_candidates": evaluated,
            "skipped_candidates": skipped,
            "measured_samples": int(measured.size),
            "parallel_jobs": jobs,
            "compensate_drift": bool(compensate_drift),
        },
    )


def coarse_search(
    dem: DEMData,
    measured_terrain_profile: np.ndarray,
    sample_rate_hz: float,
    search_center_x_m: float,
    search_center_y_m: float,
    search_radius_m: float = 2000.0,
    search_step_m: float = 250.0,
    azimuth_step_deg: float = 5.0,
    speed_min_mps: float = 20.0,
    speed_max_mps: float = 80.0,
    speed_step_mps: float = 5.0,
    top_k: int = 10,
    n_jobs: int | None = 1,
    compensate_drift: bool = True,
) -> SearchResult:
    """Convenience wrapper for the coarse search stage."""

    return search_best_match(
        dem=dem,
        measured_terrain_profile=measured_terrain_profile,
        sample_rate_hz=sample_rate_hz,
        search_center_x_m=search_center_x_m,
        search_center_y_m=search_center_y_m,
        search_radius_m=search_radius_m,
        search_step_m=search_step_m,
        azimuth_step_deg=azimuth_step_deg,
        speed_min_mps=speed_min_mps,
        speed_max_mps=speed_max_mps,
        speed_step_mps=speed_step_mps,
        top_k=top_k,
        n_jobs=n_jobs,
        compensate_drift=compensate_drift,
    )


def refine_search_around_candidates(
    dem: DEMData,
    measured_terrain_profile: np.ndarray,
    sample_rate_hz: float,
    coarse_result: SearchResult,
    search_radius_m: float = 250.0,
    search_step_m: float = 50.0,
    azimuth_window_deg: float = 5.0,
    azimuth_step_deg: float = 1.0,
    speed_window_mps: float = 5.0,
    speed_step_mps: float = 1.0,
    top_n: int = 5,
    top_k: int = 10,
    n_jobs: int | None = 1,
    compensate_drift: bool = True,
) -> SearchResult:
    """Run a fine local search around the best coarse candidates."""

    measured = np.asarray(measured_terrain_profile, dtype=float)
    if not coarse_result.candidates:
        raise ValueError("coarse_result has no candidates")

    candidate_tasks: list[tuple[float, float, float, float]] = []
    skipped = 0
    visited: set[tuple[int, int, int, int]] = set()
    azimuths_seen: list[float] = []

    for seed_candidate in coarse_result.candidates[: max(1, top_n)]:
        x_values = _range_inclusive(
            seed_candidate.start_x_m - search_radius_m,
            seed_candidate.start_x_m + search_radius_m,
            search_step_m,
        )
        y_values = _range_inclusive(
            seed_candidate.start_y_m - search_radius_m,
            seed_candidate.start_y_m + search_radius_m,
            search_step_m,
        )
        azimuth_values = _wrapped_range(seed_candidate.azimuth_deg, azimuth_window_deg, azimuth_step_deg)
        speed_values = _range_inclusive(
            max(0.1, seed_candidate.speed_mps - speed_window_mps),
            seed_candidate.speed_mps + speed_window_mps,
            speed_step_mps,
        )
        azimuths_seen.extend(float(value) for value in azimuth_values)
        combinations_per_start = len(azimuth_values) * len(speed_values)

        for start_x in x_values:
            for start_y in y_values:
                if np.hypot(start_x - seed_candidate.start_x_m, start_y - seed_candidate.start_y_m) > search_radius_m:
                    skipped += combinations_per_start
                    continue
                if not is_inside_dem(dem, float(start_x), float(start_y)):
                    skipped += combinations_per_start
                    continue
                for azimuth in azimuth_values:
                    for speed in speed_values:
                        key = (
                            int(round(start_x * 1000.0)),
                            int(round(start_y * 1000.0)),
                            int(round((azimuth % 360.0) * 1000.0)),
                            int(round(speed * 1000.0)),
                        )
                        if key in visited:
                            continue
                        visited.add(key)
                        candidate_tasks.append((float(start_x), float(start_y), float(azimuth), float(speed)))

    jobs = _resolve_n_jobs(n_jobs)
    chunks = list(_chunked(candidate_tasks, max(1, len(candidate_tasks) // max(1, jobs * 4))))
    worker = lambda chunk: _evaluate_candidate_chunk(
        chunk,
        dem,
        measured,
        sample_rate_hz,
        compensate_drift,
        max(50, top_k * 15),
    )
    if jobs == 1 or len(chunks) <= 1:
        results = [worker(chunk) for chunk in chunks]
    else:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            results = list(executor.map(worker, chunks))

    candidates: list[CandidateResult] = []
    cell_scores: dict[tuple[float, float], float] = {}
    evaluated = 0
    for result in results:
        evaluated += result.evaluated
        skipped += result.skipped
        candidates.extend(result.candidates)
        for cell_key, score in result.cell_scores.items():
            current = cell_scores.get(cell_key, -float("inf"))
            cell_scores[cell_key] = max(current, score)
        _trim_candidates(candidates, max(100, top_k * 30), max(50, top_k * 15))

    if not candidates:
        raise ValueError("fine search found no valid candidates")

    candidates.sort(key=lambda item: item.combined_score, reverse=True)
    top_candidates = candidates[: max(1, top_k)]
    x_unique = np.asarray(sorted({key[0] for key in cell_scores}), dtype=float)
    y_unique = np.asarray(sorted({key[1] for key in cell_scores}), dtype=float)
    heatmap = np.full((len(y_unique), len(x_unique)), np.nan, dtype=float)
    x_index = {value: index for index, value in enumerate(x_unique)}
    y_index = {value: index for index, value in enumerate(y_unique)}
    for (x_value, y_value), score in cell_scores.items():
        heatmap[y_index[y_value], x_index[x_value]] = score

    return SearchResult(
        best=top_candidates[0],
        candidates=top_candidates,
        heatmap=heatmap,
        azimuth_values=np.asarray(sorted(set(round(value % 360.0, 6) for value in azimuths_seen)), dtype=float),
        metadata={
            "x_values": x_unique,
            "y_values": y_unique,
            "search_radius_m": float(search_radius_m),
            "search_step_m": float(search_step_m),
            "azimuth_window_deg": float(azimuth_window_deg),
            "azimuth_step_deg": float(azimuth_step_deg),
            "speed_window_mps": float(speed_window_mps),
            "speed_step_mps": float(speed_step_mps),
            "evaluated_candidates": evaluated,
            "skipped_candidates": skipped,
            "coarse_best_score": float(coarse_result.best.combined_score),
            "measured_samples": int(measured.size),
            "parallel_jobs": jobs,
            "compensate_drift": bool(compensate_drift),
        },
    )


def _evaluate_start_cell(
    task: tuple[int, int, float, float],
    dem: DEMData,
    measured: np.ndarray,
    sample_rate_hz: float,
    azimuth_values: np.ndarray,
    speed_values: np.ndarray,
    compensate_drift: bool,
    keep_limit: int,
) -> _StartCellResult:
    x_index, y_index, start_x, start_y = task
    candidates: list[CandidateResult] = []
    evaluated = 0
    skipped = 0
    cell_best = -float("inf")
    for azimuth in azimuth_values:
        for speed in speed_values:
            candidate = _evaluate_candidate(
                dem,
                measured,
                sample_rate_hz,
                start_x,
                start_y,
                float(azimuth),
                float(speed),
                compensate_drift,
            )
            if candidate is None:
                skipped += 1
                continue
            evaluated += 1
            cell_best = max(cell_best, candidate.combined_score)
            candidates.append(candidate)
            _trim_candidates(candidates, keep_limit * 2, keep_limit)
    return _StartCellResult(x_index, y_index, cell_best, candidates, evaluated, skipped)


def _evaluate_candidate_chunk(
    tasks: list[tuple[float, float, float, float]],
    dem: DEMData,
    measured: np.ndarray,
    sample_rate_hz: float,
    compensate_drift: bool,
    keep_limit: int,
) -> _CandidateChunkResult:
    candidates: list[CandidateResult] = []
    cell_scores: dict[tuple[float, float], float] = {}
    evaluated = 0
    skipped = 0
    for start_x, start_y, azimuth, speed in tasks:
        candidate = _evaluate_candidate(
            dem,
            measured,
            sample_rate_hz,
            start_x,
            start_y,
            azimuth,
            speed,
            compensate_drift,
        )
        if candidate is None:
            skipped += 1
            continue
        evaluated += 1
        candidates.append(candidate)
        cell_key = (candidate.start_x_m, candidate.start_y_m)
        current = cell_scores.get(cell_key, -float("inf"))
        cell_scores[cell_key] = max(current, candidate.combined_score)
        _trim_candidates(candidates, keep_limit * 2, keep_limit)
    return _CandidateChunkResult(candidates, cell_scores, evaluated, skipped)


def _evaluate_candidate(
    dem: DEMData,
    measured: np.ndarray,
    sample_rate_hz: float,
    start_x: float,
    start_y: float,
    azimuth: float,
    speed: float,
    compensate_drift: bool = True,
) -> CandidateResult | None:
    reference = sample_profile(dem, start_x, start_y, azimuth, speed, sample_rate_hz, measured.size)
    if reference.size != measured.size or np.any(~np.isfinite(reference)):
        return None

    if compensate_drift:
        scored_measured, drift = align_profile_to_reference(measured, reference, degree=1)
    else:
        scored_measured = measured
        drift = {"offset_m": 0.0, "slope_m_per_sample": 0.0}

    raw_correlation = normalized_cross_correlation(measured, reference)
    correlation = normalized_cross_correlation(scored_measured, reference)
    rmse_m = rmse(scored_measured, reference)
    mae_m = mae(scored_measured, reference)
    measured_std = float(np.nanstd(scored_measured))
    normalized_rmse = rmse_m / max(measured_std, 1.0)
    drift_total_m = abs(float(drift["slope_m_per_sample"])) * max(measured.size - 1, 1)
    drift_penalty = min(max(0.0, drift_total_m - 45.0) / 350.0, 0.25)
    combined_score = float(0.82 * correlation + 0.18 * raw_correlation - 0.01 * normalized_rmse - drift_penalty)

    duration_s = max(0.0, (measured.size - 1) / float(sample_rate_hz))
    distance_m = float(speed) * duration_s
    azimuth_rad = np.deg2rad(float(azimuth) % 360.0)
    end_x = float(float(start_x) + distance_m * np.sin(azimuth_rad))
    end_y = float(float(start_y) + distance_m * np.cos(azimuth_rad))
    return CandidateResult(
        start_x_m=float(start_x),
        start_y_m=float(start_y),
        end_x_m=end_x,
        end_y_m=end_y,
        azimuth_deg=float(azimuth) % 360.0,
        speed_mps=float(speed),
        correlation=correlation,
        rmse_m=rmse_m,
        mae_m=mae_m,
        combined_score=combined_score,
        reference_profile=reference,
        drift_offset_m=float(drift["offset_m"]),
        drift_slope_m_per_sample=float(drift["slope_m_per_sample"]),
    )


def _finite_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_values = np.asarray(a, dtype=float)
    b_values = np.asarray(b, dtype=float)
    if a_values.shape != b_values.shape:
        raise ValueError("profiles must have the same shape")
    mask = np.isfinite(a_values) & np.isfinite(b_values)
    return a_values[mask] - b_values[mask]


def _range_inclusive(start: float, stop: float, step: float) -> np.ndarray:
    if step <= 0:
        raise ValueError("step must be positive")
    if stop < start:
        return np.asarray([], dtype=float)
    count = int(np.floor((stop - start) / step)) + 1
    values = start + np.arange(count, dtype=float) * step
    if values.size == 0 or values[-1] < stop - step * 1e-6:
        values = np.append(values, stop)
    return values.astype(float)


def _wrapped_range(center_deg: float, half_width_deg: float, step_deg: float) -> np.ndarray:
    raw = _range_inclusive(center_deg - half_width_deg, center_deg + half_width_deg, step_deg)
    return np.mod(raw, 360.0)


def _resolve_n_jobs(n_jobs: int | None) -> int:
    if n_jobs is None:
        return 1
    jobs = int(n_jobs)
    if jobs == 0:
        return max(1, (os.cpu_count() or 2) - 1)
    if jobs < 0:
        return max(1, (os.cpu_count() or 2) + jobs + 1)
    return max(1, jobs)


def _trim_candidates(candidates: list[CandidateResult], trigger_size: int, keep_size: int) -> None:
    if len(candidates) > trigger_size:
        candidates.sort(key=lambda item: item.combined_score, reverse=True)
        del candidates[keep_size:]


def _chunked(items: list[tuple[float, float, float, float]], chunk_size: int) -> Iterable[list[tuple[float, float, float, float]]]:
    for start in range(0, len(items), max(1, chunk_size)):
        yield items[start : start + chunk_size]
