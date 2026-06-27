import numpy as np

from tercom_uav.config import CorrelationConfig
from tercom_uav.dem import DEMGrid
from tercom_uav.estimator import (
    _is_kinematically_plausible,
    angle_error_deg,
    correlate_with_speed_search,
    estimate_single_window,
)
from tercom_uav.types import TerrainProfile


def _test_dem() -> DEMGrid:
    x = np.arange(-4000.0, 4000.1, 30.0)
    y = np.arange(-4000.0, 4000.1, 30.0)
    xx, yy = np.meshgrid(x, y)
    elevation = (
        600.0
        + 0.02 * xx
        + 0.01 * yy
        + 80.0 * np.sin(xx / 310.0)
        + 50.0 * np.cos((xx - yy) / 460.0)
    )
    return DEMGrid(elevation_m=elevation, x_coords_m=x, y_coords_m=y)


def test_estimator_returns_heading_and_speed() -> None:
    dem = _test_dem()
    speed = 50.0
    heading = 44.0
    center_x, center_y = dem.center_m
    times = np.arange(0.0, 60.1, 1.0)
    distances = speed * times
    terrain = dem.sample_along(center_x, center_y, heading, -1500.0 + distances)
    radio = 1500.0 - terrain
    profile = TerrainProfile(times_s=times, radio_alt_m=radio, terrain_msl_m=terrain)
    # speed_search_enabled=False here on purpose: this test checks that a
    # correlation fix is correctly turned into azimuth/speed/velocity
    # fields, not the speed-search feature itself (see
    # test_speed_search_recovers_speed_from_wrong_hint for that, with a
    # wide enough shift range to avoid this narrow window's own aliasing).
    config = CorrelationConfig(
        shift_min_m=-1800.0,
        shift_max_m=-1200.0,
        shift_step_m=30.0,
        sample_spacing_m=30.0,
        speed_search_enabled=False,
    )
    _, estimate = estimate_single_window(dem, profile, speed_hint_mps=speed, correlation_config=config)
    assert angle_error_deg(estimate.azimuth_deg, heading) <= 1.0
    assert estimate.speed_mps == speed
    assert estimate.vx_mps == np.sin(np.deg2rad(estimate.azimuth_deg)) * speed


def test_speed_search_recovers_speed_from_wrong_hint() -> None:
    """`correlate_with_speed_search` must resolve the speed<->distance-scale
    circularity: when the externally supplied speed hint is wrong, the
    profile is resampled at several candidate speeds and the one that
    correlates best with the DEM is kept, instead of blindly trusting the
    hint (the failure mode behind several catastrophic fixes found in the
    original prototype).

    NOTE on the modest +10% hint error and the loose tolerance below: this
    project's terrain generators (this periodic test fixture and the main
    `DEMGrid.synthetic()`) are smooth/periodic enough that a single
    isolated window can still alias onto a wrong (speed, azimuth, shift)
    combination scoring just as well as the true one - the same
    fundamental ambiguity already documented for `coarse_to_fine`. Single-
    window speed recovery is therefore best-effort, not exact; the
    practical robustness comes from combining it with the kinematic gate
    and the sliding-window carry-forward in `estimate_window_series`,
    verified end to end via `tercom-uav simulate` + `localize` with a
    deliberately wrong --speed-hint (see ОТЧЕТ_О_ПРАВКАХ.txt).
    """

    dem = _test_dem()
    true_speed = 50.0
    wrong_hint = 55.0  # +10% off
    heading = 44.0
    center_x, center_y = dem.center_m
    times = np.arange(0.0, 60.1, 1.0)
    distances = true_speed * times
    terrain = dem.sample_along(center_x, center_y, heading, -1500.0 + distances)
    radio = 1500.0 - terrain
    profile = TerrainProfile(times_s=times, radio_alt_m=radio, terrain_msl_m=terrain)
    config = CorrelationConfig(
        shift_min_m=-3500.0,
        shift_max_m=-500.0,
        shift_step_m=30.0,
        sample_spacing_m=30.0,
        speed_scale_step=0.02,
    )
    estimated_speed, result = correlate_with_speed_search(dem, profile, wrong_hint, config)
    assert abs(estimated_speed - true_speed) <= 2.0
    assert angle_error_deg(result.best_azimuth_deg, heading) <= 3.0


def test_kinematic_plausibility_gate_rejects_impossible_jumps() -> None:
    """Regression test for the window-to-window outlier rejection added after
    a real demo run produced a window implying 236 m/s / a 7+ km jump from a
    raw, ambiguous TERCOM match on smooth terrain. Such a jump must be
    rejected by `max_speed_mps`, while a plausible displacement must pass.
    """

    # 15s window, ~55 m/s cruise -> displacement of ~825m is plausible.
    assert _is_kinematically_plausible(dx_m=800.0, dy_m=200.0, dt_s=15.0, max_speed_mps=120.0) is True
    # The actual failure observed in outputs/check1: ~7.3km jump in 15s.
    assert _is_kinematically_plausible(dx_m=7000.0, dy_m=2000.0, dt_s=15.0, max_speed_mps=120.0) is False
