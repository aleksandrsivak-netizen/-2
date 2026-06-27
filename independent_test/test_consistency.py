"""
Тест интеграции «Теарком (алгоритм) <-> ГеоШтурман (визуализация)».

Папки проектов раздельные, фронтенды не тронуты. Проверяем ГОРЛЫШКО
app.core.navigation.solve_navigation, через которое визуализация получает
данные. Один и тот же DEM и NMEA подаются:
  A) solve_navigation при NAV_ENGINE=native  -> родной грид-движок;
  B) solve_navigation по умолчанию (tercom)   -> мост на ядре Теаркома.

Обе ветки возвращают объект с одинаковым контрактом (.estimated/.quality/
.heatmap/.metadata), который читает фронт. Значит дашборд показывает то же,
но считает Теарком.
"""
from __future__ import annotations

import importlib
import math
import os
import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "GeoShturman" / "backend"))

from app.core.dem import create_synthetic_dem, sample_profile          # noqa: E402
from app.core.nmea import build_gpgga_sentence                         # noqa: E402


def make_nmea(dem, sx, sy, az, speed, dur, hz, baro, noise, seed):
    rng = np.random.default_rng(seed)
    n = int(dur * hz) + 1
    t = np.arange(n) / hz
    terr = sample_profile(dem, sx, sy, az, speed, hz, n)
    radio = baro - terr + rng.normal(0, noise, size=n)
    return "\n".join(build_gpgga_sentence(float(t[i]), float(radio[i])) for i in range(n))


def solve(engine, **kw):
    os.environ["NAV_ENGINE"] = engine
    import app.core.navigation as nav
    importlib.reload(nav)  # перечитать ветку движка из окружения
    return nav.solve_navigation(**kw)


def report(tag, est, truth):
    az_err = abs((est["azimuth_deg"] - truth["az"] + 180) % 360 - 180)
    sp_err = abs(est["speed_mps"] - truth["speed"])
    pos_err = math.hypot(est["end_x_m"] - truth["end_x"], est["end_y_m"] - truth["end_y"])
    print(f"  [{tag:8}] az={est['azimuth_deg']:6.1f}° (Δ{az_err:4.1f})  "
          f"v={est['speed_mps']:5.1f} (Δ{sp_err:4.1f})  posΔ={pos_err:6.0f} м  "
          f"corr={est.get('correlation', float('nan')):.3f}  conf={est.get('confidence', float('nan')):.2f}")
    if est.get("end_lat_deg") is not None:
        print(f"             end lat/lon = {est['end_lat_deg']:.5f}, {est['end_lon_deg']:.5f}")
    return az_err, sp_err, pos_err


def main():
    dem = create_synthetic_dem(width_m=8000, height_m=8000, resolution_m=30,
                               seed=42, terrain_type="mixed",
                               origin_lat_deg=67.75, origin_lon_deg=33.70)
    az, speed, dur, hz, baro = 128.0, 40.0, 120.0, 5.0, 1500.0
    sx, sy = 4000.0, 4000.0
    nmea = make_nmea(dem, sx, sy, az, speed, dur, hz, baro, noise=2.0, seed=7)
    dist = speed * dur
    truth = {"az": az, "speed": speed,
             "end_x": sx + dist * math.sin(math.radians(az)),
             "end_y": sy + dist * math.cos(math.radians(az))}
    common = dict(dem=dem, nmea_text=nmea, barometric_altitude_msl=baro, sample_rate_hz=hz,
                  search_center_x_m=sx, search_center_y_m=sy, search_radius_m=600.0,
                  speed_min_mps=20.0, speed_max_mps=80.0)

    print(f"ИСТИНА: az={az}°, v={speed} м/с, финиш=({truth['end_x']:.0f},{truth['end_y']:.0f})")
    print(f"Один публичный вызов solve_navigation, два движка через NAV_ENGINE\n")

    print("A) NAV_ENGINE=native (родной грид-движок):")
    sol_a = solve("native", **common)
    a = report("native", sol_a.estimated, truth)

    print("\nB) NAV_ENGINE=tercom (мост на ядре Теаркома, по умолчанию):")
    sol_b = solve("tercom", **common)
    b = report("tercom", sol_b.estimated, truth)

    print("\nСОГЛАСОВАННОСТЬ (одна точка на карте):")
    daz = abs((sol_a.estimated["azimuth_deg"] - sol_b.estimated["azimuth_deg"] + 180) % 360 - 180)
    dpos = math.hypot(sol_a.estimated["end_x_m"] - sol_b.estimated["end_x_m"],
                      sol_a.estimated["end_y_m"] - sol_b.estimated["end_y_m"])
    print(f"  Δazimuth={daz:.1f}°   Δposition={dpos:.0f} м")

    print("\nКОНТРАКТ ДЛЯ ФРОНТА (мост, без правок визуализации):")
    print(f"  heatmap shape          = {sol_b.heatmap.shape}")
    print(f"  quality keys           = {sorted(sol_b.quality.keys())}")
    print(f"  engine                 = {sol_b.metadata.get('engine')}")

    ok = (a[0] <= 3 and a[2] <= 250 and b[0] <= 3 and b[2] <= 250 and daz <= 3 and dpos <= 250)
    print("\nИТОГ:", "OK — горлышко переключается, оба движка согласованы, фронт не тронут"
          if ok else "ВНИМАНИЕ — см. отклонения")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
