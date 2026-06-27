"""Civil UAV route planning helpers for TERCOM dashboard scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tercom_uav.confidence import observability_from_roughness, terrain_roughness_score
from tercom_uav.dem import DEMGrid
from tercom_uav.nmea import generate_gpgga


@dataclass(slots=True)
class RouteBuildResult:
    """Route, truth trajectory and summary metrics."""

    route: pd.DataFrame
    truth: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str]
    waypoints: list[tuple[float, float]]


def heading_between_points(x0_m: float, y0_m: float, x1_m: float, y1_m: float) -> float:
    """Return heading clockwise from north between two local metric points."""

    return float((np.degrees(np.arctan2(x1_m - x0_m, y1_m - y0_m)) + 360.0) % 360.0)


def parse_waypoints(text: str) -> list[tuple[float, float]]:
    """Parse a user-entered waypoint list with optional `x,y` header."""

    points: list[tuple[float, float]] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if line_no == 1 and any(char.isalpha() for char in line):
            continue
        parts = [part.strip() for part in line.replace(";", ",").split(",")]
        if len(parts) != 2:
            raise ValueError(f"Waypoint line {line_no} must have format x,y.")
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError as exc:
            raise ValueError(f"Waypoint line {line_no} contains non-numeric coordinates.") from exc
    if len(points) < 2:
        raise ValueError("At least two waypoint points are required.")
    return points


def _validate_hz(hz: float) -> None:
    if not 1.0 <= hz <= 10.0:
        raise ValueError("Route/NMEA frequency must be in range 1..10 Hz.")


def _sample_polyline(
    points: list[tuple[float, float]],
    speed_mps: float,
    hz: float,
    duration_s: float | None = None,
) -> pd.DataFrame:
    if speed_mps <= 0:
        raise ValueError("speed_mps must be positive.")
    _validate_hz(hz)
    coords = np.asarray(points, dtype=float)
    if coords.ndim != 2 or coords.shape[0] < 2 or coords.shape[1] != 2:
        raise ValueError("At least two route points are required.")

    segment_vectors = np.diff(coords, axis=0)
    segment_lengths = np.hypot(segment_vectors[:, 0], segment_vectors[:, 1])
    total_length = float(np.sum(segment_lengths))
    if total_length <= 0:
        raise ValueError("Route length must be positive.")
    total_duration = float(duration_s) if duration_s and duration_s > 0 else total_length / speed_mps
    sample_count = max(int(np.floor(total_duration * hz)) + 1, 2)
    times = np.linspace(0.0, total_duration, sample_count)
    distances = np.linspace(0.0, total_length, sample_count)
    cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))

    x = np.interp(distances, cumulative, coords[:, 0])
    y = np.interp(distances, cumulative, coords[:, 1])
    heading = np.empty_like(distances)
    speed = np.full_like(distances, total_length / total_duration)
    for idx, distance_m in enumerate(distances):
        segment_idx = int(np.searchsorted(cumulative, distance_m, side="right") - 1)
        segment_idx = int(np.clip(segment_idx, 0, len(segment_lengths) - 1))
        start = coords[segment_idx]
        end = coords[segment_idx + 1]
        heading[idx] = heading_between_points(start[0], start[1], end[0], end[1])

    return pd.DataFrame(
        {
            "time_s": times,
            "t": times,
            "x_m": x,
            "y_m": y,
            "x": x,
            "y": y,
            "heading_deg": heading,
            "speed_mps": speed,
            "traveled_distance_m": distances,
        }
    )


def _enrich_with_dem(route: pd.DataFrame, dem: DEMGrid, baro_alt_msl: float) -> pd.DataFrame:
    terrain = np.asarray(dem.sample(route["x_m"].to_numpy(), route["y_m"].to_numpy()), dtype=float)
    if np.any(~np.isfinite(terrain)):
        bad_count = int(np.sum(~np.isfinite(terrain)))
        raise ValueError(f"Route leaves DEM bounds or crosses nodata cells ({bad_count} invalid samples).")
    truth = route.copy()
    truth["z_dem"] = terrain
    truth["terrain_msl_m"] = terrain
    truth["baro_altitude"] = float(baro_alt_msl)
    truth["baro_alt_msl_m"] = float(baro_alt_msl)
    truth["radar_altitude"] = float(baro_alt_msl) - terrain
    truth["true_radio_alt_agl_m"] = truth["radar_altitude"]
    return truth


def _circular_mean_deg(values_deg: np.ndarray) -> float:
    radians = np.deg2rad(values_deg)
    return float((np.degrees(np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians)))) + 360.0) % 360.0)


def route_summary(
    mode: str,
    truth: pd.DataFrame,
    waypoints: list[tuple[float, float]],
) -> tuple[dict[str, Any], list[str]]:
    terrain = truth["z_dem"].to_numpy(dtype=float)
    roughness = terrain_roughness_score(terrain)
    observability = observability_from_roughness(roughness)
    if observability >= 0.55:
        suitability = "high"
    elif observability >= 0.25:
        suitability = "medium"
    else:
        suitability = "low"
    length = float(truth["traveled_distance_m"].iloc[-1])
    duration = float(truth["time_s"].iloc[-1] - truth["time_s"].iloc[0])
    warnings: list[str] = []
    if suitability == "low":
        warnings.append("Маршрут проходит по слабо выраженному рельефу. TERCOM-локализация может быть неоднозначной.")
    return (
        {
            "mode": mode,
            "length_m": length,
            "duration_s": duration,
            "mean_speed_mps": float(length / duration) if duration > 0 else 0.0,
            "mean_heading_deg": _circular_mean_deg(truth["heading_deg"].to_numpy(dtype=float)),
            "min_terrain_m": float(np.min(terrain)),
            "max_terrain_m": float(np.max(terrain)),
            "roughness": roughness,
            "observability": observability,
            "tercom_suitability": suitability,
            "point_count": int(len(truth)),
            "waypoint_count": int(len(waypoints)),
        },
        warnings,
    )


def build_simple_route(
    dem: DEMGrid,
    x0_m: float,
    y0_m: float,
    heading_deg: float,
    speed_mps: float,
    duration_s: float,
    hz: float,
    baro_alt_msl: float,
) -> RouteBuildResult:
    distance = speed_mps * duration_s
    heading_rad = np.deg2rad(heading_deg)
    end = (x0_m + np.sin(heading_rad) * distance, y0_m + np.cos(heading_rad) * distance)
    route = _sample_polyline([(x0_m, y0_m), end], speed_mps=speed_mps, hz=hz, duration_s=duration_s)
    truth = _enrich_with_dem(route, dem, baro_alt_msl)
    waypoints = [(float(x0_m), float(y0_m)), (float(end[0]), float(end[1]))]
    summary, warnings = route_summary("simple", truth, waypoints)
    return RouteBuildResult(route=route, truth=truth, summary=summary, warnings=warnings, waypoints=waypoints)


def build_waypoint_route(
    dem: DEMGrid,
    waypoint_text: str,
    speed_mps: float,
    hz: float,
    baro_alt_msl: float,
) -> RouteBuildResult:
    waypoints = parse_waypoints(waypoint_text)
    route = _sample_polyline(waypoints, speed_mps=speed_mps, hz=hz)
    truth = _enrich_with_dem(route, dem, baro_alt_msl)
    summary, warnings = route_summary("waypoints", truth, waypoints)
    return RouteBuildResult(route=route, truth=truth, summary=summary, warnings=warnings, waypoints=waypoints)


def build_automatic_route(
    dem: DEMGrid,
    start_x_m: float,
    start_y_m: float,
    end_x_m: float,
    end_y_m: float,
    speed_mps: float,
    hz: float,
    baro_alt_msl: float,
    desired_length_m: float | None = None,
    desired_duration_s: float | None = None,
    min_observability: float = 0.25,
) -> RouteBuildResult:
    start = np.array([start_x_m, start_y_m], dtype=float)
    end = np.array([end_x_m, end_y_m], dtype=float)
    vector = end - start
    straight_length = float(np.hypot(vector[0], vector[1]))
    if straight_length <= 0:
        raise ValueError("Automatic route start and end points must differ.")
    target_length = desired_length_m or ((desired_duration_s * speed_mps) if desired_duration_s and desired_duration_s > 0 else straight_length * 1.25)
    target_length = max(float(target_length), straight_length)
    midpoint = (start + end) * 0.5
    normal = np.array([-vector[1], vector[0]], dtype=float) / straight_length
    max_offset = min(max(target_length, straight_length) * 0.75, min(dem.bounds_m[2] - dem.bounds_m[0], dem.bounds_m[3] - dem.bounds_m[1]) * 0.35)
    offsets = np.linspace(-max_offset, max_offset, 25)

    best_result: RouteBuildResult | None = None
    best_score = -float("inf")
    for offset in offsets:
        candidate_mid = midpoint + normal * offset
        candidate_points = [
            (float(start[0]), float(start[1])),
            (float(candidate_mid[0]), float(candidate_mid[1])),
            (float(end[0]), float(end[1])),
        ]
        try:
            route = _sample_polyline(candidate_points, speed_mps=speed_mps, hz=hz)
            truth = _enrich_with_dem(route, dem, baro_alt_msl)
            summary, warnings = route_summary("automatic", truth, candidate_points)
        except ValueError:
            continue
        length_error = abs(summary["length_m"] - target_length) / max(target_length, 1.0)
        observability_bonus = summary["observability"]
        meets_min_bonus = 0.25 if summary["observability"] >= min_observability else 0.0
        score = observability_bonus + meets_min_bonus - 0.35 * length_error
        if score > best_score:
            best_score = score
            best_result = RouteBuildResult(route=route, truth=truth, summary=summary, warnings=warnings, waypoints=candidate_points)

    if best_result is None:
        raise ValueError("Could not build an automatic route inside DEM bounds.")
    if best_result.summary["observability"] < min_observability:
        best_result.warnings.append(
            f"Не удалось достичь заданной наблюдаемости {min_observability:.2f}; выбран лучший доступный вариант."
        )
    return best_result


def _resample_truth(truth: pd.DataFrame, target_hz: float | None) -> pd.DataFrame:
    if target_hz is None:
        return truth.copy()
    _validate_hz(target_hz)
    source = truth.sort_values("time_s").reset_index(drop=True)
    source_times = source["time_s"].to_numpy(dtype=float)
    duration_s = float(source_times[-1] - source_times[0])
    if duration_s <= 0:
        return source.copy()
    sample_count = max(int(np.floor(duration_s * target_hz)) + 1, 2)
    target_times = np.linspace(source_times[0], source_times[-1], sample_count)
    resampled: dict[str, Any] = {"time_s": target_times}
    numeric_columns = source.select_dtypes(include=[np.number]).columns
    for column in numeric_columns:
        if column in {"time_s", "heading_deg"}:
            continue
        resampled[column] = np.interp(target_times, source_times, source[column].to_numpy(dtype=float))
    if "heading_deg" in source:
        heading_rad = np.deg2rad(source["heading_deg"].to_numpy(dtype=float))
        sin_heading = np.interp(target_times, source_times, np.sin(heading_rad))
        cos_heading = np.interp(target_times, source_times, np.cos(heading_rad))
        resampled["heading_deg"] = (np.degrees(np.arctan2(sin_heading, cos_heading)) + 360.0) % 360.0
    if "t" in source:
        resampled["t"] = target_times
    return pd.DataFrame(resampled)


def generate_nmea_from_truth(
    truth: pd.DataFrame,
    noise_std_m: float = 0.0,
    outlier_prob: float = 0.0,
    outlier_std_m: float = 35.0,
    dropout_prob: float = 0.0,
    drift_mps: float = 0.0,
    random_seed: int = 42,
    target_hz: float | None = None,
) -> tuple[list[str], pd.DataFrame]:
    """Generate GPGGA radio-altimeter telemetry from a route truth table."""

    if noise_std_m < 0:
        raise ValueError("NMEA noise must be non-negative.")
    if outlier_std_m < 0:
        raise ValueError("NMEA outlier standard deviation must be non-negative.")
    if not 0.0 <= outlier_prob <= 1.0:
        raise ValueError("NMEA outlier probability must be in range 0..1.")
    if not 0.0 <= dropout_prob <= 1.0:
        raise ValueError("NMEA dropout probability must be in range 0..1.")
    rng = np.random.default_rng(random_seed)
    telemetry = _resample_truth(truth, target_hz)
    times = telemetry["time_s"].to_numpy(dtype=float)
    radio = telemetry["radar_altitude"].to_numpy(dtype=float) + drift_mps * times
    if noise_std_m > 0:
        radio = radio + rng.normal(0.0, noise_std_m, size=radio.size)
    outlier_mask = rng.random(radio.size) < outlier_prob if outlier_prob > 0 else np.zeros(radio.size, dtype=bool)
    if np.any(outlier_mask):
        radio[outlier_mask] += rng.normal(0.0, outlier_std_m, size=int(np.sum(outlier_mask)))
    dropout_mask = rng.random(radio.size) < dropout_prob if dropout_prob > 0 else np.zeros(radio.size, dtype=bool)
    telemetry["radio_alt_agl_m"] = radio
    telemetry["dropout"] = dropout_mask
    lines = [
        generate_gpgga(float(radio_alt_m), float(time_s))
        for time_s, radio_alt_m, dropped in zip(times, radio, dropout_mask, strict=True)
        if not dropped and np.isfinite(radio_alt_m)
    ]
    if not lines:
        raise ValueError("NMEA generation produced no valid samples. Reduce dropout probability.")
    return lines, telemetry


def save_route_artifacts(
    result: RouteBuildResult,
    out_dir: str | Path,
    route_config: dict[str, Any],
) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    route_payload = {
        "config": route_config,
        "route": result.summary,
        "warnings": result.warnings,
        "waypoints": [{"x_m": x, "y_m": y} for x, y in result.waypoints],
    }
    (out / "route.json").write_text(json.dumps(route_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    result.route.to_csv(out / "route.csv", index=False)
    result.truth.to_csv(out / "truth.csv", index=False)


def plot_route_plan(
    dem: DEMGrid,
    result: RouteBuildResult,
    out_dir: str | Path,
) -> Path:
    """Save route plan PNG over DEM with start/end, waypoints and direction arrows."""

    out = Path(out_dir)
    path = out / "route_plan.png"
    x_min, y_min, x_max, y_max = dem.bounds_m
    route = result.route
    waypoints = np.asarray(result.waypoints, dtype=float)
    fig, ax = plt.subplots(figsize=(9, 8))
    image = ax.imshow(
        dem.elevation_m,
        origin="lower",
        extent=[x_min, x_max, y_min, y_max],
        cmap="terrain",
        aspect="equal",
    )
    ax.plot(route["x_m"], route["y_m"], color="#e11d48", linewidth=2.2, label="route")
    ax.scatter([waypoints[0, 0]], [waypoints[0, 1]], color="#16a34a", s=70, label="start", zorder=4)
    ax.scatter([waypoints[-1, 0]], [waypoints[-1, 1]], color="#2563eb", s=70, label="finish", zorder=4)
    if len(waypoints) > 2:
        ax.scatter(waypoints[1:-1, 0], waypoints[1:-1, 1], color="#f59e0b", s=48, label="waypoints", zorder=4)
    arrow_count = min(8, max(1, len(route) // 20))
    arrow_indices = np.linspace(1, len(route) - 2, arrow_count, dtype=int)
    arrow_length_m = min(max(result.summary["length_m"] / 24.0, 120.0), 520.0)
    ax.quiver(
        route["x_m"].iloc[arrow_indices],
        route["y_m"].iloc[arrow_indices],
        np.sin(np.deg2rad(route["heading_deg"].iloc[arrow_indices])) * arrow_length_m,
        np.cos(np.deg2rad(route["heading_deg"].iloc[arrow_indices])) * arrow_length_m,
        angles="xy",
        scale_units="xy",
        scale=1.0,
        color="#111827",
        width=0.004,
    )
    label = (
        f"Длина {result.summary['length_m']:.0f} м, "
        f"средний азимут {result.summary['mean_heading_deg']:.1f}°"
    )
    ax.text(
        0.02,
        0.98,
        label,
        transform=ax.transAxes,
        va="top",
        ha="left",
        bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "#d1d5db", "boxstyle": "round,pad=0.35"},
    )
    ax.set_xlabel("x восток, м")
    ax.set_ylabel("y север, м")
    ax.set_title("План гражданского маршрута БПЛА")
    ax.legend(loc="lower right")
    fig.colorbar(image, ax=ax, label="Высота MSL, м")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path
