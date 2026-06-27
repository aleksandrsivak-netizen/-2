import numpy as np

from tercom_uav.profiles import build_terrain_profile, resample_by_distance
from tercom_uav.types import GGARecord


def test_build_terrain_profile_from_radio_altitude() -> None:
    records = [
        GGARecord(raw="", utc_seconds=0.0, radio_alt_m=500.0, checksum=None, checksum_valid=True),
        GGARecord(raw="", utc_seconds=1.0, radio_alt_m=525.0, checksum=None, checksum_valid=True),
    ]
    profile = build_terrain_profile(records, baro_alt_msl_m=1500.0)
    assert np.allclose(profile.terrain_msl_m, [1000.0, 975.0])
    assert np.allclose(profile.times_s, [0.0, 1.0])


def test_resample_profile_by_distance() -> None:
    records = [
        GGARecord(raw="", utc_seconds=0.0, radio_alt_m=500.0, checksum=None, checksum_valid=True),
        GGARecord(raw="", utc_seconds=2.0, radio_alt_m=700.0, checksum=None, checksum_valid=True),
    ]
    profile = build_terrain_profile(records, baro_alt_msl_m=1500.0)
    distances, terrain = resample_by_distance(profile, speed_mps=10.0, spacing_m=5.0)
    assert np.allclose(distances, [0.0, 5.0, 10.0, 15.0, 20.0])
    assert terrain[0] == 1000.0
    assert terrain[-1] == 800.0

