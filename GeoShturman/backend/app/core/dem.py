"""Digital elevation model helpers for terrain referenced navigation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from .geodesy import GeoPoint, GeoReference, geodetic_to_local_m, local_m_to_geodetic


@dataclass
class DEMData:
    """In-memory projected DEM grid.

    The navigation core samples this grid in local meters. Rows of
    ``elevation`` grow in the +Y direction; columns grow in the +X direction.
    If ``georef`` is present, helpers can convert between local meters and
    WGS84 latitude/longitude for API and visualization layers.
    """

    elevation: np.ndarray
    width_m: float
    height_m: float
    resolution_m: float
    origin_x_m: float = 0.0
    origin_y_m: float = 0.0
    crs: Optional[str] = None
    transform: Optional[Any] = None
    resolution_x_m: float | None = None
    resolution_y_m: float | None = None
    georef: GeoReference | None = None

    def __post_init__(self) -> None:
        self.elevation = np.asarray(self.elevation, dtype=float)
        if self.elevation.ndim != 2:
            raise ValueError("DEM elevation must be a 2D array")
        if self.elevation.shape[0] < 2 or self.elevation.shape[1] < 2:
            raise ValueError("DEM elevation must contain at least 2x2 samples")
        if self.resolution_m <= 0:
            raise ValueError("DEM resolution must be positive")
        if self.width_m <= 0 or self.height_m <= 0:
            raise ValueError("DEM dimensions must be positive")
        if self.resolution_x_m is None:
            self.resolution_x_m = float(self.resolution_m)
        if self.resolution_y_m is None:
            self.resolution_y_m = float(self.resolution_m)
        if self.resolution_x_m <= 0 or self.resolution_y_m <= 0:
            raise ValueError("DEM x/y resolutions must be positive")


def create_synthetic_dem(
    width_m: float,
    height_m: float,
    resolution_m: float,
    seed: int | None = None,
    terrain_type: str = "mixed",
    origin_lat_deg: float | None = None,
    origin_lon_deg: float | None = None,
) -> DEMData:
    """Create a deterministic synthetic terrain map for demos and tests."""

    if width_m <= 0 or height_m <= 0 or resolution_m <= 0:
        raise ValueError("width, height, and resolution must be positive")

    terrain_key = terrain_type.lower().strip()
    rng = np.random.default_rng(seed)
    n_cols = max(2, int(round(width_m / resolution_m)) + 1)
    n_rows = max(2, int(round(height_m / resolution_m)) + 1)
    x = np.linspace(0.0, width_m, n_cols)
    y = np.linspace(0.0, height_m, n_rows)
    xx, yy = np.meshgrid(x, y)

    wx = max(width_m, resolution_m)
    hy = max(height_m, resolution_m)
    xn = xx / wx
    yn = yy / hy

    if terrain_key == "flat":
        terrain = 330.0 + 0.0015 * xx + 0.001 * yy + rng.normal(0.0, 0.18, size=xx.shape)
    else:
        amp = {
            "rolling": 0.75,
            "mixed": 1.0,
            "plateau": 1.05,
            "valley": 1.25,
            "mountain": 1.8,
        }.get(terrain_key, 1.0)
        terrain = (
            320.0
            + 0.025 * xx
            + 0.018 * yy
            + amp * 45.0 * np.sin(2.4 * np.pi * xn + 0.5 * np.sin(2.0 * np.pi * yn))
            + amp * 38.0 * np.cos(2.0 * np.pi * yn - 0.8 * np.sin(1.6 * np.pi * xn))
            + amp * 22.0 * np.sin(5.0 * np.pi * (xn + 0.35 * yn))
        )

        hill_count = 7 if terrain_key == "rolling" else 9
        if terrain_key == "mountain":
            hill_count = 14
        for _ in range(hill_count):
            cx = rng.uniform(0.08 * width_m, 0.92 * width_m)
            cy = rng.uniform(0.08 * height_m, 0.92 * height_m)
            sx = rng.uniform(0.06 * width_m, 0.20 * width_m)
            sy = rng.uniform(0.06 * height_m, 0.20 * height_m)
            feature_amp = rng.uniform(-95.0, 135.0) * amp
            terrain += feature_amp * np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))

        if terrain_key in {"mixed", "plateau"}:
            edge_x = max(width_m * 0.025, resolution_m)
            edge_y = max(height_m * 0.025, resolution_m)
            left = _sigmoid((xx - 0.52 * width_m) / edge_x)
            right = _sigmoid((0.86 * width_m - xx) / edge_x)
            bottom = _sigmoid((yy - 0.16 * height_m) / edge_y)
            top = _sigmoid((0.42 * height_m - yy) / edge_y)
            terrain += 70.0 * left * right * bottom * top

        if terrain_key == "plateau":
            shelf = _sigmoid((xx - 0.18 * width_m) / (0.018 * width_m))
            shelf *= _sigmoid((0.78 * width_m - xx) / (0.018 * width_m))
            shelf *= _sigmoid((yy - 0.46 * height_m) / (0.018 * height_m))
            shelf *= _sigmoid((0.86 * height_m - yy) / (0.018 * height_m))
            terrain += 120.0 * shelf

        if terrain_key == "valley":
            valley_center = 0.55 * width_m + 0.16 * width_m * np.sin(2.0 * np.pi * yn)
            valley_width = max(0.055 * width_m, 2.0 * resolution_m)
            terrain -= 155.0 * np.exp(-((xx - valley_center) / valley_width) ** 2)
            terrain += 42.0 * np.exp(-((xx - 0.20 * width_m) / (0.09 * width_m)) ** 2)
            terrain += 48.0 * np.exp(-((xx - 0.86 * width_m) / (0.08 * width_m)) ** 2)

        if terrain_key == "mountain":
            ridge_axis = 0.25 * width_m + 0.50 * yy
            terrain += 185.0 * np.exp(-((xx - ridge_axis) / max(0.10 * width_m, resolution_m)) ** 2)
            terrain += 95.0 * np.sin(9.0 * np.pi * xn) * np.sin(3.0 * np.pi * yn)

        local_count = 10 if terrain_key == "rolling" else 18
        if terrain_key == "mountain":
            local_count = 26
        for _ in range(local_count):
            cx = rng.uniform(0.0, width_m)
            cy = rng.uniform(0.0, height_m)
            sigma = rng.uniform(1.5 * resolution_m, 4.5 * resolution_m)
            feature_amp = rng.uniform(-24.0, 28.0) * max(0.75, amp)
            terrain += feature_amp * np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma**2)))

        terrain += rng.normal(0.0, 1.2 if terrain_key != "mountain" else 2.0, size=terrain.shape)

    georef = None
    crs = "LOCAL"
    if origin_lat_deg is not None and origin_lon_deg is not None:
        georef = GeoReference(origin_lat_deg=float(origin_lat_deg), origin_lon_deg=float(origin_lon_deg))
        crs = georef.crs

    return DEMData(
        elevation=terrain.astype(float),
        width_m=float(width_m),
        height_m=float(height_m),
        resolution_m=float(resolution_m),
        resolution_x_m=float(resolution_m),
        resolution_y_m=float(resolution_m),
        crs=crs,
        georef=georef,
    )


def load_dem(path: str) -> DEMData:
    """Load a GeoTIFF DEM through rasterio when it is available.

    Projected rasters keep their projected x/y coordinates. Geographic rasters
    are converted to a local meter frame anchored at their lower-left corner.
    """

    try:
        import rasterio
    except ImportError as exc:
        raise ImportError(
            "rasterio is required to load real DEM files; use create_synthetic_dem "
            "for demos without external dependencies"
        ) from exc

    with rasterio.open(path) as src:
        band = src.read(1, masked=True)
        elevation = np.asarray(band.filled(np.nan), dtype=float)
        transform = src.transform
        crs = str(src.crs) if src.crs else None

        if transform is not None and transform.e < 0:
            elevation = np.flipud(elevation)

        rows, cols = elevation.shape
        is_geographic = bool(src.crs and getattr(src.crs, "is_geographic", False))
        if is_geographic:
            georef = GeoReference(origin_lat_deg=float(src.bounds.bottom), origin_lon_deg=float(src.bounds.left))
            max_x, max_y = geodetic_to_local_m(float(src.bounds.top), float(src.bounds.right), georef)
            width_m = abs(float(max_x))
            height_m = abs(float(max_y))
            resolution_x_m = width_m / max(cols - 1, 1)
            resolution_y_m = height_m / max(rows - 1, 1)
            origin_x = 0.0
            origin_y = 0.0
        else:
            georef = None
            x_res = abs(float(transform.a)) if transform is not None else 1.0
            y_res = abs(float(transform.e)) if transform is not None else x_res
            resolution_x_m = x_res
            resolution_y_m = y_res
            width_m = float((cols - 1) * resolution_x_m)
            height_m = float((rows - 1) * resolution_y_m)
            origin_x = float(src.bounds.left if transform is not None else 0.0)
            origin_y = float(src.bounds.bottom if transform is not None else 0.0)

    return DEMData(
        elevation=elevation,
        width_m=float(width_m),
        height_m=float(height_m),
        resolution_m=float((resolution_x_m + resolution_y_m) / 2.0),
        origin_x_m=origin_x,
        origin_y_m=origin_y,
        crs=crs,
        transform=transform,
        resolution_x_m=float(resolution_x_m),
        resolution_y_m=float(resolution_y_m),
        georef=georef,
    )


def sample_dem(dem: DEMData, x_m: float, y_m: float) -> float:
    """Sample DEM elevation using bilinear interpolation.

    Points outside the DEM return ``np.nan``. This convention makes profile
    sampling easy to filter in search code without raising in hot loops.
    """

    values = _sample_dem_array(dem, np.asarray([x_m], dtype=float), np.asarray([y_m], dtype=float))
    return float(values[0])


def sample_dem_geodetic(dem: DEMData, lat_deg: float, lon_deg: float) -> float:
    """Sample DEM elevation by latitude/longitude when georeferencing exists."""

    x_m, y_m = geodetic_to_dem_xy(dem, lat_deg, lon_deg)
    return sample_dem(dem, x_m, y_m)


def is_inside_dem(dem: DEMData, x_m: float, y_m: float) -> bool:
    """Return True when a point is inside the DEM footprint."""

    if not (np.isfinite(x_m) and np.isfinite(y_m)):
        return False
    return (
        dem.origin_x_m <= x_m <= dem.origin_x_m + dem.width_m
        and dem.origin_y_m <= y_m <= dem.origin_y_m + dem.height_m
    )


def is_inside_dem_geodetic(dem: DEMData, lat_deg: float, lon_deg: float) -> bool:
    """Return True when a geodetic point falls inside a georeferenced DEM."""

    x_m, y_m = geodetic_to_dem_xy(dem, lat_deg, lon_deg)
    return is_inside_dem(dem, x_m, y_m)


def dem_xy_to_geodetic(dem: DEMData, x_m: float, y_m: float) -> GeoPoint | None:
    """Convert DEM local meters to WGS84 lat/lon, if possible."""

    if dem.georef is None:
        return None
    return local_m_to_geodetic(float(x_m) - dem.origin_x_m, float(y_m) - dem.origin_y_m, dem.georef)


def geodetic_to_dem_xy(dem: DEMData, lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """Convert WGS84 lat/lon to DEM local meters."""

    if dem.georef is None:
        raise ValueError("DEM has no georeference for geodetic conversion")
    x_local, y_local = geodetic_to_local_m(lat_deg, lon_deg, dem.georef)
    return dem.origin_x_m + x_local, dem.origin_y_m + y_local


def sample_profile(
    dem: DEMData,
    start_x_m: float,
    start_y_m: float,
    azimuth_deg: float,
    speed_mps: float,
    sample_rate_hz: float,
    n_samples: int,
) -> np.ndarray:
    """Sample terrain along a straight trajectory.

    Azimuth convention: 0 degrees points north (+Y), 90 degrees points east
    (+X), 180 degrees points south, and 270 degrees points west.
    """

    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    if n_samples < 0:
        raise ValueError("n_samples must be non-negative")
    if n_samples == 0:
        return np.asarray([], dtype=float)

    times = np.arange(n_samples, dtype=float) / float(sample_rate_hz)
    distances = float(speed_mps) * times
    azimuth_rad = np.deg2rad(float(azimuth_deg) % 360.0)
    x = float(start_x_m) + distances * np.sin(azimuth_rad)
    y = float(start_y_m) + distances * np.cos(azimuth_rad)
    return _sample_dem_array(dem, x, y)


def _sample_dem_array(dem: DEMData, x_m: np.ndarray, y_m: np.ndarray) -> np.ndarray:
    x_arr = np.asarray(x_m, dtype=float)
    y_arr = np.asarray(y_m, dtype=float)
    out = np.full(np.broadcast_shapes(x_arr.shape, y_arr.shape), np.nan, dtype=float)
    x_arr, y_arr = np.broadcast_arrays(x_arr, y_arr)

    gx = (x_arr - dem.origin_x_m) / float(dem.resolution_x_m)
    gy = (y_arr - dem.origin_y_m) / float(dem.resolution_y_m)

    rows, cols = dem.elevation.shape
    inside = (
        np.isfinite(gx)
        & np.isfinite(gy)
        & (gx >= 0.0)
        & (gy >= 0.0)
        & (gx <= cols - 1)
        & (gy <= rows - 1)
    )
    if not np.any(inside):
        return out

    gx_inside = gx[inside]
    gy_inside = gy[inside]
    x0 = np.floor(gx_inside).astype(int)
    y0 = np.floor(gy_inside).astype(int)
    x0 = np.clip(x0, 0, cols - 2)
    y0 = np.clip(y0, 0, rows - 2)
    x1 = x0 + 1
    y1 = y0 + 1
    dx = gx_inside - x0
    dy = gy_inside - y0

    z00 = dem.elevation[y0, x0]
    z10 = dem.elevation[y0, x1]
    z01 = dem.elevation[y1, x0]
    z11 = dem.elevation[y1, x1]
    out[inside] = (
        z00 * (1.0 - dx) * (1.0 - dy)
        + z10 * dx * (1.0 - dy)
        + z01 * (1.0 - dx) * dy
        + z11 * dx * dy
    )
    return out


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -60.0, 60.0)))
