"""Lightweight constant-velocity smoothing for navigation estimates."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tercom_uav.config import KalmanConfig


def _azimuth_from_velocity(vx_mps: float, vy_mps: float) -> float:
    return float((np.degrees(np.arctan2(vx_mps, vy_mps)) + 360.0) % 360.0)


def smooth_estimates(estimates: pd.DataFrame, config: KalmanConfig | None = None) -> pd.DataFrame:
    """Apply alpha-beta smoothing to position and velocity columns.

    Confidence scales the measurement update: low-confidence TERCOM matches
    pull the state less aggressively.
    """

    cfg = config or KalmanConfig(enabled=True)
    cfg.validate()
    if estimates.empty or not cfg.enabled:
        return estimates.copy()

    result = estimates.copy().sort_values("time_s").reset_index(drop=True)
    result["raw_x_m"] = result["x_m"]
    result["raw_y_m"] = result["y_m"]
    result["raw_vx_mps"] = result["vx_mps"]
    result["raw_vy_mps"] = result["vy_mps"]

    x = float(result.loc[0, "x_m"])
    y = float(result.loc[0, "y_m"])
    vx = float(result.loc[0, "vx_mps"])
    vy = float(result.loc[0, "vy_mps"])
    previous_time = float(result.loc[0, "time_s"])

    smoothed_rows: list[tuple[float, float, float, float]] = [(x, y, vx, vy)]
    for idx in range(1, len(result)):
        time_s = float(result.loc[idx, "time_s"])
        dt = max(time_s - previous_time, 1e-6)
        previous_time = time_s

        x_pred = x + vx * dt
        y_pred = y + vy * dt
        confidence = float(result.loc[idx, "confidence_score"])
        weight = max(confidence, cfg.min_confidence_weight)
        alpha = cfg.alpha * weight
        beta = cfg.beta * weight

        residual_x = float(result.loc[idx, "x_m"]) - x_pred
        residual_y = float(result.loc[idx, "y_m"]) - y_pred
        x = x_pred + alpha * residual_x
        y = y_pred + alpha * residual_y
        vx = vx + beta * residual_x / dt
        vy = vy + beta * residual_y / dt
        smoothed_rows.append((x, y, vx, vy))

    smoothed = np.asarray(smoothed_rows, dtype=float)
    result["x_m"] = smoothed[:, 0]
    result["y_m"] = smoothed[:, 1]
    result["vx_mps"] = smoothed[:, 2]
    result["vy_mps"] = smoothed[:, 3]
    result["speed_mps"] = np.hypot(result["vx_mps"], result["vy_mps"])
    result["azimuth_deg"] = [
        _azimuth_from_velocity(vx_value, vy_value)
        for vx_value, vy_value in zip(result["vx_mps"], result["vy_mps"], strict=True)
    ]
    return result

