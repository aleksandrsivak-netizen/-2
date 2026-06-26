"""Navigation core package.

The package contains pure Python algorithm modules that can be imported by
API, CLI, or tests without starting a web framework.
"""

from .dem import (
    DEMData,
    create_synthetic_dem,
    dem_xy_to_geodetic,
    geodetic_to_dem_xy,
    is_inside_dem,
    is_inside_dem_geodetic,
    load_dem,
    sample_dem,
    sample_dem_geodetic,
    sample_profile,
)
from .geodesy import GeoPoint, GeoReference
from .navigation import NavigationSolution, run_autonomous_navigation_algorithm, solve_navigation
from .particle_filter import (
    ParticleState,
    effective_sample_size,
    estimate_state,
    initialize_particles,
    predict_particles,
    systematic_resample,
    update_weights_instant_height,
)

__all__ = [
    "DEMData",
    "GeoPoint",
    "GeoReference",
    "NavigationSolution",
    "ParticleState",
    "create_synthetic_dem",
    "dem_xy_to_geodetic",
    "effective_sample_size",
    "estimate_state",
    "geodetic_to_dem_xy",
    "initialize_particles",
    "is_inside_dem",
    "is_inside_dem_geodetic",
    "load_dem",
    "predict_particles",
    "run_autonomous_navigation_algorithm",
    "sample_dem",
    "sample_dem_geodetic",
    "sample_profile",
    "solve_navigation",
    "systematic_resample",
    "update_weights_instant_height",
]
