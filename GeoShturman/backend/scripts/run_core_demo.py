"""Run standalone demos and benchmarks for the navigation core."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from time import perf_counter


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault("MPLCONFIGDIR", str(BACKEND_ROOT / "scripts" / "core_demo_output" / "mpl_cache"))

from app.core.dem import create_synthetic_dem
from app.core.navigation import solve_navigation
from app.core.simulator import (
    generate_nmea_from_radio_profile,
    generate_radio_altimeter_profile,
    generate_truth_trajectory,
)
from app.core.visualization import (
    save_correlation_heatmap,
    save_profile_comparison,
    save_trajectory_overlay,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the GeoShturman navigation core demo.")
    parser.add_argument("--benchmarks", action="store_true", help="run multiple terrain benchmark scenarios")
    parser.add_argument("--no-plots", action="store_true", help="skip PNG plot generation for the single demo")
    args = parser.parse_args(argv)

    if args.benchmarks:
        return run_benchmarks()
    return run_single_demo(save_plots=not args.no_plots)


def run_single_demo(save_plots: bool = True) -> int:
    sample_rate_hz = 2.0
    barometric_altitude_msl = 1500.0
    true_start_x_m = 1650.0
    true_start_y_m = 1100.0
    true_azimuth_deg = 62.0
    true_speed_mps = 45.0

    dem = create_synthetic_dem(
        width_m=4000.0,
        height_m=4000.0,
        resolution_m=50.0,
        seed=42,
        terrain_type="mixed",
        origin_lat_deg=56.10,
        origin_lon_deg=37.20,
    )
    truth = generate_truth_trajectory(
        start_x_m=true_start_x_m,
        start_y_m=true_start_y_m,
        azimuth_deg=true_azimuth_deg,
        speed_mps=true_speed_mps,
        duration_s=50.0,
        sample_rate_hz=sample_rate_hz,
    )
    radio_profile = generate_radio_altimeter_profile(
        dem=dem,
        trajectory=truth,
        barometric_altitude_msl=barometric_altitude_msl,
        noise_std_m=1.5,
        outlier_probability=0.03,
        outlier_scale_m=45.0,
        dropout_probability=0.01,
        barometric_drift_m=18.0,
        seed=7,
    )
    nmea_text = generate_nmea_from_radio_profile(radio_profile, sample_rate_hz=sample_rate_hz)

    started = perf_counter()
    solution = solve_navigation(
        dem=dem,
        nmea_text=nmea_text,
        barometric_altitude_msl=barometric_altitude_msl,
        sample_rate_hz=sample_rate_hz,
        search_center_x_m=true_start_x_m + 75.0,
        search_center_y_m=true_start_y_m - 60.0,
        search_radius_m=650.0,
        coarse_step_m=250.0,
        fine_step_m=50.0,
        azimuth_coarse_step_deg=5.0,
        azimuth_fine_step_deg=1.0,
        speed_min_mps=35.0,
        speed_max_mps=55.0,
        speed_coarse_step_mps=5.0,
        speed_fine_step_mps=1.0,
        enable_kalman=True,
        parallel_jobs=0,
        compensate_baro_drift=True,
    )
    elapsed_s = perf_counter() - started

    output_dir = Path(__file__).resolve().parent / "core_demo_output"
    plot_paths: list[str] = []
    if save_plots:
        plot_paths.append(
            save_trajectory_overlay(
                dem=dem,
                truth_trajectory=truth,
                estimated_trajectory=solution.trajectory,
                output_path=str(output_dir / "trajectory_overlay.png"),
            )
        )
        plot_paths.append(
            save_correlation_heatmap(
                heatmap=solution.heatmap,
                azimuth_values=solution.metadata["refined_azimuth_values"],
                output_path=str(output_dir / "correlation_heatmap.png"),
            )
        )
        plot_paths.append(
            save_profile_comparison(
                measured_profile=solution.metadata["corrected_measured_profile"],
                reference_profile=solution.reference_profile,
                output_path=str(output_dir / "profile_comparison.png"),
            )
        )

    print(f"True azimuth: {true_azimuth_deg:.1f} deg")
    print(f"Estimated azimuth: {solution.estimated['azimuth_deg']:.1f} deg")
    print(f"True speed: {true_speed_mps:.1f} m/s")
    print(f"Estimated speed: {solution.estimated['speed_mps']:.1f} m/s")
    print(f"Correlation: {solution.estimated['correlation']:.3f}")
    print(f"RMSE: {solution.estimated['rmse_m']:.2f} m")
    print(f"Confidence: {solution.quality['confidence']:.3f}")
    print(f"Warning: {solution.quality['warning'] or 'none'}")
    print(f"Baro drift estimate: {solution.quality['baro_drift_total_m']:.1f} m total")
    print(f"Elapsed: {elapsed_s:.2f} s")
    for path in plot_paths:
        print(f"Saved plot: {path}")
    return 0


def run_benchmarks() -> int:
    scenarios = [
        ("rolling", "rolling", 10, 820.0, 720.0, 54.0, 40.0, 12.0, 0.02),
        ("mixed", "mixed", 21, 900.0, 760.0, 68.0, 42.0, 18.0, 0.03),
        ("mountain", "mountain", 32, 780.0, 640.0, 38.0, 36.0, -20.0, 0.02),
        ("valley", "valley", 43, 980.0, 620.0, 74.0, 44.0, 24.0, 0.04),
        ("plateau", "plateau", 54, 760.0, 840.0, 92.0, 38.0, 16.0, 0.03),
        ("flat-control", "flat", 65, 780.0, 740.0, 60.0, 40.0, 10.0, 0.02),
    ]
    rows = []
    started_all = perf_counter()
    for name, terrain_type, seed, start_x, start_y, azimuth, speed, drift_m, outlier_prob in scenarios:
        started = perf_counter()
        result = _run_benchmark_case(
            terrain_type=terrain_type,
            seed=seed,
            start_x_m=start_x,
            start_y_m=start_y,
            azimuth_deg=azimuth,
            speed_mps=speed,
            drift_m=drift_m,
            outlier_probability=outlier_prob,
        )
        elapsed = perf_counter() - started
        rows.append(
            {
                "name": name,
                "az_err": _angle_error_deg(result.estimated["azimuth_deg"], azimuth),
                "speed_err": abs(result.estimated["speed_mps"] - speed),
                "corr": result.estimated["correlation"],
                "rmse": result.estimated["rmse_m"],
                "conf": result.quality["confidence"],
                "warning": result.quality["warning"] or "none",
                "elapsed": elapsed,
            }
        )

    print("Scenario       AzErr  SpeedErr  Corr   RMSE   Conf   Time   Warning")
    print("-------------  -----  --------  -----  -----  -----  -----  ----------------")
    for row in rows:
        print(
            f"{row['name']:<13}  "
            f"{row['az_err']:>5.1f}  "
            f"{row['speed_err']:>8.1f}  "
            f"{row['corr']:>5.3f}  "
            f"{row['rmse']:>5.1f}  "
            f"{row['conf']:>5.3f}  "
            f"{row['elapsed']:>5.2f}  "
            f"{row['warning']}"
        )
    print(f"Total benchmark time: {perf_counter() - started_all:.2f} s")
    return 0


def _run_benchmark_case(
    terrain_type: str,
    seed: int,
    start_x_m: float,
    start_y_m: float,
    azimuth_deg: float,
    speed_mps: float,
    drift_m: float,
    outlier_probability: float,
):
    sample_rate_hz = 2.0
    barometric_altitude_msl = 1500.0
    dem = create_synthetic_dem(
        width_m=2600.0,
        height_m=2600.0,
        resolution_m=50.0,
        seed=seed,
        terrain_type=terrain_type,
        origin_lat_deg=56.0 + seed * 0.001,
        origin_lon_deg=37.0 + seed * 0.001,
    )
    truth = generate_truth_trajectory(
        start_x_m=start_x_m,
        start_y_m=start_y_m,
        azimuth_deg=azimuth_deg,
        speed_mps=speed_mps,
        duration_s=32.0,
        sample_rate_hz=sample_rate_hz,
    )
    radio = generate_radio_altimeter_profile(
        dem=dem,
        trajectory=truth,
        barometric_altitude_msl=barometric_altitude_msl,
        noise_std_m=2.0,
        outlier_probability=outlier_probability,
        outlier_scale_m=45.0,
        dropout_probability=0.01,
        barometric_drift_m=drift_m,
        seed=seed + 1000,
    )
    nmea = generate_nmea_from_radio_profile(radio, sample_rate_hz=sample_rate_hz)
    return solve_navigation(
        dem=dem,
        nmea_text=nmea,
        barometric_altitude_msl=barometric_altitude_msl,
        sample_rate_hz=sample_rate_hz,
        search_center_x_m=start_x_m + 60.0,
        search_center_y_m=start_y_m - 50.0,
        search_radius_m=300.0,
        coarse_step_m=150.0,
        fine_step_m=50.0,
        azimuth_coarse_step_deg=5.0,
        azimuth_fine_step_deg=1.0,
        speed_min_mps=max(20.0, speed_mps - 8.0),
        speed_max_mps=speed_mps + 8.0,
        speed_coarse_step_mps=4.0,
        speed_fine_step_mps=1.0,
        enable_kalman=True,
        parallel_jobs=0,
        compensate_baro_drift=True,
    )


def _angle_error_deg(a_deg: float, b_deg: float) -> float:
    return abs((float(a_deg) - float(b_deg) + 180.0) % 360.0 - 180.0)


if __name__ == "__main__":
    raise SystemExit(main())
