from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_name: str = "blind-flight-nav"
    app_version: str = "0.1.0"
    output_dir: Path = Path(__file__).resolve().parent / "outputs"
    static_dir: Path = Path(__file__).resolve().parent / "static"
    max_demo_duration_s: int = 600
    max_map_size_m: int = 20000


settings = Settings()
