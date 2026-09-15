from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.tasks.stock_financial_tasks import next_monthly_window


def test_bootstrap_runs_next_beijing_midnight_window():
    created = datetime(2026, 9, 15, 14, 24, 4, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_monthly_window(created).isoformat() == "2026-09-16T02:10:00+08:00"


def test_state_path_is_outside_untracked_runtime_files():
    from backend.tasks.stock_financial_tasks import STATE_FILE
    assert Path(STATE_FILE).name == "stock_financial_monthly.json"
