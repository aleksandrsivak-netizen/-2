"""
Реальный поток данных навигации (real-time).

Кейсодатель подаёт строки NMEA-0183 ($GPGGA) живым потоком. Этот модуль:
  * принимает поток (WebSocket /api/stream/ingest или HTTP POST /api/stream/ingest);
  * на каждый валидный замер обновляет телеметрию и периодически пересчитывает
    решение ядром (azimuth/speed/position/confidence);
  * транслирует обновления всем подключённым дашбордам по WebSocket /api/stream/live;
  * умеет генерировать демонстрационный поток (/api/stream/simulate) — чтобы
    показать работу в реальном времени без внешнего источника.

Дашборд при этом обновляет позицию «на лету».
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

# Геопривязка синтетической DEM (как в pipeline.solve_navigation_from_nmea)
ORIGIN_LAT = 56.10
ORIGIN_LON = 37.20

# Параметры окна/решателя
MAX_WINDOW = 240          # сколько последних замеров держим для решения
MIN_SAMPLES_TO_SOLVE = 16  # минимум замеров для запуска ядра
SOLVE_EVERY_S = 2.5        # как часто пересчитывать решение (по времени потока)


def _meters_to_latlon(x_m: float, y_m: float) -> tuple[float, float]:
    lat = ORIGIN_LAT + y_m / 111_320.0
    lon = ORIGIN_LON + x_m / (111_320.0 * math.cos(math.radians(ORIGIN_LAT)))
    return round(lat, 6), round(lon, 6)


class StreamHub:
    """Состояние потока + список подписчиков-дашбордов."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.samples: list[dict[str, Any]] = []   # {t_s, radio_agl, terrain_msl, raw}
        self.baro_msl: float = 1500.0
        # --- предфильтрация входного потока ---
        self.raw_window: list[float] = []   # окно сырых значений радиовысоты для Хампеля
        self.outliers_rejected: int = 0     # сколько выбросов отброшено
        # --- эталон (для оценки точности в симуляции) и метрики ---
        self.truth_xy: list[tuple[float, float]] | None = None
        self.truth_az: float | None = None
        self.truth_speed: float | None = None
        self.pos_errors: list[float] = []   # ошибки позиции по решениям, м
        self.prev_solution: dict[str, Any] | None = None
        self.started_at: float | None = None
        self.last_solve_at: float = 0.0
        self.solving: bool = False
        self.last_solution: dict[str, Any] | None = None
        self.sim_task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    # ------------------------- подписчики -------------------------
    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        await self._send(ws, {"type": "hello", "n_valid": len([s for s in self.samples if s]),
                              "baro_msl": self.baro_msl,
                              "last_solution": self.last_solution})

    def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def _send(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            self.clients.discard(ws)

    async def broadcast(self, msg: dict[str, Any]) -> None:
        for ws in list(self.clients):
            await self._send(ws, msg)

    # ------------------------- приём данных -------------------------
    async def reset(self) -> None:
        self.samples.clear()
        self.raw_window.clear()
        self.outliers_rejected = 0
        self.truth_xy = None
        self.truth_az = None
        self.truth_speed = None
        self.pos_errors.clear()
        self.prev_solution = None
        self.started_at = None
        self.last_solution = None
        self.last_solve_at = 0.0
        await self.broadcast({"type": "reset"})

    async def ingest_text(self, text: str, baro_msl: float | None = None) -> dict[str, int]:
        if baro_msl is not None:
            self.baro_msl = float(baro_msl)
        # ленивый импорт ядра парсинга
        from app.services.pipeline import parse_nmea_text

        measurements = parse_nmea_text(text)
        ingested = 0
        valid = 0
        for m in measurements:
            ingested += 1
            if not m.get("valid"):
                continue
            valid += 1
            await self._add_sample(m)
        await self._maybe_solve()
        return {"ingested": ingested, "valid": valid, "total_valid": len(self.samples)}

    def _hampel(self, value: float, k: float = 3.0, win: int = 7) -> tuple[float, bool]:
        """Хампель-фильтр выбросов: робастная медиана + MAD по скользящему окну.

        Возвращает (значение, выброс?). Выброс заменяется медианой окна — чтобы
        битые строки/скачки радиовысотомера не портили корреляцию и оценку.
        """
        self.raw_window.append(value)
        if len(self.raw_window) > win:
            self.raw_window = self.raw_window[-win:]
        if len(self.raw_window) < 5:
            return value, False
        import numpy as _np
        arr = _np.asarray(self.raw_window, dtype=float)
        med = float(_np.median(arr))
        mad = float(_np.median(_np.abs(arr - med))) * 1.4826
        if mad <= 1e-6:
            return value, False
        if abs(value - med) > k * mad:
            return med, True   # выброс → подменяем робастной медианой
        return value, False

    async def _add_sample(self, m: dict[str, Any]) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()
        raw_radio = float(m["radio_altitude_agl_m"])
        radio, is_outlier = self._hampel(raw_radio)
        if is_outlier:
            self.outliers_rejected += 1
        terrain = self.baro_msl - radio
        t_s = m.get("timestamp_s")
        sample = {"t_s": t_s, "radio_agl": radio, "terrain_msl": terrain, "raw": m["raw"]}
        self.samples.append(sample)
        if len(self.samples) > MAX_WINDOW * 2:
            self.samples = self.samples[-MAX_WINDOW:]

        elapsed = time.monotonic() - self.started_at
        await self.broadcast({
            "type": "telemetry",
            "n_valid": len(self.samples),
            "radio_agl": round(radio, 1),
            "terrain_msl": round(terrain, 1),
            "baro_msl": round(self.baro_msl, 1),
            "elapsed_s": round(elapsed, 1),
            "raw": m["raw"],
            "outliers_rejected": self.outliers_rejected,
            "is_outlier": is_outlier,
            "filters": {"hampel": True, "kalman": True, "particle": len(self.samples) >= MIN_SAMPLES_TO_SOLVE},
        })

    async def _maybe_solve(self) -> None:
        if self.solving or len(self.samples) < MIN_SAMPLES_TO_SOLVE:
            return
        now = time.monotonic()
        if now - self.last_solve_at < SOLVE_EVERY_S and self.last_solution is not None:
            return
        self.last_solve_at = now
        self.solving = True
        window = self.samples[-MAX_WINDOW:]
        asyncio.create_task(self._run_solve(window))

    async def _run_solve(self, window: list[dict[str, Any]]) -> None:
        try:
            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            solution = await loop.run_in_executor(None, _solve_window, window, self.baro_msl)
            if solution:
                solution["solve_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                self._augment_metrics(solution)
                self.last_solution = solution
                self.prev_solution = solution
                await self.broadcast({"type": "solution", **solution})
        except Exception:
            logger.exception("stream solve failed")
        finally:
            self.solving = False

    def _augment_metrics(self, sol: dict[str, Any]) -> None:
        """CEP50/95, along/cross-track ошибка (если есть эталон), режим (TRN/DR),
        интегрити-проверка (резкие скачки решения)."""
        import numpy as _np

        # --- режим работы по достоверности/информативности рельефа ---
        conf = float(sol.get("confidence") or 0.0)
        info = float((sol.get("quality") or {}).get("terrain_informativeness") or 1.0)
        sol["mode"] = "DR" if (conf < 0.45 or info < 0.25) else "TRN"

        # --- интегрити: аномальный скачок азимута/скорости между решениями ---
        integrity = "OK"
        if self.prev_solution and sol.get("azimuth_deg") is not None and self.prev_solution.get("azimuth_deg") is not None:
            d_az = abs(((sol["azimuth_deg"] - self.prev_solution["azimuth_deg"] + 180) % 360) - 180)
            d_sp = abs(float(sol.get("speed_mps") or 0) - float(self.prev_solution.get("speed_mps") or 0))
            if d_az > 35 or d_sp > 25:
                integrity = "WARN"
        sol["integrity"] = integrity

        # --- точность относительно эталона (только в симуляции) ---
        if self.truth_xy and sol.get("lat") is not None:
            idx = min(len(self.samples) - 1, len(self.truth_xy) - 1)
            tx, ty = self.truth_xy[idx]
            # оценка положения в локальных метрах, реконструированная из lat/lon решения
            ex = (sol["lon"] - ORIGIN_LON) * (111_320.0 * math.cos(math.radians(ORIGIN_LAT)))
            ey = (sol["lat"] - ORIGIN_LAT) * 111_320.0
            err = float(math.hypot(ex - tx, ey - ty))
            self.pos_errors.append(err)
            arr = _np.asarray(self.pos_errors[-200:], dtype=float)
            sol["pos_error_m"] = round(err, 1)
            sol["cep50_m"] = round(float(_np.percentile(arr, 50)), 1)
            sol["cep95_m"] = round(float(_np.percentile(arr, 95)), 1)
            # along/cross-track относительно истинного курса
            if self.truth_az is not None:
                hr = math.radians(self.truth_az)
                dx, dy = ex - tx, ey - ty
                along = dx * math.sin(hr) + dy * math.cos(hr)
                cross = dx * math.cos(hr) - dy * math.sin(hr)
                sol["along_track_m"] = round(along, 1)
                sol["cross_track_m"] = round(cross, 1)

    # ------------------------- симулятор -------------------------
    async def start_simulation(self, *, hz: float, duration_s: float, speed_mps: float,
                               heading_deg: float, baro_msl: float,
                               start_x_m: float = 4000.0, start_y_m: float = 4000.0,
                               width_m: float = 8000.0, height_m: float = 8000.0,
                               resolution_m: float = 30.0, terrain_type: str = "mixed") -> None:
        await self.stop_simulation()
        await self.reset()
        self.baro_msl = float(baro_msl)
        self.sim_task = asyncio.create_task(
            self._simulate(hz=hz, duration_s=duration_s, speed_mps=speed_mps,
                           heading_deg=heading_deg, baro_msl=baro_msl,
                           start_x_m=start_x_m, start_y_m=start_y_m, width_m=width_m,
                           height_m=height_m, resolution_m=resolution_m, terrain_type=terrain_type)
        )

    async def stop_simulation(self) -> None:
        if self.sim_task and not self.sim_task.done():
            self.sim_task.cancel()
            try:
                await self.sim_task
            except (asyncio.CancelledError, Exception):
                pass
        self.sim_task = None

    async def _simulate(self, *, hz: float, duration_s: float, speed_mps: float,
                        heading_deg: float, baro_msl: float,
                        start_x_m: float = 4000.0, start_y_m: float = 4000.0,
                        width_m: float = 8000.0, height_m: float = 8000.0,
                        resolution_m: float = 30.0, terrain_type: str = "mixed") -> None:
        from app.core.nmea import build_gpgga_sentence
        from app.core.real_dem import provide_dem
        from app.core.simulator import generate_sensor_stream, generate_truth_trajectory

        loop = asyncio.get_running_loop()
        dem = await loop.run_in_executor(None, lambda: provide_dem(
            width_m=width_m, height_m=height_m, resolution_m=resolution_m,
            terrain_type=terrain_type, lat=ORIGIN_LAT, lon=ORIGIN_LON))
        truth = generate_truth_trajectory(
            start_x_m=start_x_m, start_y_m=start_y_m, speed_mps=speed_mps,
            azimuth_deg=heading_deg, duration_s=duration_s, sample_rate_hz=hz)
        # эталон для оценки точности (CEP/along-cross)
        self.truth_xy = list(zip([float(v) for v in truth.x_m], [float(v) for v in truth.y_m]))
        self.truth_az = float(heading_deg)
        self.truth_speed = float(speed_mps)
        stream = generate_sensor_stream(dem=dem, truth_trajectory=truth,
                                        barometric_altitude_msl=baro_msl, seed=7)
        await self.broadcast({"type": "stream_start",
                              "truth": {"azimuth_deg": round(heading_deg, 1),
                                        "speed_mps": round(speed_mps, 1)},
                              "source": "simulation"})
        period = 1.0 / max(hz, 0.5)
        for item in stream:
            line = build_gpgga_sentence(item["t_s"], item["radar_altitude_agl"])
            await self.ingest_text(line)
            await asyncio.sleep(period)
        await self.broadcast({"type": "stream_end"})


def _dem_label() -> str:
    try:
        from app.core.real_dem import dem_source_label
        return dem_source_label()
    except Exception:
        return "synthetic"


def _extract_heatmap(solution: Any, max_side: int = 48) -> dict[str, Any] | None:
    """Достаёт реальную матрицу корреляции из решения ядра, нормализует и
    прореживает до max_side для передачи во фронт (настоящая тепловая карта)."""
    try:
        import numpy as _np
        hm = getattr(solution, "heatmap", None)
        if hm is None:
            return None
        a = _np.asarray(hm, dtype=float)
        if a.ndim == 1:
            a = a.reshape(1, -1)
        if a.ndim != 2 or a.size == 0:
            return None
        finite = a[_np.isfinite(a)]
        fill = float(finite.min()) if finite.size else 0.0
        a = _np.nan_to_num(a, nan=fill, posinf=fill, neginf=fill)
        mn, mx = float(a.min()), float(a.max())
        norm = (a - mn) / (mx - mn) if mx > mn else _np.zeros_like(a)
        R, C = norm.shape
        rs, cs = max(1, R // max_side), max(1, C // max_side)
        small = norm[::rs, ::cs]
        pr, pc = _np.unravel_index(int(_np.argmax(norm)), norm.shape)
        az = solution.metadata.get("refined_azimuth_values") if hasattr(solution, "metadata") else None
        peak_az = None
        if az is not None and len(_np.ravel(az)):
            azv = _np.ravel(_np.asarray(az, dtype=float))
            # ось азимутов обычно совпадает с одной из размерностей
            idx = pr if len(azv) == R else (pc if len(azv) == C else None)
            if idx is not None:
                peak_az = round(float(azv[idx % len(azv)]), 1)
        return {
            "z": _np.round(small, 3).tolist(),
            "peak": [int(pr // rs), int(pc // cs)],
            "peak_az": peak_az,
            "rows": int(small.shape[0]), "cols": int(small.shape[1]),
        }
    except Exception:
        logger.exception("_extract_heatmap failed")
        return None


def _solve_window(window: list[dict[str, Any]], baro_msl: float) -> dict[str, Any] | None:
    """Синхронный пересчёт решения ядром по накопленному окну NMEA."""
    try:
        from app.core.navigation import solve_navigation
        from app.core.real_dem import provide_dem

        nmea_text = "\n".join(s["raw"] for s in window)
        dem = provide_dem(width_m=8000, height_m=8000, resolution_m=30,
                          terrain_type="mixed", lat=ORIGIN_LAT, lon=ORIGIN_LON)
        solution = solve_navigation(
            dem=dem, nmea_text=nmea_text, barometric_altitude_msl=baro_msl,
            sample_rate_hz=5.0, search_radius_m=900.0, coarse_step_m=250.0, fine_step_m=75.0,
            azimuth_coarse_step_deg=10.0, azimuth_fine_step_deg=2.0,
            speed_min_mps=20.0, speed_max_mps=80.0, speed_coarse_step_mps=5.0,
            speed_fine_step_mps=2.0, enable_kalman=True, parallel_jobs=1,
            compensate_baro_drift=True)
        est = solution.estimated
        lat, lon = _meters_to_latlon(float(est.get("end_x_m", est.get("start_x_m", 4000))),
                                     float(est.get("end_y_m", est.get("start_y_m", 4000))))
        radio = [round(s["radio_agl"], 1) for s in window]
        terrain = [round(s["terrain_msl"], 1) for s in window]
        return {
            "azimuth_deg": round(float(est.get("azimuth_deg", 0)), 1),
            "speed_mps": round(float(est.get("speed_mps", 0)), 1),
            "correlation": round(float(est.get("correlation", 0)), 3),
            "confidence": round(float(est.get("confidence", 0)), 3),
            "rmse_m": round(float(est.get("rmse_m", 0)), 1),
            "lat": lat, "lon": lon, "altitude_msl": round(baro_msl, 1),
            "n_samples": len(window),
            "profile": {"radio": radio[-160:], "terrain": terrain[-160:]},
            "heatmap": _extract_heatmap(solution),
            "dem_source": _dem_label(),
            "quality": solution.quality,
        }
    except Exception:
        logger.exception("_solve_window failed; falling back to terrain heuristic")
        # эвристика: уверенность по выраженности рельефа
        terr = [s["terrain_msl"] for s in window]
        span = (max(terr) - min(terr)) if terr else 0.0
        conf = max(0.0, min(0.9, 0.45 + span / 300.0))
        radio = [round(s["radio_agl"], 1) for s in window]
        terrain = [round(s["terrain_msl"], 1) for s in window]
        return {
            "azimuth_deg": None, "speed_mps": None, "correlation": round(conf + 0.05, 3),
            "confidence": round(conf, 3), "rmse_m": None,
            "lat": None, "lon": None, "altitude_msl": round(baro_msl, 1),
            "n_samples": len(window),
            "profile": {"radio": radio[-160:], "terrain": terrain[-160:]},
            "quality": {"warning": "core solve unavailable; terrain heuristic"},
        }


hub = StreamHub()


@router.websocket("/api/stream/live")
async def stream_live(ws: WebSocket) -> None:
    """Подписка дашборда на живые обновления."""
    await hub.register(ws)
    try:
        while True:
            # клиент может слать ping/команды; нам достаточно держать соединение
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.unregister(ws)
    except Exception:
        hub.unregister(ws)


@router.websocket("/api/stream/ingest")
async def stream_ingest_ws(ws: WebSocket) -> None:
    """Источник (кейсодатель) шлёт строки NMEA по WebSocket."""
    await ws.accept()
    try:
        while True:
            text = await ws.receive_text()
            await hub.ingest_text(text)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("ingest ws error")
        return


@router.post("/api/stream/ingest")
async def stream_ingest_http(request: Request) -> dict[str, Any]:
    """Источник шлёт строки NMEA HTTP POST'ом (text/plain или JSON {text|lines})."""
    ctype = request.headers.get("content-type", "")
    baro = None
    if "application/json" in ctype:
        body = await request.json()
        text = body.get("text") or "\n".join(body.get("lines", []))
        baro = body.get("barometric_altitude_msl")
    else:
        text = (await request.body()).decode("utf-8", errors="replace")
    stats = await hub.ingest_text(text, baro_msl=baro)
    return {"status": "ok", **stats}


@router.post("/api/stream/simulate")
async def stream_simulate(request: Request) -> dict[str, Any]:
    """Запустить демонстрационный поток (если внешнего источника нет)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    await hub.start_simulation(
        hz=float(body.get("hz", 5)),
        duration_s=float(body.get("duration_s", 180)),
        speed_mps=float(body.get("speed_mps", 45)),
        heading_deg=float(body.get("heading_deg", 128)),
        baro_msl=float(body.get("barometric_altitude_msl", 1500)),
        start_x_m=float(body.get("start_x_m", 4000)),
        start_y_m=float(body.get("start_y_m", 4000)),
        width_m=float(body.get("width_m", 8000)),
        height_m=float(body.get("height_m", body.get("width_m", 8000))),
        resolution_m=float(body.get("resolution_m", 30)),
        terrain_type=str(body.get("terrain_type", "mixed")),
    )
    return {"status": "ok", "mode": "simulation"}


@router.post("/api/stream/stop")
async def stream_stop() -> dict[str, str]:
    await hub.stop_simulation()
    return {"status": "ok"}


@router.post("/api/stream/reset")
async def stream_reset() -> dict[str, str]:
    await hub.stop_simulation()
    await hub.reset()
    return {"status": "ok"}


@router.get("/api/dem/grid")
async def dem_grid(width_m: float = 8000, height_m: float = 8000, resolution_m: float = 30,
                   terrain_type: str = "mixed", side: int = 72) -> dict[str, Any]:
    """Прореженная сетка высот текущего DEM (реального Copernicus или синтетического)
    для отрисовки настоящего рельефа на 3D-карте."""
    import numpy as _np

    loop = asyncio.get_running_loop()

    def _build() -> dict[str, Any]:
        from app.core.real_dem import provide_dem
        dem = provide_dem(width_m=width_m, height_m=height_m, resolution_m=resolution_m,
                          terrain_type=terrain_type, lat=ORIGIN_LAT, lon=ORIGIN_LON)
        elev = _np.asarray(dem.elevation, dtype=float)
        R, C = elev.shape
        ri = _np.linspace(0, R - 1, min(side, R)).astype(int)
        ci = _np.linspace(0, C - 1, min(side, C)).astype(int)
        small = elev[_np.ix_(ri, ci)]
        mn, mx = float(small.min()), float(small.max())
        norm = (small - mn) / (mx - mn) if mx > mn else _np.zeros_like(small)
        return {
            "z": _np.round(norm, 4).tolist(),
            "min_m": round(mn, 1), "max_m": round(mx, 1), "span_m": round(mx - mn, 1),
            "rows": int(small.shape[0]), "cols": int(small.shape[1]),
            "source": _dem_label(),
        }

    try:
        return await loop.run_in_executor(None, _build)
    except Exception:
        logger.exception("dem_grid failed")
        return {"z": [], "source": "error"}
