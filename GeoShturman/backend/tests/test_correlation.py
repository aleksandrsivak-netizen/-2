from pathlib import Path
import sys

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.correlation import normalized_cross_correlation, search_best_match
from app.core.dem import create_synthetic_dem
from app.core.profile import radio_agl_to_terrain_msl
from app.core.simulator import generate_radio_altimeter_profile, generate_truth_trajectory


def test_normalized_cross_correlation_identical_profiles():
    profile = np.asarray([1.0, 2.0, 4.0, 8.0, 16.0])

    assert normalized_cross_correlation(profile, profile) == 1.0


def test_search_finds_correct_azimuth_on_synthetic_dem():
    dem = create_synthetic_dem(2000.0, 2000.0, 50.0, seed=11)
    truth = generate_truth_trajectory(700.0, 700.0, 45.0, 40.0, 25.0, 2.0)
    radio = generate_radio_altimeter_profile(dem, truth, 1500.0, noise_std_m=0.0, seed=2)
    measured = radio_agl_to_terrain_msl(radio, 1500.0)

    result = search_best_match(
        dem=dem,
        measured_terrain_profile=measured,
        sample_rate_hz=2.0,
        search_center_x_m=700.0,
        search_center_y_m=700.0,
        search_radius_m=0.0,
        search_step_m=100.0,
        azimuth_step_deg=5.0,
        speed_min_mps=40.0,
        speed_max_mps=40.0,
        speed_step_mps=5.0,
    )

    assert abs(result.best.azimuth_deg - 45.0) <= 1e-9
    assert result.best.correlation > 0.99


def test_parallel_search_matches_sequential_best():
    dem = create_synthetic_dem(1800.0, 1800.0, 60.0, seed=12)
    truth = generate_truth_trajectory(650.0, 650.0, 50.0, 38.0, 20.0, 2.0)
    radio = generate_radio_altimeter_profile(dem, truth, 1500.0, noise_std_m=0.0, seed=3)
    measured = radio_agl_to_terrain_msl(radio, 1500.0)

    kwargs = dict(
        dem=dem,
        measured_terrain_profile=measured,
        sample_rate_hz=2.0,
        search_center_x_m=650.0,
        search_center_y_m=650.0,
        search_radius_m=120.0,
        search_step_m=120.0,
        azimuth_step_deg=10.0,
        speed_min_mps=38.0,
        speed_max_mps=38.0,
        speed_step_mps=5.0,
    )
    sequential = search_best_match(**kwargs, n_jobs=1)
    parallel = search_best_match(**kwargs, n_jobs=2)

    assert parallel.best.azimuth_deg == sequential.best.azimuth_deg
    assert parallel.best.start_x_m == sequential.best.start_x_m
    assert parallel.best.start_y_m == sequential.best.start_y_m
