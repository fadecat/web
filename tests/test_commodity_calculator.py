from backend.services.commodity_calculator import WINDOW_DAYS, calculate_percentiles


def test_calculator_requires_complete_window_and_keeps_equal_latest_values():
    values = list(range(1, 22))
    result = calculate_percentiles(values)
    assert result["d21"].percentile == 100.0
    assert result["d21"].sample_count == 21
    assert result["d21"].signal == "high"
    assert result["d63"].percentile is None
    assert result["d63"].sample_count == 21
    assert result["d63"].signal == "insufficient"


def test_calculator_uses_count_less_than_or_equal_to_latest():
    result = calculate_percentiles([1, 2, 2, 4, 3], windows={"d5": 5})
    assert result["d5"].percentile == 80.0
    assert result["d5"].sample_count == 5
    assert result["d5"].signal == "neutral"


def test_calculator_exposes_fixed_windows_and_algorithm_version():
    assert WINDOW_DAYS == {"d21": 21, "d63": 63, "y1": 252, "y3": 756, "y5": 1260, "y10": 2520}
    assert calculate_percentiles([1] * 21)["d21"].algorithm_version == "v1"
