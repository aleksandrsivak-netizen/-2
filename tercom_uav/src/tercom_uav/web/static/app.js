const state = {
  mode: "demo",
  routeMode: "simple",
  activeRun: null,
  routeRunName: null,
  maps: [],
};

const form = document.querySelector("#runForm");
const routeForm = document.querySelector("#routeForm");
const runButton = document.querySelector("#runButton");
const runMessage = document.querySelector("#runMessage");
const routeMessage = document.querySelector("#routeMessage");
const runsList = document.querySelector("#runsList");
const refreshRunsButton = document.querySelector("#refreshRunsButton");
const refreshMapsButton = document.querySelector("#refreshMapsButton");
const buildRouteButton = document.querySelector("#buildRouteButton");
const generateRouteNmeaButton = document.querySelector("#generateRouteNmeaButton");
const runRouteTercomButton = document.querySelector("#runRouteTercomButton");
const statusDot = document.querySelector("#serverStatus");
const summaryLink = document.querySelector("#summaryLink");
const reportLink = document.querySelector("#reportLink");
const confidenceTile = document.querySelector("#confidenceTile");
const mapSelect = document.querySelector("#mapSelect");
const mapDescription = document.querySelector("#mapDescription");
const modeHelp = document.querySelector("#modeHelp");
const demPathInput = form.elements.demPath;
const routeMetrics = document.querySelector("#routeMetrics");
const routeWarnings = document.querySelector("#routeWarnings");

const routeNumericFields = new Set([
  "routeBaroAlt",
  "routeSimpleX0",
  "routeSimpleY0",
  "routeSimpleHeading",
  "routeSimpleSpeed",
  "routeSimpleDuration",
  "routeSimpleHz",
  "routeWaypointSpeed",
  "routeWaypointHz",
  "routeAutoStartX",
  "routeAutoStartY",
  "routeAutoEndX",
  "routeAutoEndY",
  "routeAutoSpeed",
  "routeAutoHz",
  "routeAutoDesiredLength",
  "routeAutoDesiredDuration",
  "routeAutoMinObservability",
  "routeNmeaHz",
  "routeNmeaNoiseStd",
  "routeNmeaOutlierProb",
  "routeNmeaDropoutProb",
  "routeNmeaDriftMps",
]);

const fieldGroups = {
  nmeaPath: document.querySelector("#nmeaPathGroup"),
  truthPath: document.querySelector("#truthPathGroup"),
  heading: document.querySelector("#headingGroup"),
  duration: document.querySelector("#durationGroup"),
  hz: document.querySelector("#hzGroup"),
  noise: document.querySelector("#noiseGroup"),
  outlier: document.querySelector("#outlierGroup"),
  dropout: document.querySelector("#dropoutGroup"),
  drift: document.querySelector("#driftGroup"),
  seed: document.querySelector("#seedGroup"),
};

const imageTargets = {
  "route_plan.png": document.querySelector("#routePlanImage"),
  "dem_tracks.png": document.querySelector("#demTracksImage"),
  "correlation_heatmap.png": document.querySelector("#heatmapImage"),
  "terrain_profile.png": document.querySelector("#profileImage"),
  "speed.png": document.querySelector("#speedImage"),
  "confidence.png": document.querySelector("#confidenceImage"),
};

function numberOrDefault(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function formatNumber(value, digits = 2, suffix = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }
  return `${Number(value).toFixed(digits)}${suffix}`;
}

function setMessage(text, isError = false) {
  runMessage.textContent = text;
  runMessage.classList.toggle("error", isError);
}

function setRouteMessage(text, isError = false) {
  routeMessage.textContent = text;
  routeMessage.classList.toggle("error", isError);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function selectedMap() {
  return state.maps.find((map) => map.id === mapSelect.value);
}

function updateMapFields() {
  const map = selectedMap();
  const manual = mapSelect.value === "manual";
  demPathInput.disabled = !manual;
  if (map && map.path && !manual) {
    demPathInput.value = map.path;
  }
  if (!manual && (!map || !map.path)) {
    demPathInput.value = "";
  }
  if (manual) {
    mapDescription.textContent =
      "Введите абсолютный путь к GeoTIFF/VRT. Например: /Users/.../dem.tif.";
  } else if (map) {
    mapDescription.textContent = map.description || map.label;
  } else {
    mapDescription.textContent = "Карта не выбрана.";
  }
}

function readPayload() {
  const data = new FormData(form);
  const toggles = Object.fromEntries(
    [...document.querySelectorAll(".toggle-grid input")].map((input) => [input.name, input.checked]),
  );
  const mapId = mapSelect.value || "synthetic";
  return {
    mapId,
    demPath: mapId === "manual" ? data.get("demPath") : "",
    nmeaPath: data.get("nmeaPath"),
    truthPath: data.get("truthPath"),
    baroAlt: numberOrDefault(data.get("baroAlt"), 1500),
    speed: numberOrDefault(data.get("speed"), 55),
    speedHint: numberOrDefault(data.get("speed"), 55),
    heading: numberOrDefault(data.get("heading"), 73),
    duration: numberOrDefault(data.get("duration"), 90),
    hz: numberOrDefault(data.get("hz"), 5),
    noiseStd: numberOrDefault(data.get("noiseStd"), 2.5),
    outlierProb: numberOrDefault(data.get("outlierProb"), 0),
    dropoutProb: numberOrDefault(data.get("dropoutProb"), 0),
    driftMps: numberOrDefault(data.get("driftMps"), 0),
    shiftStep: numberOrDefault(data.get("shiftStep"), 120),
    randomSeed: numberOrDefault(data.get("randomSeed"), 42),
    ...toggles,
    coarseToFine: toggles.strictMode ? false : Boolean(toggles.coarseToFine),
  };
}

function readRoutePayload() {
  const payload = {
    ...readPayload(),
    routeMode: state.routeMode,
  };
  const data = new FormData(routeForm);
  data.forEach((value, key) => {
    if (routeNumericFields.has(key) && String(value).trim() !== "") {
      payload[key] = numberOrDefault(value, 0);
    } else {
      payload[key] = value;
    }
  });
  return payload;
}

function readRouteNmeaPayload() {
  const payload = {
    runName: state.routeRunName,
    strictMode: readPayload().strictMode,
    coarseToFine: readPayload().coarseToFine,
    useKalman: readPayload().useKalman,
    shiftStep: readPayload().shiftStep,
  };
  const data = new FormData(routeForm);
  [
    "routeNmeaHz",
    "routeNmeaNoiseStd",
    "routeNmeaOutlierProb",
    "routeNmeaDropoutProb",
    "routeNmeaDriftMps",
  ].forEach((key) => {
    payload[key] = numberOrDefault(data.get(key), key === "routeNmeaHz" ? 5 : 0);
  });
  return payload;
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  const isLocalize = mode === "localize";
  fieldGroups.nmeaPath.classList.toggle("hidden", !isLocalize);
  fieldGroups.truthPath.classList.toggle("hidden", !isLocalize);
  fieldGroups.heading.classList.toggle("hidden", isLocalize);
  fieldGroups.duration.classList.toggle("hidden", isLocalize);
  fieldGroups.hz.classList.toggle("hidden", isLocalize);
  fieldGroups.noise.classList.toggle("hidden", isLocalize);
  fieldGroups.outlier.classList.toggle("hidden", isLocalize);
  fieldGroups.dropout.classList.toggle("hidden", isLocalize);
  fieldGroups.drift.classList.toggle("hidden", isLocalize);
  fieldGroups.seed.classList.toggle("hidden", isLocalize);
  modeHelp.textContent = isLocalize
    ? "Локализация читает готовый NMEA-файл, строит профиль рельефа и ищет его на выбранной карте."
    : "Демо использует быстрый обзорный поиск; строгий full-grid режим можно включить отдельным флагом.";
}

function setRouteMode(mode) {
  state.routeMode = mode;
  document.querySelectorAll(".route-mode-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.routeMode === mode);
  });
  document.querySelectorAll("[data-route-fields]").forEach((group) => {
    group.classList.toggle("hidden", group.dataset.routeFields !== mode);
  });
}

function metric(id, value) {
  document.querySelector(`#${id}`).textContent = value;
}

function renderDetails(summary) {
  const estimate = summary.estimate || {};
  const correlation = summary.correlation || {};
  const metrics = summary.metrics || {};
  const details = [
    ["Лучший сдвиг", formatNumber(correlation.best_shift_m, 1, " м")],
    ["Pearson r", formatNumber(correlation.best_score, 4)],
    ["Второй максимум", formatNumber(correlation.second_best_score, 4)],
    ["Разделимость", formatNumber(correlation.discrimination_ratio, 4)],
    ["Шероховатость", formatNumber(metrics.terrain_roughness_score, 2)],
    ["Наблюдаемость", formatNumber(metrics.observability_score, 3)],
    ["x", formatNumber(estimate.x_m, 1, " м")],
    ["y", formatNumber(estimate.y_m, 1, " м")],
  ];
  document.querySelector("#detailsGrid").innerHTML = details
    .map((item) => `<div class="detail-cell"><span>${item[0]}</span><strong>${item[1]}</strong></div>`)
    .join("");
}

function suitabilityLabel(value) {
  if (value === "high") {
    return "высокая";
  }
  if (value === "medium") {
    return "средняя";
  }
  if (value === "low") {
    return "низкая";
  }
  return "—";
}

function renderRouteSummary(summary) {
  const route = summary.route || null;
  const warnings = summary.route_warnings || [];
  const hasRoute = Boolean(route && route.length_m !== undefined);
  if (!hasRoute) {
    routeMetrics.innerHTML = "";
    routeWarnings.innerHTML = "";
    generateRouteNmeaButton.disabled = true;
    runRouteTercomButton.disabled = true;
    return;
  }
  state.routeRunName = summary.run_name;
  generateRouteNmeaButton.disabled = false;
  runRouteTercomButton.disabled = false;
  const metrics = [
    ["Длина", formatNumber(route.length_m, 0, " м")],
    ["Время", formatNumber(route.duration_s, 1, " с")],
    ["Средняя скорость", formatNumber(route.mean_speed_mps, 1, " м/с")],
    ["Средний азимут", formatNumber(route.mean_heading_deg, 1, "°")],
    ["Рельеф min/max", `${formatNumber(route.min_terrain_m, 0, " м")} / ${formatNumber(route.max_terrain_m, 0, " м")}`],
    ["Шероховатость", formatNumber(route.roughness, 2)],
    ["Наблюдаемость", formatNumber(route.observability, 3)],
    ["Пригодность TERCOM", suitabilityLabel(route.tercom_suitability)],
    ["Точек", formatNumber(route.point_count, 0)],
  ];
  routeMetrics.innerHTML = metrics
    .map((item) => `<div class="route-metric"><span>${item[0]}</span><strong>${item[1]}</strong></div>`)
    .join("");
  routeWarnings.innerHTML = warnings.length
    ? warnings.map((warning) => `<div class="route-warning">${escapeHtml(warning)}</div>`).join("")
    : `<div class="route-ok">Рельеф пригоден для демонстрации TERCOM, явных предупреждений нет.</div>`;
}

function renderSummary(summary) {
  state.activeRun = summary.run_name;
  const metrics = summary.metrics || {};
  const estimate = summary.estimate || {};
  const urls = summary.artifact_urls || {};
  const cacheKey = Date.now();

  metric("metricConfidence", formatNumber(metrics.confidence_score, 2));
  metric("metricAmbiguity", metrics.ambiguity_flag === true ? "Да" : metrics.ambiguity_flag === false ? "Нет" : "—");
  metric("metricAzimuth", formatNumber(estimate.azimuth_deg, 1, "°"));
  metric("metricSpeed", formatNumber(estimate.speed_mps, 1, " м/с"));
  metric("metricX", formatNumber(estimate.x_m, 1, " м"));
  metric("metricY", formatNumber(estimate.y_m, 1, " м"));
  metric("metricHorizontalError", formatNumber(metrics.horizontal_error_m, 1, " м"));
  metric("metricHeadingError", formatNumber(metrics.heading_error_deg, 2, "°"));
  const confidenceValue = Number(metrics.confidence_score);
  confidenceTile.classList.toggle("bad", Number.isFinite(confidenceValue) && confidenceValue < 0.35);
  confidenceTile.classList.toggle("warn", Number.isFinite(confidenceValue) && confidenceValue >= 0.35 && confidenceValue < 0.65);

  document.querySelector("#activeRunTitle").textContent = summary.run_name || "Запуск";
  document.querySelector("#activeRunPath").textContent = summary.run_dir || "";
  renderDetails(summary);
  renderRouteSummary(summary);

  Object.entries(imageTargets).forEach(([name, image]) => {
    if (urls[name]) {
      image.src = `${urls[name]}?t=${cacheKey}`;
    } else {
      image.removeAttribute("src");
    }
  });

  if (urls["summary.json"]) {
    summaryLink.href = urls["summary.json"];
    summaryLink.classList.remove("disabled");
  } else {
    summaryLink.href = "#";
    summaryLink.classList.add("disabled");
  }

  if (urls["report.html"]) {
    reportLink.href = urls["report.html"];
    reportLink.classList.remove("disabled");
  } else {
    reportLink.href = "#";
    reportLink.classList.add("disabled");
  }

  document.querySelectorAll(".run-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.runName === state.activeRun);
  });
}

async function postJson(endpoint, payload) {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(result.error || "Запрос завершился ошибкой");
  }
  return result;
}

async function loadMaps() {
  const previous = mapSelect.value;
  const response = await fetch("/api/maps");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Не удалось загрузить список карт");
  }
  state.maps = [
    ...(payload.maps || []),
    {
      id: "manual",
      label: "Указать путь вручную",
      kind: "manual",
      path: null,
      description: "Используйте, если DEM лежит вне рабочей папки проекта.",
    },
  ];
  mapSelect.replaceChildren();
  state.maps.forEach((map) => {
    const option = document.createElement("option");
    option.value = map.id;
    option.textContent = map.label;
    mapSelect.appendChild(option);
  });
  if (state.maps.some((map) => map.id === previous)) {
    mapSelect.value = previous;
  } else {
    mapSelect.value = "synthetic";
  }
  updateMapFields();
}

async function loadRuns() {
  const response = await fetch("/api/runs");
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Не удалось загрузить историю");
  }
  statusDot.classList.add("online");
  runsList.innerHTML = "";
  if (!payload.runs.length) {
    runsList.innerHTML = `<div class="run-item"><strong>Пока нет запусков</strong><span>Запустите демо-сценарий.</span></div>`;
    return;
  }
  payload.runs.forEach((run) => {
    const button = document.createElement("button");
    button.className = "run-item";
    button.type = "button";
    button.dataset.runName = run.run_name;
    button.innerHTML = `
      <strong>${run.run_name}</strong>
      <span>${run.created_at}</span>
      <span>conf ${formatNumber(run.confidence_score, 2)} · az ${formatNumber(run.azimuth_deg, 1, "°")}</span>
    `;
    button.addEventListener("click", () => loadRunSummary(run.run_name));
    runsList.appendChild(button);
  });
}

async function loadRunSummary(runName) {
  setMessage(`Открываю ${runName}`);
  const response = await fetch(`/api/runs/${encodeURIComponent(runName)}/summary`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Не удалось открыть запуск");
  }
  renderSummary(payload);
  setMessage("Готово к запуску");
}

async function submitRun() {
  runButton.disabled = true;
  setMessage(state.mode === "demo" ? "Запускаю демо-сценарий..." : "Запускаю локализацию...");
  try {
    const endpoint = state.mode === "demo" ? "/api/demo" : "/api/localize";
    const payload = await postJson(endpoint, readPayload());
    renderSummary(payload);
    await loadRuns();
    setMessage(`Сохранено: ${payload.run_name}`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    runButton.disabled = false;
  }
}

async function buildRoute() {
  buildRouteButton.disabled = true;
  generateRouteNmeaButton.disabled = true;
  runRouteTercomButton.disabled = true;
  setRouteMessage("Строю маршрут и сохраняю route/truth/PNG...");
  try {
    const payload = await postJson("/api/route/build", readRoutePayload());
    renderSummary(payload);
    await loadRuns();
    setRouteMessage(`Маршрут сохранён: ${payload.run_name}`);
  } catch (error) {
    setRouteMessage(error.message, true);
  } finally {
    buildRouteButton.disabled = false;
  }
}

async function generateRouteNmea() {
  if (!state.routeRunName) {
    setRouteMessage("Сначала постройте маршрут.", true);
    return;
  }
  generateRouteNmeaButton.disabled = true;
  setRouteMessage("Генерирую telemetry.nmea по маршруту...");
  try {
    const payload = await postJson("/api/route/generate-nmea", readRouteNmeaPayload());
    renderSummary(payload);
    setRouteMessage(`NMEA создан: ${payload.run_name}/telemetry.nmea`);
  } catch (error) {
    setRouteMessage(error.message, true);
  } finally {
    generateRouteNmeaButton.disabled = false;
  }
}

async function runRouteLocalization() {
  if (!state.routeRunName) {
    setRouteMessage("Сначала постройте маршрут.", true);
    return;
  }
  runRouteTercomButton.disabled = true;
  setRouteMessage("Запускаю TERCOM по truth.csv и telemetry.nmea...");
  try {
    const payload = await postJson("/api/route/run-localization", readRouteNmeaPayload());
    renderSummary(payload);
    await loadRuns();
    setRouteMessage(`TERCOM завершён: ${payload.run_name}`);
  } catch (error) {
    setRouteMessage(error.message, true);
  } finally {
    runRouteTercomButton.disabled = false;
  }
}

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode));
});

document.querySelectorAll(".route-mode-button").forEach((button) => {
  button.addEventListener("click", () => setRouteMode(button.dataset.routeMode));
});

mapSelect.addEventListener("change", updateMapFields);
refreshMapsButton.addEventListener("click", async () => {
  try {
    await loadMaps();
    setMessage("Список карт обновлён");
  } catch (error) {
    setMessage(error.message, true);
  }
});

runButton.addEventListener("click", submitRun);
buildRouteButton.addEventListener("click", buildRoute);
generateRouteNmeaButton.addEventListener("click", generateRouteNmea);
runRouteTercomButton.addEventListener("click", runRouteLocalization);
refreshRunsButton.addEventListener("click", async () => {
  try {
    await loadRuns();
    setMessage("История обновлена");
  } catch (error) {
    setMessage(error.message, true);
  }
});

setMode("demo");
setRouteMode("simple");
Promise.all([loadMaps(), loadRuns()]).catch((error) => {
  statusDot.classList.remove("online");
  setMessage(error.message, true);
});
