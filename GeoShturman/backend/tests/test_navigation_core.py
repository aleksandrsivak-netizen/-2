from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.dem import create_synthetic_dem
from app.core.navigation import solve_navigation
from app.core.simulator import (
    generate_nmea_from_radio_profile,
    generate_radio_altimeter_profile,
    generate_truth_trajectory,
)


def test_solve_navigation_demo_accuracy():
    dem = create_synthetic_dem(2500.0, 2500.0, 50.0, seed=21)
    truth = generate_truth_trajectory(850.0, 650.0, 70.0, 40.0, 35.0, 2.0)
    radio = generate_radio_altimeter_profile(dem, truth, 1500.0, noise_std_m=0.5, seed=5)
    nmea = generate_nmea_from_radio_profile(radio, sample_rate_hz=2.0)

    solution = solve_navigation(
        dem=dem,
        nmea_text=nmea,
        barometric_altitude_msl=1500.0,
        sample_rate_hz=2.0,
        search_center_x_m=850.0,
        search_center_y_m=650.0,
        search_radius_m=150.0,
        coarse_step_m=150.0,
        fine_step_m=50.0,
        azimuth_coarse_step_deg=5.0,
        azimuth_fine_step_deg=1.0,
        speed_min_mps=35.0,
        speed_max_mps=45.0,
        speed_coarse_step_mps=5.0,
        speed_fine_step_mps=1.0,
        enable_kalman=True,
    )

    assert abs(solution.estimated["azimuth_deg"] - 70.0) <= 3.0
    assert abs(solution.estimated["speed_mps"] - 40.0) <= 2.0
    assert solution.quality["confidence"] >= 0.5


def test_solve_navigation_with_baro_drift_and_outliers():
    dem = create_synthetic_dem(2500.0, 2500.0, 50.0, seed=31, terrain_type="mountain")
    truth = generate_truth_trajectory(800.0, 700.0, 55.0, 38.0, 32.0, 2.0)
    radio = generate_radio_altimeter_profile(
        dem,
        truth,
        1500.0,
        noise_std_m=1.0,
        outlier_probability=0.04,
        outlier_scale_m=35.0,
        dropout_probability=0.01,
        barometric_drift_m=20.0,
        seed=6,
    )
    nmea = generate_nmea_from_radio_profile(radio, sample_rate_hz=2.0)

    solution = solve_navigation(
        dem=dem,
        nmea_text=nmea,
        barometric_altitude_msl=1500.0,
        sample_rate_hz=2.0,
        search_center_x_m=800.0,
        search_center_y_m=700.0,
        search_radius_m=150.0,
        coarse_step_m=150.0,
        fine_step_m=50.0,
        azimuth_coarse_step_deg=5.0,
        azimuth_fine_step_deg=1.0,
        speed_min_mps=34.0,
        speed_max_mps=42.0,
        speed_coarse_step_mps=4.0,
        speed_fine_step_mps=1.0,
        enable_kalman=True,
        parallel_jobs=2,
        compensate_baro_drift=True,
    )

    assert abs(solution.estimated["azimuth_deg"] - 55.0) <= 4.0
    assert abs(solution.estimated["speed_mps"] - 38.0) <= 2.0
    assert solution.quality["confidence"] >= 0.7
    assert solution.metadata["compensate_baro_drift"]
