# TERCOM UAV Prototype

Мини-проект для хакатонной задачи terrain-referenced navigation: по DEM и NMEA-0183 GPGGA-потоку, где поле высоты трактуется как радиовысота AGL, восстанавливается профиль рельефа, находится корреляционное совпадение на карте и оцениваются координаты, курс, скорость и уверенность.

## Структура

```text
project/
  pyproject.toml
  README.md
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
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
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

## Проверка

```bash
pytest
```

Тесты покрывают checksum NMEA, GPGGA-парсинг, билинейную выборку DEM, построение профиля, идеальное и шумное совпадение, плоский рельеф с пониженной уверенностью, оценку азимута и скорости.

## Интеграция с БПЛА

Места расширения:

- `mavlink_stub.py` - заменить заглушку на реальный MAVLink/serial publisher.
- `nmea.py` - подключить потоковый serial/UDP reader вместо файлового чтения.
- `estimator.py` - запускать скользящие окна онлайн и отдавать обновления автопилоту.
- `kalman.py` - заменить alpha-beta сглаживание на полноценный EKF/UKF с IMU и баро.
- `dem.py` - добавить тайловый кэш DEM и подгрузку по району полета.
