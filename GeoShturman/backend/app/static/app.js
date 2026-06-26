/* =========================================================================
   ГеоШтурман · «Полёт вслепую» — клиентская логика демонстрационного стенда.
   Vanilla JS. Без внешних библиотек, без localStorage/cookies/гео.
   Работает с бэкендом (POST /api/navigation/autonomous-demo) и автономно (demo-fallback).
   ========================================================================= */
"use strict";

(() => {
  /* ----------------------------- Утилиты -------------------------------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const pad2 = (n) => String(n).padStart(2, "0");

  // Детерминированный ГПСЧ (mulberry32) — стабильная демонстрация по seed.
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Подгонка canvas под реальный размер с учётом плотности пикселей.
  function fit(cv) {
    const dpr = window.devicePixelRatio || 1;
    const r = cv.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width) || cv.width);
    const h = Math.max(1, Math.round(r.height) || cv.height);
    cv.width = w * dpr;
    cv.height = h * dpr;
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  /* ------------------------- Часы системного времени -------------------- */
  const clockEl = $("#sysClock");
  function tickClock() {
    const d = new Date();
    clockEl.textContent =
      `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())} UTC`;
  }
  tickClock();
  setInterval(tickClock, 1000);

  /* --------------------------- Свёртка параметров ----------------------- */
  const paramsToggle = $("#paramsToggle");
  const paramsBody = $("#paramsBody");
  paramsToggle.addEventListener("click", () => {
    const open = paramsBody.hasAttribute("hidden");
    if (open) {
      paramsBody.removeAttribute("hidden");
      paramsToggle.textContent = "Свернуть";
      paramsToggle.setAttribute("aria-expanded", "true");
    } else {
      paramsBody.setAttribute("hidden", "");
      paramsToggle.textContent = "Развернуть";
      paramsToggle.setAttribute("aria-expanded", "false");
    }
  });

  /* ---------------------- Переключатель вида карты ----------------------- */
  $$(".seg__btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      $$(".seg__btn").forEach((b) => b.classList.remove("is-active"));
      btn.classList.add("is-active");
    });
  });

  /* ============================ Сбор формы ============================== */
  function collectPayload() {
    const num = (id) => parseFloat($("#" + id).value);
    const trueSpeed = num("speed_mps");
    const trueHeading = num("azimuth_deg");
    return {
      width_m: num("width_m"),
      height_m: num("height_m"),
      resolution_m: num("resolution_m"),
      duration_s: num("duration_s"),
      sample_rate_hz: num("sample_rate_hz"),
      true_speed_mps: trueSpeed,
      true_heading_deg: trueHeading,
      speed_mps: trueSpeed,
      azimuth_deg: trueHeading,
      barometric_altitude_msl: num("barometric_altitude_msl"),
      initial_uncertainty_radius_m: num("initial_uncertainty_radius_m"),
      n_particles: Math.round(num("n_particles")),
      profile_window_s: num("profile_window_s"),
      noise_std_m: 2,
      outlier_probability: 0.01,
      terrain_type: "mixed",
      enable_kalman: $("#enable_kalman").checked,
      seed: 42,
    };
  }

  /* ===================== Состояние загрузки / шаги ====================== */
  const runBtn = $("#runBtn");
  const loading = $("#loading");
  const loadingFill = $("#loadingFill");
  const algoSteps = $$("#algoSteps .algo__step");
  const STEP_LABELS = [
    "Генерация DEM и sensor stream",
    "Расчёт Dead Reckoning baseline",
    "Инициализация Particle Filter",
    "Terrain Lock и profile matching",
    "Автономная оценка получена",
  ];
  let stepTimer = null;

  function resetSteps() {
    algoSteps.forEach((s) => {
      s.classList.remove("is-active", "is-done");
      s.querySelector(".algo__ok").textContent = "—";
    });
  }

  function startStepper() {
    resetSteps();
    let i = 0;
    const advance = () => {
      if (i > 0) {
        algoSteps[i - 1].classList.remove("is-active");
        algoSteps[i - 1].classList.add("is-done");
        algoSteps[i - 1].querySelector(".algo__ok").textContent = "OK";
      }
      if (i < algoSteps.length) {
        algoSteps[i].classList.add("is-active");
        loadingFill.style.width = `${((i + 1) / algoSteps.length) * 100}%`;
        if (i < STEP_LABELS.length - 1) pushMessage(STEP_LABELS[i]);
        i += 1;
        stepTimer = setTimeout(advance, 480 + Math.random() * 360);
      }
    };
    advance();
  }

  function finishSteps() {
    clearTimeout(stepTimer);
    algoSteps.forEach((s) => {
      s.classList.remove("is-active");
      s.classList.add("is-done");
      s.querySelector(".algo__ok").textContent = "OK";
    });
    loadingFill.style.width = "100%";
  }

  function setLoading(on) {
    runBtn.disabled = on;
    runBtn.classList.toggle("is-loading", on);
    runBtn.querySelector(".btn__label").textContent = on ? "Расчёт…" : "Запустить демонстрацию";
    loading.hidden = !on;
    $$("#demoForm input").forEach((el) => (el.disabled = on));
    if (on) {
      loadingFill.style.width = "0%";
      startStepper();
    } else {
      finishSteps();
    }
  }

  /* ============================ Лог сообщений =========================== */
  const msgLog = $("#msgLog");
  let logInitialised = false;
  function nowHMS() {
    const d = new Date();
    return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
  }
  function pushMessage(text, kind = "") {
    if (!logInitialised) {
      msgLog.innerHTML = "";
      logInitialised = true;
    }
    const li = document.createElement("li");
    li.className = "msg" + (kind ? " msg--" + kind : "");
    const t = document.createElement("time");
    t.textContent = nowHMS();
    const s = document.createElement("span");
    s.textContent = text;
    li.append(t, s);
    msgLog.prepend(li);
    while (msgLog.children.length > 24) msgLog.lastChild.remove();
  }

  /* ===================== Толерантный разбор ответа ====================== */
  // Приводит ответ любого из агентов к единому виду, заполняя пропуски.
  function normalizeResponse(raw, payload) {
    raw = raw || {};
    const est = raw.final_estimate || raw.estimated || raw.result || {};
    const truth = raw.truth || raw.ground_truth || {};
    const m = raw.metrics || raw.quality || {};
    const confidence = raw.confidence || {};
    const fp = raw.found_position || raw.position || {};
    const art = raw.artifacts || raw.files || {};
    const profileMatch = raw.profile_match || {};
    const finalError = raw.truth_error || {};
    const deadReckoningError = raw.dead_reckoning_error || {};
    const navMode = pick(confidence.mode, m.mode, raw.mode, null);
    const confidenceValue = pick(confidence.value, confidence.confidence, m.confidence, m.conf, null);

    return {
      status: raw.status || "ok",
      warning: raw.warning || raw.warnings || null,
      estimated: {
        azimuth_deg: pick(est.heading_deg, est.azimuth_deg, est.azimuth, payload.true_heading_deg, payload.azimuth_deg),
        speed_mps: pick(est.speed_mps, est.speed, payload.speed_mps),
        start_x_m: pick(est.start_x_m, est.x_m, est.x, null),
        start_y_m: pick(est.start_y_m, est.y_m, est.y, null),
      },
      truth: {
        azimuth_deg: pick(truth.heading_deg, truth.azimuth_deg, truth.azimuth, payload.true_heading_deg, payload.azimuth_deg),
        speed_mps: pick(truth.speed_mps, truth.speed, payload.true_speed_mps, payload.speed_mps),
        start_x_m: pick(truth.start_x_m, truth.x, payload.width_m / 2),
        start_y_m: pick(truth.start_y_m, truth.y, payload.height_m / 2),
      },
      metrics: {
        correlation: pick(m.improvement_factor, raw.improvement_factor, m.correlation, m.profile_correlation, null),
        rmse_m: pick(m.final_position_error_m, finalError.final_position_error_m, m.rmse_m, m.rmse, null),
        confidence: confidenceValue,
        confidence_pct: pick(m.confidence_pct, confidenceValue != null ? confidenceValue * 100 : null, null),
        cep_m: pick(m.error_radius_m, est.error_radius_m, m.cep_m, m.cep, null),
        sep_m: pick(m.final_position_error_m, finalError.final_position_error_m, m.sep_m, m.sep, null),
        vertical_m: pick(m.mean_position_error_m, finalError.mean_position_error_m, m.vertical_m, m.vertical, null),
        distance_km: pick(m.distance_km, raw.distance_km, null),
        offset_km: pick(m.offset_km, m.offset, finalError.final_position_error_m != null ? finalError.final_position_error_m / 1000 : null, null),
        dead_reckoning_final_error_m: pick(m.dead_reckoning_final_error_m, deadReckoningError.final_position_error_m, null),
        terrain_lock_ratio: pick(m.terrain_lock_ratio, raw.quality && raw.quality.terrain_lock_ratio, null),
        mode: navMode,
      },
      found_position: {
        lat: pick(fp.lat, fp.latitude, null),
        lon: pick(fp.lon, fp.longitude, null),
        altitude_msl: pick(fp.altitude_msl, fp.alt, payload.barometric_altitude_msl),
      },
      profile: raw.profile || (profileMatch.observed_profile ? {
        radio: profileMatch.observed_profile,
        dem: profileMatch.best_dem_profile,
      } : null),
      path: raw.path || raw.trajectory || null, // [[x,y],...] доли 0..1 или метры
      artifacts: {
        trajectory_overlay_png: art.trajectory_comparison_png || art.trajectory_overlay_png || art.map || null,
        correlation_heatmap_png: art.particle_cloud_png || art.correlation_heatmap_png || art.heatmap || null,
        confidence_timeline_png: art.confidence_timeline_png || null,
        profile_comparison_png: art.terrain_profile_match_png || art.profile_comparison_png || art.profile || null,
        nmea_log: art.nmea_log || art.nmea || null,
        result_json: art.result_json || art.json || null,
      },
      log: raw.log || null,
    };
  }
  function pick(...vals) {
    for (const v of vals) if (v !== undefined && v !== null && !(typeof v === "number" && Number.isNaN(v))) return v;
    return null;
  }

  /* ===================== Автономный demo-генератор ====================== */
  // Используется, когда бэкенд недоступен — чтобы стенд работал на демо.
  let blobUrls = [];
  function revokeBlobs() {
    blobUrls.forEach((u) => URL.revokeObjectURL(u));
    blobUrls = [];
  }

  function valueNoise(rand) {
    // Несколько октав синусов со случайными фазами → плавный «рельеф».
    const oct = [];
    for (let i = 0; i < 5; i++) {
      oct.push({
        fx: (0.6 + rand() * 2.4) * (i + 1) * 0.6,
        fy: (0.6 + rand() * 2.4) * (i + 1) * 0.6,
        px: rand() * Math.PI * 2,
        py: rand() * Math.PI * 2,
        a: 1 / (i + 1),
      });
    }
    return (u, v) => {
      let s = 0, norm = 0;
      for (const o of oct) {
        s += o.a * Math.sin(u * o.fx * Math.PI + o.px) * Math.cos(v * o.fy * Math.PI + o.py);
        norm += o.a;
      }
      return (s / norm) * 0.5 + 0.5; // 0..1
    };
  }

  function buildSynthetic(payload) {
    const rand = rng((payload.seed || 42) + Math.floor(payload.azimuth_deg));
    const elev = valueNoise(rand);
    const baseElevMin = 1100, baseElevMax = 1700;
    const heightAt = (u, v) => baseElevMin + elev(u, v) * (baseElevMax - baseElevMin);

    // Истинная траектория из центра по азимуту.
    const azTrue = payload.azimuth_deg;
    const azRad = (azTrue * Math.PI) / 180;
    const dist = payload.speed_mps * payload.duration_s; // м
    const sx = payload.width_m / 2, sy = payload.height_m / 2;
    const ex = sx + Math.sin(azRad) * dist;
    const ey = sy - Math.cos(azRad) * dist;

    // Сэмплы профиля вдоль пути.
    const n = clamp(Math.round(payload.duration_s * payload.sample_rate_hz), 30, 1200);
    const radio = [], dem = [], path = [];
    for (let i = 0; i < n; i++) {
      const t = i / (n - 1);
      const x = sx + (ex - sx) * t;
      const y = sy + (ey - sy) * t;
      const u = clamp(x / payload.width_m, 0, 1);
      const v = clamp(y / payload.height_m, 0, 1);
      const terr = heightAt(u, v);
      let r = payload.barometric_altitude_msl - terr; // AGL
      // шум + редкие выбросы
      r += (rand() - 0.5) * 2 * payload.noise_std_m;
      if (rand() < payload.outlier_probability) r += (rand() - 0.5) * 120;
      radio.push(payload.barometric_altitude_msl - r); // восстановленный рельеф из радио
      dem.push(terr);
      path.push([u, v]);
    }

    // «Найденные» параметры — с правдоподобной погрешностью.
    const estAz = azTrue + Math.round((rand() - 0.5) * 3);
    const estSpeed = +(payload.speed_mps + (rand() - 0.5) * 1.2).toFixed(1);
    const estX = Math.round(sx + (rand() - 0.5) * 60);
    const estY = Math.round(sy + (rand() - 0.5) * 60);
    const corr = +(0.93 + rand() * 0.05).toFixed(3);
    const rmse = +(payload.noise_std_m * (3 + rand() * 2)).toFixed(1);
    const conf = +(0.84 + rand() * 0.12).toFixed(2);
    const cep = Math.round(payload.resolution_m * (0.8 + rand() * 0.6));
    const sep = Math.round(cep * 1.5);

    const lat = +(59.5 + rand() * 0.8).toFixed(4);
    const lon = +(150.9 + rand() * 0.9).toFixed(4);

    // NMEA-лог.
    const nmea = buildNmea(radio, payload);
    const result = {
      status: "ok",
      mode: "demo-fallback",
      params: payload,
      estimated: { azimuth_deg: estAz, speed_mps: estSpeed, start_x_m: estX, start_y_m: estY },
      truth: { azimuth_deg: azTrue, speed_mps: payload.speed_mps, start_x_m: sx, start_y_m: sy },
      metrics: {
        correlation: corr, rmse_m: rmse, confidence: conf, confidence_pct: +(conf * 100).toFixed(1),
        cep_m: cep, sep_m: sep, vertical_m: Math.round(cep * 0.45),
        distance_km: +(dist / 1000).toFixed(1), offset_km: +(Math.abs(estX - sx) / 1000 + 0.1).toFixed(1),
      },
      found_position: { lat, lon, altitude_msl: payload.barometric_altitude_msl },
      profile: { radio, dem },
      path,
    };

    // Blob-ссылки на скачивание.
    const nmeaUrl = makeBlob(nmea, "text/plain");
    const jsonUrl = makeBlob(JSON.stringify(result, null, 2), "application/json");

    return Object.assign(result, {
      _heightAt: heightAt,
      artifacts: {
        nmea_log: nmeaUrl,
        result_json: jsonUrl,
        trajectory_overlay_png: null,
        correlation_heatmap_png: null,
        profile_comparison_png: null,
      },
      log: [
        { t: nowHMS(), msg: "Потеря сигнала GNSS — переход в автономный режим" },
        { t: nowHMS(), msg: "Сбор данных радиовысотомера (NMEA-0183)" },
        { t: nowHMS(), msg: "Построение профиля рельефа" },
        { t: nowHMS(), msg: "Поиск совпадений по DEM" },
        { t: nowHMS(), msg: `Решение найдено (${(conf * 100).toFixed(1)}%)` },
      ],
    });
  }

  function buildNmea(radio, payload) {
    const lines = ["# Синтетический трек радиовысотомера, формат NMEA-0183 v3 (GGA)"];
    const rate = payload.sample_rate_hz;
    radio.forEach((agl, i) => {
      const sec = i / rate;
      const hh = pad2(Math.floor(sec / 3600) % 24);
      const mm = pad2(Math.floor(sec / 60) % 60);
      const ss = (sec % 60).toFixed(2).padStart(5, "0");
      const alt = Math.max(0, agl).toFixed(1);
      const body = `GPGGA,${hh}${mm}${ss},,,,,,,,${alt},M,46.9,M,,`;
      lines.push(`$${body}*${nmeaChecksum(body)}`);
    });
    return lines.join("\n");
  }
  function nmeaChecksum(s) {
    let c = 0;
    for (let i = 0; i < s.length; i++) c ^= s.charCodeAt(i);
    return c.toString(16).toUpperCase().padStart(2, "0");
  }
  function makeBlob(text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    blobUrls.push(url);
    return url;
  }

  /* =========================== Визуализации ============================ */
  // Компас курса.
  function drawCompass(az) {
    const cv = $("#compass");
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 6;
    ctx.clearRect(0, 0, w, h);
    // циферблат
    ctx.strokeStyle = "#1f3142"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    for (let a = 0; a < 360; a += 15) {
      const rad = (a * Math.PI) / 180;
      const r1 = a % 90 === 0 ? R - 10 : R - 5;
      ctx.strokeStyle = a % 90 === 0 ? "#3a5670" : "#22384c";
      ctx.beginPath();
      ctx.moveTo(cx + Math.sin(rad) * R, cy - Math.cos(rad) * R);
      ctx.lineTo(cx + Math.sin(rad) * r1, cy - Math.cos(rad) * r1);
      ctx.stroke();
    }
    // буквы
    ctx.fillStyle = "#8da3ba"; ctx.font = "10px sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([l, a]) => {
      const rad = (a * Math.PI) / 180;
      ctx.fillText(l, cx + Math.sin(rad) * (R - 16), cy - Math.cos(rad) * (R - 16));
    });
    // стрелка азимута
    if (az != null) {
      const rad = (az * Math.PI) / 180;
      ctx.strokeStyle = "#34d399"; ctx.lineWidth = 2.5; ctx.lineCap = "round";
      ctx.shadowColor = "#34d399"; ctx.shadowBlur = 8;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.sin(rad) * (R - 14), cy - Math.cos(rad) * (R - 14));
      ctx.stroke();
      ctx.shadowBlur = 0;
    }
    // центр и значение
    ctx.fillStyle = "#34d399";
    ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#e7eef6"; ctx.font = "bold 22px monospace";
    ctx.fillText(az != null ? `${Math.round(az)}°` : "—", cx, cy + R * 0.55);
  }

  // Круговой гейдж достоверности.
  function drawGauge(pct) {
    const cv = $("#matchGauge");
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 10;
    ctx.clearRect(0, 0, w, h);
    ctx.lineWidth = 9; ctx.lineCap = "round";
    ctx.strokeStyle = "#16222f";
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    const p = clamp((pct || 0) / 100, 0, 1);
    const start = -Math.PI / 2;
    const grad = ctx.createLinearGradient(0, 0, w, h);
    grad.addColorStop(0, "#22d3ee"); grad.addColorStop(1, "#34d399");
    ctx.strokeStyle = grad; ctx.shadowColor = "#34d39988"; ctx.shadowBlur = 12;
    ctx.beginPath(); ctx.arc(cx, cy, R, start, start + p * Math.PI * 2); ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "#e7eef6"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "bold 26px monospace";
    ctx.fillText(pct != null ? `${Math.round(pct)}%` : "—", cx, cy);
  }

  // Sparkline высоты над землёй.
  function drawSparkline(values) {
    const cv = $("#sparkRadio");
    const { ctx, w, h } = fit(cv);
    ctx.clearRect(0, 0, w, h);
    if (!values || !values.length) return;
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const xs = (i) => (i / (values.length - 1)) * w;
    const ys = (v) => h - 6 - ((v - min) / span) * (h - 12);
    // заливка
    ctx.beginPath();
    ctx.moveTo(0, h);
    values.forEach((v, i) => ctx.lineTo(xs(i), ys(v)));
    ctx.lineTo(w, h); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, "#34d39955"); g.addColorStop(1, "#34d39900");
    ctx.fillStyle = g; ctx.fill();
    // линия
    ctx.beginPath();
    values.forEach((v, i) => (i ? ctx.lineTo(xs(i), ys(v)) : ctx.moveTo(xs(i), ys(v))));
    ctx.strokeStyle = "#34d399"; ctx.lineWidth = 1.5; ctx.stroke();
  }

  // Полярный эллипс точности.
  function drawPolar(cep, sep) {
    const cv = $("#accuracyPolar");
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 6;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#1c2c3d"; ctx.lineWidth = 1;
    [0.33, 0.66, 1].forEach((k) => { ctx.beginPath(); ctx.arc(cx, cy, R * k, 0, Math.PI * 2); ctx.stroke(); });
    ctx.beginPath(); ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
    ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R); ctx.stroke();
    // эллипс рассеяния
    const rx = R * clamp((cep || 30) / 60, 0.15, 0.9);
    const ry = R * clamp((sep || 45) / 90, 0.2, 0.95);
    ctx.fillStyle = "#a855f733"; ctx.strokeStyle = "#a855f7"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2); ctx.fill();
  }

  // Профиль высот (радиовысотомер vs DEM).
  function drawProfile(radio, dem) {
    const cv = $("#profileCanvas");
    const { ctx, w, h } = fit(cv);
    ctx.clearRect(0, 0, w, h);
    if (!radio || !radio.length) return;
    const all = radio.concat(dem || []);
    const min = Math.min(...all), max = Math.max(...all), span = max - min || 1;
    const padL = 34, padB = 18, padT = 8;
    const xs = (i, arr) => padL + (i / (arr.length - 1)) * (w - padL - 6);
    const ys = (v) => padT + (1 - (v - min) / span) * (h - padT - padB);
    // сетка + подписи высоты
    ctx.strokeStyle = "#13202d"; ctx.fillStyle = "#5f7387"; ctx.font = "9px monospace"; ctx.textAlign = "right";
    for (let k = 0; k <= 4; k++) {
      const val = min + (span * k) / 4;
      const y = ys(val);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - 6, y); ctx.stroke();
      ctx.fillText(Math.round(val), padL - 4, y + 3);
    }
    const line = (arr, color) => {
      ctx.beginPath();
      arr.forEach((v, i) => (i ? ctx.lineTo(xs(i, arr), ys(v)) : ctx.moveTo(xs(i, arr), ys(v))));
      ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.stroke();
    };
    if (dem) line(dem, "#34d399");
    line(radio, "#a855f7");
  }

  // Террейн-карта с траекторией (для demo-fallback).
  function drawTerrainMap(heightAt, path) {
    const mapEl = $(".map");
    let cv = $("#mapCanvas");
    if (!cv) {
      cv = document.createElement("canvas");
      cv.id = "mapCanvas";
      cv.style.position = "absolute";
      cv.style.inset = "0";
      cv.style.width = "100%";
      cv.style.height = "100%";
      mapEl.insertBefore(cv, mapEl.firstChild);
    }
    const { ctx, w, h } = fit(cv);
    const img = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const e = heightAt(x / w, y / h); // 1100..1700
        const t = clamp((e - 1100) / 600, 0, 1);
        // тёмно-сине-зелёная палитра рельефа
        const r = 8 + t * 70;
        const g = 22 + t * 150;
        const b = 30 + t * 90;
        const idx = (y * w + x) * 4;
        img.data[idx] = r; img.data[idx + 1] = g; img.data[idx + 2] = b; img.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    // лёгкая виньетка
    const vg = ctx.createRadialGradient(w / 2, h * 0.45, h * 0.2, w / 2, h / 2, h * 0.8);
    vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, "rgba(0,0,0,.55)");
    ctx.fillStyle = vg; ctx.fillRect(0, 0, w, h);
    // траектория
    if (path && path.length) {
      ctx.beginPath();
      path.forEach(([u, v], i) => (i ? ctx.lineTo(u * w, v * h) : ctx.moveTo(u * w, v * h)));
      ctx.strokeStyle = "#a855f7"; ctx.lineWidth = 2.4; ctx.shadowColor = "#a855f7"; ctx.shadowBlur = 8;
      ctx.stroke(); ctx.shadowBlur = 0;
      const [u0, v0] = path[0], [u1, v1] = path[path.length - 1];
      ctx.fillStyle = "#22d3ee"; ctx.beginPath(); ctx.arc(u0 * w, v0 * h, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(u1 * w, v1 * h, 5, 0, Math.PI * 2); ctx.fill();
    }
    $("#mapPlaceholder").style.display = "none";
  }

  // Heatmap корреляции (азимут × смещение) для demo-fallback.
  function drawHeatmap(targetCanvasSel, truthAz) {
    const cv = $(targetCanvasSel);
    const { ctx, w, h } = fit(cv);
    const img = ctx.createImageData(w, h);
    const peakX = ((truthAz % 360) / 360) * w;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        // горизонтальная полоса максимума по смещению 0 + пик у истинного азимута
        const dy = Math.abs(y - h / 2) / (h / 2);
        const dx = Math.min(Math.abs(x - peakX), w - Math.abs(x - peakX)) / (w * 0.5);
        let val = Math.exp(-dy * dy * 6) * (0.45 + 0.55 * Math.exp(-dx * dx * 14));
        val = clamp(val + (Math.random() - 0.5) * 0.05, 0, 1);
        const [r, g, b] = jet(val);
        const idx = (y * w + x) * 4;
        img.data[idx] = r; img.data[idx + 1] = g; img.data[idx + 2] = b; img.data[idx + 3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
  }
  // Палитра «jet» 0..1 → [r,g,b].
  function jet(v) {
    const c = (x) => clamp(Math.round(255 * x), 0, 255);
    const r = c(clamp(1.5 - Math.abs(4 * v - 3), 0, 1));
    const g = c(clamp(1.5 - Math.abs(4 * v - 2), 0, 1));
    const b = c(clamp(1.5 - Math.abs(4 * v - 1), 0, 1));
    return [r, g, b];
  }

  /* ============================ Рендер ответа ========================== */
  function setText(id, val) { const el = $("#" + id); if (el) el.textContent = val; }
  function setBar(id, pct) { const el = $("#" + id); if (el) el.style.width = clamp(pct, 0, 100) + "%"; }

  function renderResults(data, payload) {
    const e = data.estimated, t = data.truth, m = data.metrics, fp = data.found_position;

    // --- Левая телеметрия ---
    setText("m-speed", fmt(e.speed_mps, 1));
    setText("m-azimuth", fmt(e.azimuth_deg, 0));
    setText("m-baro", fmt(payload.barometric_altitude_msl, 0));
    setText("m-prob", fmt(m.confidence_pct ?? (m.confidence != null ? m.confidence * 100 : null), 1));
    setText("m-distance", fmt(m.distance_km, 1));
    setText("m-radio", fmt(payload.barometric_altitude_msl - (data.profile ? avg(data.profile.dem) : payload.barometric_altitude_msl - 356), 0));
    // время полёта из длительности
    const sec = Math.round(payload.duration_s);
    setText("m-time", `${pad2(Math.floor(sec / 3600))}:${pad2(Math.floor(sec / 60) % 60)}:${pad2(sec % 60)}`);

    setBar("bar-speed", (e.speed_mps / 120) * 100);
    setBar("bar-azimuth", (e.azimuth_deg / 360) * 100);
    setBar("bar-prob", m.confidence_pct ?? 90);
    setBar("bar-distance", clamp((m.distance_km / 50) * 100, 5, 100));
    setBar("bar-time", clamp((payload.duration_s / 600) * 100, 5, 100));

    // --- Калман-индикатор ---
    const kOn = payload.enable_kalman;
    $("#kalmanState").textContent = kOn ? "АКТИВЕН" : "ВЫКЛ";
    $("#kalmanState").className = "sensor__state " + (kOn ? "ok" : "danger");
    $("#kalmanDot").className = "dot " + (kOn ? "dot--ok" : "dot--danger");

    // --- Найденное положение на карте ---
    if (fp.lat != null && fp.lon != null) {
      setText("fp-lat", fmt(fp.lat, 4));
      setText("fp-lon", fmt(fp.lon, 4));
      setText("fp-alt", fmt(fp.altitude_msl, 0));
      setText("fp-acc", fmt(m.confidence_pct, 1));
      $("#foundPos").hidden = false;
    }

    // --- Лучшее совпадение + гейдж ---
    setText("bm-azimuth", fmt(e.azimuth_deg, 0));
    setText("bm-offset", fmt(m.offset_km, 1));
    setText("bm-corr", fmt(m.terrain_lock_ratio, 2));
    setText("bm-conf", fmt(m.confidence_pct, 1));
    drawGauge(m.confidence_pct);

    // --- Карточки метрик ---
    setText("r-azimuth", fmt(e.azimuth_deg, 0));
    setText("r-speed", fmt(e.speed_mps, 1));
    setText("r-corr", fmt(m.correlation, 2));
    setText("r-rmse", fmt(m.rmse_m, 1));
    setText("r-conf", fmt(m.confidence, 2));
    const qEl = $("#r-quality");
    qEl.textContent = m.mode || quality(m).label;
    qEl.className = "quality " + quality(m).cls;

    // --- Точность ---
    setText("a-cep", fmt(m.cep_m, 0));
    setText("a-sep", fmt(m.sep_m, 0));
    setText("a-vert", fmt(m.vertical_m, 0));
    drawPolar(m.cep_m, m.sep_m);

    // --- Компас ---
    drawCompass(e.azimuth_deg);

    // --- Таблица сравнения ---
    fillCompare(t, e);

    // --- Изображения / canvas ---
    const art = data.artifacts;
    bindImage("#mapTrajectory", "#mapPlaceholder", art.trajectory_overlay_png, () => {
      if (data._heightAt) drawTerrainMap(data._heightAt, data.path);
    });
    bindImage("#heatmap", "#heatmapPlaceholder", art.correlation_heatmap_png, () => {
      drawHeatmap("#heatmapDemo", t.azimuth_deg ?? payload.azimuth_deg);
    });
    bindImage("#confidenceImg", null, art.confidence_timeline_png, () => {});
    bindImage("#profileImg", null, art.profile_comparison_png, () => {});
    if (data.profile) {
      if (!art.profile_comparison_png) {
        $("#profileCanvas").hidden = false;
        drawProfile(data.profile.radio, data.profile.dem);
        drawSparkline(sampleRadio(data.profile, payload));
      } else {
        $("#profileCanvas").hidden = true;
      }
    }
    if (data.path) drawTrajectory(data.path);

    // --- Скачивание ---
    bindDownload("#dl-json", art.result_json, "result.json");
    bindDownload("#dl-traj", art.trajectory_overlay_png, "trajectory_comparison.png");
    bindDownload("#dl-heat", art.correlation_heatmap_png, "particle_cloud.png");
    bindDownload("#dl-confidence", art.confidence_timeline_png, "confidence_timeline.png");
    bindDownload("#dl-profile", art.profile_comparison_png, "profile_match.png");

    // --- Лог из ответа ---
    if (Array.isArray(data.log)) {
      data.log.forEach((row) => {
        const k = /lost|потер|ошиб/i.test(row.msg) ? "alert" : /найден|ok|решен/i.test(row.msg) ? "ok" : "";
        pushMessage(row.msg, k);
      });
    }
    pushMessage(`Решение найдено — достоверность ${fmt(m.confidence_pct, 1)}%`, "ok");

    // --- Баннер навигации ---
    $("#navBanner").hidden = false;
  }

  function avg(arr) { return arr && arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }
  function sampleRadio(profile, payload) {
    // высота над землёй = baro - terrain
    return (profile.dem || []).map((d) => payload.barometric_altitude_msl - d);
  }

  function quality(m) {
    const c = m.confidence != null ? m.confidence : (m.confidence_pct ?? 0) / 100;
    if (c >= 0.85 && (m.correlation ?? 1) >= 0.9) return { label: "OK", cls: "ok" };
    if (c >= 0.6) return { label: "WARNING", cls: "warn" };
    return { label: "LOW", cls: "low" };
  }

  function fillCompare(t, e) {
    const rows = $$("#cmpBody tr");
    const data = [
      [fmt(t.azimuth_deg, 0) + "°", fmt(e.azimuth_deg, 0) + "°", delta(e.azimuth_deg, t.azimuth_deg, "°", 3)],
      [fmt(t.speed_mps, 1) + " м/с", fmt(e.speed_mps, 1) + " м/с", delta(e.speed_mps, t.speed_mps, " м/с", 2)],
      [fmt(t.start_x_m, 0) + " м", fmt(e.start_x_m, 0) + " м", delta(e.start_x_m, t.start_x_m, " м", 50)],
      [fmt(t.start_y_m, 0) + " м", fmt(e.start_y_m, 0) + " м", delta(e.start_y_m, t.start_y_m, " м", 50)],
    ];
    rows.forEach((tr, i) => {
      const cells = $$("td", tr);
      cells[1].textContent = data[i][0];
      cells[2].textContent = data[i][1];
      cells[3].textContent = data[i][2].text;
      cells[3].className = data[i][2].cls;
    });
  }
  function delta(est, truth, unit, goodAbs) {
    if (est == null || truth == null) return { text: "—", cls: "" };
    const d = est - truth;
    const sign = d > 0 ? "+" : "";
    const cls = Math.abs(d) <= goodAbs ? "delta-good" : Math.abs(d) <= goodAbs * 2.5 ? "delta-warn" : "";
    return { text: `${sign}${(+d).toFixed(Math.abs(d) < 10 ? 1 : 0)}${unit}`, cls };
  }

  function drawTrajectory(path) {
    const cv = $("#trajCanvas");
    const { ctx, w, h } = fit(cv);
    ctx.clearRect(0, 0, w, h);
    // сетка
    ctx.strokeStyle = "#13202d"; ctx.lineWidth = 1;
    for (let i = 0; i <= 6; i++) { const x = (i / 6) * w; ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
    for (let i = 0; i <= 4; i++) { const y = (i / 4) * h; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); }
    const pad = 14;
    const X = (u) => pad + u * (w - pad * 2), Y = (v) => pad + v * (h - pad * 2);
    // пройденный путь
    ctx.beginPath();
    path.forEach(([u, v], i) => (i ? ctx.lineTo(X(u), Y(v)) : ctx.moveTo(X(u), Y(v))));
    ctx.strokeStyle = "#a855f7"; ctx.lineWidth = 2.2; ctx.stroke();
    // найденная (пунктир, чуть смещена)
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    path.forEach(([u, v], i) => { const x = X(u) + 3, y = Y(v) - 2; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = "#34d399"; ctx.lineWidth = 1.6; ctx.stroke();
    ctx.setLineDash([]);
    const [eu, ev] = path[path.length - 1];
    ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(X(eu), Y(ev), 4, 0, Math.PI * 2); ctx.fill();
  }

  function bindImage(imgSel, phSel, url, drawFallback) {
    const img = $(imgSel);
    const ph = phSel ? $(phSel) : null;
    if (url) {
      img.src = url; img.hidden = false;
      if (ph) ph.style.display = "none";
    } else {
      img.hidden = true;
      if (ph) ph.style.display = "";
      if (typeof drawFallback === "function") drawFallback();
    }
  }
  function bindDownload(sel, url, name) {
    const a = $(sel);
    if (url) { a.href = url; a.download = name; a.classList.remove("is-disabled"); }
    else { a.removeAttribute("href"); a.classList.add("is-disabled"); }
  }

  /* ============================ Ошибки ================================= */
  function renderError(err) {
    const box = $("#warning");
    box.hidden = false;
    box.textContent = "Ошибка выполнения: " + (err && err.message ? err.message : String(err));
    pushMessage("Ошибка: " + (err && err.message ? err.message : err), "alert");
  }

  /* ============================ Запуск ================================= */
  async function runDemo(payload) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 120000);
    try {
      const res = await fetch("/api/navigation/autonomous-demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const raw = await res.json();
      return { data: normalizeResponse(raw, payload), mode: "backend" };
    } finally {
      clearTimeout(timeout);
    }
  }

  const form = $("#demoForm");
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!form.reportValidity()) return;

    const payload = collectPayload();
    revokeBlobs();
    $("#warning").hidden = true;
    $("#navBanner").hidden = true;
    pushMessage("Потеря сигнала GNSS — переход в автономный режим", "alert");
    setLoading(true);

    const minDelay = new Promise((r) => setTimeout(r, 2400)); // дать анимации шагов проиграться
    try {
      let result;
      try {
        result = await runDemo(payload);
      } catch (netErr) {
        // Бэкенд недоступен → автономный demo-режим.
        pushMessage("Бэкенд недоступен — автономный demo-режим", "");
        result = { data: buildSynthetic(payload), mode: "demo" };
      }
      await minDelay;
      setLoading(false);
      renderResults(result.data, payload);
      if (result.mode === "demo") {
        $("#formHint").textContent = "Показан автономный demo-режим (бэкенд не отвечает).";
      } else {
        $("#formHint").textContent = "Получен ответ автономного Terrain Lock алгоритма.";
      }
    } catch (err) {
      setLoading(false);
      renderError(err);
    }
  });

  /* ----------------- Перерисовка canvas при ресайзе --------------------- */
  let resizeRAF = null;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeRAF);
    resizeRAF = requestAnimationFrame(() => {
      const azEl = $("#m-azimuth").textContent;
      if (azEl && azEl !== "—") drawCompass(parseFloat(azEl));
    });
  });

  /* ----------------------- Инициализация вида --------------------------- */
  drawCompass(128);
  drawGauge(96);
  drawHeatmap("#heatmapDemo", 180);
})();
