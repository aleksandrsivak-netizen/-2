"""Lightweight geodesy helpers for local DEM coordinates.

The core search operates in local meters. These helpers bridge WGS84
latitude/longitude with that local east/north frame without requiring pyproj.
For small civil aviation demo areas this approximation is accurate enough for
search-window setup, display, and benchmark truth reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class GeoReference:
    """Origin tying a local ENU meter frame to WGS84 coordinates."""

    origin_lat_deg: float
    origin_lon_deg: float
    origin_alt_m: float = 0.0
    crs: str = "EPSG:4326"
    projected_crs: str | None = None


@dataclass(frozen=True)
class GeoPoint:
    """Geodetic point in WGS84-style coordinates."""

    lat_deg: float
    lon_deg: float
    alt_m: float | None = None


def normalize_longitude_deg(lon_deg: float) -> float:
    """Normalize longitude to the [-180, 180) range."""

    return ((float(lon_deg) + 180.0) % 360.0) - 180.0


def meters_per_degree_lat(lat_deg: float) -> float:
    """Approximate WGS84 meters per degree of latitude."""

    lat = math.radians(float(lat_deg))
    return (
        111_132.92
        - 559.82 * math.cos(2.0 * lat)
        + 1.175 * math.cos(4.0 * lat)
        - 0.0023 * math.cos(6.0 * lat)
    )


def meters_per_degree_lon(lat_deg: float) -> float:
    """Approximate WGS84 meters per degree of longitude."""

    lat = math.radians(float(lat_deg))
    return (
        111_412.84 * math.cos(lat)
        - 93.5 * math.cos(3.0 * lat)
        + 0.118 * math.cos(5.0 * lat)
    )


def geodetic_to_local_m(
    lat_deg: float,
    lon_deg: float,
    reference: GeoReference,
) -> tuple[float, float]:
    """Convert WGS84 lat/lon to local east/north meters."""

    d_lat = float(lat_deg) - reference.origin_lat_deg
    d_lon = normalize_longitude_deg(float(lon_deg) - reference.origin_lon_deg)
    mid_lat = reference.origin_lat_deg + 0.5 * d_lat
    x_m = d_lon * meters_per_degree_lon(mid_lat)
    y_m = d_lat * meters_per_degree_lat(mid_lat)
    return x_m, y_m


def local_m_to_geodetic(
    x_m: float,
    y_m: float,
    reference: GeoReference,
) -> GeoPoint:
    """Convert local east/north meters to WGS84 lat/lon."""

    lat = reference.origin_lat_deg + float(y_m) / meters_per_degree_lat(reference.origin_lat_deg)
    lon = reference.origin_lon_deg + float(x_m) / max(meters_per_degree_lon(lat), 1e-9)
    return GeoPoint(lat_deg=lat, lon_deg=normalize_longitude_deg(lon), alt_m=reference.origin_alt_m)


def haversine_distance_m(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """Great-circle distance between two geodetic points."""

    lat1 = math.radians(float(lat1_deg))
    lat2 = math.radians(float(lat2_deg))
    d_lat = lat2 - lat1
    d_lon = math.radians(normalize_longitude_deg(float(lon2_deg) - float(lon1_deg)))
    a = math.sin(d_lat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def initial_bearing_deg(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """Initial great-circle bearing from point 1 to point 2."""

    lat1 = math.radians(float(lat1_deg))
    lat2 = math.radians(float(lat2_deg))
    d_lon = math.radians(normalize_longitude_deg(float(lon2_deg) - float(lon1_deg)))
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination_point(
    lat_deg: float,
    lon_deg: float,
    bearing_deg: float,
    distance_m: float,
) -> GeoPoint:
    """Project a WGS84 point by bearing and distance on a spherical Earth."""

    angular_distance = float(distance_m) / EARTH_RADIUS_M
    bearing = math.radians(float(bearing_deg))
    lat1 = math.radians(float(lat_deg))
    lon1 = math.radians(float(lon_deg))

    sin_lat2 = (
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lat2 = math.asin(max(-1.0, min(1.0, sin_lat2)))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )
    return GeoPoint(lat_deg=math.degrees(lat2), lon_deg=normalize_longitude_deg(math.degrees(lon2)))
