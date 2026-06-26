"""Matplotlib visualization helpers for demo artifacts."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .dem import DEMData
from .simulator import Trajectory


def save_trajectory_overlay(
    dem: DEMData,
    truth_trajectory: Trajectory | None,
    estimated_trajectory: dict,
    output_path: str,
) -> str:
    """Save a DEM map with truth and estimated trajectories."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    fig, ax = plt.subplots(figsize=(9, 7), constrained_layout=True)
    extent = [
        dem.origin_x_m,
        dem.origin_x_m + dem.width_m,
        dem.origin_y_m,
        dem.origin_y_m + dem.height_m,
    ]
    image = ax.imshow(dem.elevation, origin="lower", extent=extent, cmap="terrain", aspect="equal")
    fig.colorbar(image, ax=ax, label="Elevation MSL, m")

    if truth_trajectory is not None:
        ax.plot(truth_trajectory.x_m, truth_trajectory.y_m, color="white", linewidth=2.5, label="Truth")
        ax.plot(truth_trajectory.x_m[0], truth_trajectory.y_m[0], "o", color="white", markersize=6)

    start = estimated_trajectory.get("start", {})
    end = estimated_trajectory.get("end", {})
    if start and end:
        ax.plot(
            [start.get("x_m"), end.get("x_m")],
            [start.get("y_m"), end.get("y_m")],
            color="crimson",
            linewidth=2.0,
            linestyle="--",
            label="Estimated",
        )
        ax.plot(start.get("x_m"), start.get("y_m"), "o", color="crimson", markersize=6)

    ax.set_title("Terrain Overlay With Flight Track")
    ax.set_xlabel("Local X, m")
    ax.set_ylabel("Local Y, m")
    ax.legend(loc="best")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_correlation_heatmap(
    heatmap: np.ndarray,
    azimuth_values: np.ndarray,
    output_path: str,
) -> str:
    """Save a candidate score heatmap."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    image = ax.imshow(np.asarray(heatmap, dtype=float), origin="lower", aspect="auto", cmap="viridis")
    fig.colorbar(image, ax=ax, label="Combined score")
    ax.set_title("Correlation Search Heatmap")
    ax.set_xlabel("Candidate X index")
    ax.set_ylabel("Candidate Y index")
    if azimuth_values.size:
        ax.text(
            0.01,
            0.99,
            f"Azimuth bins: {azimuth_values.size}",
            transform=ax.transAxes,
            va="top",
            ha="left",
            color="white",
            fontsize=9,
        )
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_profile_comparison(
    measured_profile: np.ndarray,
    reference_profile: np.ndarray,
    output_path: str,
) -> str:
    """Save a measured-vs-reference terrain profile plot."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    measured = np.asarray(measured_profile, dtype=float)
    reference = np.asarray(reference_profile, dtype=float)
    fig, ax = plt.subplots(figsize=(9, 4.8), constrained_layout=True)
    ax.plot(measured, label="Measured terrain", linewidth=2.0)
    ax.plot(reference, label="Best DEM reference", linewidth=1.8, linestyle="--")
    ax.set_title("Measured Terrain Profile vs DEM Reference")
    ax.set_xlabel("Sample index")
    ax.set_ylabel("Elevation MSL, m")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_trajectory_comparison(
    dem: DEMData,
    truth_trajectory: Trajectory,
    dead_reckoning_trajectory: list[dict],
    terrain_lock_trajectory: list[dict],
    output_path: str,
    initial_uncertainty_radius_m: float | None = None,
) -> str:
    """Save DEM map with truth, dead reckoning and Terrain Lock estimates."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    fig, ax = plt.subplots(figsize=(9, 7), constrained_layout=True)
    extent = [
        dem.origin_x_m,
        dem.origin_x_m + dem.width_m,
        dem.origin_y_m,
        dem.origin_y_m + dem.height_m,
    ]
    image = ax.imshow(dem.elevation, origin="lower", extent=extent, cmap="terrain", aspect="equal")
    fig.colorbar(image, ax=ax, label="Elevation MSL, m")

    ax.plot(truth_trajectory.x_m, truth_trajectory.y_m, color="white", linewidth=2.4, label="Truth")
    dr_x, dr_y = _xy_from_dict_trajectory(dead_reckoning_trajectory)
    if dr_x.size:
        ax.plot(dr_x, dr_y, color="#ef4444", linewidth=1.9, linestyle="--", label="Dead Reckoning")
    tl_x, tl_y = _xy_from_dict_trajectory(terrain_lock_trajectory)
    if tl_x.size:
        ax.plot(tl_x, tl_y, color="#22d3ee", linewidth=2.0, label="BlindFlight Terrain Lock")

    ax.scatter([truth_trajectory.x_m[0]], [truth_trajectory.y_m[0]], color="white", s=34, zorder=5)
    if initial_uncertainty_radius_m is not None:
        circle = plt.Circle(
            (truth_trajectory.x_m[0], truth_trajectory.y_m[0]),
            float(initial_uncertainty_radius_m),
            color="#22d3ee",
            fill=False,
            linewidth=1.4,
            linestyle=":",
            alpha=0.85,
            label="Initial uncertainty",
        )
        ax.add_patch(circle)

    ax.set_title("Truth vs Dead Reckoning vs Terrain Lock")
    ax.set_xlabel("Local X, m")
    ax.set_ylabel("Local Y, m")
    ax.legend(loc="best")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_particle_cloud(
    dem: DEMData,
    particles_snapshot: dict,
    estimate: dict,
    output_path: str,
) -> str:
    """Save particle cloud over DEM with final estimate marker."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    extent = [
        dem.origin_x_m,
        dem.origin_x_m + dem.width_m,
        dem.origin_y_m,
        dem.origin_y_m + dem.height_m,
    ]
    ax.imshow(dem.elevation, origin="lower", extent=extent, cmap="terrain", aspect="equal", alpha=0.92)
    x = np.asarray(particles_snapshot.get("x_m", []), dtype=float)
    y = np.asarray(particles_snapshot.get("y_m", []), dtype=float)
    weights = np.asarray(particles_snapshot.get("weights", np.ones_like(x)), dtype=float)
    if x.size and y.size:
        sizes = 10.0 + 1500.0 * weights / max(float(np.max(weights)), 1e-12)
        ax.scatter(x, y, s=sizes, c=weights, cmap="viridis", alpha=0.55, edgecolors="none", label="Particles")
    ax.scatter([estimate.get("x_m")], [estimate.get("y_m")], marker="x", s=90, color="#ef4444", label="Estimate")
    ax.set_title("Particle Cloud")
    ax.set_xlabel("Local X, m")
    ax.set_ylabel("Local Y, m")
    ax.legend(loc="best")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_confidence_timeline(
    trajectory_estimates: list[dict],
    output_path: str,
) -> str:
    """Save confidence and error-radius timeline."""

    plt = _load_pyplot()
    output = _prepare_output_path(output_path)
    t_s = np.asarray([row.get("t_s", 0.0) for row in trajectory_estimates], dtype=float)
    confidence = np.asarray([row.get("confidence", 0.0) for row in trajectory_estimates], dtype=float)
    error_radius = np.asarray([row.get("error_radius_m", np.nan) for row in trajectory_estimates], dtype=float)

    fig, ax_conf = plt.subplots(figsize=(9, 4.8), constrained_layout=True)
    ax_conf.plot(t_s, confidence, color="#22d3ee", linewidth=2.0, label="Confidence")
    ax_conf.set_ylim(0.0, 1.02)
    ax_conf.set_xlabel("Time, s")
    ax_conf.set_ylabel("Confidence")
    ax_conf.grid(True, alpha=0.25)
    ax_err = ax_conf.twinx()
    ax_err.plot(t_s, error_radius, color="#ef4444", linewidth=1.6, linestyle="--", label="Error radius")
    ax_err.set_ylabel("Error radius, m")
    lines, labels = ax_conf.get_legend_handles_labels()
    lines2, labels2 = ax_err.get_legend_handles_labels()
    ax_conf.legend(lines + lines2, labels + labels2, loc="best")
    ax_conf.set_title("Navigation Confidence Timeline")
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return str(output)


def save_terrain_profile_match(
    observed_profile: np.ndarray,
    best_dem_profile: np.ndarray,
    output_path: str,
) -> str:
    """Save observed terrain profile against the best DEM profile."""

    return save_profile_comparison(observed_profile, best_dem_profile, output_path)


def _xy_from_dict_trajectory(trajectory: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    if not trajectory:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    return (
        np.asarray([row.get("x_m", np.nan) for row in trajectory], dtype=float),
        np.asarray([row.get("y_m", np.nan) for row in trajectory], dtype=float),
    )


def _load_pyplot():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise ImportError("matplotlib is required to save demo plots") from exc
    return plt


def _prepare_output_path(output_path: str) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    return output
