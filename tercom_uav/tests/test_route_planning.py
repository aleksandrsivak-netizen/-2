import pytest

from tercom_uav.dem import DEMGrid
from tercom_uav.nmea import parse_gpgga
from tercom_uav.route_planning import build_simple_route, build_waypoint_route, generate_nmea_from_truth


def test_simple_route_truth_columns_and_altitudes() -> None:
    dem = DEMGrid.synthetic(width_m=6000.0, height_m=6000.0, resolution_m=30.0)
    result = build_simple_route(
        dem=dem,
        x0_m=-1200.0,
        y0_m=-1200.0,
        heading_deg=45.0,
        speed_mps=40.0,
        duration_s=20.0,
        hz=5.0,
        baro_alt_msl=1500.0,
    )

    required = {
        "t",
        "time_s",
        "x",
        "y",
        "x_m",
        "y_m",
        "z_dem",
        "baro_altitude",
        "baro_alt_msl_m",
        "radar_altitude",
        "true_radio_alt_agl_m",
        "heading_deg",
        "speed_mps",
    }
    assert required.issubset(result.truth.columns)
    first = result.truth.iloc[0]
    assert first["radar_altitude"] == pytest.approx(first["baro_altitude"] - first["z_dem"])
    assert result.summary["length_m"] == pytest.approx(800.0)
    assert result.summary["duration_s"] == pytest.approx(20.0)


def test_route_outside_dem_raises_clear_error() -> None:
    dem = DEMGrid.synthetic(width_m=3000.0, height_m=3000.0, resolution_m=30.0)
    with pytest.raises(ValueError, match="DEM bounds"):
        build_simple_route(
            dem=dem,
            x0_m=1400.0,
            y0_m=1400.0,
            heading_deg=45.0,
            speed_mps=80.0,
            duration_s=20.0,
            hz=5.0,
            baro_alt_msl=1500.0,
        )


def test_waypoint_route_generates_resampled_gpgga_radio_altitude() -> None:
    dem = DEMGrid.synthetic(width_m=6000.0, height_m=6000.0, resolution_m=30.0)
    result = build_waypoint_route(
        dem=dem,
        waypoint_text="-1200,-900\n-300,-200\n900,800",
        speed_mps=50.0,
        hz=5.0,
        baro_alt_msl=1500.0,
    )

    lines, telemetry = generate_nmea_from_truth(result.truth, noise_std_m=0.0, target_hz=2.0)

    expected_count = int(result.summary["duration_s"] * 2.0) + 1
    assert len(lines) == expected_count
    assert len(telemetry) == expected_count
    first_record = parse_gpgga(lines[0])
    assert first_record.radio_alt_m == pytest.approx(result.truth.iloc[0]["radar_altitude"], abs=0.01)
