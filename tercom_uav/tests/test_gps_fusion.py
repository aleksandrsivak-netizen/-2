import pytest

from tercom_uav.config import GPSFusionConfig
from tercom_uav.gps import GPSFix, GPSFusionState, NavigationMode, fuse_navigation_estimate
from tercom_uav.types import NavigationEstimate


def _tercom_estimate(confidence: float = 0.8) -> NavigationEstimate:
    return NavigationEstimate(
        time_s=10.0,
        x_m=100.0,
        y_m=200.0,
        azimuth_deg=45.0,
        speed_mps=50.0,
        vx_mps=35.355,
        vy_mps=35.355,
        traveled_distance_m=500.0,
        confidence_score=confidence,
        ambiguous_match=False,
    )


def test_gps_off_keeps_tercom_only_estimate() -> None:
    tercom = _tercom_estimate()
    result = fuse_navigation_estimate(tercom, gps_fix=None)

    assert result.diagnostics["mode"] == NavigationMode.GPS_OFF_TERCOM_ONLY.value
    assert result.estimate.x_m == pytest.approx(tercom.x_m)
    assert result.estimate.y_m == pytest.approx(tercom.y_m)
    assert result.diagnostics["fusion"]["gps_weight"] == 0.0


def test_healthy_gps_assists_without_replacing_tercom() -> None:
    tercom = _tercom_estimate()
    gps = GPSFix(
        timestamp_s=10.0,
        receive_time_s=10.0,
        x_m=120.0,
        y_m=210.0,
        hdop=0.8,
        satellites=10,
        fix_quality=1,
        checksum_valid=True,
    )

    result = fuse_navigation_estimate(tercom, gps)

    assert result.diagnostics["mode"] == NavigationMode.GPS_HEALTHY_ASSISTED.value
    assert 0.0 < result.diagnostics["fusion"]["gps_weight"] < 1.0
    assert tercom.x_m < result.estimate.x_m < gps.x_m
    assert tercom.y_m < result.estimate.y_m < gps.y_m
    assert result.diagnostics["gps"]["accepted"] is True


def test_impossible_gps_jump_is_rejected_and_reacquire_starts() -> None:
    cfg = GPSFusionConfig(
        gps_max_position_jump_m=100.0,
        gps_tercom_max_disagreement_m=2000.0,
        max_uav_speed_mps=60.0,
    )
    state = GPSFusionState()
    state.evaluate(
        GPSFix(
            timestamp_s=0.0,
            receive_time_s=0.0,
            x_m=100.0,
            y_m=200.0,
            hdop=0.8,
            satellites=10,
            fix_quality=1,
            checksum_valid=True,
        ),
        cfg,
    )
    jump = GPSFix(
        timestamp_s=1.0,
        receive_time_s=1.0,
        x_m=1000.0,
        y_m=200.0,
        hdop=0.8,
        satellites=10,
        fix_quality=1,
        checksum_valid=True,
    )

    result = fuse_navigation_estimate(_tercom_estimate(), jump, cfg, state)

    assert result.diagnostics["mode"] == NavigationMode.GPS_REJECTED_REACQUIRE.value
    assert result.diagnostics["gps"]["accepted"] is False
    assert result.diagnostics["gps"]["reject_reason"] == "position_jump_exceeds_limit"
    assert result.diagnostics["tercom"]["reacquire"] is True


def test_stale_gps_is_not_used_for_position_update() -> None:
    cfg = GPSFusionConfig(gps_max_age_ms=100.0, stale_data_timeout_ms=500.0)
    tercom = _tercom_estimate()
    stale = GPSFix(
        timestamp_s=1.0,
        receive_time_s=3.0,
        x_m=120.0,
        y_m=210.0,
        hdop=0.8,
        satellites=10,
        fix_quality=1,
        checksum_valid=True,
    )

    result = fuse_navigation_estimate(tercom, stale, cfg)

    assert result.diagnostics["mode"] == NavigationMode.DATA_STALE.value
    assert result.diagnostics["gps"]["accepted"] is False
    assert result.estimate.x_m == pytest.approx(tercom.x_m)
