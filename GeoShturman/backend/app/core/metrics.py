"""Quality metrics for terrain-referenced navigation results."""

from __future__ import annotations

import math

import numpy as np

from .correlation import CandidateResult


def terrain_informativeness(profile: np.ndarray) -> float:
    """Estimate how useful a terrain profile is for correlation matching."""

    values = np.asarray(profile, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return 0.0
    std_score = min(float(np.std(values)) / 45.0, 1.0)
    range_score = min(float(np.ptp(values)) / 160.0, 1.0)
    gradient = np.diff(values)
    gradient_energy = float(np.sqrt(np.mean(gradient**2))) if gradient.size else 0.0
    gradient_score = min(gradient_energy / 9.0, 1.0)
    return float(np.clip(0.42 * std_score + 0.33 * range_score + 0.25 * gradient_score, 0.0, 1.0))


def peak_sharpness(candidates: list[CandidateResult]) -> float:
    """Return a normalized measure of how isolated the best score is."""

    if len(candidates) < 2:
        return 1.0 if candidates else 0.0
    scores = np.asarray([item.combined_score for item in candidates], dtype=float)
    scores = np.sort(scores)[::-1]
    spread = float(np.std(scores))
    if spread < 1e-9:
        return 0.0
    return float(np.clip((scores[0] - scores[1]) / spread, 0.0, 1.0))


def top1_top2_gap(candidates: list[CandidateResult]) -> float:
    """Return score gap between the two best candidates."""

    if len(candidates) < 2:
        return float("inf") if candidates else 0.0
    scores = sorted((item.combined_score for item in candidates), reverse=True)
    return float(scores[0] - scores[1])


def confidence_score(
    correlation: float,
    rmse_m: float,
    terrain_info: float,
    peak_gap: float,
) -> float:
    """Combine quality signals into a 0..1 confidence score."""

    corr_score = np.clip((float(correlation) + 1.0) / 2.0, 0.0, 1.0)
    rmse_score = math.exp(-max(float(rmse_m), 0.0) / 45.0)
    info_score = np.clip(float(terrain_info), 0.0, 1.0)
    gap_score = np.clip(float(peak_gap) / 0.04, 0.0, 1.0) if np.isfinite(peak_gap) else 1.0
    confidence = 0.45 * corr_score + 0.25 * rmse_score + 0.20 * info_score + 0.10 * gap_score
    return float(np.clip(confidence, 0.0, 1.0))


def build_quality_report(
    candidates: list[CandidateResult],
    measured_profile: np.ndarray,
    best: CandidateResult | None = None,
) -> dict:
    """Build a dictionary with confidence inputs and warnings."""

    best_candidate = best or (candidates[0] if candidates else None)
    terrain_info = terrain_informativeness(measured_profile)
    gap = top1_top2_gap(candidates)
    sharpness = peak_sharpness(candidates)
    correlation = best_candidate.correlation if best_candidate else 0.0
    rmse_m = best_candidate.rmse_m if best_candidate else float("inf")
    confidence = confidence_score(correlation, rmse_m, terrain_info, gap)

    warnings: list[str] = []
    if terrain_info < 0.08:
        warnings.append("terrain_flat")
    if len(candidates) >= 2 and gap < 0.005:
        warnings.append("top_candidates_close")
    if correlation < 0.65:
        warnings.append("low_correlation")
    if not np.isfinite(rmse_m) or rmse_m > 55.0:
        warnings.append("high_rmse")
    if best_candidate is not None:
        drift_total = abs(best_candidate.drift_slope_m_per_sample) * max(len(measured_profile) - 1, 0)
        if drift_total > 80.0:
            warnings.append("large_baro_drift")

    return {
        "confidence": confidence,
        "terrain_informativeness": terrain_info,
        "peak_gap": gap,
        "peak_sharpness": sharpness,
        "correlation": float(correlation),
        "rmse_m": float(rmse_m),
        "baro_drift_total_m": float(
            abs(best_candidate.drift_slope_m_per_sample) * max(len(measured_profile) - 1, 0)
            if best_candidate is not None
            else 0.0
        ),
        "warnings": warnings,
        "warning": "; ".join(warnings) if warnings else None,
    }
