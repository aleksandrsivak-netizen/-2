"""E2E: demo-пайплайн ГеоШтурмана целиком проходит через мост на Теаркоме.

Это путь, который кормит дашборд (/api/demo/run). Если он отрабатывает и
отдаёт estimated/quality/heatmap — значит визуализация показывает результат
Теаркома без правок фронта.
"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "GeoShturman" / "backend"))

from app.api.schemas import DemoRunRequest          # noqa: E402
from app.services.pipeline import run_demo_pipeline  # noqa: E402

req = DemoRunRequest(width_m=8000, height_m=8000, resolution_m=30, duration_s=120,
                     sample_rate_hz=5, speed_mps=40, azimuth_deg=128,
                     barometric_altitude_msl=1500, terrain_type="mixed", seed=42)
resp = run_demo_pipeline(req)
d = resp.model_dump() if hasattr(resp, "model_dump") else resp.dict()
est = d["estimated"]
q = d["quality"]
print("demo-пайплайн через мост: OK")
print(f"  истина   az=128  v=40")
print(f"  оценка   az={est['azimuth_deg']}  v={est['speed_mps']}  "
      f"corr={est['correlation']}  conf={est['confidence']}")
print(f"  quality  terrain_info={q['terrain_informativeness']}  peak_sharpness={q['peak_sharpness']}")
az_err = abs((float(est["azimuth_deg"]) - 128.0 + 180) % 360 - 180)
print(f"  азимут-ошибка демо = {az_err:.1f}°")
print("  ВЫВОД: пайплайн визуализации полностью отработал на ядре Теаркома")
