import numpy as np

from app.core.dem import DEMData
from app.core.particle_filter import (
    ParticleState,
    effective_sample_size,
    systematic_resample,
    update_weights_instant_height,
)


def test_height_update_prefers_particle_matching_radar_measurement() -> None:
    dem = DEMData(
        elevation=np.full((4, 4), 100.0),
        width_m=300.0,
        height_m=300.0,
        resolution_m=100.0,
    )
    particles = ParticleState(
        x_m=np.asarray([100.0, 100.0]),
        y_m=np.asarray([100.0, 100.0]),
        heading_deg=np.asarray([0.0, 0.0]),
        speed_mps=np.asarray([10.0, 10.0]),
        baro_bias_m=np.asarray([0.0, 80.0]),
        radar_bias_m=np.asarray([0.0, 0.0]),
        weights=np.asarray([0.5, 0.5]),
    )

    updated = update_weights_instant_height(
        particles,
        dem,
        barometric_altitude_msl=1000.0,
        radar_altitude_agl=900.0,
        sigma_alt_m=10.0,
    )

    assert updated.weights[0] > updated.weights[1]
    assert np.isclose(np.sum(updated.weights), 1.0)


def test_ess_and_resampling_keep_particle_count() -> None:
    weights = np.asarray([0.7, 0.2, 0.1])
    assert 1.0 < effective_sample_size(weights) < 3.0

    particles = ParticleState(
        x_m=np.asarray([0.0, 1.0, 2.0]),
        y_m=np.asarray([0.0, 1.0, 2.0]),
        heading_deg=np.asarray([0.0, 0.0, 0.0]),
        speed_mps=np.asarray([1.0, 1.0, 1.0]),
        baro_bias_m=np.zeros(3),
        radar_bias_m=np.zeros(3),
        weights=weights,
    )
    resampled = systematic_resample(particles, seed=2)

    assert resampled.size == particles.size
    assert np.allclose(resampled.weights, np.full(3, 1 / 3))
