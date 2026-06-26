# BlindFlight Terrain Lock · «Полёт вслепую»

**BlindFlight Terrain Lock** — алгоритмический прототип автономной резервной навигации БПЛА без GNSS по цифровой модели рельефа и данным бортовых датчиков.

Веб-интерфейс, FastAPI и графики являются демонстрационной оболочкой для жюри. Основная ценность проекта — алгоритм, который оценивает положение, скорость, курс, радиус ошибки и confidence по данным радиовысотомера, барометра, инерциальной оценки движения и DEM-карты.

Проект строго **гражданский**: доставка медикаментов и грузов в удалённые районы, снабжение арктических станций, геологоразведочные лагеря, высокогорные базы, безопасность гражданских маршрутов и постфактум-анализ полёта.

![status](https://img.shields.io/badge/main-Terrain%20Lock-22d3ee) ![stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20NumPy%20%2B%20Vanilla%20JS-34d399)

---

## Что Решает Алгоритм

Когда GNSS/GPS/ГЛОНАСС недоступен или ненадёжен, БПЛА всё ещё может оценивать своё положение по форме рельефа под собой.

Система использует:

- радиовысотомер — высота над землёй, AGL;
- барометрическую высоту — высота над уровнем моря, MSL;
- инерциальную оценку движения — скорость, курс и время;
- DEM-карту — цифровую модель высот местности.

Ключевая формула:

```text
terrain_msl = barometric_altitude_msl - radar_altitude_agl
```

Так восстанавливается наблюдаемый профиль рельефа под БПЛА. Далее алгоритм сопоставляет этот профиль с DEM и уточняет навигационную гипотезу.

---

## Главный Алгоритм

Основной сценарий теперь:

```text
Dead Reckoning + Terrain Referenced Navigation + Particle Filter + Profile Matching
```

Логика:

1. **Dead Reckoning** интегрирует скорость, курс и время без GNSS.
2. **Радиовысотомер + барометр** восстанавливают наблюдаемый профиль рельефа.
3. **Particle Filter** хранит множество гипотез положения БПЛА.
4. Каждая частица движется по модели движения.
5. Частицы сравниваются с DEM по текущей высоте и профилю за окно времени.
6. Веса частиц обновляются по вероятности совпадения с рельефом.
7. Алгоритм выдаёт координаты, скорость, курс, error radius и confidence.
8. На плоском или неоднозначном рельефе confidence снижается.

Главная функция ядра:

```python
run_autonomous_navigation_algorithm(...)
```

Файл: `backend/app/core/navigation.py`

---

## Архитектура

```text
Браузер
  backend/app/static
      index.html / styles.css / app.js
      Демонстрационная оболочка

FastAPI
  backend/app/api
      /api/navigation/autonomous-demo
      /api/demo/run legacy
      /api/nmea/parse
      /api/navigation/solve

Services
  backend/app/services
      orchestration, artifacts, JSON/PNG outputs

Core Algorithm
  backend/app/core
      DEM, simulator, dead reckoning,
      particle filter, terrain matcher,
      confidence, navigation, visualization
```

Основные core-модули:

| Файл | Назначение |
|------|------------|
| `dem.py` | Synthetic DEM, sampling, geodesy binding |
| `simulator.py` | Истинная траектория и sensor stream |
| `dead_reckoning.py` | Baseline без коррекции по рельефу |
| `particle_filter.py` | Частицы, prediction, measurement update, ESS, resampling |
| `terrain_matcher.py` | Профиль рельефа, RMSE, correlation, profile update |
| `confidence.py` | Terrain informativeness, режимы confidence |
| `navigation.py` | Главный автономный алгоритм |
| `visualization.py` | Графики для демонстрации |

---

## Быстрый Запуск

Через Docker:

```bash
cp .env.example .env
docker compose up -d --build
```

Открыть:

| URL | Назначение |
|-----|------------|
| http://localhost | Демонстрационный интерфейс |
| http://localhost/docs | Swagger UI |
| http://localhost/health | Проверка API |

Локально без Docker:

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Открыть: http://127.0.0.1:8000/

---

## Главный Endpoint

```text
POST /api/navigation/autonomous-demo
```

Пример запроса:

```json
{
  "width_m": 8000,
  "height_m": 8000,
  "resolution_m": 30,
  "duration_s": 180,
  "sample_rate_hz": 5,
  "true_speed_mps": 18,
  "true_heading_deg": 73,
  "barometric_altitude_msl": 1500,
  "initial_uncertainty_radius_m": 500,
  "n_particles": 5000,
  "profile_window_s": 30,
  "seed": 42
}
```

Ответ содержит:

```json
{
  "status": "ok",
  "algorithm": "BlindFlight Terrain Lock",
  "final_estimate": {
    "x_m": 4120.5,
    "y_m": 6180.2,
    "heading_deg": 73.1,
    "speed_mps": 18.4,
    "error_radius_m": 92.0
  },
  "confidence": {
    "value": 0.84,
    "mode": "terrain_lock",
    "warning": null
  },
  "truth_error": {
    "final_position_error_m": 86.5,
    "mean_position_error_m": 122.3
  },
  "dead_reckoning_error": {
    "final_position_error_m": 640.0,
    "mean_position_error_m": 310.0
  },
  "improvement_factor": 7.4,
  "artifacts": {
    "trajectory_comparison_png": "/api/artifacts/<id>/trajectory_comparison.png",
    "particle_cloud_png": "/api/artifacts/<id>/particle_cloud.png",
    "confidence_timeline_png": "/api/artifacts/<id>/confidence_timeline.png",
    "terrain_profile_match_png": "/api/artifacts/<id>/terrain_profile_match.png",
    "result_json": "/api/artifacts/<id>/result.json"
  }
}
```

Legacy endpoint сохранён:

```text
POST /api/demo/run
```

Он оставлен для старой корреляционной демонстрации и обратной совместимости.

---

## Что Показывает Демо

Главная демонстрация доказывает:

```text
Dead Reckoning без GNSS уходит в сторону.
BlindFlight Terrain Lock использует рельеф и снижает ошибку.
```

Графики:

- сравнение истинной траектории, Dead Reckoning и Terrain Lock;
- облако частиц Particle Filter;
- confidence и error radius по времени;
- совпадение наблюдаемого профиля рельефа с DEM-профилем.

Метрики:

- `final_position_error_m`;
- `mean_position_error_m`;
- `dead_reckoning_final_error_m`;
- `improvement_factor`;
- `confidence`;
- `mode`: `terrain_lock`, `degraded`, `low_confidence`, `lost`;
- `terrain_lock_ratio`;
- `error_radius_m`;
- `profile_correlation`.

---

## Тесты

```bash
python -B -m pytest -p no:cacheprovider backend/tests
```

Покрываются:

- инициализация частиц;
- prediction step;
- update весов по высоте;
- ESS и systematic resampling;
- Dead Reckoning baseline;
- автономное demo, где Terrain Lock уменьшает ошибку относительно Dead Reckoning.

---

## Ограничения MVP

- DEM и sensor stream синтетические, чтобы демо было воспроизводимым.
- Модель движения упрощённая: постоянная скорость и курс с шумом.
- Profile matching запускается периодически по окну, а не как полноценный real-time SLAM.
- Точность зависит от информативности рельефа: на плоской местности confidence падает.
- Нет подключения реальных DEM-тайлов и реального бортового протокола телеметрии.

---

## Что Улучшить После Хакатона

- Подключить реальные DEM-тайлы Copernicus GLO-30, SRTM или ALOS.
- Добавить полноценную геопривязку CRS/геоид для production-карт.
- Сделать online-режим через WebSocket sensor stream.
- Оптимизировать Particle Filter под бортовой вычислитель.
- Добавить адаптивный размер облака частиц по уровню неопределённости.
- Улучшить модель баро-дрейфа, ветра и инерциальных ошибок.

---

## Гражданские Сценарии

- доставка медикаментов в удалённые посёлки;
- снабжение арктических и высокогорных станций;
- доставка оборудования в геологоразведочные лагеря;
- резервный навигационный слой для гражданских БПЛА;
- анализ траектории после потери GNSS.

Деплой на Beget VPS/VDS — см. [README_DEPLOY_BEGET.md](README_DEPLOY_BEGET.md).
