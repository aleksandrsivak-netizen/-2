"""DEM loading, local coordinate handling and bilinear terrain sampling."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(slots=True)
class DEMGrid:
    """Regular DEM grid in local metric coordinates.

    `x_coords_m` and `y_coords_m` are strictly increasing cell-center
    coordinates. Elevation values are meters above mean sea level.
    """

    elevation_m: np.ndarray
    x_coords_m: np.ndarray
    y_coords_m: np.ndarray
    crs: Any = None
    source_path: str | None = None
    nodata: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _wgs84_to_local: Any = field(default=None, repr=False)
    _local_to_wgs84: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.elevation_m = np.asarray(self.elevation_m, dtype=float)
        self.x_coords_m = np.asarray(self.x_coords_m, dtype=float)
        self.y_coords_m = np.asarray(self.y_coords_m, dtype=float)
        if self.elevation_m.ndim != 2:
            raise ValueError("elevation_m must be a 2D array.")
        if self.elevation_m.shape != (self.y_coords_m.size, self.x_coords_m.size):
            raise ValueError("DEM shape must match y/x coordinate arrays.")
        if np.any(np.diff(self.x_coords_m) <= 0) or np.any(np.diff(self.y_coords_m) <= 0):
            raise ValueError("DEM coordinates must be strictly increasing.")

    @property
    def bounds_m(self) -> tuple[float, float, float, float]:
        return (
            float(self.x_coords_m[0]),
            float(self.y_coords_m[0]),
            float(self.x_coords_m[-1]),
            float(self.y_coords_m[-1]),
        )

    @property
    def resolution_m(self) -> tuple[float, float]:
        dx = float(np.median(np.diff(self.x_coords_m))) if self.x_coords_m.size > 1 else 1.0
        dy = float(np.median(np.diff(self.y_coords_m))) if self.y_coords_m.size > 1 else 1.0
        return dx, dy

    @property
    def center_m(self) -> tuple[float, float]:
        x_min, y_min, x_max, y_max = self.bounds_m
        return (x_min + x_max) * 0.5, (y_min + y_max) * 0.5

    @classmethod
    def synthetic(
        cls,
        width_m: float = 12000.0,
        height_m: float = 12000.0,
        resolution_m: float = 30.0,
        seed: int = 7,
        flat: bool = False,
    ) -> "DEMGrid":
        """Create a deterministic synthetic DEM for demos and tests."""

        rng = np.random.default_rng(seed)
        x = np.arange(-width_m / 2.0, width_m / 2.0 + resolution_m, resolution_m)
        y = np.arange(-height_m / 2.0, height_m / 2.0 + resolution_m, resolution_m)
        xx, yy = np.meshgrid(x, y)
        if flat:
            elevation = np.full_like(xx, 500.0, dtype=float)
        else:
            elevation = (
                550.0
                + 0.018 * xx
                - 0.012 * yy
                + 75.0 * np.sin(xx / 720.0)
                + 55.0 * np.cos(yy / 610.0)
                + 35.0 * np.sin((xx + yy) / 430.0)
            )
            for _ in range(9):
                cx = rng.uniform(x.min() * 0.8, x.max() * 0.8)
                cy = rng.uniform(y.min() * 0.8, y.max() * 0.8)
                amp = rng.uniform(-95.0, 130.0)
                sigma = rng.uniform(350.0, 1250.0)
                elevation += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2.0 * sigma**2))
        return cls(
            elevation_m=elevation,
            x_coords_m=x,
            y_coords_m=y,
            crs="LOCAL_SYNTHETIC_METERS",
            source_path=None,
            metadata={"synthetic": True, "flat": flat, "resolution_m": resolution_m},
        )

    @classmethod
    def from_geotiff(cls, path: str | Path) -> "DEMGrid":
        """Load a single-band GeoTIFF DEM and expose it in local meters."""

        try:
            import rasterio
            from pyproj import CRS, Transformer
        except ImportError as exc:
            raise ImportError("GeoTIFF DEM loading requires rasterio and pyproj.") from exc

        dem_path = Path(path)
        with rasterio.open(dem_path) as dataset:
            data = dataset.read(1).astype(float)
            nodata = dataset.nodata
            if nodata is not None:
                data[data == nodata] = np.nan

            rows = np.arange(dataset.height)
            cols = np.arange(dataset.width)
            xs_src = np.array([dataset.transform * (col + 0.5, 0.5) for col in cols])[:, 0]
            ys_src = np.array([dataset.transform * (0.5, row + 0.5) for row in rows])[:, 1]
            source_crs = dataset.crs
            metadata = {
                "bounds": tuple(dataset.bounds),
                "crs": str(source_crs),
                "resolution": tuple(dataset.res),
                "width": dataset.width,
                "height": dataset.height,
            }

            if source_crs is None:
                x_coords = xs_src
                y_coords = ys_src
                local_crs = None
                wgs84_to_local = None
                local_to_wgs84 = None
            else:
                crs = CRS.from_user_input(source_crs)
                if crs.is_geographic:
                    bounds = dataset.bounds
                    center_lon = (bounds.left + bounds.right) * 0.5
                    center_lat = (bounds.bottom + bounds.top) * 0.5
                    local_crs = CRS.from_proj4(
                        f"+proj=aeqd +lat_0={center_lat} +lon_0={center_lon} "
                        "+datum=WGS84 +units=m +no_defs"
                    )
                    to_local = Transformer.from_crs(crs, local_crs, always_xy=True)
                    to_wgs84 = Transformer.from_crs(local_crs, CRS.from_epsg(4326), always_xy=True)
                    center_y_src = (bounds.bottom + bounds.top) * 0.5
                    center_x_src = (bounds.left + bounds.right) * 0.5
                    x_coords, _ = to_local.transform(xs_src, np.full_like(xs_src, center_y_src))
                    _, y_coords = to_local.transform(np.full_like(ys_src, center_x_src), ys_src)
                    wgs84_to_local = Transformer.from_crs(CRS.from_epsg(4326), local_crs, always_xy=True)
                    local_to_wgs84 = to_wgs84
                else:
                    x_coords = xs_src
                    y_coords = ys_src
                    local_crs = crs
                    wgs84_to_local = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
                    local_to_wgs84 = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)

        if x_coords[0] > x_coords[-1]:
            x_coords = x_coords[::-1]
            data = data[:, ::-1]
        if y_coords[0] > y_coords[-1]:
            y_coords = y_coords[::-1]
            data = data[::-1, :]

        return cls(
            elevation_m=data,
            x_coords_m=np.asarray(x_coords, dtype=float),
            y_coords_m=np.asarray(y_coords, dtype=float),
            crs=local_crs,
            source_path=str(dem_path),
            nodata=nodata,
            metadata=metadata,
            _wgs84_to_local=wgs84_to_local,
            _local_to_wgs84=local_to_wgs84,
        )

    def sample(self, x_m: np.ndarray | float, y_m: np.ndarray | float, fill_value: float = np.nan) -> np.ndarray | float:
        """Bilinearly sample elevation at arbitrary local metric points."""

        scalar = np.isscalar(x_m) and np.isscalar(y_m)
        x = np.asarray(x_m, dtype=float)
        y = np.asarray(y_m, dtype=float)
        x_b, y_b = np.broadcast_arrays(x, y)
        flat_x = x_b.ravel()
        flat_y = y_b.ravel()
        result = np.full(flat_x.shape, fill_value, dtype=float)

        x_coords = self.x_coords_m
        y_coords = self.y_coords_m
        inside = (
            (flat_x >= x_coords[0])
            & (flat_x <= x_coords[-1])
            & (flat_y >= y_coords[0])
            & (flat_y <= y_coords[-1])
        )
        if not np.any(inside):
            reshaped = result.reshape(x_b.shape)
            return float(reshaped) if scalar else reshaped

        xi = np.searchsorted(x_coords, flat_x[inside], side="right") - 1
        yi = np.searchsorted(y_coords, flat_y[inside], side="right") - 1
        xi = np.clip(xi, 0, x_coords.size - 2)
        yi = np.clip(yi, 0, y_coords.size - 2)

        x0 = x_coords[xi]
        x1 = x_coords[xi + 1]
        y0 = y_coords[yi]
        y1 = y_coords[yi + 1]
        tx = np.divide(flat_x[inside] - x0, x1 - x0, out=np.zeros_like(x0), where=(x1 != x0))
        ty = np.divide(flat_y[inside] - y0, y1 - y0, out=np.zeros_like(y0), where=(y1 != y0))

        z00 = self.elevation_m[yi, xi]
        z10 = self.elevation_m[yi, xi + 1]
        z01 = self.elevation_m[yi + 1, xi]
        z11 = self.elevation_m[yi + 1, xi + 1]
        valid = ~(np.isnan(z00) | np.isnan(z10) | np.isnan(z01) | np.isnan(z11))
        interpolated = (
            (1.0 - tx) * (1.0 - ty) * z00
            + tx * (1.0 - ty) * z10
            + (1.0 - tx) * ty * z01
            + tx * ty * z11
        )
        inside_indices = np.flatnonzero(inside)
        result[inside_indices[valid]] = interpolated[valid]

        reshaped = result.reshape(x_b.shape)
        return float(reshaped) if scalar else reshaped

    def sample_along(
        self,
        start_x_m: float,
        start_y_m: float,
        azimuth_deg: float,
        distances_m: np.ndarray,
    ) -> np.ndarray:
        """Sample DEM along an azimuth, clockwise from north."""

        azimuth_rad = np.deg2rad(azimuth_deg)
        distances = np.asarray(distances_m, dtype=float)
        x = start_x_m + np.sin(azimuth_rad) * distances
        y = start_y_m + np.cos(azimuth_rad) * distances
        return np.asarray(self.sample(x, y), dtype=float)

    def crop(self, x_min_m: float, y_min_m: float, x_max_m: float, y_max_m: float) -> "DEMGrid":
        """Return a DEM cropped to a local metric bounding box."""

        x_mask = (self.x_coords_m >= x_min_m) & (self.x_coords_m <= x_max_m)
        y_mask = (self.y_coords_m >= y_min_m) & (self.y_coords_m <= y_max_m)
        if not np.any(x_mask) or not np.any(y_mask):
            raise ValueError("Crop bounds do not intersect DEM.")
        return DEMGrid(
            elevation_m=self.elevation_m[np.ix_(y_mask, x_mask)],
            x_coords_m=self.x_coords_m[x_mask],
            y_coords_m=self.y_coords_m[y_mask],
            crs=self.crs,
            source_path=self.source_path,
            nodata=self.nodata,
            metadata={**self.metadata, "crop_bounds_m": (x_min_m, y_min_m, x_max_m, y_max_m)},
            _wgs84_to_local=self._wgs84_to_local,
            _local_to_wgs84=self._local_to_wgs84,
        )

    def wgs84_to_local(self, lon: float, lat: float) -> tuple[float, float]:
        """Convert WGS84 lon/lat to local DEM coordinates when CRS is known."""

        if self._wgs84_to_local is None:
            raise ValueError("DEM has no WGS84 transformer.")
        x, y = self._wgs84_to_local.transform(lon, lat)
        return float(x), float(y)

    def local_to_wgs84(self, x_m: float, y_m: float) -> tuple[float, float]:
        """Convert local DEM coordinates to WGS84 lon/lat when CRS is known."""

        if self._local_to_wgs84 is None:
            raise ValueError("DEM has no WGS84 transformer.")
        lon, lat = self._local_to_wgs84.transform(x_m, y_m)
        return float(lon), float(lat)

