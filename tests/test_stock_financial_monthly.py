from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.tasks.stock_financial_tasks import next_monthly_window, _state_for_month, _majority_trade_date, initialize_stock_financial_bootstrap


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


def test_trade_date_uses_majority_of_partition_dates():
    assert _majority_trade_date(["2026-09-11", "2026-09-11", "2026-09-10"]) == "2026-09-11"


def test_bootstrap_state_tracks_request_cost(tmp_path=None):
    path = Path("data/state/.test_stock_financial_state.json")
    if path.exists():
        path.unlink()
    state = initialize_stock_financial_bootstrap(datetime(2026, 9, 15, 14, 24, 4, tzinfo=ZoneInfo("Asia/Shanghai")), path)
    assert state["request_count"] == 0
    assert state["failed_queries"] == 0
    path.unlink()


# ---------------------------------------------------------------------------
# 两晚均匀铺开: 配额 / 旧状态迁移 / 匀速间隔
# ---------------------------------------------------------------------------

_CN = ZoneInfo("Asia/Shanghai")


def test_nights_quota_splits_remaining_over_two_nights():
    from backend.tasks.stock_financial_tasks import _nights_quota

    # 首夜: 322 片 2 晚 → 161
    assert _nights_quota({"total_nights": 2, "run_dates": []}, "2026-09-17", 322) == 161
    # 次夜: 已用 1 晚 → 剩余 161 一晚收尾
    assert _nights_quota({"total_nights": 2, "run_dates": ["2026-09-17"]}, "2026-09-18", 161) == 161
    # 夜数耗尽仍剩 40 片 → 兜底一晚跑完, 不会饿死
    assert _nights_quota({"total_nights": 2, "run_dates": ["2026-09-17", "2026-09-18"]}, "2026-09-19", 40) == 40
    # 同日重复触发只算一晚(今日不计入已用)
    assert _nights_quota({"total_nights": 2, "run_dates": ["2026-09-17"]}, "2026-09-17", 100) == 50


def test_ensure_plan_fields_migrates_legacy_state(tmp_path):
    from backend.tasks.stock_financial_tasks import _ensure_plan_fields

    path = tmp_path / "state.json"
    legacy = {"target_month": "2026-09", "status": "PAUSED_WINDOW_END", "completed": ["a", "b"]}
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    state = _ensure_plan_fields(json.loads(path.read_text(encoding="utf-8")), path)
    assert state["total_nights"] == 2
    assert state["run_dates"] == []
    assert state["completed"] == ["a", "b"], "已落库进度原样保留"
    # 迁移结果已持久化, 再跑一次幂等
    again = json.loads(path.read_text(encoding="utf-8"))
    _ensure_plan_fields(again, path)
    assert again["total_nights"] == 2


def test_even_pace_sleep_evenly_spreads_remaining_quota(monkeypatch):
    import backend.tasks.stock_financial_tasks as m

    fixed = datetime(2026, 9, 17, 2, 10, tzinfo=_CN)

    class FakeDT:
        combine = staticmethod(datetime.combine)
        now = classmethod(lambda cls, tz=None: fixed)

    slept = []
    monkeypatch.setattr(m, "datetime", FakeDT)
    monkeypatch.setattr(m, "sleep", lambda s: slept.append(s))

    m._even_pace_sleep(161, 2.0)  # 02:10 → 04:50 共 9600s, 均摊到 161 片
    assert abs(slept[0] - (9600 / 161 - 2.0)) < 0.01

    # 窗口已过(04:50 之后) → budget 为负, 不再 sleep
    FakeDT.now = classmethod(lambda cls, tz=None: datetime(2026, 9, 17, 4, 55, tzinfo=_CN))
    m._even_pace_sleep(5, 0.0)
    assert len(slept) == 1

    # 本片耗时已超过配额预算 → sleep 0 而不是负数
    FakeDT.now = classmethod(lambda cls, tz=None: datetime(2026, 9, 17, 4, 49, tzinfo=_CN))
    m._even_pace_sleep(1, 120.0)
    assert slept[-1] == 0.0


class _FakeDT:
    """固定当前时间, 供主流程测试脱离真实时钟。"""

    combine = staticmethod(datetime.combine)
    fromisoformat = staticmethod(datetime.fromisoformat)

    def __init__(self, now: datetime):
        cls = type(self)
        cls.now = classmethod(lambda _c, tz=None: now)


def _install_fake_clock(monkeypatch, now: datetime):
    import backend.tasks.stock_financial_tasks as m

    monkeypatch.setattr(m, "datetime", _FakeDT(now))
    monkeypatch.setattr(m, "get_cookie", lambda: "cookie")
    monkeypatch.setattr(m, "_even_pace_sleep", lambda *a: None)


def _fake_snapshot_factory(rows_per_partition: int, trade_date: str = "2026-09-16"):
    calls: list[str] = []

    def fake_snapshot(_cookie, nodes, *, min_total_value):
        val = nodes[0]["val"]
        calls.append(val)
        rows = [{"stock_id": f"{val}-{j}", "last_dt": trade_date} for j in range(rows_per_partition)]
        return {
            "meta": {"request_count": 2, "failed_queries": 0, "trade_date": trade_date},
            "rows": rows,
        }

    return calls, fake_snapshot


class _FakeBatch:
    def __init__(self, count: int):
        self.id, self.actual_count = 99, count


class _FakeDB:
    def close(self):
        pass


def test_two_night_plan_processes_quota_then_finishes(tmp_path, monkeypatch):
    import backend.tasks.stock_financial_tasks as m

    _install_fake_clock(monkeypatch, datetime(2026, 9, 17, 2, 30, tzinfo=_CN))
    calls, fake_snapshot = _fake_snapshot_factory(rows_per_partition=800)
    monkeypatch.setattr(m, "fetch_dividend_snapshot", fake_snapshot)

    path = tmp_path / "state.json"
    legacy = {
        "target_month": "2026-09", "status": "PAUSED_WINDOW_END",
        "scheduled_for": "2026-09-01T02:10:00+08:00",
        "partitions": [f"p{i}" for i in range(10)],
        "completed": ["p0", "p1", "p2"], "rows": {},
        "request_count": 0, "failed_queries": 0,
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    # 首夜: 7 片剩余 2 晚 → 只跑 4 片
    result = m.run_stock_financial_monthly(state_path=path)
    assert result["status"] == "PAUSED_WINDOW_END"
    assert result["remaining"] == 3
    assert result["success_count"] == 4 * 800
    assert calls == ["p3", "p4", "p5", "p6"]
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["total_nights"] == 2, "旧状态自动迁移"
    assert state["run_dates"] == ["2026-09-17"]
    assert len(state["completed"]) == 7

    # 次夜: 最后一晚 → 剩余 3 片跑完并原子发布
    _install_fake_clock(monkeypatch, datetime(2026, 9, 18, 2, 30, tzinfo=_CN))
    published = {}

    def fake_save(_db, rows, **kwargs):
        published["count"] = len(rows)
        published["kwargs"] = kwargs
        return _FakeBatch(len(rows))

    monkeypatch.setattr(m, "save_stock_financial_snapshot", fake_save)
    monkeypatch.setattr(m, "SessionLocal", lambda: _FakeDB())

    result2 = m.run_stock_financial_monthly(state_path=path)
    assert result2["status"] == "success"
    assert result2["success_count"] == 7 * 800  # 首夜 4 片 + 次夜 3 片(遗留 p0-p2 无行)
    assert calls == [f"p{i}" for i in range(3, 10)]
    assert published["count"] == 7 * 800
    assert published["kwargs"]["snapshot_month"] == "2026-09"
    state2 = json.loads(path.read_text(encoding="utf-8"))
    assert state2["status"] == "SUCCESS"
    assert state2["run_dates"] == ["2026-09-17", "2026-09-18"]


def test_force_run_ignores_quota_and_pacing(tmp_path, monkeypatch):
    import backend.tasks.stock_financial_tasks as m

    # 窗口外(上午 10 点) force 触发 → 一次性全量, 不受配额/硬停限制
    _install_fake_clock(monkeypatch, datetime(2026, 9, 17, 10, 0, tzinfo=_CN))
    calls, fake_snapshot = _fake_snapshot_factory(rows_per_partition=1100)
    monkeypatch.setattr(m, "fetch_dividend_snapshot", fake_snapshot)
    monkeypatch.setattr(m, "save_stock_financial_snapshot", lambda _db, rows, **kwargs: _FakeBatch(len(rows)))
    monkeypatch.setattr(m, "SessionLocal", lambda: _FakeDB())

    path = tmp_path / "state.json"
    state = {
        "target_month": "2026-09", "status": "PAUSED_WINDOW_END",
        "scheduled_for": "2026-09-01T02:10:00+08:00",
        "partitions": [f"p{i}" for i in range(5)],
        "completed": [], "rows": {},
        "request_count": 0, "failed_queries": 0,
        "total_nights": 2, "run_dates": [],
    }
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    result = m.run_stock_financial_monthly(force=True, state_path=path)
    assert result["status"] == "success"
    assert len(calls) == 5, "force 全速一次跑完, 不按两晚切分"


def test_success_state_skips_rest_of_month(tmp_path, monkeypatch):
    """本月已 SUCCESS → 每日 02:10 触发直接跳过, 不重复发布旧快照。"""
    import backend.tasks.stock_financial_tasks as m

    _install_fake_clock(monkeypatch, datetime(2026, 9, 25, 2, 10, tzinfo=_CN))
    calls, fake_snapshot = _fake_snapshot_factory(rows_per_partition=100)
    monkeypatch.setattr(m, "fetch_dividend_snapshot", fake_snapshot)

    path = tmp_path / "state.json"
    state = {
        "target_month": "2026-09", "status": "SUCCESS",
        "scheduled_for": "2026-09-01T02:10:00+08:00",
        "partitions": [f"p{i}" for i in range(3)],
        "completed": ["p0", "p1", "p2"], "rows": {"x": {}},
        "request_count": 10, "failed_queries": 0,
        "total_nights": 2, "run_dates": ["2026-09-16", "2026-09-17"],
    }
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    result = m.run_stock_financial_monthly(state_path=path)
    assert result["status"] == "already_done"
    assert calls == [], "成功后不应再抓取"
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "SUCCESS", "状态不被改写"
