"""
Загрузка реальной цифровой модели рельефа Copernicus GLO-30.

Окно DEM вокруг заданной точки читается напрямую из облачного COG-тайла
(AWS Open Data) через GDAL /vsicurl/ — без скачивания всего тайла.
Результат кэшируется в памяти и совместим с ядром (DEMData).

Управление через переменные окружения:
  DEM_SOURCE=real        — включить реальный DEM (по умолчанию synthetic)
  DEM_LOCAL_PATH=/path    — взять локальный GeoTIFF вместо облака
  COPERNICUS_DEM_URL=...  — базовый URL (по умолчанию AWS Open Data)
"""
from __future__ import annotations

import logging
import math
import os
from typing import Any

import numpy as np

from app.core.dem import DEMData

logger = logging.getLogger(__name__)

_CACHE: dict[tuple, DEMData] = {}


def _tile_name(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{int(abs(lat)):02d}_00_{ew}{int(abs(lon)):03d}_00_DEM"


def load_real_dem(lat: float, lon: float, width_m: float, height_m: float,
                  resolution_m: float) -> DEMData:
    """Прочитать окно реального DEM Copernicus GLO-30 вокруг (lat, lon)."""
    key = (round(lat, 3), round(lon, 3), int(width_m), int(height_m), int(resolution_m))
    if key in _CACHE:
        return _CACHE[key]

    # обход проблем SSL/чтения каталога в облаке
    os.environ.setdefault("GDAL_HTTP_UNSAFESSL", os.environ.get("GDAL_HTTP_UNSAFESSL", "YES"))
    os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    base = os.environ.get("COPERNICUS_DEM_URL", "https://copernicus-dem-30m.s3.amazonaws.com")
    local = os.environ.get("DEM_LOCAL_PATH")
    tile = _tile_name(lat, lon)
    src = local if local else f"/vsicurl/{base}/{tile}/{tile}.tif"

    n_cols = max(2, int(round(width_m / resolution_m)) + 1)
    n_rows = max(2, int(round(height_m / resolution_m)) + 1)
    dlat = (height_m / 2.0) / 111_320.0
    dlon = (width_m / 2.0) / (111_320.0 * math.cos(math.radians(lat)))

    with rasterio.open(src) as ds:
        win = from_bounds(lon - dlon, lat - dlat, lon + dlon, lat + dlat, ds.transform)
        data = ds.read(1, window=win, out_shape=(n_rows, n_cols),
                       resampling=Resampling.bilinear, boundless=True, fill_value=float("nan"))
    elev = np.flipud(np.asarray(data, dtype=float))  # строки растут на север (+Y)
    if np.isnan(elev).any():
        elev = np.nan_to_num(elev, nan=float(np.nanmean(elev)) if np.isfinite(elev).any() else 0.0)

    dem = DEMData(elevation=elev, width_m=float(width_m), height_m=float(height_m),
                  resolution_m=float(resolution_m), crs="Copernicus GLO-30 (local meters)")
    _CACHE[key] = dem
    logger.info("Real DEM %s loaded: %dx%d, span=%.1f m", tile, n_rows, n_cols,
                float(elev.max() - elev.min()))
    return dem


def provide_dem(width_m: float, height_m: float, resolution_m: float,
                terrain_type: str = "mixed", lat: float = 67.75, lon: float = 33.70) -> DEMData:
    """Единая точка получения DEM: реальный (если DEM_SOURCE=real) или синтетический."""
    if os.environ.get("DEM_SOURCE", "synthetic").lower() == "real":
        try:
            return load_real_dem(lat, lon, width_m, height_m, resolution_m)
        except Exception:
            logger.exception("Real DEM load failed — fallback to synthetic")
    from app.core.dem import create_synthetic_dem
    return create_synthetic_dem(width_m=width_m, height_m=height_m, resolution_m=resolution_m,
                                seed=42, terrain_type=terrain_type,
                                origin_lat_deg=lat, origin_lon_deg=lon)


def dem_source_label() -> str:
    return "Copernicus GLO-30" if os.environ.get("DEM_SOURCE", "synthetic").lower() == "real" else "synthetic"
