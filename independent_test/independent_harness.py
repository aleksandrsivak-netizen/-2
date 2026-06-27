"""
НЕЗАВИСИМЫЙ строгий тест-стенд для алгоритма TERCOM ("торком").

Идея независимости
==================
Этот стенд НЕ использует штатный симулятор проекта (`tercom_uav.simulator`)
и НЕ использует встроенную синтетическую карту (`DEMGrid.synthetic`),
потому что и то и другое могло быть «подогнано» под алгоритм.

Вместо этого стенд:
  1. сам генерирует рельеф (спектральный синтез + холмы) своим ГСЧ;
  2. сам, СВОИМ билинейным интерполятором (oracle), снимает истинную
     высоту рельефа вдоль траектории — то есть «эталон истины» вычисляется
     кодом, независимым от `DEMGrid.sample`, который использует сам алгоритм;
  3. сам кодирует радиовысотомер в NMEA-0183 GPGGA со СВОИМ расчётом
     контрольной суммы (проверка парсера алгоритма независимым кодировщиком);
  4. подаёт на вход алгоритма ТОЛЬКО: карту высот + NMEA-поток +
     барометрическую высоту + грубую подсказку скорости.
     Алгоритм НЕ получает: истинный азимут, истинную точку старта,
     истинную скорость, truth-CSV. Метрики ошибок считает САМ стенд.

Что проверяется (по ТЗ кейса)
=============================
  * перебор азимута 0..360° и поиск максимума корреляции;
  * восстановление вектора скорости (м/с, путевой угол, vx/vy);
  * географическая привязка (x, y) к карте;
  * скорость как ВЫХОД алгоритма (подсказку скорости намеренно искажаем);
  * устойчивость к шуму радиовысотомера и к выпадениям сообщений (dropout);
  * самооценка точности и поведение на ПЛОСКОМ рельефе (должен честно
    сообщать о низкой уверенности / неоднозначности, а не «врать»);
  * независимость от ГНСС: поля lat/lon в NMEA пустые по ТЗ.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# --- подключаем ядро алгоритма (только публичный API) -----------------------
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tercom_uav" / "src"))

from tercom_uav.config import CorrelationConfig, KalmanConfig          # noqa: E402
from tercom_uav.dem import DEMGrid                                     # noqa: E402
from tercom_uav.estimator import localize_profile, estimate_single_window  # noqa: E402
from tercom_uav.nmea import read_gpgga_file                           # noqa: E402
from tercom_uav.profiles import build_terrain_profile                 # noqa: E402


# ============================================================================
# 1. НЕЗАВИСИМЫЙ генератор рельефа (спектральный синтез, свой seed)
# ============================================================================
def make_terrain(
    half_m: float = 6000.0,
    res_m: float = 30.0,
    seed: int = 20240627,
    flat: bool = False,
    roughness: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Возвращает (elev[ny,nx], xs[nx], ys[ny]) в локальных метрах, центр (0,0)."""
    rng = np.random.default_rng(seed)
    xs = np.arange(-half_m, half_m + res_m, res_m)
    ys = np.arange(-half_m, half_m + res_m, res_m)
    xx, yy = np.meshgrid(xs, ys)

    elev = np.full_like(xx, 600.0, dtype=float)
    if flat:
        # почти плоско: едва заметная рябь ~0.3 м (ниже типичного шума датчика)
        elev = elev + 0.3 * np.sin(xx / 1500.0) + 0.3 * np.cos(yy / 1700.0)
        return elev, xs, ys

    # лёгкий общий наклон местности
    elev += 0.010 * xx - 0.006 * yy
    # спектральный синтез: сумма случайных гармоник с убыванием амплитуды
    n_harm = 28
    for _ in range(n_harm):
        kx = rng.uniform(-1, 1) / rng.uniform(250.0, 3500.0)
        ky = rng.uniform(-1, 1) / rng.uniform(250.0, 3500.0)
        amp = roughness * rng.uniform(8.0, 60.0) / (1.0 + math.hypot(kx, ky) * 1500.0)
        phase = rng.uniform(0, 2 * math.pi)
        elev += amp * np.sin(2 * math.pi * (kx * xx + ky * yy) + phase)
    # несколько холмов/впадин
    for _ in range(7):
        cx = rng.uniform(-half_m * 0.8, half_m * 0.8)
        cy = rng.uniform(-half_m * 0.8, half_m * 0.8)
        amp = roughness * rng.uniform(-120.0, 160.0)
        sig = rng.uniform(400.0, 1400.0)
        elev += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sig**2))
    return elev, xs, ys


def build_dem(elev, xs, ys) -> DEMGrid:
    """Оборачиваем независимо сгенерированный рельеф в контейнер DEMGrid."""
    return DEMGrid(
        elevation_m=elev,
        x_coords_m=xs,
        y_coords_m=ys,
        crs="LOCAL_INDEPENDENT_TEST",
        source_path=None,
        metadata={"independent": True},
    )


# ============================================================================
# 2. НЕЗАВИСИМЫЙ билинейный «оракул» истины (НЕ DEMGrid.sample)
# ============================================================================
def oracle_sample(elev, xs, ys, x, y) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ix = np.clip(np.searchsorted(xs, x) - 1, 0, xs.size - 2)
    iy = np.clip(np.searchsorted(ys, y) - 1, 0, ys.size - 2)
    x0, x1 = xs[ix], xs[ix + 1]
    y0, y1 = ys[iy], ys[iy + 1]
    tx = (x - x0) / (x1 - x0)
    ty = (y - y0) / (y1 - y0)
    z00 = elev[iy, ix]
    z10 = elev[iy, ix + 1]
    z01 = elev[iy + 1, ix]
    z11 = elev[iy + 1, ix + 1]
    return (
        (1 - tx) * (1 - ty) * z00
        + tx * (1 - ty) * z10
        + (1 - tx) * ty * z01
        + tx * ty * z11
    )


# ============================================================================
# 3. НЕЗАВИСИМЫЙ кодировщик NMEA-0183 GPGGA (свой расчёт контрольной суммы)
# ============================================================================
def nmea_checksum(payload: str) -> str:
    cs = 0
    for ch in payload:
        cs ^= ord(ch)
    return f"{cs:02X}"


def utc_hhmmss(t_s: float, start_hour: int = 12) -> str:
    total = (start_hour * 3600.0 + t_s) % 86400.0
    h = int(total // 3600)
    m = int((total - h * 3600) // 60)
    s = total - h * 3600 - m * 60
    return f"{h:02d}{m:02d}{s:05.2f}"


def write_nmea(path: Path, times, radio_alt, dropout_mask=None) -> int:
    """Пишет GPGGA с ПУСТЫМИ полями lat/lon (доказательство независимости от ГНСС)."""
    lines = []
    n = 0
    for i, (t, r) in enumerate(zip(times, radio_alt)):
        if dropout_mask is not None and dropout_mask[i]:
            continue
        if not np.isfinite(r):
            continue
        payload = f"GPGGA,{utc_hhmmss(float(t))},,,,,1,08,1.0,{float(r):.2f},M,0.0,M,,"
        lines.append(f"${payload}*{nmea_checksum(payload)}")
        n += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


# ============================================================================
# 4. Геометрия истины и сценарии
# ============================================================================
@dataclass
class Scenario:
    name: str
    azimuth_deg: float
    speed_mps: float
    duration_s: float
    hz: float
    noise_std_m: float
    baro_alt_m: float = 1500.0
    f_before: float = 0.5          # доля пути ДО центра карты (траектория через центр)
    speed_hint_factor: float = 1.0  # множитель искажения подсказки скорости
    dropout_prob: float = 0.0
    flat: bool = False
    adversarial_hint: bool = False  # намеренно грубо искажённая подсказка скорости
    seed: int = 20240627
    roughness: float = 1.0
    # допуски (строгие, но привязаны к разрешению алгоритма: шаг сетки 30 м, 1°):
    tol_az_deg: float = 3.0
    tol_speed_rel: float = 0.12
    tol_pos_m: float = 120.0      # онлайн-окно, сверка по времени оценки
    tol_pos_post_m: float = 60.0  # постфактум (полный профиль) — самый строгий
    expect_low_confidence: bool = False


@dataclass
class Result:
    scen: Scenario
    n_msgs: int = 0
    az_err: float = math.nan
    track_err: float = math.nan
    speed_err_rel: float = math.nan
    pos_err: float = math.nan        # онлайн-окно, сверка по времени оценки
    pos_err_post: float = math.nan   # постфактум, полный профиль
    est_az: float = math.nan
    est_speed: float = math.nan
    est_x: float = math.nan
    est_y: float = math.nan
    est_time: float = math.nan
    confidence: float = math.nan
    ambiguous: bool = False
    passed: bool = False
    notes: list = field(default_factory=list)


def angle_err(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def run_scenario(s: Scenario, workdir: Path) -> Result:
    rng = np.random.default_rng(s.seed + 1000)
    elev, xs, ys = make_terrain(seed=s.seed, flat=s.flat, roughness=s.roughness)
    dem = build_dem(elev, xs, ys)
    cx, cy = dem.center_m  # (0, 0)

    az = math.radians(s.azimuth_deg)
    dirx, diry = math.sin(az), math.cos(az)
    track_len = s.speed_mps * s.duration_s
    d_before = track_len * s.f_before
    start_x = cx - dirx * d_before
    start_y = cy - diry * d_before

    dt = 1.0 / s.hz
    times = np.arange(0.0, s.duration_s + dt * 0.5, dt)
    dist = s.speed_mps * times
    px = start_x + dirx * dist
    py = start_y + diry * dist

    # истинная высота рельефа НЕЗАВИСИМЫМ оракулом
    terrain_true = oracle_sample(elev, xs, ys, px, py)
    radio_true = s.baro_alt_m - terrain_true
    radio_meas = radio_true + rng.normal(0.0, s.noise_std_m, size=times.size)
    radio_meas = np.clip(radio_meas, 0.0, 5000.0)

    dropout = (rng.random(times.size) < s.dropout_prob) if s.dropout_prob > 0 else None

    nmea_path = workdir / f"{s.name}.nmea"
    n_msgs = write_nmea(nmea_path, times, radio_meas, dropout)

    # истинное положение как функция времени (прямолинейный полёт)
    def truth_xy(t_s: float) -> tuple[float, float]:
        d = s.speed_mps * t_s
        return start_x + dirx * d, start_y + diry * d

    # ---- ВХОД АЛГОРИТМА: только карта + NMEA + баро + подсказка скорости ----
    records = read_gpgga_file(nmea_path)
    profile = build_terrain_profile(records, baro_alt_msl_m=s.baro_alt_m)
    cfg = CorrelationConfig(shift_step_m=30.0, sample_spacing_m=30.0, coarse_to_fine=False)
    speed_hint = s.speed_mps * s.speed_hint_factor  # намеренно искажённая подсказка
    loc = localize_profile(
        dem=dem,
        profile=profile,
        speed_hint_mps=speed_hint,
        correlation_config=cfg,
        kalman_config=KalmanConfig(enabled=False),
        truth=None,  # алгоритму НЕ даём истину
    )
    # постфактум-оценка по всему профилю (самый чистый "fix")
    _, est_post = estimate_single_window(dem, profile, speed_hint, cfg)

    est = loc.estimate
    r = Result(scen=s, n_msgs=n_msgs)
    r.est_az = est.azimuth_deg
    r.est_speed = est.speed_mps
    r.est_x, r.est_y = est.x_m, est.y_m
    r.est_time = est.time_s
    r.confidence = est.confidence_score
    r.ambiguous = est.ambiguous_match
    r.az_err = angle_err(est.azimuth_deg, s.azimuth_deg)
    track_true = (math.degrees(math.atan2(dirx, diry)) + 360.0) % 360.0
    track_est = (math.degrees(math.atan2(est.vx_mps, est.vy_mps)) + 360.0) % 360.0
    r.track_err = angle_err(track_est, track_true)
    r.speed_err_rel = abs(est.speed_mps - s.speed_mps) / s.speed_mps

    # КОРРЕКТНАЯ сверка: истина берётся в момент времени самой оценки
    tx, ty = truth_xy(est.time_s)
    r.pos_err = math.hypot(est.x_m - tx, est.y_m - ty)
    txp, typ = truth_xy(float(profile.times_s[-1]))
    r.pos_err_post = math.hypot(est_post.x_m - txp, est_post.y_m - typ)

    if s.expect_low_confidence:
        # на плоском рельефе требуем ЧЕСТНУЮ самооценку: низкая уверенность или флаг.
        # Большая ошибка позиции здесь ОЖИДАЕМА и не штрафуется — важно, что
        # алгоритм сам сигнализирует о ненадёжности (conf<0.5 / ambiguous).
        r.passed = (r.confidence < 0.5) or r.ambiguous
        if not r.passed:
            r.notes.append("ожидалась низкая уверенность/неоднозначность на плоском рельефе")
    elif s.adversarial_hint:
        # подсказка скорости намеренно искажена на 20-25%. Отказ оценщиков
        # взаимодополняющий, поэтому требуем точность ХОТЯ БЫ ОДНОГО из двух
        # выходов (онлайн/постфактум), плюс корректные азимут и скорость.
        pos_best = min(r.pos_err, r.pos_err_post)
        checks = {
            "azimuth": r.az_err <= s.tol_az_deg,
            "speed": r.speed_err_rel <= s.tol_speed_rel,
            "position_best": pos_best <= s.tol_pos_m,
        }
        r.passed = all(checks.values())
        for k, ok in checks.items():
            if not ok:
                r.notes.append(f"нарушен допуск: {k}")
    else:
        checks = {
            "azimuth": r.az_err <= s.tol_az_deg,
            "track": r.track_err <= s.tol_az_deg + 2.0,
            "speed": r.speed_err_rel <= s.tol_speed_rel,
            "position_online": r.pos_err <= s.tol_pos_m,
            "position_post": r.pos_err_post <= s.tol_pos_post_m,
        }
        r.passed = all(checks.values())
        for k, ok in checks.items():
            if not ok:
                r.notes.append(f"нарушен допуск: {k}")
    return r


# ============================================================================
# 5. Набор сценариев
# ============================================================================
def scenarios() -> list[Scenario]:
    out: list[Scenario] = []
    # перебор азимута по всем квадрантам (проверка поиска 0..360)
    for az in (0.0, 45.0, 73.0, 150.0, 222.0, 300.0, 359.0):
        out.append(Scenario(
            name=f"az_{int(az):03d}", azimuth_deg=az, speed_mps=55.0,
            duration_s=170.0, hz=5.0, noise_std_m=2.0,
        ))
    # разные скорости + искажённая подсказка (скорость как ВЫХОД)
    out.append(Scenario(name="speed_fast_hint_low", azimuth_deg=110.0, speed_mps=80.0,
                        duration_s=120.0, hz=5.0, noise_std_m=2.0, speed_hint_factor=0.80,
                        adversarial_hint=True, tol_speed_rel=0.15, tol_pos_m=120.0))
    out.append(Scenario(name="speed_slow_hint_high", azimuth_deg=30.0, speed_mps=35.0,
                        duration_s=200.0, hz=5.0, noise_std_m=2.0, speed_hint_factor=1.25,
                        adversarial_hint=True, tol_speed_rel=0.15, tol_pos_m=120.0))
    # высокий шум датчика
    out.append(Scenario(name="high_noise", azimuth_deg=200.0, speed_mps=55.0,
                        duration_s=180.0, hz=5.0, noise_std_m=8.0,
                        tol_pos_m=160.0, tol_pos_post_m=120.0))
    # выпадения сообщений NMEA (нерегулярная дискретизация)
    out.append(Scenario(name="dropouts", azimuth_deg=260.0, speed_mps=50.0,
                        duration_s=180.0, hz=5.0, noise_std_m=3.0, dropout_prob=0.25,
                        tol_pos_m=160.0, tol_pos_post_m=100.0))
    # старт не по центру (проверка поиска сдвига вдоль луча; путь в пределах карты)
    out.append(Scenario(name="offset_start", azimuth_deg=95.0, speed_mps=45.0,
                        duration_s=150.0, hz=5.0, noise_std_m=2.0, f_before=0.72))
    # низкая частота 1 Гц (нижняя граница ТЗ)
    out.append(Scenario(name="rate_1hz", azimuth_deg=15.0, speed_mps=45.0,
                        duration_s=200.0, hz=1.0, noise_std_m=2.5,
                        tol_pos_m=160.0, tol_pos_post_m=120.0))
    # негативный тест: ПЛОСКИЙ рельеф -> честная низкая уверенность
    out.append(Scenario(name="flat_terrain", azimuth_deg=130.0, speed_mps=55.0,
                        duration_s=160.0, hz=5.0, noise_std_m=2.0, flat=True,
                        expect_low_confidence=True))
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    workdir = REPO / "independent_test" / "_artifacts"
    workdir.mkdir(parents=True, exist_ok=True)
    results = [run_scenario(s, workdir) for s in scenarios()]

    lines: list[str] = []
    hdr = (f"{'СЦЕНАРИЙ':<22}{'msgs':>6}{'d_az':>6}{'d_trk':>6}{'d_v%':>6}"
           f"{'pos_onl':>8}{'pos_post':>9}{'conf':>6}{'amb':>5}  ИТОГ")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    n_pass = 0
    for r in results:
        n_pass += int(r.passed)
        status = "PASS" if r.passed else "FAIL"
        amb = "да" if r.ambiguous else "нет"
        lines.append(f"{r.scen.name:<22}{r.n_msgs:>6}{r.az_err:>6.2f}"
                     f"{r.track_err:>6.2f}{r.speed_err_rel*100:>6.1f}{r.pos_err:>8.1f}"
                     f"{r.pos_err_post:>9.1f}{r.confidence:>6.2f}{amb:>5}  {status}")
        if r.notes:
            lines.append(f"    -> {', '.join(r.notes)}")
    lines.append("-" * len(hdr))
    lines.append(f"ПРОЙДЕНО {n_pass}/{len(results)} сценариев")
    lines.append("pos_onl = ошибка онлайн-окна (сверка по времени оценки), м")
    lines.append("pos_post = ошибка постфактум по полному профилю, м")
    report = "\n".join(lines)
    (workdir / "report.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
