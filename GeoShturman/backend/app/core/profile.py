"""Profile preprocessing utilities."""

from __future__ import annotations

import numpy as np


def radio_agl_to_terrain_msl(
    radio_altitude_agl: np.ndarray,
    barometric_altitude_msl: float | np.ndarray,
) -> np.ndarray:
    """Convert AGL radio altitude to terrain MSL elevation.

    ``barometric_altitude_msl`` may be a scalar or a sample-aligned array. The
    array form is useful when later API layers provide a barometric altitude
    stream rather than one nominal altitude.
    """

    return np.asarray(barometric_altitude_msl, dtype=float) - np.asarray(radio_altitude_agl, dtype=float)


def clean_profile(
    profile: np.ndarray,
    median_window: int = 3,
    max_jump_m: float | None = None,
    hampel_window: int = 5,
    outlier_sigma: float = 3.5,
) -> np.ndarray:
    """Clean NaNs, isolated spikes, and implausible jumps from a profile."""

    values = np.asarray(profile, dtype=float)
    if values.size == 0:
        return values.copy()

    cleaned = _fill_nan_linear(values)
    if hampel_window > 1 and outlier_sigma > 0:
        cleaned = hampel_filter(cleaned, window_size=hampel_window, n_sigmas=outlier_sigma)
    if median_window > 1:
        cleaned = rolling_median(cleaned, window_size=median_window)

    if max_jump_m is not None and max_jump_m > 0 and cleaned.size > 1:
        cleaned = limit_profile_jumps(cleaned, max_jump_m=max_jump_m)
    return cleaned


def hampel_filter(
    profile: np.ndarray,
    window_size: int = 5,
    n_sigmas: float = 3.5,
) -> np.ndarray:
    """Replace local outliers using a Hampel median/MAD filter."""

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size == 0 or window_size <= 1:
        return values.copy()
    window = _odd_window(window_size)
    radius = window // 2
    filtered = values.copy()
    scale = 1.4826
    for index in range(values.size):
        start = max(0, index - radius)
        stop = min(values.size, index + radius + 1)
        local = values[start:stop]
        median = float(np.median(local))
        mad = float(np.median(np.abs(local - median)))
        threshold = n_sigmas * scale * max(mad, 1e-9)
        if abs(values[index] - median) > threshold:
            filtered[index] = median
    return filtered


def rolling_median(profile: np.ndarray, window_size: int = 3) -> np.ndarray:
    """Apply an edge-padded rolling median."""

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size == 0 or window_size <= 1:
        return values.copy()
    window = _odd_window(window_size)
    radius = window // 2
    padded = np.pad(values, radius, mode="edge")
    filtered = np.empty_like(values)
    for index in range(values.size):
        filtered[index] = float(np.median(padded[index : index + window]))
    return filtered


def limit_profile_jumps(profile: np.ndarray, max_jump_m: float) -> np.ndarray:
    """Limit single-sample discontinuities while preserving slow trends."""

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size <= 1:
        return values.copy()
    limited = values.copy()
    for index in range(1, limited.size):
        jump = limited[index] - limited[index - 1]
        if abs(jump) > max_jump_m:
            limited[index] = limited[index - 1] + np.sign(jump) * max_jump_m
    return limited


def normalize_profile(profile: np.ndarray) -> np.ndarray:
    """Return zero-mean, unit-std profile; flat data becomes zeros."""

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size == 0:
        return values.copy()
    mean = float(np.mean(values))
    std = float(np.std(values))
    if not np.isfinite(std) or std < 1e-9:
        return np.zeros_like(values, dtype=float)
    return (values - mean) / std


def remove_linear_trend(profile: np.ndarray, keep_mean: bool = True) -> np.ndarray:
    """Remove a best-fit linear trend from a profile."""

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size < 2:
        return values.copy()
    x = np.arange(values.size, dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    detrended = values - (slope * x + intercept)
    if keep_mean:
        detrended = detrended + float(np.mean(values))
    return detrended


def align_profile_to_reference(
    measured_profile: np.ndarray,
    reference_profile: np.ndarray,
    degree: int = 1,
) -> tuple[np.ndarray, dict]:
    """Remove a low-order measured-vs-reference residual drift.

    Returns the corrected measured profile and a small metadata dictionary with
    the fitted offset and slope in meters per sample. A linear residual absorbs
    slow barometric drift without changing the terrain signature.
    """

    measured = _fill_nan_linear(np.asarray(measured_profile, dtype=float))
    reference = _fill_nan_linear(np.asarray(reference_profile, dtype=float))
    if measured.shape != reference.shape:
        raise ValueError("profiles must have the same shape")
    if measured.size == 0:
        return measured.copy(), {"offset_m": 0.0, "slope_m_per_sample": 0.0, "degree": degree}

    mask = np.isfinite(measured) & np.isfinite(reference)
    if np.count_nonzero(mask) < max(2, degree + 1):
        return measured.copy(), {"offset_m": 0.0, "slope_m_per_sample": 0.0, "degree": degree}

    fit_degree = max(0, min(int(degree), np.count_nonzero(mask) - 1, 2))
    x = np.arange(measured.size, dtype=float)
    residual = measured[mask] - reference[mask]
    coefficients = np.polyfit(x[mask], residual, fit_degree)
    drift = np.polyval(coefficients, x)
    corrected = measured - drift
    if fit_degree == 0:
        slope = 0.0
        offset = float(coefficients[-1])
    else:
        offset = float(np.polyval(coefficients, 0.0))
        slope = float(np.polyval(coefficients, 1.0) - offset)
    return corrected, {"offset_m": offset, "slope_m_per_sample": slope, "degree": fit_degree}


def resample_profile(profile: np.ndarray, target_length: int) -> np.ndarray:
    """Resample a 1D profile to ``target_length`` with linear interpolation."""

    if target_length < 0:
        raise ValueError("target_length must be non-negative")
    if target_length == 0:
        return np.asarray([], dtype=float)

    values = _fill_nan_linear(np.asarray(profile, dtype=float))
    if values.size == 0:
        return np.zeros(target_length, dtype=float)
    if values.size == target_length:
        return values.copy()
    if values.size == 1:
        return np.full(target_length, values[0], dtype=float)

    source_x = np.linspace(0.0, 1.0, values.size)
    target_x = np.linspace(0.0, 1.0, target_length)
    return np.interp(target_x, source_x, values)


def _fill_nan_linear(values: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=float).copy()
    if result.size == 0:
        return result
    finite = np.isfinite(result)
    if np.all(finite):
        return result
    if not np.any(finite):
        return np.zeros_like(result, dtype=float)
    x = np.arange(result.size)
    result[~finite] = np.interp(x[~finite], x[finite], result[finite])
    return result


def _odd_window(window_size: int) -> int:
    window = max(1, int(window_size))
    return window if window % 2 == 1 else window + 1
