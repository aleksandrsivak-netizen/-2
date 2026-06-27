import numpy as np
import pytest

from tercom_uav.dem import DEMGrid


def test_bilinear_dem_sampling() -> None:
    dem = DEMGrid(
        elevation_m=np.array([[0.0, 10.0], [20.0, 30.0]]),
        x_coords_m=np.array([0.0, 10.0]),
        y_coords_m=np.array([0.0, 10.0]),
    )
    assert dem.sample(5.0, 5.0) == pytest.approx(15.0)
    assert dem.sample(0.0, 0.0) == pytest.approx(0.0)


def test_dem_nodata_propagates_to_nan() -> None:
    dem = DEMGrid(
        elevation_m=np.array([[0.0, np.nan], [20.0, 30.0]]),
        x_coords_m=np.array([0.0, 10.0]),
        y_coords_m=np.array([0.0, 10.0]),
    )
    assert np.isnan(dem.sample(5.0, 5.0))


def test_sample_along_azimuth_convention() -> None:
    dem = DEMGrid.synthetic(width_m=1000.0, height_m=1000.0, resolution_m=50.0)
    distances = np.array([0.0, 100.0])
    north = dem.sample_along(0.0, 0.0, 0.0, distances)
    east = dem.sample_along(0.0, 0.0, 90.0, distances)
    assert north.shape == (2,)
    assert east.shape == (2,)

