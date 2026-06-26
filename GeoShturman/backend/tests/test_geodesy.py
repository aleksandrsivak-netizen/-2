from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.dem import create_synthetic_dem, dem_xy_to_geodetic, geodetic_to_dem_xy, is_inside_dem_geodetic
from app.core.geodesy import geodetic_to_local_m, haversine_distance_m, local_m_to_geodetic, GeoReference


def test_geodesy_local_roundtrip():
    reference = GeoReference(origin_lat_deg=56.0, origin_lon_deg=37.0)
    point = local_m_to_geodetic(850.0, 1200.0, reference)

    x_m, y_m = geodetic_to_local_m(point.lat_deg, point.lon_deg, reference)

    assert abs(x_m - 850.0) < 1.0
    assert abs(y_m - 1200.0) < 1.0
    assert haversine_distance_m(reference.origin_lat_deg, reference.origin_lon_deg, point.lat_deg, point.lon_deg) > 1400.0


def test_georeferenced_dem_coordinate_helpers():
    dem = create_synthetic_dem(1200.0, 1200.0, 100.0, seed=4, origin_lat_deg=56.0, origin_lon_deg=37.0)
    geo = dem_xy_to_geodetic(dem, 500.0, 600.0)
    assert geo is not None

    x_m, y_m = geodetic_to_dem_xy(dem, geo.lat_deg, geo.lon_deg)

    assert abs(x_m - 500.0) < 1.0
    assert abs(y_m - 600.0) < 1.0
    assert is_inside_dem_geodetic(dem, geo.lat_deg, geo.lon_deg)
