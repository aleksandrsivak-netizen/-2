"""Dorabotka pipeline: heights.txt + GeoTIFF -> corrected trajectory."""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from tercom_uav.dem import DEMGrid


class DorabotkaError(ValueError):
    """User-facing Dorabotka pipeline error."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


@dataclass(slots=True)
class DorabotkaSearchConfig:
    sample_step_m: float = 1.0
    search_radius_m: float = 200.0
    search_step_m: float = 5.0
    heading_search_deg: float = 5.0
    heading_step_deg: float = 1.0
    start_coord_type: str = "auto"
    normalize_profile: bool = True
    coarse_to_fine: bool = True
    max_candidates: int = 8
    max_hypotheses: int = 250_000
    min_points: int = 5

    def validate(self) -> None:
        if self.sample_step_m <= 0:
            raise DorabotkaError("invalid_sample_step", "sample_step_m must be positive")
        if self.search_radius_m < 0:
            raise DorabotkaError("invalid_search_radius", "search_radius_m must be non-negative")
        if self.search_step_m <= 0:
            raise DorabotkaError("invalid_search_step", "search_step_m must be positive")
        if self.heading_search_deg < 0:
            raise DorabotkaError("invalid_heading_search", "heading_search_deg must be non-negative")
        if self.heading_step_deg <= 0:
            raise DorabotkaError("invalid_heading_step", "heading_step_deg must be positive")
        if self.start_coord_type not in {"auto", "map", "pixel"}:
            raise DorabotkaError("invalid_start_coord_type", "start_coord_type must be auto, map or pixel")
        if self.max_candidates <= 0:
            raise DorabotkaError("invalid_max_candidates", "max_candidates must be positive")
        if self.max_hypotheses <= 0:
            raise DorabotkaError("invalid_max_hypotheses", "max_hypotheses must be positive")
        if self.min_points < 2:
            raise DorabotkaError("invalid_min_points", "min_points must be >= 2")


@dataclass(slots=True)
class GeoTiffContext:
    path: Path
    dem: DEMGrid
    width: int
    height: int
    transform: Any
    bounds: Any
    source_crs: Any

    @classmethod
    def from_path(cls, path: str | Path) -> "GeoTiffContext":
        geotiff_path = Path(path)
        if not geotiff_path.exists():
            raise DorabotkaError("geotiff_not_found", f"GeoTIFF not found: {geotiff_path}")
        try:
            import rasterio
        except ImportError as exc:
            raise DorabotkaError("geotiff_dependency_missing", "GeoTIFF loading requires rasterio") from exc

        try:
            dem = DEMGrid.from_geotiff(geotiff_path)
            with rasterio.open(geotiff_path) as dataset:
                return cls(
                    path=geotiff_path,
                    dem=dem,
                    width=int(dataset.width),
                    height=int(dataset.height),
                    transform=dataset.transform,
                    bounds=dataset.bounds,
                    source_crs=dataset.crs,
                )
        except DorabotkaError:
            raise
        except Exception as exc:
            raise DorabotkaError("geotiff_unreadable", f"GeoTIFF cannot be read: {geotiff_path}") from exc

    def source_to_local(self, x: float, y: float) -> tuple[float, float]:
        if self.source_crs is None:
            return float(x), float(y)
        try:
            from pyproj import CRS
        except ImportError as exc:
            raise DorabotkaError("geotiff_dependency_missing", "CRS conversion requires pyproj") from exc

        crs = CRS.from_user_input(self.source_crs)
        if crs.is_geographic:
            try:
                return self.dem.wgs84_to_local(float(x), float(y))
            except Exception as exc:
                raise DorabotkaError("coordinate_transform_failed", "Cannot convert GeoTIFF map coordinates to local meters") from exc
        return float(x), float(y)

    def pixel_to_local(self, col: float, row: float) -> tuple[float, float]:
        source_x, source_y = self.transform * (float(col) + 0.5, float(row) + 0.5)
        return self.source_to_local(source_x, source_y)

    def local_to_global(self, x: float, y: float) -> tuple[float | None, float | None]:
        try:
            lon, lat = self.dem.local_to_wgs84(float(x), float(y))
            return float(lat), float(lon)
        except Exception:
            return None, None

    def source_bounds_contains(self, x: float, y: float) -> bool:
        left = min(float(self.bounds.left), float(self.bounds.right))
        right = max(float(self.bounds.left), float(self.bounds.right))
        bottom = min(float(self.bounds.bottom), float(self.bounds.top))
        top = max(float(self.bounds.bottom), float(self.bounds.top))
        return left <= float(x) <= right and bottom <= float(y) <= top

    def local_bounds_contains(self, x: float, y: float) -> bool:
        x_min, y_min, x_max, y_max = self.dem.bounds_m
        dx, dy = self.dem.resolution_m
        tol = 0.5 * max(abs(dx), abs(dy), 1.0)
        return (x_min - tol) <= float(x) <= (x_max + tol) and (y_min - tol) <= float(y) <= (y_max + tol)


@dataclass(slots=True)
class Candidate:
    offset_x_m: float
    offset_y_m: float
    heading_deg: float
    score: float
    correlation: float
    rmse_m: float
    mae_m: float
    shape_rmse: float
    height_bias_m: float
    start_x_m: float
    start_y_m: float
    map_heights_m: np.ndarray


def parse_heights_text(text: str, source: str = "<text>", min_points: int = 5) -> np.ndarray:
    """Parse one height value per line, ignoring blank lines."""

    heights: list[float] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            heights.append(float(value))
        except ValueError as exc:
            raise DorabotkaError(
                "invalid_heights_file",
                f"Line {line_no} is not a valid height value",
                line=line_no,
                source=source,
            ) from exc
    if len(heights) < min_points:
        raise DorabotkaError(
            "not_enough_heights",
            f"At least {min_points} height values are required",
            count=len(heights),
            min_points=min_points,
        )
    return np.asarray(heights, dtype=float)


def read_heights_file(path: str | Path, min_points: int = 5) -> np.ndarray:
    heights_path = Path(path)
    if not heights_path.exists():
        raise DorabotkaError("heights_not_found", f"Heights file not found: {heights_path}")
    try:
        return parse_heights_text(heights_path.read_text(encoding="utf-8"), source=str(heights_path), min_points=min_points)
    except UnicodeDecodeError as exc:
        raise DorabotkaError("invalid_heights_file", f"Heights file is not valid UTF-8: {heights_path}") from exc


def resolve_start_point(
    context: GeoTiffContext,
    start_x: float,
    start_y: float,
    start_coord_type: str,
    warnings: list[str],
) -> tuple[float, float, str]:
    """Resolve input start coordinates to DEM local meters."""

    pixel_possible = 0.0 <= float(start_x) < context.width and 0.0 <= float(start_y) < context.height

    map_candidates: list[tuple[float, float, str]] = []
    if context.local_bounds_contains(start_x, start_y):
        map_candidates.append((float(start_x), float(start_y), "map_local"))
    if context.source_bounds_contains(start_x, start_y):
        local = context.source_to_local(start_x, start_y)
        if context.local_bounds_contains(*local):
            map_candidates.append((local[0], local[1], "map_source_crs"))

    if start_coord_type == "pixel":
        if not pixel_possible:
            raise DorabotkaError("start_out_of_bounds", "start_x/start_y are outside GeoTIFF pixel bounds")
        x_m, y_m = context.pixel_to_local(start_x, start_y)
        return x_m, y_m, "pixel"

    if start_coord_type == "map":
        if not map_candidates:
            raise DorabotkaError("start_out_of_bounds", "start_x/start_y are outside GeoTIFF map bounds")
        return map_candidates[0]

    if map_candidates and pixel_possible:
        warnings.append("start_coord_type=auto matched both map and pixel bounds; map coordinates were used")
        return map_candidates[0]
    if map_candidates:
        return map_candidates[0]
    if pixel_possible:
        x_m, y_m = context.pixel_to_local(start_x, start_y)
        return x_m, y_m, "pixel"
    raise DorabotkaError("start_out_of_bounds", "start_x/start_y are outside GeoTIFF bounds")


def build_trajectory_points(start_x_m: float, start_y_m: float, heading_deg: float, distances_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    heading_rad = math.radians(float(heading_deg) % 360.0)
    distances = np.asarray(distances_m, dtype=float)
    x = float(start_x_m) + math.sin(heading_rad) * distances
    y = float(start_y_m) + math.cos(heading_rad) * distances
    return x, y


def _axis_values(radius: float, step: float) -> np.ndarray:
    if radius <= 0:
        return np.asarray([0.0], dtype=float)
    count = int(math.ceil(radius / step))
    values = np.arange(-count, count + 1, dtype=float) * float(step)
    return values[np.abs(values) <= radius + 1e-9]


def _offset_grid(radius_m: float, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    values = _axis_values(radius_m, step_m)
    xx, yy = np.meshgrid(values, values)
    mask = np.hypot(xx, yy) <= radius_m + 1e-9
    return xx[mask].astype(float), yy[mask].astype(float)


def _heading_values(heading_deg: float, search_deg: float, step_deg: float) -> np.ndarray:
    deltas = _axis_values(search_deg, step_deg)
    return (float(heading_deg) + deltas) % 360.0


def _row_metrics(input_heights: np.ndarray, references: np.ndarray, normalize: bool, min_points: int = 5) -> dict[str, np.ndarray]:
    references = np.asarray(references, dtype=float)
    count = references.shape[0]
    scores = np.full(count, -np.inf, dtype=float)
    correlations = np.full(count, np.nan, dtype=float)
    rmses = np.full(count, np.nan, dtype=float)
    maes = np.full(count, np.nan, dtype=float)
    shape_rmses = np.full(count, np.nan, dtype=float)
    biases = np.full(count, np.nan, dtype=float)
    input_arr = np.asarray(input_heights, dtype=float)
    finite_input = np.isfinite(input_arr)
    finite_pairs = np.isfinite(references) & finite_input[None, :]
    valid = finite_pairs.sum(axis=1) >= int(min_points)
    if not np.any(valid):
        return {
            "score": scores,
            "correlation": correlations,
            "rmse": rmses,
            "mae": maes,
            "shape_rmse": shape_rmses,
            "bias": biases,
            "valid": valid,
        }

    if finite_input.all() and np.isfinite(references[valid]).all():
        refs = references[valid]
        diff = input_arr[None, :] - refs
        rmses[valid] = np.sqrt(np.mean(diff**2, axis=1))
        maes[valid] = np.mean(np.abs(diff), axis=1)
        biases[valid] = np.mean(refs - input_arr[None, :], axis=1)

        input_centered = input_arr - float(np.mean(input_arr))
        input_std = float(np.std(input_centered))
        refs_centered = refs - np.mean(refs, axis=1, keepdims=True)
        refs_std = np.std(refs_centered, axis=1)
        nonflat = (refs_std > 1e-12) & (input_std > 1e-12)
        corr_rows = np.zeros(refs.shape[0], dtype=float)
        if np.any(nonflat):
            corr_rows[nonflat] = np.mean(
                (refs_centered[nonflat] / refs_std[nonflat, None]) * (input_centered / input_std),
                axis=1,
            )
        correlations[valid] = corr_rows

        if normalize and input_std > 1e-12:
            input_norm = input_centered / input_std
            shape = np.full(refs.shape[0], np.inf, dtype=float)
            if np.any(refs_std > 1e-12):
                refs_norm = refs_centered[refs_std > 1e-12] / refs_std[refs_std > 1e-12, None]
                shape[refs_std > 1e-12] = np.sqrt(np.mean((input_norm[None, :] - refs_norm) ** 2, axis=1))
            shape_rmses[valid] = shape
            scores[valid] = corr_rows - 0.10 * shape
        else:
            scale = max(float(np.ptp(input_arr)), 1.0)
            shape_rmses[valid] = rmses[valid] / scale
            scores[valid] = corr_rows - 0.05 * (rmses[valid] / scale)
        return {
            "score": scores,
            "correlation": correlations,
            "rmse": rmses,
            "mae": maes,
            "shape_rmse": shape_rmses,
            "bias": biases,
            "valid": valid,
        }

    for idx in np.flatnonzero(valid):
        mask = finite_pairs[idx]
        row_input = input_arr[mask]
        row_ref = references[idx, mask]
        diff = row_input - row_ref
        rmses[idx] = float(np.sqrt(np.mean(diff**2)))
        maes[idx] = float(np.mean(np.abs(diff)))
        biases[idx] = float(np.mean(row_ref - row_input))

        input_centered = row_input - float(np.mean(row_input))
        input_std = float(np.std(input_centered))
        ref_centered = row_ref - float(np.mean(row_ref))
        ref_std = float(np.std(ref_centered))
        corr = 0.0
        if ref_std > 1e-12 and input_std > 1e-12:
            corr = float(np.mean((ref_centered / ref_std) * (input_centered / input_std)))
        correlations[idx] = corr

        if normalize and input_std > 1e-12 and ref_std > 1e-12:
            input_norm = input_centered / input_std
            ref_norm = ref_centered / ref_std
            shape = float(np.sqrt(np.mean((input_norm - ref_norm) ** 2)))
            shape_rmses[idx] = shape
            scores[idx] = corr - 0.10 * shape
        else:
            scale = max(float(np.ptp(row_input)), 1.0)
            shape_rmses[idx] = float(rmses[idx] / scale)
            scores[idx] = corr - 0.05 * float(rmses[idx] / scale)

    return {
        "score": scores,
        "correlation": correlations,
        "rmse": rmses,
        "mae": maes,
        "shape_rmse": shape_rmses,
        "bias": biases,
        "valid": valid,
    }


def _evaluate_candidates(
    dem: DEMGrid,
    input_heights: np.ndarray,
    distances_m: np.ndarray,
    nominal_start_x_m: float,
    nominal_start_y_m: float,
    offsets_x_m: np.ndarray,
    offsets_y_m: np.ndarray,
    headings_deg: np.ndarray,
    normalize_profile: bool,
    keep_top: int,
    min_points: int,
    nominal_heading_deg: float,
    search_radius_m: float,
    heading_search_deg: float,
) -> tuple[list[Candidate], int]:
    top: list[Candidate] = []
    checked = 0
    starts_x = float(nominal_start_x_m) + np.asarray(offsets_x_m, dtype=float)
    starts_y = float(nominal_start_y_m) + np.asarray(offsets_y_m, dtype=float)
    distances = np.asarray(distances_m, dtype=float)

    for heading_deg in headings_deg:
        heading_rad = math.radians(float(heading_deg) % 360.0)
        x = starts_x[:, None] + math.sin(heading_rad) * distances[None, :]
        y = starts_y[:, None] + math.cos(heading_rad) * distances[None, :]
        references = np.asarray(dem.sample(x, y), dtype=float)
        checked += int(references.shape[0])
        metrics = _row_metrics(input_heights, references, normalize_profile, min_points=min_points)
        adjusted_scores = np.array(metrics["score"], copy=True)
        if search_radius_m > 1e-9:
            offset_fraction = np.hypot(offsets_x_m, offsets_y_m) / float(search_radius_m)
            adjusted_scores -= 0.002 * offset_fraction**2
        if heading_search_deg > 1e-9:
            heading_delta = abs(((float(heading_deg) - float(nominal_heading_deg) + 180.0) % 360.0) - 180.0)
            adjusted_scores -= 0.002 * (heading_delta / float(heading_search_deg)) ** 2
        finite = np.flatnonzero(np.isfinite(adjusted_scores))
        if finite.size == 0:
            continue
        local_count = min(max(keep_top, 1), finite.size)
        selected = finite[np.argsort(adjusted_scores[finite])[-local_count:]]
        for idx in selected:
            top.append(
                Candidate(
                    offset_x_m=float(offsets_x_m[idx]),
                    offset_y_m=float(offsets_y_m[idx]),
                    heading_deg=float(heading_deg % 360.0),
                    score=float(adjusted_scores[idx]),
                    correlation=float(metrics["correlation"][idx]),
                    rmse_m=float(metrics["rmse"][idx]),
                    mae_m=float(metrics["mae"][idx]),
                    shape_rmse=float(metrics["shape_rmse"][idx]),
                    height_bias_m=float(metrics["bias"][idx]),
                    start_x_m=float(starts_x[idx]),
                    start_y_m=float(starts_y[idx]),
                    map_heights_m=np.asarray(references[idx], dtype=float),
                )
            )
    top.sort(key=lambda item: (item.score, item.correlation, -item.rmse_m), reverse=True)
    return top[:keep_top], checked


def _unique_pairs(xs: list[float], ys: list[float]) -> tuple[np.ndarray, np.ndarray]:
    pairs = sorted({(round(float(x), 6), round(float(y), 6)) for x, y in zip(xs, ys)})
    if not pairs:
        return np.asarray([0.0]), np.asarray([0.0])
    return np.asarray([p[0] for p in pairs], dtype=float), np.asarray([p[1] for p in pairs], dtype=float)


def _search_best_candidate(
    dem: DEMGrid,
    input_heights: np.ndarray,
    distances_m: np.ndarray,
    nominal_start_x_m: float,
    nominal_start_y_m: float,
    heading_deg: float,
    config: DorabotkaSearchConfig,
    warnings: list[str],
) -> tuple[Candidate, dict[str, Any]]:
    full_offsets_x, full_offsets_y = _offset_grid(config.search_radius_m, config.search_step_m)
    full_headings = _heading_values(heading_deg, config.heading_search_deg, config.heading_step_deg)
    full_hypotheses = int(full_offsets_x.size * full_headings.size)
    total_checked = 0

    if not config.coarse_to_fine or full_hypotheses <= min(config.max_hypotheses, 20_000):
        top, checked = _evaluate_candidates(
            dem,
            input_heights,
            distances_m,
            nominal_start_x_m,
            nominal_start_y_m,
            full_offsets_x,
            full_offsets_y,
            full_headings,
            config.normalize_profile,
            keep_top=1,
            min_points=config.min_points,
            nominal_heading_deg=heading_deg,
            search_radius_m=config.search_radius_m,
            heading_search_deg=config.heading_search_deg,
        )
        total_checked += checked
        if not top:
            raise DorabotkaError("trajectory_out_of_bounds", "No valid trajectory candidate stays inside the GeoTIFF")
        return top[0], {
            "coarse_to_fine": False,
            "full_hypotheses": full_hypotheses,
            "candidates_checked": total_checked,
        }

    coarse_step = max(config.search_step_m * 4.0, config.search_radius_m / 10.0, config.search_step_m)
    coarse_heading_step = max(config.heading_step_deg * 4.0, config.heading_search_deg / 3.0, config.heading_step_deg)
    coarse_offsets_x, coarse_offsets_y = _offset_grid(config.search_radius_m, coarse_step)
    coarse_headings = _heading_values(heading_deg, config.heading_search_deg, coarse_heading_step)
    coarse_top, checked = _evaluate_candidates(
        dem,
        input_heights,
        distances_m,
        nominal_start_x_m,
        nominal_start_y_m,
        coarse_offsets_x,
        coarse_offsets_y,
        coarse_headings,
        config.normalize_profile,
        keep_top=config.max_candidates,
        min_points=config.min_points,
        nominal_heading_deg=heading_deg,
        search_radius_m=config.search_radius_m,
        heading_search_deg=config.heading_search_deg,
    )
    total_checked += checked
    if not coarse_top:
        raise DorabotkaError("trajectory_out_of_bounds", "No valid coarse trajectory candidate stays inside the GeoTIFF")

    fine_xs: list[float] = []
    fine_ys: list[float] = []
    fine_headings: list[float] = []
    local_dx, local_dy = _offset_grid(coarse_step, config.search_step_m)
    for candidate in coarse_top:
        xs = candidate.offset_x_m + local_dx
        ys = candidate.offset_y_m + local_dy
        inside = np.hypot(xs, ys) <= config.search_radius_m + 1e-9
        fine_xs.extend(float(value) for value in xs[inside])
        fine_ys.extend(float(value) for value in ys[inside])

        deltas = _axis_values(coarse_heading_step, config.heading_step_deg)
        nominal_delta = ((candidate.heading_deg - float(heading_deg) + 180.0) % 360.0) - 180.0
        for delta in deltas + nominal_delta:
            if abs(delta) <= config.heading_search_deg + 1e-9:
                fine_headings.append((float(heading_deg) + float(delta)) % 360.0)

    fine_offsets_x, fine_offsets_y = _unique_pairs(fine_xs, fine_ys)
    if not fine_headings:
        fine_headings = [float(heading_deg) % 360.0]
    fine_heading_values = np.asarray(sorted({round(value, 6) for value in fine_headings}), dtype=float)
    fine_hypotheses = int(fine_offsets_x.size * fine_heading_values.size)
    if fine_hypotheses > config.max_hypotheses:
        keep_offsets = max(1, config.max_hypotheses // max(1, fine_heading_values.size))
        order = np.argsort(np.hypot(fine_offsets_x, fine_offsets_y))
        keep = order[:keep_offsets]
        fine_offsets_x = fine_offsets_x[keep]
        fine_offsets_y = fine_offsets_y[keep]
        fine_hypotheses = int(fine_offsets_x.size * fine_heading_values.size)
        warnings.append("fine candidate set was limited by max_hypotheses")

    fine_top, checked = _evaluate_candidates(
        dem,
        input_heights,
        distances_m,
        nominal_start_x_m,
        nominal_start_y_m,
        fine_offsets_x,
        fine_offsets_y,
        fine_heading_values,
        config.normalize_profile,
        keep_top=1,
        min_points=config.min_points,
        nominal_heading_deg=heading_deg,
        search_radius_m=config.search_radius_m,
        heading_search_deg=config.heading_search_deg,
    )
    total_checked += checked
    if not fine_top:
        raise DorabotkaError("trajectory_out_of_bounds", "No valid refined trajectory candidate stays inside the GeoTIFF")
    return fine_top[0], {
        "coarse_to_fine": True,
        "full_hypotheses": full_hypotheses,
        "coarse_hypotheses": int(coarse_offsets_x.size * coarse_headings.size),
        "fine_hypotheses": fine_hypotheses,
        "candidates_checked": total_checked,
    }


def _confidence(candidate: Candidate) -> float:
    if not math.isfinite(candidate.correlation):
        return 0.0
    corr_part = max(0.0, min(1.0, (candidate.correlation + 1.0) * 0.5))
    shape_part = math.exp(-max(0.0, candidate.shape_rmse) * 0.25) if math.isfinite(candidate.shape_rmse) else 0.0
    return float(max(0.0, min(1.0, corr_part * shape_part)))


def _trajectory_records(
    context: GeoTiffContext,
    candidate: Candidate,
    input_heights: np.ndarray,
    distances_m: np.ndarray,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    xs, ys = build_trajectory_points(candidate.start_x_m, candidate.start_y_m, candidate.heading_deg, distances_m)
    local_records: list[dict[str, Any]] = []
    global_records: list[dict[str, Any]] = []
    global_missing = False
    for idx, (x_m, y_m, distance_m, input_h, map_h) in enumerate(zip(xs, ys, distances_m, input_heights, candidate.map_heights_m)):
        error = float(input_h - map_h)
        local_records.append(
            {
                "i": int(idx),
                "x": float(x_m),
                "y": float(y_m),
                "distance_m": float(distance_m),
                "input_height_m": float(input_h),
                "map_height_m": float(map_h),
                "height_error_m": error,
            }
        )
        lat, lon = context.local_to_global(float(x_m), float(y_m))
        if lat is None or lon is None:
            global_missing = True
        global_records.append(
            {
                "i": int(idx),
                "lat": lat,
                "lon": lon,
                "distance_m": float(distance_m),
                "input_height_m": float(input_h),
                "map_height_m": float(map_h),
                "height_error_m": error,
            }
        )
    if global_missing and "global coordinates are unavailable for this GeoTIFF CRS" not in warnings:
        warnings.append("global coordinates are unavailable for this GeoTIFF CRS")
    return local_records, global_records


def _load_reference_local(path: str | Path, context: GeoTiffContext) -> np.ndarray:
    reference_path = Path(path)
    if not reference_path.exists():
        raise DorabotkaError("reference_not_found", f"Reference trajectory not found: {reference_path}")
    if reference_path.suffix.lower() in {".json", ".geojson"}:
        data = json.loads(reference_path.read_text(encoding="utf-8"))
        coords: list[Any] | None = None
        if data.get("type") == "FeatureCollection":
            for feature in data.get("features", []):
                geom = feature.get("geometry") or {}
                if geom.get("type") == "LineString":
                    coords = geom.get("coordinates")
                    break
        elif data.get("type") == "LineString":
            coords = data.get("coordinates")
        if not coords:
            raise DorabotkaError("invalid_reference_trajectory", "GeoJSON reference must contain a LineString")
        points = [context.dem.wgs84_to_local(float(lon), float(lat)) for lon, lat, *_ in coords]
        return np.asarray(points, dtype=float)

    with reference_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        points: list[tuple[float, float]] = []
        for row in reader:
            if "x" in row and "y" in row:
                points.append((float(row["x"]), float(row["y"])))
            elif "x_m" in row and "y_m" in row:
                points.append((float(row["x_m"]), float(row["y_m"])))
            elif "lon" in row and "lat" in row:
                points.append(context.dem.wgs84_to_local(float(row["lon"]), float(row["lat"])))
            else:
                raise DorabotkaError("invalid_reference_trajectory", "Reference CSV must contain x/y, x_m/y_m or lon/lat columns")
        if not points:
            raise DorabotkaError("invalid_reference_trajectory", "Reference trajectory is empty")
        return np.asarray(points, dtype=float)


def _reference_metrics(reference_path: str | Path | None, local_records: list[dict[str, Any]], context: GeoTiffContext) -> dict[str, float] | None:
    if reference_path is None:
        return None
    reference = _load_reference_local(reference_path, context)
    estimated = np.asarray([[row["x"], row["y"]] for row in local_records], dtype=float)
    count = min(reference.shape[0], estimated.shape[0])
    if count == 0:
        raise DorabotkaError("invalid_reference_trajectory", "Reference trajectory is empty")
    diff = estimated[:count] - reference[:count]
    errors = np.hypot(diff[:, 0], diff[:, 1])
    return {
        "mean_horizontal_error_m": float(np.mean(errors)),
        "max_horizontal_error_m": float(np.max(errors)),
        "rmse_horizontal_error_m": float(np.sqrt(np.mean(errors**2))),
        "start_offset_m": float(errors[0]),
        "end_offset_m": float(errors[-1]),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_geojson(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _geojson_payload(result: dict[str, Any]) -> dict[str, Any]:
    global_rows = result["trajectory"]["global"]
    local_rows = result["trajectory"]["local"]
    has_global = all(row["lat"] is not None and row["lon"] is not None for row in global_rows)
    if has_global:
        line_coordinates = [[row["lon"], row["lat"]] for row in global_rows]
        start_coordinates = line_coordinates[0]
        end_coordinates = line_coordinates[-1]
        coordinate_type = "wgs84"
    else:
        line_coordinates = [[row["x"], row["y"]] for row in local_rows]
        start_coordinates = line_coordinates[0]
        end_coordinates = line_coordinates[-1]
        coordinate_type = "local"
    properties = {
        "mode": "dorabotka",
        "coordinate_type": coordinate_type,
        "confidence": result["result"]["confidence"],
        "rmse_m": result["result"]["rmse_m"],
        "correlation": result["result"]["correlation"],
        "heading_deg": result["result"]["best_heading_deg"],
        "sample_step_m": result["input"]["sample_step_m"],
    }
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {**properties, "feature": "trajectory"}, "geometry": {"type": "LineString", "coordinates": line_coordinates}},
            {"type": "Feature", "properties": {**properties, "feature": "start"}, "geometry": {"type": "Point", "coordinates": start_coordinates}},
            {"type": "Feature", "properties": {**properties, "feature": "end"}, "geometry": {"type": "Point", "coordinates": end_coordinates}},
        ],
    }


def _plot_trajectory(path: Path, context: GeoTiffContext, result: dict[str, Any]) -> None:
    dem = context.dem
    x_min, y_min, x_max, y_max = dem.bounds_m
    max_dim = max(dem.elevation_m.shape)
    stride = max(1, int(math.ceil(max_dim / 1200)))
    elevation = dem.elevation_m[::stride, ::stride]
    rows = result["trajectory"]["local"]
    xs = np.asarray([row["x"] for row in rows], dtype=float)
    ys = np.asarray([row["y"] for row in rows], dtype=float)

    fig, ax = plt.subplots(figsize=(9, 8))
    image = ax.imshow(
        elevation,
        origin="lower",
        extent=[x_min, x_max, y_min, y_max],
        cmap="terrain",
        aspect="equal",
    )
    ax.plot(xs, ys, color="black", linewidth=3.0, alpha=0.6)
    ax.plot(xs, ys, color="#f43f5e", linewidth=1.8, label="dorabotka trajectory")
    ax.scatter([xs[0]], [ys[0]], c="#34d399", s=50, label="start", zorder=5)
    ax.scatter([xs[-1]], [ys[-1]], c="#2a93ff", s=50, label="end", zorder=5)
    ax.set_xlabel("x, local map m")
    ax.set_ylabel("y, local map m")
    ax.set_title(
        "Dorabotka trajectory | "
        f"conf={result['result']['confidence']:.2f}, "
        f"corr={result['result']['correlation']:.2f}, "
        f"rmse={result['result']['rmse_m']:.2f} m"
    )
    ax.legend(loc="best")
    fig.colorbar(image, ax=ax, label="Elevation, m")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def save_dorabotka_artifacts(result: dict[str, Any], context: GeoTiffContext, output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    local_csv = out / "trajectory_local.csv"
    global_csv = out / "trajectory_global.csv"
    geojson_path = out / "trajectory.geojson"
    plot_path = out / "trajectory_plot.png"
    result_path = out / "result.json"

    _write_csv(
        local_csv,
        result["trajectory"]["local"],
        ["i", "x", "y", "distance_m", "input_height_m", "map_height_m", "height_error_m"],
    )
    _write_csv(
        global_csv,
        result["trajectory"]["global"],
        ["i", "lat", "lon", "distance_m", "input_height_m", "map_height_m", "height_error_m"],
    )
    _write_geojson(geojson_path, _geojson_payload(result))
    _plot_trajectory(plot_path, context, result)
    artifacts = {
        "trajectory_local_csv": str(local_csv),
        "trajectory_global_csv": str(global_csv),
        "trajectory_geojson": str(geojson_path),
        "trajectory_plot_png": str(plot_path),
        "result_json": str(result_path),
    }
    result["artifacts"] = artifacts
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return artifacts


def run_dorabotka(
    heights_path: str | Path,
    geotiff_path: str | Path,
    start_x: float,
    start_y: float,
    heading_deg: float,
    output_dir: str | Path | None = None,
    config: DorabotkaSearchConfig | None = None,
    reference_trajectory: str | Path | None = None,
) -> dict[str, Any]:
    """Run the full Dorabotka pipeline and optionally save artifacts."""

    cfg = config or DorabotkaSearchConfig()
    cfg.validate()
    warnings: list[str] = []
    started = time.perf_counter()

    context = GeoTiffContext.from_path(geotiff_path)
    heights = read_heights_file(heights_path, min_points=cfg.min_points)
    finite_heights = int(np.isfinite(heights).sum())
    if finite_heights < cfg.min_points:
        raise DorabotkaError(
            "not_enough_valid_heights",
            f"At least {cfg.min_points} finite height values are required",
            count=finite_heights,
            min_points=cfg.min_points,
        )
    if finite_heights < int(heights.size):
        warnings.append(f"input heights contain {int(heights.size) - finite_heights} non-finite values; valid samples are used for matching")
    heading = float(heading_deg) % 360.0
    start_x_m, start_y_m, resolved_start_type = resolve_start_point(context, float(start_x), float(start_y), cfg.start_coord_type, warnings)
    distances = np.arange(heights.size, dtype=float) * float(cfg.sample_step_m)

    best, search_diag = _search_best_candidate(
        context.dem,
        heights,
        distances,
        start_x_m,
        start_y_m,
        heading,
        cfg,
        warnings,
    )
    local_records, global_records = _trajectory_records(context, best, heights, distances, warnings)
    reference = _reference_metrics(reference_trajectory, local_records, context)
    processing_time_ms = (time.perf_counter() - started) * 1000.0

    result: dict[str, Any] = {
        "mode": "dorabotka",
        "input": {
            "heights_count": int(heights.size),
            "start_x": float(start_x),
            "start_y": float(start_y),
            "start_coord_type": cfg.start_coord_type,
            "resolved_start_coord_type": resolved_start_type,
            "resolved_start_x_m": float(start_x_m),
            "resolved_start_y_m": float(start_y_m),
            "heading_deg": heading,
            "sample_step_m": float(cfg.sample_step_m),
            "geotiff": str(geotiff_path),
            "reference_trajectory": str(reference_trajectory) if reference_trajectory else None,
        },
        "result": {
            "confidence": _confidence(best),
            "score": float(best.score),
            "correlation": float(best.correlation),
            "rmse_m": float(best.rmse_m),
            "mae_m": float(best.mae_m),
            "shape_rmse": float(best.shape_rmse),
            "height_bias_m": float(best.height_bias_m),
            "best_offset_x_m": float(best.offset_x_m),
            "best_offset_y_m": float(best.offset_y_m),
            "best_heading_deg": float(best.heading_deg),
            "best_start_x_m": float(best.start_x_m),
            "best_start_y_m": float(best.start_y_m),
        },
        "trajectory": {
            "local": local_records,
            "global": global_records,
        },
        "reference_metrics": reference,
        "diagnostics": {
            "processing_time_ms": float(processing_time_ms),
            "candidates_checked": int(search_diag["candidates_checked"]),
            "best_score": float(best.score),
            "best_rmse": float(best.rmse_m),
            "best_correlation": float(best.correlation),
            **search_diag,
        },
        "warnings": warnings,
    }
    if output_dir is not None:
        save_dorabotka_artifacts(result, context, output_dir)
    return result


__all__ = [
    "DorabotkaError",
    "DorabotkaSearchConfig",
    "GeoTiffContext",
    "build_trajectory_points",
    "parse_heights_text",
    "read_heights_file",
    "resolve_start_point",
    "run_dorabotka",
    "save_dorabotka_artifacts",
]
