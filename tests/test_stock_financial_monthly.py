from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.tasks.stock_financial_tasks import next_monthly_window, _state_for_month


def test_bootstrap_runs_next_beijing_midnight_window():
    created = datetime(2026, 9, 15, 14, 24, 4, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_monthly_window(created).isoformat() == "2026-09-16T02:10:00+08:00"


def test_state_path_is_outside_untracked_runtime_files():
    from backend.tasks.stock_financial_tasks import STATE_FILE
    assert Path(STATE_FILE).name == "stock_financial_monthly.json"


def test_success_state_creates_new_cursor_for_next_month():
    path = Path("data/state/.test_stock_financial_state.json")
    old = {"target_month": "2026-09", "status": "SUCCESS", "completed": ["x"], "rows": {"1": {}}}
    fresh = _state_for_month(old, datetime(2026, 10, 1, 2, 10, tzinfo=ZoneInfo("Asia/Shanghai")), path)
    assert fresh["target_month"] == "2026-10"
    assert fresh["status"] == "SCHEDULED"
    assert fresh["completed"] == []
    assert path.exists()
    path.unlink()


def test_failed_state_in_same_month_is_resumable():
    path = Path("data/state/.test_stock_financial_state.json")
    old = {"target_month": "2026-09", "status": "FAILED", "completed": ["x"], "rows": {"1": {}}}
    same = _state_for_month(old, datetime(2026, 9, 20, 2, 10, tzinfo=ZoneInfo("Asia/Shanghai")), path)
    assert same["status"] == "FAILED"
    assert same["completed"] == ["x"]
