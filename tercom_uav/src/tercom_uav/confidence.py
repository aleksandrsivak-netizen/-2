"""Confidence, ambiguity and observability helpers."""

from __future__ import annotations

import numpy as np


def terrain_roughness_score(profile_m: np.ndarray) -> float:
    """Return terrain roughness as standard deviation of first differences."""

    values = np.asarray(profile_m, dtype=float)
    values = values[np.isfinite(values)]
    if values.size < 3:
        return 0.0
    return float(np.std(np.diff(values)))


def observability_from_roughness(roughness_m: float, scale_m: float = 8.0) -> float:
    """Map roughness to 0..1 observability score."""

    roughness = max(float(roughness_m), 0.0)
    return float(roughness / (roughness + scale_m))


def confidence_from_scores(
    best_score: float,
    second_best_score: float,
    roughness_m: float,
    min_correlation: float = 0.55,
    min_score_gap: float = 0.05,
    min_observability: float = 0.2,
    heatmap_std: float | None = None,
    min_relative_gap: float = 0.75,
    low_observability: bool = False,
) -> tuple[float, bool, float, float]:
    """Compute confidence and ambiguity from correlation diagnostics.

    `min_score_gap` is an absolute floor (kept for backward compatibility and
    for cases where the heatmap spread is unknown), but the primary
    discriminator is the *relative* gap: the raw absolute Pearson-correlation
    gap between the best and second-best match is almost always tiny (~0.01)
    on smooth low-frequency terrain (taiga/tundra/steppe), even for a perfect
    fix, so an absolute threshold of 0.05 flags nearly every fix as ambiguous
    regardless of whether it is correct. Normalizing the gap by the standard
    deviation of the whole heatmap (how "peaky" the correlation surface is
    relative to its own noise floor) gives a scale-invariant signal that
    still distinguishes a sharp, unique peak from a degenerate one.
    """

    if not np.isfinite(best_score):
        return 0.0, True, 0.0, 0.0

    score_gap = max(float(best_score - second_best_score), 0.0)
    observability = observability_from_roughness(roughness_m)
    if low_observability:
        observability = min(observability, min_observability * 0.5)
    corr_component = np.clip((best_score + 1.0) * 0.5, 0.0, 1.0)

    if heatmap_std is not None and heatmap_std > 1e-9:
        relative_gap = score_gap / float(heatmap_std)
    else:
        relative_gap = score_gap / max(min_score_gap * 3.0, 1e-6)
    gap_component = np.clip(relative_gap / max(min_relative_gap, 1e-6), 0.0, 1.0)
    terrain_component = observability
    confidence = float(0.45 * corr_component + 0.35 * gap_component + 0.20 * terrain_component)

    ambiguous = (
        best_score < min_correlation
        or score_gap < min_score_gap
        or relative_gap < min_relative_gap
        or observability < min_observability
        or low_observability
    )
    if ambiguous:
        confidence = min(confidence, 0.45)
    if low_observability:
        confidence = min(confidence, 0.25)
    return confidence, ambiguous, observability, score_gap
