"""Command line interface for the TERCOM UAV prototype."""

from __future__ import annotations

import json
import logging
from html import escape
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import typer

from tercom_uav.config import CorrelationConfig, KalmanConfig, SimulationConfig, ensure_output_dir
from tercom_uav.dem import DEMGrid
from tercom_uav.estimator import localize_profile
from tercom_uav.nmea import read_gpgga_file
from tercom_uav.profiles import build_terrain_profile, save_profile_csv
from tercom_uav.simulator import simulate_flight
from tercom_uav.visualization import save_visual_artifacts


app = typer.Typer(help="TERCOM UAV terrain-referenced navigation prototype.")
logger = logging.getLogger("tercom_uav")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable.")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")


def _format_report_value(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return escape(str(value))


def _table_rows(payload: dict) -> str:
    return "\n".join(
        f"<tr><th>{escape(str(key))}</th><td>{_format_report_value(value)}</td></tr>"
        for key, value in payload.items()
        if not isinstance(value, (dict, list))
    )


def _write_html_report(out_dir: Path, summary: dict, figures: list[Path]) -> Path:
    report_path = out_dir / "report.html"
    figure_html = "\n".join(
        f'<figure><img src="{escape(path.name)}" alt="{escape(path.stem)}"><figcaption>{escape(path.name)}</figcaption></figure>'
        for path in figures
    )
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>TERCOM UAV report</title>
  <style>
    body {{ margin: 0; background: #eef2f6; color: #17202a; font-family: Inter, system-ui, sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    header, section {{ margin-bottom: 18px; padding: 18px; border: 1px solid #d8e0e8; border-radius: 10px; background: #fff; }}
    h1, h2 {{ margin: 0 0 12px; }}
    p {{ margin: 0; color: #65717e; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid #e4e9ee; text-align: left; }}
    th {{ width: 260px; color: #65717e; font-weight: 600; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    figure {{ margin: 0; padding: 12px; border: 1px solid #e4e9ee; border-radius: 8px; background: #f8fafc; }}
    img {{ display: block; width: 100%; height: auto; border-radius: 6px; }}
    figcaption {{ margin-top: 8px; color: #65717e; font-size: 13px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>TERCOM UAV report</h1>
      <p>Автоматический отчёт по запуску {escape(out_dir.name)}</p>
    </header>
    <section>
      <h2>Итоговая оценка</h2>
      <table>{_table_rows(summary.get("estimate", {}))}</table>
    </section>
    <section>
      <h2>Метрики качества</h2>
      <table>{_table_rows(summary.get("metrics", {}))}</table>
    </section>
    <section>
      <h2>Корреляция</h2>
      <table>{_table_rows(summary.get("correlation", {}))}</table>
    </section>
    <section>
      <h2>Графики</h2>
      <div class="grid">{figure_html}</div>
    </section>
  </main>
</body>
</html>
"""
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _timestamped_output(base: Optional[Path], prefix: str) -> Path:
    if base is not None:
        return ensure_output_dir(base)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ensure_output_dir(Path("outputs") / f"{prefix}_{stamp}")


def _load_dem(dem_path: Optional[Path], synthetic_flat: bool = False) -> DEMGrid:
    if dem_path is None:
        logger.info("No DEM path provided; using synthetic DEM.")
        return DEMGrid.synthetic(flat=synthetic_flat)
    if not dem_path.exists():
        raise FileNotFoundError(f"DEM file does not exist: {dem_path}")
    logger.info("Loading DEM: %s", dem_path)
    return DEMGrid.from_geotiff(dem_path)


def _correlation_config(dem: DEMGrid, shift_step_m: float, coarse_to_fine: bool) -> CorrelationConfig:
    resolution_x, resolution_y = dem.resolution_m
    sample_spacing = max(10.0, float(np.median([resolution_x, resolution_y])))
    return CorrelationConfig(
        shift_step_m=shift_step_m,
        sample_spacing_m=sample_spacing,
        coarse_to_fine=coarse_to_fine,
    )


def _save_run_artifacts(
    out_dir: Path,
    dem: DEMGrid,
    profile,
    localization,
    truth: pd.DataFrame | None,
    config_payload: dict,
) -> None:
    save_profile_csv(profile, out_dir / "observed_profile.csv")
    localization.estimates.to_csv(out_dir / "estimates.csv", index=False)
    np.save(out_dir / "correlation_heatmap.npy", localization.correlation.heatmap)
    np.savetxt(out_dir / "correlation_heatmap.csv", localization.correlation.heatmap, delimiter=",")
    figures = save_visual_artifacts(
        dem=dem,
        profile=profile,
        result=localization.correlation,
        estimates=localization.estimates,
        out_dir=out_dir,
        truth=truth,
    )
    summary = {
        "config": config_payload,
        "dem": {
            "source_path": dem.source_path,
            "bounds_m": dem.bounds_m,
            "resolution_m": dem.resolution_m,
            "metadata": dem.metadata,
        },
        "estimate": localization.estimate.to_dict(),
        "correlation": localization.correlation.to_summary(),
        "metrics": localization.metrics.to_dict(),
        "artifacts": [str(path) for path in figures],
    }
    report_path = _write_html_report(out_dir, summary, figures)
    summary["artifacts"].append(str(report_path))
    _write_json(out_dir / "summary.json", summary)


@app.command()
def demo(
    dem: Optional[Path] = typer.Option(None, "--dem", help="Path to GeoTIFF DEM. Synthetic DEM is used when omitted."),
    baro_alt: float = typer.Option(1500.0, "--baro-alt", help="Barometric altitude AMSL, m."),
    speed: float = typer.Option(55.0, "--speed", help="True simulator speed, m/s."),
    speed_hint: Optional[float] = typer.Option(
        None,
        "--speed-hint",
        help="Initial speed hypothesis fed to the localizer, m/s. Defaults to --speed; "
        "set it deliberately wrong to test that correlate_with_speed_search recovers "
        "the true speed instead of trusting this value blindly.",
    ),
    heading: float = typer.Option(73.0, "--heading", help="True simulator heading, deg clockwise from north."),
    duration: float = typer.Option(180.0, "--duration", help="Scenario duration, s."),
    hz: float = typer.Option(5.0, "--hz", help="NMEA message rate, Hz."),
    noise_std: float = typer.Option(2.5, "--noise-std", help="Radio-altimeter Gaussian noise std, m."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output run directory."),
    use_kalman: bool = typer.Option(False, "--use-kalman", help="Enable alpha-beta smoothing."),
    coarse_to_fine: bool = typer.Option(False, "--coarse-to-fine/--full-grid", help="Use full grid (slow, default, reliable) or coarse-to-fine search (~45x faster but can lock onto the wrong azimuth on smooth terrain - validate before using on real flights)."),
    shift_step: float = typer.Option(30.0, "--shift-step", help="Along-track shift step, m."),
    max_speed: float = typer.Option(120.0, "--max-speed", help="Reject window-to-window fixes implying speed above this, m/s; fall back to dead reckoning."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Run simulation, localization, metrics and visualization."""

    _setup_logging(verbose)
    out_dir = _timestamped_output(out, "demo")
    dem_grid = _load_dem(dem)
    sim_config = SimulationConfig(
        dem_path=str(dem) if dem else None,
        baro_alt_msl=baro_alt,
        speed_mps=speed,
        heading_deg=heading,
        duration_s=duration,
        hz=hz,
        noise_std_m=noise_std,
    )
    correlation_config = _correlation_config(dem_grid, shift_step, coarse_to_fine)
    kalman_config = KalmanConfig(enabled=use_kalman)

    logger.info("Simulating flight.")
    simulation = simulate_flight(dem_grid, sim_config)
    simulation.export_nmea(out_dir / "telemetry.nmea")
    simulation.export_truth(out_dir / "truth.csv")
    simulation.export_telemetry(out_dir / "telemetry.csv")

    records = read_gpgga_file(out_dir / "telemetry.nmea")
    profile = build_terrain_profile(records, baro_alt_msl_m=baro_alt)
    logger.info("Running localization.")
    localization = localize_profile(
        dem=dem_grid,
        profile=profile,
        speed_hint_mps=speed_hint if speed_hint is not None else speed,
        correlation_config=correlation_config,
        kalman_config=kalman_config,
        truth=simulation.truth,
        max_speed_mps=max_speed,
    )
    config_payload = {
        "simulation": sim_config.to_dict(),
        "correlation": correlation_config.to_dict(),
        "kalman": kalman_config.to_dict(),
    }
    _write_json(out_dir / "config.json", config_payload)
    _save_run_artifacts(out_dir, dem_grid, profile, localization, simulation.truth, config_payload)
    typer.echo(f"Demo run saved to {out_dir}")
    typer.echo(json.dumps(localization.metrics.to_dict(), indent=2, ensure_ascii=False, default=_json_default))


@app.command()
def simulate(
    dem: Optional[Path] = typer.Option(None, "--dem", help="Path to GeoTIFF DEM. Synthetic DEM is used when omitted."),
    baro_alt: float = typer.Option(1500.0, "--baro-alt", help="Barometric altitude AMSL, m."),
    speed: float = typer.Option(55.0, "--speed", help="True simulator speed, m/s."),
    heading: float = typer.Option(73.0, "--heading", help="True simulator heading, deg."),
    duration: float = typer.Option(180.0, "--duration", help="Scenario duration, s."),
    hz: float = typer.Option(5.0, "--hz", help="NMEA message rate, Hz."),
    noise_std: float = typer.Option(2.5, "--noise-std", help="Radio-altimeter Gaussian noise std, m."),
    outlier_prob: float = typer.Option(0.0, "--outlier-prob", help="Outlier probability per sample."),
    dropout_prob: float = typer.Option(0.0, "--dropout-prob", help="Dropout probability per sample."),
    drift_mps: float = typer.Option(0.0, "--drift-mps", help="Slow barometric drift, m/s."),
    export_nmea: Optional[Path] = typer.Option(None, "--export-nmea", help="Output NMEA file."),
    export_truth: Optional[Path] = typer.Option(None, "--export-truth", help="Output truth CSV."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output run directory."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Run only the flight and radio-altimeter simulator."""

    _setup_logging(verbose)
    out_dir = _timestamped_output(out, "simulate") if out or not (export_nmea and export_truth) else None
    dem_grid = _load_dem(dem)
    sim_config = SimulationConfig(
        dem_path=str(dem) if dem else None,
        baro_alt_msl=baro_alt,
        speed_mps=speed,
        heading_deg=heading,
        duration_s=duration,
        hz=hz,
        noise_std_m=noise_std,
        outlier_prob=outlier_prob,
        dropout_prob=dropout_prob,
        drift_mps=drift_mps,
    )
    simulation = simulate_flight(dem_grid, sim_config)
    nmea_path = export_nmea or (out_dir / "telemetry.nmea" if out_dir else Path("telemetry.nmea"))
    truth_path = export_truth or (out_dir / "truth.csv" if out_dir else Path("truth.csv"))
    telemetry_path = out_dir / "telemetry.csv" if out_dir else Path("telemetry.csv")
    nmea_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    simulation.export_nmea(nmea_path)
    simulation.export_truth(truth_path)
    simulation.export_telemetry(telemetry_path)
    if out_dir:
        _write_json(out_dir / "config.json", {"simulation": sim_config.to_dict()})
    typer.echo(f"NMEA saved to {nmea_path}")
    typer.echo(f"Truth saved to {truth_path}")


@app.command()
def localize(
    nmea: Path = typer.Option(..., "--nmea", help="Input NMEA file with GPGGA radio-altimeter records."),
    dem: Optional[Path] = typer.Option(None, "--dem", help="Path to GeoTIFF DEM. Synthetic DEM is used when omitted."),
    baro_alt: float = typer.Option(1500.0, "--baro-alt", help="Barometric altitude AMSL, m."),
    out: Optional[Path] = typer.Option(None, "--out", help="Output run directory."),
    speed_hint: float = typer.Option(55.0, "--speed-hint", help="Initial speed hypothesis, m/s."),
    truth: Optional[Path] = typer.Option(None, "--truth", help="Optional simulator truth CSV for metrics."),
    use_kalman: bool = typer.Option(False, "--use-kalman", help="Enable alpha-beta smoothing."),
    coarse_to_fine: bool = typer.Option(False, "--coarse-to-fine/--full-grid", help="Use full grid (slow, default, reliable) or coarse-to-fine search (~45x faster but can lock onto the wrong azimuth on smooth terrain - validate before using on real flights)."),
    shift_step: float = typer.Option(30.0, "--shift-step", help="Along-track shift step, m."),
    max_speed: float = typer.Option(120.0, "--max-speed", help="Reject window-to-window fixes implying speed above this, m/s; fall back to dead reckoning."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Run localization over an existing NMEA stream."""

    _setup_logging(verbose)
    out_dir = _timestamped_output(out, "localize")
    dem_grid = _load_dem(dem)
    records = read_gpgga_file(nmea)
    profile = build_terrain_profile(records, baro_alt_msl_m=baro_alt)
    truth_frame = pd.read_csv(truth) if truth else None
    correlation_config = _correlation_config(dem_grid, shift_step, coarse_to_fine)
    kalman_config = KalmanConfig(enabled=use_kalman)
    localization = localize_profile(
        dem=dem_grid,
        profile=profile,
        speed_hint_mps=speed_hint,
        correlation_config=correlation_config,
        kalman_config=kalman_config,
        truth=truth_frame,
        max_speed_mps=max_speed,
    )
    config_payload = {
        "input": {"nmea": str(nmea), "dem": str(dem) if dem else None, "truth": str(truth) if truth else None},
        "baro_alt_msl": baro_alt,
        "speed_hint_mps": speed_hint,
        "correlation": correlation_config.to_dict(),
        "kalman": kalman_config.to_dict(),
    }
    _write_json(out_dir / "config.json", config_payload)
    _save_run_artifacts(out_dir, dem_grid, profile, localization, truth_frame, config_payload)
    typer.echo(f"Localization run saved to {out_dir}")
    typer.echo(json.dumps(localization.estimate.to_dict(), indent=2, ensure_ascii=False, default=_json_default))


@app.command()
def report(
    run: Path = typer.Option(..., "--run", help="Run directory created by demo/localize."),
) -> None:
    """Print a compact report for a finished run."""

    summary_path = run / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"summary.json not found in {run}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    typer.echo(f"Run: {run}")
    typer.echo("Estimate:")
    typer.echo(json.dumps(summary.get("estimate", {}), indent=2, ensure_ascii=False))
    typer.echo("Metrics:")
    typer.echo(json.dumps(summary.get("metrics", {}), indent=2, ensure_ascii=False))
    typer.echo("Artifacts:")
    for artifact in summary.get("artifacts", []):
        typer.echo(f"  {artifact}")


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", "--host", help="Dashboard bind host."),
    port: int = typer.Option(8765, "--port", help="Dashboard bind port."),
) -> None:
    """Start the local web dashboard."""

    from tercom_uav.webapp import serve

    typer.echo(f"Dashboard: http://{host}:{port}")
    serve(host=host, port=port)


if __name__ == "__main__":
    app()
