import numpy as np

from app.core.particle_filter import ParticleState, predict_particles


def test_prediction_moves_particles_east_for_heading_90() -> None:
    particles = ParticleState(
        x_m=np.zeros(3),
        y_m=np.zeros(3),
        heading_deg=np.full(3, 90.0),
        speed_mps=np.full(3, 10.0),
        baro_bias_m=np.zeros(3),
        radar_bias_m=np.zeros(3),
        weights=np.full(3, 1 / 3),
    )

    predicted = predict_particles(
        particles,
        dt_s=1.0,
        measured_speed_mps=10.0,
        measured_heading_deg=90.0,
        speed_noise_std_mps=0.0,
        heading_noise_std_deg=0.0,
        position_noise_std_m=0.0,
        seed=1,
    )

    assert np.allclose(predicted.x_m, 10.0)
    assert np.allclose(predicted.y_m, 0.0, atol=1e-9)
    assert np.allclose(predicted.heading_deg, 90.0)
