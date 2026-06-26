from pathlib import Path
import sys

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.metrics import build_quality_report, terrain_informativeness
from app.core.profile import align_profile_to_reference, clean_profile, radio_agl_to_terrain_msl


def test_radio_agl_to_terrain_msl():
    radio = np.asarray([100.0, 120.0, 80.0])

    terrain = radio_agl_to_terrain_msl(radio, 1500.0)

    np.testing.assert_allclose(terrain, [1400.0, 1380.0, 1420.0])


def test_flat_terrain_low_informativeness_warning():
    profile = np.full(50, 310.0)

    report = build_quality_report([], profile)

    assert terrain_informativeness(profile) < 0.05
    assert "terrain_flat" in report["warnings"]


def test_clean_profile_removes_isolated_outlier():
    profile = np.asarray([100.0, 101.0, 102.0, 260.0, 103.0, 104.0, 105.0])

    cleaned = clean_profile(profile, median_window=3, hampel_window=5, outlier_sigma=3.0)

    assert cleaned[3] < 110.0


def test_align_profile_to_reference_removes_linear_drift():
    reference = np.linspace(200.0, 260.0, 30) + np.sin(np.linspace(0.0, 6.0, 30)) * 8.0
    drift = np.linspace(5.0, 25.0, reference.size)
    measured = reference + drift

    corrected, report = align_profile_to_reference(measured, reference)

    assert np.sqrt(np.mean((corrected - reference) ** 2)) < 1e-9
    assert report["slope_m_per_sample"] > 0.0
