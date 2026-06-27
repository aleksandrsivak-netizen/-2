# TERCOM UAV Prototype

Мини-проект для хакатонной задачи terrain-referenced navigation: по DEM и NMEA-0183 GPGGA-потоку, где поле высоты трактуется как радиовысота AGL, восстанавливается профиль рельефа, находится корреляционное совпадение на карте и оцениваются координаты, курс, скорость и уверенность.

## Структура

```text
project/
  pyproject.toml
  README.md
  docs/
    CHANGELOG_DEV.txt
  src/tercom_uav/
    __init__.py
    config.py
    types.py
    nmea.py
    dem.py
    simulator.py
    profiles.py
    correlation.py
    estimator.py
    kalman.py
    confidence.py
    visualization.py
    cli.py
    mavlink_stub.py
  tests/
    test_nmea.py
    test_dem.py
    test_profiles.py
    test_correlation.py
    test_estimator.py
  examples/
    demo_config.yaml
  outputs/
```

## Установка

```bash
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Если GeoTIFF не передан, CLI использует синтетическую DEM-карту, поэтому демо запускается без внешних данных.

## Быстрый запуск

```bash
python -m tercom_uav.cli demo \
  --baro-alt 1500 \
  --speed 55 \
  --heading 73 \
  --duration 180 \
  --hz 5 \
  --noise-std 2.5 \
  --out outputs/demo1
```

## Web-интерфейс

Локальный dashboard позволяет вводить параметры симуляции/локализации, запускать алгоритм и смотреть метрики, heatmap, карту DEM с траекторией, профиль рельефа, скорость и confidence.
Интерфейс показывает встроенные синтетические карты и автоматически импортирует найденные в рабочей папке `*.tif`, `*.tiff`, `*.vrt` DEM-файлы в выпадающий список.

```bash
python -m tercom_uav.cli ui
```

Откройте:

```text
http://127.0.0.1:8765
```

Запуски из web-интерфейса сохраняются в `outputs/web/<run_name>/`.

С DEM GeoTIFF:

```bash
python -m tercom_uav.cli demo \
  --dem path/to/dem.tif \
  --baro-alt 1500 \
  --speed 55 \
  --heading 73 \
  --duration 180 \
  --hz 5 \
  --noise-std 2.5 \
  --out outputs/demo1
```

Только симуляция:

```bash
python -m tercom_uav.cli simulate --dem path/to/dem.tif --export-nmea outputs/input.nmea --export-truth outputs/truth.csv
```

Локализация по готовому NMEA:

```bash
python -m tercom_uav.cli localize \
  --dem path/to/dem.tif \
  --nmea input.nmea \
  --baro-alt 1500 \
  --out outputs/localize1
```

Постобработка:

```bash
python -m tercom_uav.cli report --run outputs/demo1
```

## Формулы

В этом ТЗ поле высоты GPGGA не является GNSS-высотой. Оно трактуется как радиовысота:

```text
radio_alt_agl = baro_alt_msl - terrain_elevation_msl
terrain_profile_abs = baro_alt_msl - radio_alt_agl
```

Азимут измеряется в градусах по часовой стрелке от севера. В локальной метрической системе `x` направлен на восток, `y` на север:

```text
x = x0 + sin(azimuth) * distance
y = y0 + cos(azimuth) * distance
vx = sin(azimuth) * speed
vy = cos(azimuth) * speed
```

TERCOM-поиск сравнивает наблюдаемый профиль рельефа с эталонными профилями DEM для азимутов `0..359` и сдвигов вдоль луча. Основная метрика - Pearson correlation coefficient после центрирования и нормализации профилей. Дополнительно сохраняются NCC, MSE, MAD, discrimination ratio, roughness и observability.

## Известные ограничения и как они обрабатываются

### Низкая наблюдаемость рельефа

TERCOM требует информативного профиля высот. Над плоской или очень гладкой местностью высокий коэффициент корреляции сам по себе не гарантирует правильный фикс: несколько маршрутов могут давать почти одинаковый профиль, а разрыв между лучшим и вторым максимумом heatmap становится слабым диагностическим признаком.

Поэтому перед оценкой confidence модуль дополнительно проверяет наблюдаемость эталонного DEM-профиля: для найденного кандидата считаются `reference_profile_std_m` и `reference_profile_range_m`. Если стандартное отклонение ниже `min_reference_std_m` или диапазон ниже `min_reference_range_m`, результат получает `low_observability=True`, `ambiguous_match=True`, а `confidence_score` детерминированно ограничивается сверху. Это независимый сигнал, не завязанный только на постфактумный `score_gap / heatmap_std`.

На полностью вырожденном поиске, когда все эталонные профили плоские и нормализованная корреляция не определена, `correlate_profile()` не падает с исключением, а возвращает корректный no-fix результат: `confidence_score=0.0`, `ambiguous_match=True`, `low_observability=True`.

### Coarse-to-fine поиск

Двухэтапный `coarse_to_fine` оставлен выключенным по умолчанию. На пересеченном рельефе тестовый сценарий показывает совпадение с full-grid поиском по азимуту и сдвигу, но на гладком/плоском рельефе coarse-сетка может выбрать неверную область уточнения или вообще не иметь информативного пика. CLI оставляет режим доступным через `--coarse-to-fine`, но help-текст явно предупреждает, что перед применением на реальных данных его нужно валидировать против full-grid на конкретном DEM и уровне шума.

### Скачки между окнами

В скользящей локализации сырой TERCOM-фикс дополнительно проходит кинематический gate. Если переход от предыдущего принятого окна требует скорости выше `max_speed_mps`, фикс отбрасывается и заменяется dead reckoning с `dead_reckoning=True`, `confidence_score=0.0` и `ambiguous_match=True`. Это защищает метрики и сглаживание от одиночных ложных максимумов на слабонаблюдаемом рельефе.

### Масштаб скорости

Радиовысота сама по себе не задает метрический масштаб времени. Поэтому `correlate_with_speed_search()` перебирает несколько гипотез скорости вокруг `--speed-hint` и выбирает ту, чей профиль лучше согласуется с DEM. Это снижает зависимость от внешней подсказки скорости, но на периодическом гладком рельефе одиночное окно все равно может оставаться неоднозначным; практическая устойчивость достигается совместно с observability-флагом и кинематическим gate.

История предыдущих правок сохранена как dev-log в `docs/CHANGELOG_DEV.txt`; основной источник актуальных ограничений - этот раздел README.

## Артефакты запуска

В `outputs/<run>/` сохраняются:

- `config.json` - параметры сценария.
- `telemetry.nmea` - сгенерированный GPGGA-поток.
- `truth.csv` - истинная траектория симулятора, если доступна.
- `telemetry.csv` - измерения радиовысотомера.
- `observed_profile.csv` - восстановленный профиль рельефа.
- `estimates.csv` - оценки положения, курса, скорости и confidence.
- `summary.json` - итоговая оценка и метрики.
- `correlation_heatmap.npy` - матрица корреляций `[azimuth, shift]`.
- `correlation_heatmap.png`, `dem_tracks.png`, `terrain_profile.png`, `speed.png`, `confidence.png` - графики.
В `summary.json` и HTML-отчете дополнительно видны `low_observability`, `reference_profile_std_m` и `reference_profile_range_m`.

## Проверка

```bash
.\.venv\Scripts\python -m pytest -q
```

Тесты покрывают checksum NMEA, GPGGA-парсинг, билинейную выборку DEM, построение профиля, идеальное и шумное совпадение, low-observability флаг, поведение coarse-to-fine на пересеченном и гладком рельефе, оценку азимута и скорости.

## Интеграция с БПЛА

Места расширения:

- `mavlink_stub.py` - заменить заглушку на реальный MAVLink/serial publisher.
- `nmea.py` - подключить потоковый serial/UDP reader вместо файлового чтения.
- `estimator.py` - запускать скользящие окна онлайн и отдавать обновления автопилоту.
- `kalman.py` - заменить alpha-beta сглаживание на полноценный EKF/UKF с IMU и баро.
- `dem.py` - добавить тайловый кэш DEM и подгрузку по району полета.
