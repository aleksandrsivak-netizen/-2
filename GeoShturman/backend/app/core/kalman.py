"""Small 1D Kalman smoother."""

from __future__ import annotations

import numpy as np


def kalman_smooth_1d(
    values: np.ndarray,
    process_variance: float = 1.0,
    measurement_variance: float = 4.0,
) -> np.ndarray:
    """Smooth a 1D signal with a constant-value Kalman filter."""

    observations = np.asarray(values, dtype=float)
    if observations.size == 0:
        return observations.copy()
    if process_variance <= 0 or measurement_variance <= 0:
        raise ValueError("Kalman variances must be positive")

    result = np.empty_like(observations, dtype=float)
    finite = observations[np.isfinite(observations)]
    if finite.size == 0:
        return np.zeros_like(observations, dtype=float)

    estimate = float(finite[0])
    error_covariance = 1.0
    for index, measurement in enumerate(observations):
        error_covariance += float(process_variance)
        if np.isfinite(measurement):
            kalman_gain = error_covariance / (error_covariance + float(measurement_variance))
            estimate = estimate + kalman_gain * (float(measurement) - estimate)
            error_covariance = (1.0 - kalman_gain) * error_covariance
        result[index] = estimate
    return result
