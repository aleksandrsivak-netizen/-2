"""Dead-reckoning baseline for GNSS-denied navigation demos."""

from __future__ import annotations

import numpy as np


def run_dead_reckoning(
    sensor_stream: list[dict],
    initial_x_m: float,
    initial_y_m: float,
) -> list[dict]:
    """Integrate measured speed and heading without terrain correction."""

    if not sensor_stream:
        return []

    x_m = float(initial_x_m)
    y_m = float(initial_y_m)
    trajectory: list[dict] = []
    previous_t = float(sensor_stream[0].get("t_s", 0.0))

    for index, measurement in enumerate(sensor_stream):
        t_s = float(measurement.get("t_s", previous_t))
        dt_s = max(0.0, t_s - previous_t) if index > 0 else 0.0
        speed_mps = float(measurement.get("speed_mps", 0.0))
        heading_deg = float(measurement.get("heading_deg", 0.0)) % 360.0
        heading_rad = np.deg2rad(heading_deg)
        x_m += speed_mps * dt_s * float(np.sin(heading_rad))
        y_m += speed_mps * dt_s * float(np.cos(heading_rad))
        trajectory.append(
            {
                "t_s": t_s,
                "x_m": x_m,
                "y_m": y_m,
                "heading_deg": heading_deg,
                "speed_mps": speed_mps,
            }
        )
        previous_t = t_s

    return trajectory
