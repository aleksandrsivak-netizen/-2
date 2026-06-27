from tercom_uav.dem import DEMGrid
from tercom_uav.webapp import _web_correlation_config


def test_dashboard_default_correlation_config_is_fast() -> None:
    dem = DEMGrid.synthetic(width_m=1200.0, height_m=1200.0, resolution_m=30.0)

    config = _web_correlation_config(dem, {"shiftStep": 120.0}, strict_mode=False)

    assert config.azimuth_step_deg == 5.0
    assert config.shift_step_m == 120.0
    assert config.speed_search_enabled is False


def test_dashboard_strict_correlation_config_keeps_full_grid() -> None:
    dem = DEMGrid.synthetic(width_m=1200.0, height_m=1200.0, resolution_m=30.0)

    config = _web_correlation_config(dem, {"shiftStep": 30.0, "coarseToFine": True}, strict_mode=True)

    assert config.azimuth_step_deg == 1.0
    assert config.shift_step_m == 30.0
    assert config.coarse_to_fine is False
    assert config.speed_search_enabled is True
