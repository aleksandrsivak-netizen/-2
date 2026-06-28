# Dorabotka: heights.txt + GeoTIFF

This mode is for the third technical review input format:

- `heights.txt`: one terrain height value in meters per line.
- `start_x`, `start_y`: initial point.
- `heading_deg`: heading clockwise from north (`0` north, `90` east).
- `map.tif` / `map.geotiff`: GeoTIFF DEM.

The implementation lives in `tercom_uav.dorabotka` and is exposed through:

- CLI command: `tercom-uav dorabotka` or `python -m tercom_uav.cli dorabotka`.
- GeoShturman backend endpoint: `POST /api/dorabotka/run`.

## heights.txt

Example:

```text
120.5
121.0
121.4
122.2
121.9
```

Rules:

- one number per line;
- integer and decimal values are supported;
- empty lines are ignored;
- whitespace is ignored;
- invalid lines return `invalid_heights_file` with the bad line number;
- at least five values are required by default.

## Coordinates

`start_coord_type` controls how `start_x/start_y` are interpreted:

- `map`: map/local GeoTIFF coordinates;
- `pixel`: pixel coordinates where `x=column`, `y=row`;
- `auto`: try map and pixel. If both are possible, map is used and a warning is added.

GeoTIFF loading uses rasterio and pyproj through the existing `DEMGrid.from_geotiff()` path. Raster values are treated as elevations, CRS and affine transform are respected, and nodata becomes `NaN`.

## Motion Model

Heading is normalized:

```text
heading_deg = heading_deg % 360
```

Direction vector in local map meters:

```text
dx = sin(heading_rad)
dy = cos(heading_rad)
```

Each height sample gets distance:

```text
distance_i = i * sample_step_m
```

`sample_step_m` defaults to `1.0` meter and is configurable in CLI/API.

## TERCOM Correction

The Dorabotka pipeline does not only draw the input line. It searches for the best local trajectory hypothesis:

- offset start point inside `search_radius_m`;
- optionally vary heading inside `heading_search_deg`;
- sample GeoTIFF heights along each candidate trajectory;
- compute RMSE, MAE, correlation and a combined score;
- return the trajectory with the best score.

Important parameters:

```json
{
  "sample_step_m": 1.0,
  "search_radius_m": 200,
  "search_step_m": 5,
  "heading_search_deg": 5,
  "heading_step_deg": 1,
  "normalize_profile": true,
  "coarse_to_fine": true,
  "max_candidates": 8,
  "max_hypotheses": 250000
}
```

`normalize_profile=true` compares the terrain shape after demeaning and scaling, so constant sensor bias is less destructive.

## CLI

```powershell
tercom-uav dorabotka `
  --heights ./data/heights.txt `
  --geotiff ./data/map.tif `
  --start-x 5000 `
  --start-y 7000 `
  --start-coord-type auto `
  --heading-deg 42 `
  --sample-step-m 1 `
  --search-radius-m 200 `
  --search-step-m 5 `
  --heading-search-deg 5 `
  --heading-step-deg 1 `
  --output-dir ./outputs/dorabotka
```

From a source checkout without package installation:

```powershell
$env:PYTHONPATH="C:\path\to\repo\tercom_uav\src"
python -m tercom_uav.cli dorabotka --heights .\heights.txt --geotiff .\map.tif --start-x 5000 --start-y 7000 --heading-deg 42
```

## API

Endpoint:

```text
POST /api/dorabotka/run
```

Multipart fields:

- `heights_file`: required file.
- `geotiff_file`: required file.
- `start_x`: required number.
- `start_y`: required number.
- `heading_deg`: required number.
- `start_coord_type`: `auto`, `map`, `pixel`.
- `sample_step_m`, `search_radius_m`, `search_step_m`, `heading_search_deg`, `heading_step_deg`.
- `reference_trajectory`: optional CSV or GeoJSON.

Example with curl:

```bash
curl -X POST http://127.0.0.1:8000/api/dorabotka/run \
  -F heights_file=@heights.txt \
  -F geotiff_file=@map.tif \
  -F start_x=5000 \
  -F start_y=7000 \
  -F start_coord_type=auto \
  -F heading_deg=42 \
  -F sample_step_m=1 \
  -F search_radius_m=200 \
  -F search_step_m=5
```

## Output JSON

The response and `result.json` contain:

```json
{
  "mode": "dorabotka",
  "input": {
    "heights_count": 250,
    "start_x": 5000.0,
    "start_y": 7000.0,
    "start_coord_type": "auto",
    "resolved_start_coord_type": "map_local",
    "heading_deg": 42.0,
    "sample_step_m": 1.0,
    "geotiff": "map.tif"
  },
  "result": {
    "confidence": 0.86,
    "score": 0.91,
    "correlation": 0.91,
    "rmse_m": 2.4,
    "mae_m": 1.7,
    "best_offset_x_m": -12.0,
    "best_offset_y_m": 8.0,
    "best_heading_deg": 41.5
  },
  "trajectory": {
    "local": [],
    "global": []
  },
  "diagnostics": {
    "processing_time_ms": 123.4,
    "candidates_checked": 24000,
    "best_score": 0.91,
    "best_rmse": 2.4,
    "best_correlation": 0.91
  },
  "warnings": []
}
```

## Output Files

When `--output-dir` is used, or when the API endpoint runs, these files are created:

- `trajectory_local.csv`
- `trajectory_global.csv`
- `trajectory.geojson`
- `trajectory_plot.png`
- `result.json`

`trajectory_plot.png` is the required visual map: GeoTIFF DEM background with start, end and corrected trajectory.

## Reference Trajectory

Optional `reference_trajectory` can be:

- CSV with `x,y` or `x_m,y_m`;
- CSV with `lon,lat`;
- GeoJSON `LineString`.

When present, the result includes:

- `mean_horizontal_error_m`
- `max_horizontal_error_m`
- `rmse_horizontal_error_m`
- `start_offset_m`
- `end_offset_m`

## Metric Interpretation

- `correlation`: profile-shape agreement. Closer to `1.0` is better.
- `rmse_m`: raw height difference in meters.
- `mae_m`: mean absolute height error.
- `confidence`: compact 0..1 confidence derived from correlation and normalized shape error.
- `best_offset_x_m/best_offset_y_m`: local correction from the provided start point.

If coordinates do not match the expected reference, check:

- whether `start_coord_type` should be `map` or `pixel`;
- whether `sample_step_m` matches the data source;
- whether the GeoTIFF CRS is correct;
- whether heights are terrain elevations, not radio altitude AGL.
