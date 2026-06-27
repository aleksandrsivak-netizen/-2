"""
Независимый CLI поверх алгоритма TERCOM — два режима работы:

  1) АВТОНОМНЫЙ (`auto`): стенд сам синтезирует поток радиовысотомера (NMEA)
     по заданным курсу/скорости, после чего алгоритм, НЕ зная истины,
     определяет где он и куда летит. Истина печатается отдельно — только
     для сравнения.

  2) РУЧНОЙ (`manual`): на вход подаётся ГОТОВЫЙ NMEA-файл (как с реального
     борта) + карта высот + барометрическая высота. Алгоритм выдаёт
     координаты, путевой угол и путевую скорость.

Пример:
  python geonav_cli.py auto   --azimuth 73 --speed 55 --duration 150
  python geonav_cli.py manual --nmea board.nmea --baro 1500 --speed-hint 50
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

from independent_harness import (   # переиспользуем независимые генераторы
    REPO, make_terrain, build_dem, oracle_sample, write_nmea,
)

sys.path.insert(0, str(REPO / "tercom_uav" / "src"))
from tercom_uav.config import CorrelationConfig, KalmanConfig   # noqa: E402
from tercom_uav.dem import DEMGrid                              # noqa: E402
from tercom_uav.estimator import localize_profile              # noqa: E402
from tercom_uav.nmea import read_gpgga_file                    # noqa: E402
from tercom_uav.profiles import build_terrain_profile          # noqa: E402


def _localize(dem: DEMGrid, nmea_path: Path, baro: float, speed_hint: float):
    records = read_gpgga_file(nmea_path)
    profile = build_terrain_profile(records, baro_alt_msl_m=baro)
    cfg = CorrelationConfig(shift_step_m=30.0, sample_spacing_m=30.0, coarse_to_fine=False)
    return localize_profile(
        dem=dem, profile=profile, speed_hint_mps=speed_hint,
        correlation_config=cfg, kalman_config=KalmanConfig(enabled=False), truth=None,
    )


def _print_fix(loc) -> None:
    e = loc.estimate
    track = (math.degrees(math.atan2(e.vx_mps, e.vy_mps)) + 360.0) % 360.0
    print("=== ОПРЕДЕЛЁННОЕ СОСТОЯНИЕ (выход алгоритма) ===")
    print(f"  координаты x,y, м : ({e.x_m:+.1f}, {e.y_m:+.1f})")
    print(f"  путевой угол, °   : {track:.1f}  (азимут совпадения {e.azimuth_deg:.1f})")
    print(f"  путевая скорость  : {e.speed_mps:.2f} м/с  ({e.speed_mps*3.6:.1f} км/ч)")
    print(f"  вектор vx,vy, м/с : ({e.vx_mps:+.2f}, {e.vy_mps:+.2f})")
    print(f"  пройдено, м       : {e.traveled_distance_m:.0f}")
    print(f"  уверенность       : {e.confidence_score:.2f}  | неоднозначно: {e.ambiguous_match}")


def cmd_auto(a) -> int:
    work = REPO / "independent_test" / "_artifacts"
    work.mkdir(parents=True, exist_ok=True)
    elev, xs, ys = make_terrain(seed=a.seed)
    dem = build_dem(elev, xs, ys)
    cx, cy = dem.center_m

    az = math.radians(a.azimuth)
    dirx, diry = math.sin(az), math.cos(az)
    track_len = a.speed * a.duration
    sx, sy = cx - dirx * track_len * 0.5, cy - diry * track_len * 0.5
    dt = 1.0 / a.hz
    t = np.arange(0.0, a.duration + dt * 0.5, dt)
    px, py = sx + dirx * a.speed * t, sy + diry * a.speed * t
    terrain = oracle_sample(elev, xs, ys, px, py)
    rng = np.random.default_rng(a.seed + 7)
    radio = a.baro - terrain + rng.normal(0.0, a.noise, size=t.size)
    nmea = work / "auto_board.nmea"
    n = write_nmea(nmea, t, radio)

    print(f"[АВТОНОМНО] синтезировано {n} сообщений NMEA -> {nmea.name}")
    print(f"[ИСТИНА для сверки] азимут {a.azimuth:.1f}°, скорость {a.speed:.1f} м/с, "
          f"финиш x,y=({sx+dirx*track_len:+.1f}, {sy+diry*track_len:+.1f})")
    loc = _localize(dem, nmea, a.baro, a.speed * a.hint_factor)
    _print_fix(loc)
    e = loc.estimate
    pos_err = math.hypot(e.x_m - (sx + dirx * track_len), e.y_m - (sy + diry * track_len))
    print(f"[СВЕРКА] ошибка позиции {pos_err:.1f} м, "
          f"ошибка скорости {abs(e.speed_mps - a.speed):.2f} м/с")
    return 0


def cmd_manual(a) -> int:
    # карта: GeoTIFF, если задан, иначе независимая синтетическая (тот же seed!)
    if a.dem:
        dem = DEMGrid.from_geotiff(a.dem)
    else:
        elev, xs, ys = make_terrain(seed=a.seed)
        dem = build_dem(elev, xs, ys)
    loc = _localize(dem, Path(a.nmea), a.baro, a.speed_hint)
    print(f"[РУЧНОЙ ВВОД] файл {a.nmea}")
    _print_fix(loc)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Геонавигация по рельефу (TERCOM) — авто/ручной режим.")
    sub = p.add_subparsers(dest="mode", required=True)

    pa = sub.add_parser("auto", help="автономный режим: синтез датчика + локализация")
    pa.add_argument("--azimuth", type=float, default=73.0)
    pa.add_argument("--speed", type=float, default=55.0)
    pa.add_argument("--duration", type=float, default=150.0)
    pa.add_argument("--hz", type=float, default=5.0)
    pa.add_argument("--baro", type=float, default=1500.0)
    pa.add_argument("--noise", type=float, default=2.0)
    pa.add_argument("--hint-factor", type=float, default=1.0, dest="hint_factor")
    pa.add_argument("--seed", type=int, default=20240627)
    pa.set_defaults(func=cmd_auto)

    pm = sub.add_parser("manual", help="ручной режим: локализация по готовому NMEA")
    pm.add_argument("--nmea", required=True)
    pm.add_argument("--dem", default=None, help="GeoTIFF DEM (опц.); иначе синтетическая карта")
    pm.add_argument("--baro", type=float, default=1500.0)
    pm.add_argument("--speed-hint", type=float, default=55.0, dest="speed_hint")
    pm.add_argument("--seed", type=int, default=20240627)
    pm.set_defaults(func=cmd_manual)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
