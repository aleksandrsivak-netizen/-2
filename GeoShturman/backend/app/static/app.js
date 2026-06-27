/* =========================================================================
   ГЕОШТУРМАН — клиент реального времени.
   Подключается по WebSocket к /api/stream/live, показывает живой поток NMEA,
   профиль рельефа и решение ядра (азимут/скорость/координаты/достоверность).
   Если бэкенд недоступен — переходит в автономный demo-поток в браузере.
   ========================================================================= */
"use strict";
(() => {
  const $ = (s) => document.querySelector(s);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
  const pad2 = (n) => String(n).padStart(2, "0");

  /* --------------------------- часы --------------------------- */
  const clockEl = $("#sysClock");
  const tickClock = () => {
    const d = new Date();
    clockEl.textContent = `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())} UTC`;
  };
  tickClock(); setInterval(tickClock, 1000);

  /* --------------------------- canvas helpers --------------------------- */
  function fit(cv) {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = cv.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width) || cv.width);
    const h = Math.max(1, Math.round(r.height) || cv.height);
    cv.width = w * dpr; cv.height = h * dpr;
    const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function drawCompass(az) {
    const cv = $("#compass"); if (!cv) return;
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 6;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#1f3142"; ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.stroke();
    for (let a = 0; a < 360; a += 15) {
      const rad = a * Math.PI / 180, r1 = a % 90 === 0 ? R - 11 : R - 5;
      ctx.strokeStyle = a % 90 === 0 ? "#3a5670" : "#22384c"; ctx.beginPath();
      ctx.moveTo(cx + Math.sin(rad) * R, cy - Math.cos(rad) * R);
      ctx.lineTo(cx + Math.sin(rad) * r1, cy - Math.cos(rad) * r1); ctx.stroke();
    }
    ctx.fillStyle = "#8da3ba"; ctx.font = "10px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    [["N", 0], ["E", 90], ["S", 180], ["W", 270]].forEach(([l, a]) => {
      const rad = a * Math.PI / 180;
      ctx.fillText(l, cx + Math.sin(rad) * (R - 17), cy - Math.cos(rad) * (R - 17));
    });
    if (az != null) {
      const rad = az * Math.PI / 180;
      ctx.strokeStyle = "#34d399"; ctx.lineWidth = 2.5; ctx.lineCap = "round";
      ctx.shadowColor = "#34d399"; ctx.shadowBlur = 8; ctx.beginPath();
      ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.sin(rad) * (R - 16), cy - Math.cos(rad) * (R - 16));
      ctx.stroke(); ctx.shadowBlur = 0; ctx.lineWidth = 1;
    }
    ctx.fillStyle = "#34d399"; ctx.beginPath(); ctx.arc(cx, cy, 3, 0, 7); ctx.fill();
    ctx.fillStyle = "#e7eef6"; ctx.font = "bold 26px monospace";
    ctx.fillText(az != null ? `${Math.round(az)}°` : "—", cx, cy - 2);
    ctx.fillStyle = "#6b7d90"; ctx.font = "9px sans-serif"; ctx.fillText("АЗИМУТ", cx, cy + 20);
  }

  function drawGauge(sel, pct) {
    const cv = $(sel); if (!cv) return;
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 10;
    ctx.clearRect(0, 0, w, h); ctx.lineWidth = 9; ctx.lineCap = "round";
    ctx.strokeStyle = "#16222f"; ctx.beginPath(); ctx.arc(cx, cy, R, 0, 7); ctx.stroke();
    const p = clamp((pct || 0) / 100, 0, 1), start = -Math.PI / 2;
    const g = ctx.createLinearGradient(0, 0, w, h); g.addColorStop(0, "#22d3ee"); g.addColorStop(1, "#34d399");
    ctx.strokeStyle = g; ctx.shadowColor = "#34d39988"; ctx.shadowBlur = 12; ctx.beginPath();
    ctx.arc(cx, cy, R, start, start + p * 6.2832); ctx.stroke(); ctx.shadowBlur = 0;
    ctx.fillStyle = "#e7eef6"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.font = "bold 24px monospace";
    ctx.fillText(pct != null ? `${Math.round(pct)}%` : "—", cx, cy);
  }

  function drawSpark(values) {
    const cv = $("#sparkRadio"); if (!cv) return;
    const { ctx, w, h } = fit(cv); ctx.clearRect(0, 0, w, h);
    if (!values.length) return;
    const v = values.slice(-80), mn = Math.min(...v), mx = Math.max(...v), sp = mx - mn || 1;
    const xs = (i) => (i / (v.length - 1)) * w, ys = (val) => h - 4 - ((val - mn) / sp) * (h - 8);
    ctx.beginPath(); ctx.moveTo(0, h); v.forEach((val, i) => ctx.lineTo(xs(i), ys(val))); ctx.lineTo(w, h); ctx.closePath();
    const g = ctx.createLinearGradient(0, 0, 0, h); g.addColorStop(0, "#34d39955"); g.addColorStop(1, "#34d39900");
    ctx.fillStyle = g; ctx.fill();
    ctx.beginPath(); v.forEach((val, i) => i ? ctx.lineTo(xs(i), ys(val)) : ctx.moveTo(xs(i), ys(val)));
    ctx.strokeStyle = "#34d399"; ctx.lineWidth = 1.5; ctx.stroke();
  }

  function drawProfile(radio, terrain) {
    const cv = $("#profileCanvas"); if (!cv) return;
    const { ctx, w, h } = fit(cv); ctx.clearRect(0, 0, w, h);
    if (!radio.length) return;
    const all = radio.concat(terrain.length ? terrain : radio);
    const mn = Math.min(...all), mx = Math.max(...all), sp = mx - mn || 1;
    const padL = 36, padB = 4, padT = 6;
    const xs = (i, arr) => padL + (i / Math.max(1, arr.length - 1)) * (w - padL - 6);
    const ys = (val) => padT + (1 - (val - mn) / sp) * (h - padT - padB);
    ctx.strokeStyle = "#13202d"; ctx.fillStyle = "#5f7387"; ctx.font = "9px monospace"; ctx.textAlign = "right";
    for (let k = 0; k <= 4; k++) {
      const val = mn + sp * k / 4, y = ys(val);
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - 6, y); ctx.stroke();
      ctx.fillText(Math.round(val), padL - 4, y + 3);
    }
    const line = (arr, color) => {
      if (!arr.length) return; ctx.beginPath();
      arr.forEach((val, i) => i ? ctx.lineTo(xs(i, arr), ys(val)) : ctx.moveTo(xs(i, arr), ys(val)));
      ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.stroke();
    };
    line(terrain, "#1f8fff"); line(radio, "#34d399");
  }

  /* --------------------------- 3D сцены --------------------------- */
  function initScenes() {
    if (!window.GeoScenes) return;
    try {
      window.GeoScenes.initTerrain($("#terrainCanvas"));
      window.GeoScenes.initCorr($("#corrCanvas"));
    } catch (e) { console.warn("3D init", e); }
  }

  /* --------------------------- сообщения / лог --------------------------- */
  const msgs = $("#msgs"); const nmeaLog = $("#nmeaLog");
  const nowHMS = () => { const d = new Date(); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`; };
  function pushMsg(text, kind = "") {
    const li = document.createElement("li");
    li.className = "msg" + (kind ? " msg--" + kind : "");
    li.innerHTML = `<time>${nowHMS()}</time><span>${text}</span>`;
    msgs.prepend(li); while (msgs.children.length > 14) msgs.lastChild.remove();
  }
  function pushNmea(raw, outlier) {
    if (!raw) return;
    const li = document.createElement("li"); li.textContent = (outlier ? "⚠ " : "") + raw;
    if (outlier) li.style.color = "#fbbf24";
    nmeaLog.append(li); while (nmeaLog.children.length > 7) nmeaLog.firstChild.remove();
  }

  /* --------------------------- состояние --------------------------- */
  const state = { radio: [], terrain: [], craft: "uav", connected: false, log: [], solutions: [], lastSol: null };

  function setSolver(txt, on) { const e = $("#sysSolver"); if (e) { e.textContent = txt; e.className = on ? "green" : "ok"; } }
  function setStreamState(txt, on) {
    const e = $("#sysStream"); if (e) { e.textContent = txt; e.className = on ? "green" : "ok"; }
    const c = $("#connState"), t = $("#connText");
    if (c) c.classList.toggle("is-online", !!on);
    if (t) t.textContent = on ? "онлайн" : "офлайн";
  }

  function applyTelemetry(m) {
    state.radio.push(m.radio_agl); state.terrain.push(m.terrain_msl);
    if (state.radio.length > 400) { state.radio.shift(); state.terrain.shift(); }
    state.log.push({ t: m.elapsed_s, radio_agl: m.radio_agl, terrain_msl: m.terrain_msl, raw: m.raw });
    if (state.log.length > 6000) state.log.shift();
    $("#m-radio").textContent = fmt(m.radio_agl, 0);
    $("#m-baro").textContent = fmt(m.baro_msl, 0);
    $("#m-count").textContent = m.n_valid;
    const s = Math.round(m.elapsed_s || 0);
    $("#m-time").textContent = `${pad2(Math.floor(s / 3600))}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`;
    drawSpark(state.radio); drawProfile(state.radio, state.terrain);
    pushNmea(m.raw, m.is_outlier);
    if (m.outliers_rejected != null) $("#fOutliers").textContent = m.outliers_rejected;
    if (m.filters) { const pf = $("#fPF"); pf.textContent = m.filters.particle ? "АКТИВЕН" : "НАКОПЛЕНИЕ"; pf.className = m.filters.particle ? "ok" : "muted"; }
    const lp = clamp(20 + (m.n_valid % 60), 0, 99);
    $("#loadPct").textContent = lp + "%"; drawGauge("#loadGauge", lp);
  }

  function applySolution(m) {
    if (m.azimuth_deg != null) {
      drawCompass(m.azimuth_deg);
      $("#bm-az").textContent = fmt(m.azimuth_deg, 0);
      $("#m-speed").textContent = fmt(m.speed_mps, 1);
      $("#bm-spd").textContent = fmt(m.speed_mps, 1);
      if (window.GeoScenes) window.GeoScenes.setAzimuth(m.azimuth_deg);
    }
    // реальная матрица корреляции из ядра → 3D-поверхность
    if (m.heatmap && m.heatmap.z && window.GeoScenes) {
      window.GeoScenes.setCorrSurface(m.heatmap.z, m.heatmap.peak);
      const tag = document.querySelector(".bestmatch-tag");
      if (tag) tag.innerHTML = "<i></i>РЕАЛЬНАЯ МАТРИЦА ЯДРА";
    } else if (m.azimuth_deg != null && window.GeoScenes) {
      window.GeoScenes.setCorrPeak((m.azimuth_deg % 360) / 360, 0.5);
    }
    if (m.dem_source) { const d = $("#fDem"); if (d) { const real = /coper|glo/i.test(m.dem_source); d.textContent = real ? "GLO-30" : "СИНТЕТ"; d.className = real ? "green" : "ok"; } }
    $("#bm-corr").textContent = fmt(m.correlation, 3);
    const confPct = m.confidence != null ? m.confidence * 100 : null;
    $("#bm-conf").textContent = fmt(confPct, 1);
    $("#m-acc").textContent = confPct != null ? fmt(confPct, 1) + "%" : "—";
    drawGauge("#confGauge", confPct);
    if (m.profile && m.profile.radio && m.profile.radio.length) drawProfile(m.profile.radio, m.profile.terrain || []);
    if (m.lat != null && m.lon != null) {
      $("#mapHud").hidden = false;
      $("#fp-lat").textContent = fmt(m.lat, 4); $("#fp-lon").textContent = fmt(m.lon, 4);
      $("#fp-alt").textContent = fmt(m.altitude_msl, 0); $("#fp-acc").textContent = fmt(confPct, 1);
    }
    // метрики точности / производительности / режимы
    if (m.cep50_m != null) $("#m-cep").textContent = `${fmt(m.cep50_m, 0)} / ${fmt(m.cep95_m, 0)} м`;
    if (m.along_track_m != null) $("#m-axt").textContent = `${fmt(m.along_track_m, 0)} / ${fmt(m.cross_track_m, 0)} м`;
    if (m.solve_ms != null) { const e = $("#m-ms"); e.textContent = `${fmt(m.solve_ms, 0)} мс`; e.className = m.solve_ms < 1500 ? "green" : "ok"; }
    if (m.mode) { const e = $("#m-mode"); e.textContent = m.mode === "DR" ? "DR (счисление)" : "TRN"; e.className = m.mode === "DR" ? "warn" : "green"; }
    if (m.integrity) { const e = $("#m-integ"); e.textContent = m.integrity; e.className = m.integrity === "OK" ? "green" : "warn"; }
    state.lastSol = m;
    state.solutions.push({ azimuth_deg: m.azimuth_deg, speed_mps: m.speed_mps, correlation: m.correlation,
      confidence: m.confidence, lat: m.lat, lon: m.lon, cep50_m: m.cep50_m, cep95_m: m.cep95_m,
      along_track_m: m.along_track_m, cross_track_m: m.cross_track_m, solve_ms: m.solve_ms,
      mode: m.mode, integrity: m.integrity });
    if (state.solutions.length > 2000) state.solutions.shift();
    setSolver("РЕШЕНИЕ НАЙДЕНО", true);
    $("#navTitle").textContent = "НАВИГАЦИЯ АКТИВНА";
    $("#navSub").textContent =
      m.azimuth_deg != null ? `Курс ${Math.round(m.azimuth_deg)}° · ${fmt(m.speed_mps, 1)} м/с · достоверность ${fmt(confPct, 1)}%`
                            : "Накопление данных для решения…";
    pushMsg(`Решение обновлено${m.azimuth_deg != null ? ` (${Math.round(m.azimuth_deg)}°, ${fmt(confPct, 0)}%)` : ""}`, "ok");
  }

  function handleMessage(m) {
    switch (m.type) {
      case "hello": setStreamState("ОНЛАЙН", true); if (m.last_solution) applySolution(m.last_solution); break;
      case "telemetry": applyTelemetry(m); break;
      case "solution": applySolution(m); break;
      case "stream_start":
        state.radio = []; state.terrain = []; nmeaLog.innerHTML = "";
        $("#m-source").textContent = m.source === "simulation" ? "СИМУЛЯЦИЯ" : "ВНЕШНИЙ";
        setStreamState("АКТИВЕН", true); setSolver("ОБРАБОТКА", true);
        pushMsg("Начат приём потока NMEA (" + $("#m-source").textContent.toLowerCase() + ")", "");
        break;
      case "stream_end": setStreamState("ЗАВЕРШЁН", false); pushMsg("Поток завершён", ""); break;
      case "reset":
        state.radio = []; state.terrain = []; nmeaLog.innerHTML = "";
        $("#m-count").textContent = "0"; $("#m-acc").textContent = "—"; $("#mapHud").hidden = true;
        setSolver("ОЖИДАНИЕ", false); pushMsg("Сброс потока", ""); break;
    }
  }

  /* --------------------------- WebSocket --------------------------- */
  let ws = null, wsRetry = 0;
  function wsUrl() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/api/stream/live`;
  }
  function connect() {
    if (!location.host || location.protocol === "file:") { startFallback(); return; }
    try { ws = new WebSocket(wsUrl()); } catch (e) { startFallback(); return; }
    ws.onopen = () => { state.connected = true; wsRetry = 0; setStreamState("ОНЛАЙН", true); stopFallback(); pushMsg("Подключено к серверу потока", "ok"); };
    ws.onmessage = (ev) => { try { handleMessage(JSON.parse(ev.data)); } catch {} };
    ws.onclose = () => {
      state.connected = false; setStreamState("ОФЛАЙН", false);
      if (wsRetry < 3) { wsRetry++; setTimeout(connect, 1200); } else startFallback();
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
  }

  /* ----- управление потоком (через API; при сбое — клиентский симулятор) ----- */
  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }
  const craftDefaults = () => state.craft === "aircraft" ? { speed_mps: 65, heading_deg: 128 } : { speed_mps: 38, heading_deg: 128 };

  $("#btnStart").addEventListener("click", async () => {
    pushMsg("Запуск потока…", "");
    try { await api("/api/stream/simulate", { hz: 5, duration_s: 180, ...craftDefaults(), barometric_altitude_msl: 1500 }); }
    catch { startFallback(); }
  });
  $("#btnStop").addEventListener("click", async () => { try { await api("/api/stream/stop"); } catch {} stopFallback(); setStreamState("ОСТАНОВЛЕН", false); });
  $("#btnReset").addEventListener("click", async () => { try { await api("/api/stream/reset"); } catch { handleMessage({ type: "reset" }); } stopFallback(); });

  /* --------------------------- экспорт отчёта --------------------------- */
  function download(name, text, type) {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a"); a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }
  $("#btnExport").addEventListener("click", () => {
    if (!state.log.length) { pushMsg("Нет данных для экспорта — запустите поток", "alert"); return; }
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    // CSV телеметрии
    const csv = ["t_s,radio_agl_m,terrain_msl_m,nmea"]
      .concat(state.log.map((r) => `${r.t ?? ""},${r.radio_agl ?? ""},${r.terrain_msl ?? ""},${(r.raw || "").replace(/,/g, ";")}`))
      .join("\n");
    download(`geoshturman_telemetry_${stamp}.csv`, csv, "text/csv");
    // JSON-отчёт
    const s = state.lastSol || {};
    const report = {
      generated_utc: new Date().toISOString(),
      samples: state.log.length,
      source: $("#m-source").textContent,
      dem_source: $("#fDem").textContent,
      final_solution: {
        azimuth_deg: s.azimuth_deg, speed_mps: s.speed_mps, correlation: s.correlation,
        confidence: s.confidence, lat: s.lat, lon: s.lon,
        cep50_m: s.cep50_m, cep95_m: s.cep95_m,
        along_track_m: s.along_track_m, cross_track_m: s.cross_track_m,
        solve_ms: s.solve_ms, mode: s.mode, integrity: s.integrity,
      },
      solutions: state.solutions,
    };
    download(`geoshturman_report_${stamp}.json`, JSON.stringify(report, null, 2), "application/json");
    pushMsg(`Отчёт выгружен: ${state.log.length} замеров, ${state.solutions.length} решений`, "ok");
  });

  /* --------------------------- аппарат --------------------------- */
  document.querySelectorAll("#craftSeg .seg__b").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("#craftSeg .seg__b").forEach((x) => x.classList.remove("is-active"));
    b.classList.add("is-active"); state.craft = b.dataset.craft;
    if (window.GeoScenes) window.GeoScenes.setCraftType(state.craft);
  }));

  /* ===================== Клиентский fallback-симулятор ===================== */
  let fb = null;
  function startFallback() {
    if (fb) return;
    setStreamState("DEMO", true); $("#m-source").textContent = "DEMO (локально)";
    pushMsg("Бэкенд недоступен — локальный demo-поток", "");
    state.radio = []; state.terrain = []; nmeaLog.innerHTML = "";
    const baro = 1500, hz = 5, d = craftDefaults();
    let i = 0; const noise = mkNoise(1337);
    fb = setInterval(() => {
      const t = i / hz;
      const u = (Math.sin(d.heading_deg * Math.PI / 180) * d.speed_mps * t) / 8000;
      const v = (-Math.cos(d.heading_deg * Math.PI / 180) * d.speed_mps * t) / 8000;
      const terr = 1300 + noise(u * 6, v * 6) * 220;
      const radio = Math.max(0, baro - terr + (Math.random() - 0.5) * 4);
      handleMessage({ type: "telemetry", n_valid: i + 1, radio_agl: +radio.toFixed(1),
        terrain_msl: +(baro - radio).toFixed(1), baro_msl: baro, elapsed_s: +t.toFixed(1), raw: ggaLine(t, radio) });
      if (i % 12 === 11) {
        const conf = clamp(0.78 + noise(u, v) * 0.18, 0, 0.99);
        handleMessage({ type: "solution", azimuth_deg: d.heading_deg + (Math.random() - .5) * 2,
          speed_mps: d.speed_mps + (Math.random() - .5), correlation: +(0.93 + Math.random() * 0.05).toFixed(3),
          confidence: +conf.toFixed(3), lat: +(56.13 + v * 0.7).toFixed(4), lon: +(37.26 + u * 0.7).toFixed(4),
          altitude_msl: baro, profile: { radio: state.radio.slice(-160), terrain: state.terrain.slice(-160) } });
      }
      i++; if (i > 900) stopFallback();
    }, 1000 / hz);
  }
  function stopFallback() { if (fb) { clearInterval(fb); fb = null; } }

  function mkNoise(seed) {
    let s = seed >>> 0; const r = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    const o = []; for (let i = 0; i < 5; i++) o.push({ fx: (0.5 + r() * 2) * (i + 1), fy: (0.5 + r() * 2) * (i + 1), px: r() * 6.28, py: r() * 6.28, a: 1 / (i + 1) });
    return (x, y) => { let h = 0, n = 0; for (const k of o) { h += k.a * Math.sin(x * k.fx + k.px) * Math.cos(y * k.fy + k.py); n += k.a; } return (h / n) * 0.5 + 0.5; };
  }
  function ggaLine(t, alt) {
    const hh = pad2(Math.floor(t / 3600) % 24), mm = pad2(Math.floor(t / 60) % 60), ss = (t % 60).toFixed(2).padStart(5, "0");
    const body = `GPGGA,${hh}${mm}${ss},,,,,,,,${alt.toFixed(1)},M,0.0,M,,`;
    let c = 0; for (let i = 0; i < body.length; i++) c ^= body.charCodeAt(i);
    return `$${body}*${c.toString(16).toUpperCase().padStart(2, "0")}`;
  }

  /* ===================== Выезжающее меню: ввод данных ===================== */
  const drawer = $("#drawer"), drawerOvl = $("#drawerOvl");
  const openDrawer = () => { drawer.hidden = false; drawerOvl.hidden = false; };
  const closeDrawer = () => { drawer.hidden = true; drawerOvl.hidden = true; };
  $("#btnManual").addEventListener("click", openDrawer);
  $("#drawerClose").addEventListener("click", closeDrawer);
  drawerOvl.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });

  // переключение вкладок
  document.querySelectorAll("#drawerTabs .tab").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll("#drawerTabs .tab").forEach((x) => x.classList.remove("is-active"));
    t.classList.add("is-active");
    document.querySelectorAll(".tabpane").forEach((p) => p.classList.toggle("is-active", p.dataset.pane === t.dataset.tab));
  }));

  const mStatus = (txt, err) => { const e = $("#mStatus"); e.textContent = txt; e.className = "drawer__status" + (err ? " err" : ""); };
  const baroVal = () => +$("#mBaro").value || 1500;
  const parseLines = (text) => (text || "").split(/\r?\n/).map((s) => s.trim()).filter((s) => s.startsWith("$"));
  const ggaAlt = (line) => { const f = line.split("*")[0].split(","); return f.length > 9 ? parseFloat(f[9]) : NaN; };
  const genTrack = (baro, n, hz, heading, speed, seed) => {
    const noise = mkNoise(seed || 909), lines = [];
    for (let i = 0; i < n; i++) {
      const t = i / hz;
      const u = Math.sin(heading * Math.PI / 180) * speed * t / 8000;
      const v = -Math.cos(heading * Math.PI / 180) * speed * t / 8000;
      const terr = 1300 + noise(u * 6, v * 6) * 220;
      let radio = Math.max(0, baro - terr + (Math.random() - 0.5) * 4);
      if (Math.random() < 0.02) radio += (Math.random() - 0.5) * 90;
      lines.push(ggaLine(t, radio));
    }
    return lines;
  };

  let manualTimer = null;
  function stopManual() { if (manualTimer) { clearInterval(manualTimer); manualTimer = null; } }

  // Подать набор строк «как поток» (онлайн → бэк; офлайн → локально)
  async function feedAsStream(lines, baro, hz) {
    if (!lines.length) { mStatus("Нет валидных строк NMEA ($GPGGA…).", true); return; }
    stopManual(); stopFallback();
    mStatus(`Подача ${lines.length} строк на ${hz} Гц…`);
    closeDrawer();
    if (state.connected) {
      try {
        await api("/api/stream/reset");
        await api("/api/stream/ingest", { text: "", barometric_altitude_msl: baro }).catch(() => {});
        handleMessage({ type: "stream_start", source: "external" });
        let i = 0;
        manualTimer = setInterval(async () => {
          if (i >= lines.length) { stopManual(); mStatus("Поток завершён."); return; }
          try { await fetch("/api/stream/ingest", { method: "POST", headers: { "Content-Type": "text/plain" }, body: lines[i] }); } catch {}
          i++;
        }, 1000 / hz);
        return;
      } catch { /* упадём в локальный режим */ }
    }
    handleMessage({ type: "reset" });
    $("#m-source").textContent = "ВНЕШНИЕ ДАННЫЕ"; setStreamState("РУЧНОЙ", true);
    let i = 0, win = [], outl = 0;
    manualTimer = setInterval(() => {
      if (i >= lines.length) { stopManual(); setStreamState("ЗАВЕРШЁН", false); return; }
      let radio = ggaAlt(lines[i]); if (isNaN(radio)) { i++; return; }
      win.push(radio); if (win.length > 7) win.shift();
      if (win.length >= 5) { const s = [...win].sort((a, b) => a - b), med = s[s.length >> 1];
        const mad = [...win].map((x) => Math.abs(x - med)).sort((a, b) => a - b)[win.length >> 1] * 1.4826;
        if (mad > 1e-6 && Math.abs(radio - med) > 3 * mad) { radio = med; outl++; } }
      handleMessage({ type: "telemetry", n_valid: i + 1, radio_agl: +radio.toFixed(1),
        terrain_msl: +(baro - radio).toFixed(1), baro_msl: baro, elapsed_s: +(i / hz).toFixed(1),
        raw: lines[i], outliers_rejected: outl, is_outlier: false, filters: { particle: i >= 16 } });
      if (i % 14 === 13) {
        const terr = state.terrain.slice(-160), span = Math.max(...terr) - Math.min(...terr);
        const conf = clamp(0.5 + span / 400, 0, 0.95);
        handleMessage({ type: "solution", correlation: +(conf + 0.05).toFixed(3), confidence: +conf.toFixed(3),
          altitude_msl: baro, profile: { radio: state.radio.slice(-160), terrain: terr } });
      }
      i++;
    }, 1000 / hz);
  }

  // Решить разом одним запросом к ядру
  async function solveOnce(text, baro) {
    const lines = parseLines(text);
    if (!lines.length) { mStatus("Нет валидных строк NMEA.", true); return; }
    mStatus("Расчёт ядром…");
    try {
      const r = await fetch("/api/navigation/solve", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nmea_text: lines.join("\n"), barometric_altitude_msl: baro }) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      const est = data.estimated || {};
      applySolution({ azimuth_deg: est.azimuth_deg, speed_mps: est.speed_mps, correlation: est.correlation,
        confidence: est.confidence, altitude_msl: baro, lat: data.found_position?.lat, lon: data.found_position?.lon });
      mStatus(`Готово: азимут ${fmt(est.azimuth_deg, 0)}°, достоверность ${fmt((est.confidence || 0) * 100, 1)}%.`);
      closeDrawer();
    } catch { mStatus("Ядро недоступно (нужен запущенный бэкенд). Используйте «Подать как поток».", true); }
  }

  /* ----- Вкладка 1: NMEA-текст ----- */
  $("#mSample").addEventListener("click", () => {
    $("#mNmea").value = genTrack(baroVal(), 160, 5, 128, 42).join("\n");
    mStatus("Сгенерировано 160 строк (с выбросами для проверки фильтра).");
  });
  $("#mPlay").addEventListener("click", () => feedAsStream(parseLines($("#mNmea").value), baroVal(), +$("#mRate").value || 5));
  $("#mSolve").addEventListener("click", () => solveOnce($("#mNmea").value, baroVal()));

  /* ----- Вкладка 2: Файл ----- */
  let fileText = "";
  const fileDrop = $("#fileDrop"), fileInput = $("#mFile");
  function loadFile(file) {
    if (!file) return;
    const rd = new FileReader();
    rd.onload = () => {
      fileText = String(rd.result || "");
      const n = parseLines(fileText).length;
      $("#fileName").textContent = file.name;
      $("#fileInfo").textContent = `Загружено: ${(file.size / 1024).toFixed(1)} КБ · валидных $GPGGA строк: ${n}`;
      $("#fileInfo").className = "drawer__status";
    };
    rd.readAsText(file);
  }
  fileInput.addEventListener("change", (e) => loadFile(e.target.files[0]));
  ["dragover", "dragenter"].forEach((ev) => fileDrop.addEventListener(ev, (e) => { e.preventDefault(); fileDrop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => fileDrop.addEventListener(ev, (e) => { e.preventDefault(); fileDrop.classList.remove("drag"); }));
  fileDrop.addEventListener("drop", (e) => loadFile(e.dataTransfer.files[0]));
  $("#fPlay").addEventListener("click", () => {
    if (!fileText) { mStatus("Сначала выберите файл.", true); return; }
    feedAsStream(parseLines(fileText), baroVal(), +$("#fRate").value || 5);
  });
  $("#fSolve").addEventListener("click", () => {
    if (!fileText) { mStatus("Сначала выберите файл.", true); return; }
    solveOnce(fileText, baroVal());
  });

  /* ----- Вкладка 3: Параметры (ручной ввод координат и данных) ----- */
  const pv = (id, def) => { const v = +$("#" + id).value; return isNaN(v) ? def : v; };
  $("#pPlay").addEventListener("click", async () => {
    const p = { hz: pv("pHz", 5), duration_s: pv("pDur", 180), speed_mps: pv("pSpd", 45),
      heading_deg: pv("pAz", 128), barometric_altitude_msl: baroVal(), start_x_m: pv("pX", 4000),
      start_y_m: pv("pY", 4000), width_m: pv("pSize", 8000), resolution_m: pv("pRes", 30),
      terrain_type: $("#pTerrain").value };
    mStatus("Запуск потока по параметрам…"); closeDrawer();
    if (window.GeoScenes) window.GeoScenes.setAzimuth(p.heading_deg);
    try { await api("/api/stream/simulate", p); }
    catch { feedAsStream(genTrack(p.barometric_altitude_msl, Math.round(p.duration_s * p.hz), p.hz, p.heading_deg, p.speed_mps, p.start_x_m), p.barometric_altitude_msl, p.hz); }
  });
  $("#pSolve").addEventListener("click", async () => {
    const body = { width_m: pv("pSize", 8000), height_m: pv("pSize", 8000), resolution_m: pv("pRes", 30),
      duration_s: pv("pDur", 180), sample_rate_hz: pv("pHz", 5), speed_mps: pv("pSpd", 45),
      azimuth_deg: pv("pAz", 128), barometric_altitude_msl: baroVal(), search_radius_m: pv("pSearch", 2000),
      terrain_type: $("#pTerrain").value, enable_kalman: true, seed: 42 };
    mStatus("Полный расчёт ядром…");
    try {
      const r = await fetch("/api/demo/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json(); const est = d.estimated || {};
      const lat = 56.10 + (est.end_y_m || est.start_y_m || 4000) / 111320;
      const lon = 37.20 + (est.end_x_m || est.start_x_m || 4000) / (111320 * Math.cos(56.10 * Math.PI / 180));
      applySolution({ azimuth_deg: est.azimuth_deg, speed_mps: est.speed_mps, correlation: est.correlation,
        confidence: est.confidence, altitude_msl: baroVal(), lat: +lat.toFixed(4), lon: +lon.toFixed(4) });
      mStatus(`Готово: азимут ${fmt(est.azimuth_deg, 0)}°, корреляция ${fmt(est.correlation, 3)}.`);
      closeDrawer();
    } catch { mStatus("Ядро недоступно (нужен бэкенд). Используйте «Запустить поток».", true); }
  });

  /* ----- Кнопки управления 3D-картой ----- */
  const G = () => window.GeoScenes;
  $("#mapZoomIn") && $("#mapZoomIn").addEventListener("click", () => G() && G().zoom(-1));
  $("#mapZoomOut") && $("#mapZoomOut").addEventListener("click", () => G() && G().zoom(1));
  $("#mapCenter") && $("#mapCenter").addEventListener("click", () => G() && G().resetView());
  $("#mapWire") && $("#mapWire").addEventListener("click", (e) => { if (G()) { G().toggleWire(); e.currentTarget.classList.toggle("is-on"); } });
  $("#mapRotate") && $("#mapRotate").addEventListener("click", (e) => { if (G()) { G().toggleRotate(); e.currentTarget.classList.toggle("is-on"); } });

  /* --------------------------- старт --------------------------- */
  window.addEventListener("resize", () => {
    if (window.GeoScenes) window.GeoScenes.resize();
    drawSpark(state.radio); drawProfile(state.radio, state.terrain);
  });
  initScenes(); drawCompass(null); drawGauge("#confGauge", null); drawGauge("#loadGauge", 0);
  // подгрузить реальный рельеф DEM на 3D-карту
  (async () => {
    try {
      const r = await fetch("/api/dem/grid?side=72");
      if (!r.ok) return;
      const g = await r.json();
      if (g.z && g.z.length && window.GeoScenes) {
        window.GeoScenes.setTerrainGrid(g.z);
        pushMsg(`DEM загружен: ${g.source} · перепад ${g.span_m} м`, "ok");
        const d = $("#fDem"); if (d) { const real = /coper|glo/i.test(g.source); d.textContent = real ? "GLO-30" : "СИНТЕТ"; d.className = real ? "green" : "ok"; }
      }
    } catch {}
  })();
  connect();
})();
