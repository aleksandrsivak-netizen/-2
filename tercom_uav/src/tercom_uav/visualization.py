"""Matplotlib artifact generation for TERCOM runs."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tercom_uav.dem import DEMGrid
from tercom_uav.types import CorrelationResult, TerrainProfile


def plot_correlation_heatmap(result: CorrelationResult, out_dir: str | Path) -> Path:
    """Save heatmap of Pearson correlation over azimuth and shift."""

    out = Path(out_dir)
    path = out / "correlation_heatmap.png"
    fig, ax = plt.subplots(figsize=(11, 6))
    image = ax.imshow(
        result.heatmap,
        origin="lower",
        aspect="auto",
        extent=[
            float(result.shifts_m[0]),
            float(result.shifts_m[-1]),
            float(result.azimuths_deg[0]),
            float(result.azimuths_deg[-1]),
        ],
        cmap="viridis",
        vmin=-1,
        vmax=1,
    )
    ax.scatter([result.best_shift_m], [result.best_azimuth_deg], c="red", s=28, label="best")
    ax.set_xlabel("Along-track shift, m")
    ax.set_ylabel("Azimuth, deg")
    ax.set_title("TERCOM Pearson correlation heatmap")
    ax.legend(loc="upper right")
    fig.colorbar(image, ax=ax, label="Pearson r")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_dem_tracks(
    dem: DEMGrid,
    out_dir: str | Path,
    truth: pd.DataFrame | None = None,
    estimates: pd.DataFrame | None = None,
) -> Path:
    """Save DEM map with truth and estimated tracks."""

    out = Path(out_dir)
    path = out / "dem_tracks.png"
    x_min, y_min, x_max, y_max = dem.bounds_m
    fig, ax = plt.subplots(figsize=(9, 8))
    image = ax.imshow(
        dem.elevation_m,
        origin="lower",
        extent=[x_min, x_max, y_min, y_max],
        cmap="terrain",
        aspect="equal",
    )
    if truth is not None and not truth.empty:
        ax.plot(truth["x_m"], truth["y_m"], color="white", linewidth=2.0, label="truth")
        ax.plot(truth["x_m"], truth["y_m"], color="black", linewidth=0.8)
    if estimates is not None and not estimates.empty:
        ax.plot(estimates["x_m"], estimates["y_m"], color="crimson", marker="o", markersize=3, label="estimated")
    ax.set_xlabel("x east, m")
    ax.set_ylabel("y north, m")
    ax.set_title("DEM and trajectories")
    ax.legend(loc="best")
    fig.colorbar(image, ax=ax, label="Elevation MSL, m")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_terrain_profile(result: CorrelationResult, out_dir: str | Path) -> Path:
    """Save observed-vs-reference terrain profile plot."""

    out = Path(out_dir)
    path = out / "terrain_profile.png"
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(result.distances_m, result.observed_profile_m, label="observed")
    ax.plot(result.distances_m, result.best_reference_profile_m, label="best reference")
    ax.set_xlabel("Distance from window start, m")
    ax.set_ylabel("Terrain elevation MSL, m")
    ax.set_title("Observed terrain profile vs DEM reference")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_speed(
    estimates: pd.DataFrame,
    out_dir: str | Path,
    truth: pd.DataFrame | None = None,
) -> Path:
    """Save speed-over-time plot."""

    out = Path(out_dir)
    path = out / "speed.png"
    fig, ax = plt.subplots(figsize=(10, 4))
    if truth is not None and not truth.empty:
        ax.plot(truth["time_s"], truth["speed_mps"], label="truth")
    if estimates is not None and not estimates.empty:
        ax.plot(estimates["time_s"], estimates["speed_mps"], marker="o", label="estimated")
    ax.set_xlabel("Time, s")
    ax.set_ylabel("Speed, m/s")
    ax.set_title("Ground speed")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def plot_confidence(estimates: pd.DataFrame, out_dir: str | Path) -> Path:
    """Save confidence and ambiguity plot."""

    out = Path(out_dir)
    path = out / "confidence.png"
    fig, ax = plt.subplots(figsize=(10, 4))
    if estimates is not None and not estimates.empty:
        ax.plot(estimates["time_s"], estimates["confidence_score"], marker="o", label="confidence")
        ambiguous = estimates["ambiguous_match"].astype(bool)
        if ambiguous.any():
            ax.scatter(
                estimates.loc[ambiguous, "time_s"],
                estimates.loc[ambiguous, "confidence_score"],
                color="crimson",
                label="ambiguous",
            )
    ax.set_ylim(0, 1)
    ax.set_xlabel("Time, s")
    ax.set_ylabel("Score")
    ax.set_title("Confidence and ambiguity")
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def save_visual_artifacts(
    dem: DEMGrid,
    profile: TerrainProfile,
    result: CorrelationResult,
    estimates: pd.DataFrame,
    out_dir: str | Path,
    truth: pd.DataFrame | None = None,
) -> list[Path]:
    """Generate all standard run figures."""

    _ = profile
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = [
        plot_correlation_heatmap(result, out),
        plot_dem_tracks(dem, out, truth=truth, estimates=estimates),
        plot_terrain_profile(result, out),
        plot_speed(estimates, out, truth=truth),
        plot_confidence(estimates, out),
    ]
    return paths

