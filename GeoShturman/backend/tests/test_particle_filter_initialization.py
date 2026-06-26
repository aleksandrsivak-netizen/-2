import numpy as np

from app.core.particle_filter import initialize_particles


def test_particles_initialize_inside_radius_with_uniform_weights() -> None:
    particles = initialize_particles(
        n_particles=1000,
        center_x_m=500.0,
        center_y_m=600.0,
        radius_m=200.0,
        heading_deg=70.0,
        heading_std_deg=5.0,
        speed_mps=18.0,
        speed_std_mps=2.0,
        seed=7,
    )

    distances = np.hypot(particles.x_m - 500.0, particles.y_m - 600.0)
    assert np.max(distances) <= 200.0 + 1e-9
    assert particles.weights.shape == (1000,)
    assert np.isclose(np.sum(particles.weights), 1.0)
    assert np.all(particles.speed_mps > 0.0)
