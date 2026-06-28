"""
МОСТ «Теарком (алгоритм) -> ГеоШтурман (визуализация)».

Папки проектов РАЗДЕЛЬНЫЕ: ядро алгоритма живёт в соседнем каталоге
``<repo>/tercom_uav``. Этот модуль подключает его во время выполнения (без
слияния кодовых баз) и считает навигационное решение Теаркомом, возвращая
объект с ТЕМИ ЖЕ полями, что и родной ``NavigationSolution``. Поэтому
визуализация (pipeline.py / stream.py / app.js) и её фронтенд НЕ меняются —
дашборд показывает ровно то, что посчитал алгоритм.

Системы координат и азимут совпадают: x=восток=sin(az), y=север=cos(az),
азимут по часовой от севера. DEMData оборачивается в tercom DEMGrid в ТОЙ ЖЕ
локальной метрической рамке, поэтому геопривязка остаётся согласованной.

Достоверность считается формулой РОДНОГО движка (app.core.metrics), а не
встроенной в tercom — так самооценка остаётся честной и совместимой с
дашбордом.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .dem import DEMData, dem_xy_to_geodetic, geodetic_to_dem_xy
from .metrics import confidence_score as native_confidence_score
from .metrics import terrain_informativeness as native_terrain_info

logger = logging.getLogger(__name__)

# --- подключаем соседний пакет tercom_uav, не сливая папки ---
# tercom_bridge.py: <repo>/GeoShturman/backend/app/core/tercom_bridge.py
#   parents[4] == <repo>
_REPO = Path(__file__).resolve().parents[4]
_TERCOM_SRC = _REPO / "tercom_uav" / "src"
if str(_TERCOM_SRC) not in sys.path:
    sys.path.insert(0, str(_TERCOM_SRC))

from tercom_uav.config import CorrelationConfig, GPSFusionConfig, correlation_config_for_quality  # noqa: E402
from tercom_uav.dem import DEMGrid                              # noqa: E402
from tercom_uav.estimator import estimate_single_window        # noqa: E402
from tercom_uav.gps import (                                    # noqa: E402
    GPSFusionState,
    first_usable_gps_anchor,
    fuse_navigation_estimate,
    gga_records_to_gps_fixes,
    tercom_only_diagnostics,
)
from tercom_uav.nmea import NMEAError, parse_gpgga             # noqa: E402
from tercom_uav.profiles import build_terrain_profile          # noqa: E402
from tercom_uav.types import GGARecord                         # noqa: E402


@dataclass
class BridgeSolution:
    """Повторяет поля app.core.navigation.NavigationSolution, которые читают
    pipeline.py / stream.py / визуализация."""
    estimated: dict
    quality: dict
    measured_profile: np.ndarray
    reference_profile: np.ndarray
    trajectory: dict
    candidates: list
    heatmap: np.ndarray
    metadata: dict


def dem_data_to_grid(dem: DEMData) -> DEMGrid:
    """Обернуть DEMData в tercom DEMGrid в ТОЙ ЖЕ рамке (origin слева-снизу)."""
    rows, cols = dem.elevation.shape
    xs = dem.origin_x_m + np.arange(cols, dtype=float) * float(dem.resolution_x_m)
    ys = dem.origin_y_m + np.arange(rows, dtype=float) * float(dem.resolution_y_m)
    return DEMGrid(
        elevation_m=np.asarray(dem.elevation, dtype=float),
        x_coords_m=xs, y_coords_m=ys,
        crs=dem.crs, source_path=None, metadata={"bridged_from": "DEMData"},
    )


def _records_from_nmea(nmea_text: str) -> list[GGARecord]:
    records: list[GGARecord] = []
    for line in nmea_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            records.append(parse_gpgga(s, require_checksum=False))
        except NMEAError:
            continue
    return records


def _gps_fixes_for_dem(records: list[GGARecord], dem: DEMData):
    converter = None
    if dem.georef is not None:
        converter = lambda lat, lon: geodetic_to_dem_xy(dem, lat, lon)
    return gga_records_to_gps_fixes(records, local_converter=converter)


def _gps_assisted_config(config: CorrelationConfig, gps_config: GPSFusionConfig) -> CorrelationConfig:
    radius = float(gps_config.reacquire_window_radius_m)
    return CorrelationConfig(
        azimuth_step_deg=config.azimuth_step_deg,
        shift_min_m=max(config.shift_min_m, -radius),
        shift_max_m=min(config.shift_max_m, radius),
        shift_step_m=config.shift_step_m,
        sample_spacing_m=config.sample_spacing_m,
        coarse_to_fine=config.coarse_to_fine,
        coarse_azimuth_step_deg=config.coarse_azimuth_step_deg,
        coarse_shift_step_m=config.coarse_shift_step_m,
        fine_azimuth_radius_deg=config.fine_azimuth_radius_deg,
        fine_shift_radius_m=min(config.fine_shift_radius_m, radius),
        min_correlation=config.min_correlation,
        min_score_gap=config.min_score_gap,
        min_relative_gap=config.min_relative_gap,
        min_observability=config.min_observability,
        speed_search_enabled=config.speed_search_enabled,
        speed_scale_min=config.speed_scale_min,
        speed_scale_max=config.speed_scale_max,
        speed_scale_step=config.speed_scale_step,
        speed_search_azimuth_step_deg=config.speed_search_azimuth_step_deg,
        speed_search_shift_step_m=config.speed_search_shift_step_m,
        speed_search_use_coarse_azimuth=config.speed_search_use_coarse_azimuth,
        tercom_quality_mode=config.tercom_quality_mode,
        max_search_radius_m=min(
            radius,
            config.max_search_radius_m if config.max_search_radius_m is not None else radius,
        ),
        max_candidates=config.max_candidates,
    )


def solve_navigation_via_tercom(
    dem: DEMData,
    nmea_text: str,
    barometric_altitude_msl: float = 1500.0,
    sample_rate_hz: float = 5.0,
    speed_min_mps: float = 20.0,
    speed_max_mps: float = 80.0,
    shift_step_m: float = 30.0,
    tercom_quality_mode: str = "balanced",
    **_ignored: Any,
) -> BridgeSolution:
    """Drop-in замена app.core.navigation.solve_navigation на ядре Теаркома."""
    grid = dem_data_to_grid(dem)
    records = _records_from_nmea(nmea_text)
    profile = build_terrain_profile(records, baro_alt_msl_m=barometric_altitude_msl)
    gps_config = GPSFusionConfig(max_uav_speed_mps=max(120.0, float(speed_max_mps) * 1.5))
    gps_fixes = _gps_fixes_for_dem(records, dem)
    gps_anchor = first_usable_gps_anchor(gps_fixes, gps_config)

    hint = max(1.0, 0.5 * (float(speed_min_mps) + float(speed_max_mps)))
    cfg = correlation_config_for_quality(
        CorrelationConfig(
            shift_step_m=float(shift_step_m),
            sample_spacing_m=max(10.0, float(np.median(grid.resolution_m))),
            coarse_to_fine=False,
            speed_search_enabled=True,
            speed_scale_min=max(0.05, float(speed_min_mps) / hint),
            speed_scale_max=max(float(speed_min_mps) / hint + 0.1, float(speed_max_mps) / hint),
            speed_scale_step=0.1,
            tercom_quality_mode=tercom_quality_mode,
            max_search_radius_m=float(_ignored.get("search_radius_m", 900.0)) if _ignored.get("search_radius_m") else None,
        ),
        mode=tercom_quality_mode,
    )
    search_cfg = _gps_assisted_config(cfg, gps_config) if gps_anchor is not None else cfg
    search_center_x = gps_anchor.x_m if gps_anchor is not None else None
    search_center_y = gps_anchor.y_m if gps_anchor is not None else None

    correlation, estimate = estimate_single_window(
        grid,
        profile,
        hint,
        search_cfg,
        search_center_x_m=search_center_x,
        search_center_y_m=search_center_y,
    )

    # --- геометрия найденного трека (полный профиль = постфактум-фикс) ---
    az = float(correlation.best_azimuth_deg)
    az_rad = math.radians(az)
    dx, dy = math.sin(az_rad), math.cos(az_rad)
    end_dist = float(correlation.distances_m[-1])
    speed = float(estimate.speed_mps)

    rmse_m = math.sqrt(correlation.mse_m2) if math.isfinite(correlation.mse_m2) else float("inf")
    mae_m = float(correlation.mad_m)
    best_score = float(correlation.best_score)

    # --- ДОСТОВЕРНОСТЬ: формула РОДНОГО движка (честная, дискриминирующая) ---
    observed = np.asarray(correlation.observed_profile_m, dtype=float)
    terrain_info = float(native_terrain_info(observed))
    peak_gap = float(best_score - correlation.second_best_score) if math.isfinite(correlation.second_best_score) else float("inf")
    confidence = float(native_confidence_score(best_score, rmse_m, terrain_info, peak_gap))
    estimate.confidence_score = confidence

    position_fixes = [fix for fix in gps_fixes if fix.has_position]
    navigation_diagnostics = tercom_only_diagnostics(
        estimate,
        search_window_m=gps_config.reacquire_window_radius_m if gps_anchor is not None else None,
    )
    if position_fixes:
        gps_state = GPSFusionState()
        for fix in position_fixes[:-1]:
            gps_state.evaluate(fix, gps_config)
        fusion = fuse_navigation_estimate(
            estimate,
            position_fixes[-1],
            config=gps_config,
            state=gps_state,
            search_window_m=gps_config.reacquire_window_radius_m if gps_anchor is not None else None,
        )
        estimate = fusion.estimate
        confidence = float(estimate.confidence_score)
        navigation_diagnostics = fusion.diagnostics

    az = float(estimate.azimuth_deg)
    az_rad = math.radians(az)
    dx, dy = math.sin(az_rad), math.cos(az_rad)
    speed = float(estimate.speed_mps)
    end_x = float(estimate.x_m)
    end_y = float(estimate.y_m)
    start_x = end_x - dx * end_dist
    start_y = end_y - dy * end_dist

    estimated: dict[str, Any] = {
        "start_x_m": start_x, "start_y_m": start_y,
        "end_x_m": end_x, "end_y_m": end_y,
        "azimuth_deg": az, "speed_mps": speed,
        "correlation": best_score, "rmse_m": rmse_m, "mae_m": mae_m,
        "combined_score": best_score, "confidence": confidence,
        "baro_drift_offset_m": 0.0, "baro_drift_slope_m_per_sample": 0.0,
        "navigation_mode": navigation_diagnostics["mode"],
        "navigation_diagnostics": navigation_diagnostics,
        "tercom_profile": correlation.profile,
    }
    start_geo = dem_xy_to_geodetic(dem, start_x, start_y)
    end_geo = dem_xy_to_geodetic(dem, end_x, end_y)
    if start_geo is not None and end_geo is not None:
        estimated.update({
            "start_lat_deg": start_geo.lat_deg, "start_lon_deg": start_geo.lon_deg,
            "end_lat_deg": end_geo.lat_deg, "end_lon_deg": end_geo.lon_deg,
        })

    warning = None
    if not math.isfinite(best_score):
        warning = "no fix (degenerate/flat terrain)"
    elif terrain_info < 0.08:
        warning = "terrain_flat"
    elif best_score < 0.65:
        warning = "low_correlation"

    quality = {
        "confidence": confidence,
        "terrain_informativeness": terrain_info,
        "peak_sharpness": best_score,
        "peak_gap": peak_gap if math.isfinite(peak_gap) else 1.0,
        "correlation": best_score,
        "rmse_m": rmse_m,
        "warning": warning,
        "navigation_mode": navigation_diagnostics["mode"],
        "navigation_diagnostics": navigation_diagnostics,
        "tercom_profile": correlation.profile,
    }
    trajectory = {
        "start": {"x_m": start_x, "y_m": start_y},
        "end": {"x_m": end_x, "y_m": end_y},
        "duration_s": max(0.0, (profile.times_s.size - 1) / float(sample_rate_hz)),
        "sample_count": int(profile.times_s.size),
    }
    metadata = {
        # tercom heatmap = [азимут × сдвиг] — «карта корреляции по всем
        # направлениям» из ТЗ. Ось азимутов -> подпись пика без правок фронта.
        "refined_azimuth_values": np.asarray(correlation.azimuths_deg, dtype=float),
        "refined_shift_values": np.asarray(correlation.shifts_m, dtype=float),
        "corrected_measured_profile": observed,
        "sample_rate_hz": float(sample_rate_hz),
        "engine": "tercom_uav",
        "navigation_diagnostics": navigation_diagnostics,
        "tercom_profile": correlation.profile,
    }
    return BridgeSolution(
        estimated=estimated,
        quality=quality,
        measured_profile=observed,
        reference_profile=np.asarray(correlation.best_reference_profile_m, dtype=float),
        trajectory=trajectory,
        candidates=[estimated],
        heatmap=np.asarray(correlation.heatmap, dtype=float),
        metadata=metadata,
    )
