import numpy as np

from tercom_uav.config import CorrelationConfig
from tercom_uav.correlation import correlate_profile
from tercom_uav.dem import DEMGrid


def _test_dem() -> DEMGrid:
    x = np.arange(-3500.0, 3500.1, 30.0)
    y = np.arange(-3500.0, 3500.1, 30.0)
    xx, yy = np.meshgrid(x, y)
    elevation = (
        500.0
        + 0.01 * xx
        - 0.015 * yy
        + 70.0 * np.sin(xx / 260.0)
        + 45.0 * np.cos(yy / 370.0)
        + 30.0 * np.sin((2.0 * xx + yy) / 510.0)
    )
    return DEMGrid(elevation_m=elevation, x_coords_m=x, y_coords_m=y)


def _angle_error(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def test_ideal_correlation_match() -> None:
    dem = _test_dem()
    center_x, center_y = dem.center_m
    distances = np.arange(0.0, 1500.1, 30.0)
    observed = dem.sample_along(center_x, center_y, 73.0, -900.0 + distances)
    config = CorrelationConfig(shift_min_m=-1200.0, shift_max_m=-600.0, shift_step_m=30.0)
    result = correlate_profile(dem, observed, distances, config)
    assert _angle_error(result.best_azimuth_deg, 73.0) <= 1.0
    assert abs(result.best_shift_m + 900.0) <= 30.0
    assert result.best_score > 0.99
    assert result.best_score >= result.second_best_score


def test_noisy_correlation_match() -> None:
    rng = np.random.default_rng(123)
    dem = _test_dem()
    center_x, center_y = dem.center_m
    distances = np.arange(0.0, 1500.1, 30.0)
    observed = dem.sample_along(center_x, center_y, 121.0, -600.0 + distances)
    observed = observed + rng.normal(0.0, 2.0, size=observed.size)
    config = CorrelationConfig(shift_min_m=-900.0, shift_max_m=-300.0, shift_step_m=30.0)
    result = correlate_profile(dem, observed, distances, config)
    assert _angle_error(result.best_azimuth_deg, 121.0) <= 2.0
    assert abs(result.best_shift_m + 600.0) <= 60.0
    assert result.best_score > 0.85


def test_flat_terrain_lowers_confidence() -> None:
    dem = DEMGrid.synthetic(width_m=3000.0, height_m=3000.0, resolution_m=30.0, flat=True)
    distances = np.arange(0.0, 900.1, 30.0)
    observed = np.full(distances.shape, 500.0)
    config = CorrelationConfig(shift_min_m=-600.0, shift_max_m=600.0, shift_step_m=60.0)
    result = correlate_profile(dem, observed, distances, config)
    assert result.ambiguous_match is True
    assert result.confidence_score <= 0.45
    assert result.observability_score < 0.2
    assert result.low_observability is True
    assert result.reference_profile_range_m == 0.0


def test_fully_degenerate_search_returns_no_fix_instead_of_raising() -> None:
    """Regression test: every reference profile in the search grid is flat.

    Before the fix, `correlate_profile` raised `ValueError` for this case
    with a misleading "left the search grid" message even though the real
    cause is a perfectly flat terrain segment (a realistic case over
    taiga/tundra/steppe), and the task's own README lists "adaptation to
    flat terrain" as a feature that should degrade gracefully, not crash.
    """

    dem = DEMGrid.synthetic(width_m=1500.0, height_m=1500.0, resolution_m=30.0, flat=True)
    distances = np.arange(0.0, 300.1, 30.0)
    rng = np.random.default_rng(0)
    # Observed varies (sensor noise) while every map reference is perfectly
    # flat: every reference row's std is 0, so `_score_references` cannot
    # normalize any row and the whole heatmap stays NaN end to end.
    observed = 500.0 + rng.normal(0.0, 2.0, size=distances.shape)
    config = CorrelationConfig(shift_min_m=-100.0, shift_max_m=100.0, shift_step_m=50.0)
    result = correlate_profile(dem, observed, distances, config)
    assert result.ambiguous_match is True
    assert result.confidence_score == 0.0
    assert result.low_observability is True
    assert np.isnan(result.best_score)


def test_low_reference_observability_caps_confidence_even_for_high_correlation() -> None:
    x = np.arange(-1200.0, 1200.1, 30.0)
    y = np.arange(-1200.0, 1200.1, 30.0)
    xx, yy = np.meshgrid(x, y)
    elevation = 500.0 + 0.8 * np.sin(xx / 600.0) + 0.4 * np.cos(yy / 700.0)
    dem = DEMGrid(elevation_m=elevation, x_coords_m=x, y_coords_m=y)
    center_x, center_y = dem.center_m
    distances = np.arange(0.0, 600.1, 30.0)
    observed = dem.sample_along(center_x, center_y, 47.0, -300.0 + distances)
    config = CorrelationConfig(
        shift_min_m=-420.0,
        shift_max_m=-180.0,
        shift_step_m=30.0,
        min_reference_std_m=1.0,
        min_reference_range_m=5.0,
    )

    result = correlate_profile(dem, observed, distances, config)

    assert result.best_score > 0.99
    assert result.low_observability is True
    assert result.reference_profile_range_m < config.min_reference_range_m
    assert result.ambiguous_match is True
    assert result.confidence_score <= 0.25


def test_coarse_to_fine_matches_full_grid_on_observable_terrain() -> None:
    dem = _test_dem()
    center_x, center_y = dem.center_m
    distances = np.arange(0.0, 1500.1, 30.0)
    observed = dem.sample_along(center_x, center_y, 73.0, -900.0 + distances)
    base_config = CorrelationConfig(shift_min_m=-1200.0, shift_max_m=-600.0, shift_step_m=30.0)
    fast_config = CorrelationConfig(
        shift_min_m=-1200.0,
        shift_max_m=-600.0,
        shift_step_m=30.0,
        coarse_to_fine=True,
    )

    full = correlate_profile(dem, observed, distances, base_config)
    fast = correlate_profile(dem, observed, distances, fast_config)

    assert full.low_observability is False
    assert fast.low_observability is False
    assert _angle_error(fast.best_azimuth_deg, full.best_azimuth_deg) <= fast_config.azimuth_step_deg
    assert abs(fast.best_shift_m - full.best_shift_m) <= fast_config.shift_step_m


def test_coarse_to_fine_keeps_low_observability_flag_on_smooth_terrain() -> None:
    dem = DEMGrid.synthetic(width_m=1500.0, height_m=1500.0, resolution_m=30.0, flat=True)
    distances = np.arange(0.0, 300.1, 30.0)
    observed = np.full(distances.shape, 500.0)
    config = CorrelationConfig(
        shift_min_m=-100.0,
        shift_max_m=100.0,
        shift_step_m=50.0,
        coarse_to_fine=True,
    )

    result = correlate_profile(dem, observed, distances, config)

    assert result.low_observability is True
    assert result.ambiguous_match is True
    assert result.confidence_score <= 0.25
