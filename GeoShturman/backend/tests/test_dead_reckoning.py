from app.core.dead_reckoning import run_dead_reckoning


def test_dead_reckoning_integrates_heading_with_north_east_convention() -> None:
    trajectory = run_dead_reckoning(
        [
            {"t_s": 0.0, "speed_mps": 10.0, "heading_deg": 90.0},
            {"t_s": 1.0, "speed_mps": 10.0, "heading_deg": 90.0},
            {"t_s": 2.0, "speed_mps": 10.0, "heading_deg": 0.0},
        ],
        initial_x_m=100.0,
        initial_y_m=200.0,
    )

    assert trajectory[1]["x_m"] == 110.0
    assert round(trajectory[1]["y_m"], 9) == 200.0
    assert trajectory[2]["x_m"] == 110.0
    assert trajectory[2]["y_m"] == 210.0
