# -*- coding: utf-8 -*-
"""股票高股息快照: fetcher 覆盖算法 / 任务双重闸门 / 树缓存与漂移 / 落库幂等。

设计依据: docs/superpowers/plans/2026-09-11-stock-dividend-module-design.md。
所有 jisilu 交互均 mock(测试禁外网); 真实响应形态蓝本见
.test-artifacts/ggx-research/(研究产物, 不入 repo)。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.services.fetchers import stock_dividend
from backend.tasks import stock_dividend_tasks

_CST = ZoneInfo("Asia/Shanghai")


def _today_cst() -> str:
    return datetime.now(_CST).date().isoformat()


def _resp(json_body: dict) -> httpx.Response:
    """构造带 request 的 httpx.Response(raise_for_status 需要 request)。"""
    request = httpx.Request("POST", "https://test/x")
    return httpx.Response(200, json=json_body, request=request)


def _cell(sid: str, sw_cd: str, *, last_dt: str | None = None) -> dict:
    """最小可用 cell(代表字段子集; 48 字段全量映射由 store 测试覆盖)。"""
    return {
        "stock_id": sid,
        "stock_nm": f"股{sid}",
        "sw_cd": sw_cd,
        "industry_nm": "测试行业",
        "price": "10.5",
        "total_value": "300.0",
        "dividend_rate": "5.2",
        "pledge_rt": "buy",  # 徽标串: store 层 parse_float 转 None
        "last_dt": last_dt or _today_cst(),
        "last_time": "15:00:30",
    }


def _rows(sw_prefix: str, n: int, seg: int = 0) -> list[dict]:
    """造 n 行响应; seg 区段号保证不同请求段的股票 ID 互不重复。"""
    return [
        {"cell": _cell(f"{sw_prefix}{seg}{i:04d}", f"{sw_prefix}0011")}
        for i in range(n)
    ]


# 测试树: 根 10(直收) / 根 45(超限下钻) → 4510(直收) + 4520(叶子超限→二分)
TREE = [
    {"val": "10", "sw": "801010", "nm": "测试一级行业A", "level": 1, "cnts": 3},
    {"val": "45", "sw": "801450", "nm": "测试一级行业B", "level": 1, "cnts": 213},
    {"val": "4510", "sw": "801451", "nm": "测试一级行业B/二级行业B1", "level": 2, "cnts": 90},
    {"val": "4520", "sw": "801452", "nm": "测试一级行业B/二级行业B2", "level": 2, "cnts": 123},
]

# 默认 min_total_value=200 下各节点的 (真实总数, 返回行数) —— 括号外为覆盖路径
TABLE_SPEC = {
    ("10", "200", ""): (3, 3),        # 直接收下
    ("45", "200", ""): (213, 100),    # 超 cap → 下钻(截断行作废)
    ("4510", "200", ""): (90, 90),    # 直接收下
    ("4520", "200", ""): (123, 100),  # 叶子超 cap → 二分(该键会被重复查一次)
    ("4520", "200", "400"): (80, 80),
    ("4520", "400", ""): (43, 43),
}


def _materialize(spec: dict) -> dict:
    """把 (总数, 行数) 规格转成带行的 mock 响应表, seg 保证 ID 唯一。"""
    return {
        key: (total, _rows(key[0], min(n, total), seg))
        for seg, (key, (total, n)) in enumerate(spec.items())
    }


def _router(monkeypatch, table: dict, *, fail_keys: frozenset = frozenset(), member: int = 0):
    """按 (industry, total_value_a, total_value_b) 路由 mock 响应, 记录请求键。"""

    calls: list[tuple] = []

    def fake_post(url, params=None, data=None, headers=None, timeout=None):
        key = (data.get("industry"), data.get("total_value_a"), data.get("total_value_b"))
        calls.append(key)
        if key in fail_keys:
            raise httpx.ConnectError("mock down")
        total, rows = table[key]
        return _resp({"count_Info": f"{total} 条", "rows": rows, "is_member": member})

    monkeypatch.setattr(stock_dividend.httpx, "post", fake_post)
    monkeypatch.setattr(stock_dividend, "REQUEST_PAUSE", 0)
    monkeypatch.setattr(stock_dividend, "RETRY_BACKOFF", (0.0, 0.0, 0.0))
    return calls


# ---------------------------------------------------------------------------
# fetcher: 覆盖算法
# ---------------------------------------------------------------------------

def test_snapshot_covers_all_branches(monkeypatch):
    """直收/下钻/叶子二分三分支全走到, 去重行数与请求计数精确可预期。"""
    calls = _router(monkeypatch, _materialize(TABLE_SPEC))

    result = stock_dividend.fetch_dividend_snapshot("ck", TREE)

    assert len(result["rows"]) == 3 + 90 + 80 + 43  # 216 只去重
    meta = result["meta"]
    assert meta["request_count"] == 7  # 10,45,4510 + 4520 原查/二分重查/两半区
    assert meta["node_count"] == 4  # 二分不新增节点
    assert meta["trade_date"] == _today_cst()
    assert meta["tree_drift"] is False
    assert meta["warnings"] == []
    assert calls[0][0] == "10"  # 请求按树序发出


def test_member_cap_300_accepts_root_without_drill(monkeypatch):
    """is_member=1 时 cap=300, 213 只在根节点一次收齐, 无需下钻。"""
    table = _materialize({
        ("10", "200", ""): (3, 3),
        ("45", "200", ""): (213, 213),
    })
    calls = _router(monkeypatch, table, member=1)

    result = stock_dividend.fetch_dividend_snapshot("ck", TREE)

    assert len(result["rows"]) == 216
    assert result["meta"]["request_count"] == 2
    assert result["meta"]["cap"] == 300  # 生效上限随会员态上报
    assert [c[0] for c in calls] == ["10", "45"]  # 未下钻


def test_tree_drift_flagged_when_api_total_exceeds_cached_counts(monkeypatch):
    """信号①: API total 超过缓存树 cnts → tree_drift(树缺新节点)。"""
    spec = dict(TABLE_SPEC)
    spec[("4510", "200", "")] = (95, 95)  # 树里 cnts=90
    _router(monkeypatch, _materialize(spec))

    result = stock_dividend.fetch_dividend_snapshot("ck", TREE)

    assert result["meta"]["tree_drift"] is True


def test_missing_counts_do_not_trigger_drift(monkeypatch):
    """cnts 缺失(=0)不比对 —— 树计数只是规划优化项, 退化纯自适应不误报。"""
    tree0 = [dict(n, cnts=0) for n in TREE]
    _router(monkeypatch, _materialize(TABLE_SPEC))

    result = stock_dividend.fetch_dividend_snapshot("ck", tree0)

    assert result["meta"]["tree_drift"] is False


def test_tree_drift_flagged_when_row_sw_cd_outside_queried_nodes(monkeypatch):
    """信号②: 行 sw_cd 不以任何已查节点为前缀 → tree_drift + warning。"""
    bad_rows = [
        {"cell": _cell("s001", "100011")},
        {"cell": _cell("sbad", "990011")},  # 99 不在测试树里
        {"cell": _cell("s002", "100011")},
    ]
    table = _materialize(TABLE_SPEC)
    table[("10", "200", "")] = (3, bad_rows)
    _router(monkeypatch, table)

    result = stock_dividend.fetch_dividend_snapshot("ck", TREE)

    assert result["meta"]["tree_drift"] is True
    assert any("sw_cd" in w for w in result["meta"]["warnings"])


def test_failed_node_retries_then_continues(monkeypatch):
    """单节点连续失败: 重试 3 次→warning, 不中断整轮(其余节点照常收)。"""
    calls = _router(
        monkeypatch, _materialize(TABLE_SPEC),
        fail_keys=frozenset({("4510", "200", "")}),
    )

    result = stock_dividend.fetch_dividend_snapshot("ck", TREE)

    assert len(result["rows"]) == 3 + 80 + 43
    assert calls.count(("4510", "200", "")) == 4  # 1 + 3 次重试
    assert result["meta"]["failed_queries"] == 1  # 缺数分支数显式上报
    assert any("节点查询失败" in w for w in result["meta"]["warnings"])


def test_full_market_mode_bisect_handles_open_lower_bound(monkeypatch):
    """全市场模式(min_total_value=0): 开下界 None 的二分不得 TypeError。"""
    tree_full = [dict(n) for n in TREE]
    tree_full[1]["cnts"] = 260  # 全市场口径下 cnts=全量计数
    spec = {
        ("10", "", ""): (3, 3),
        ("45", "", ""): (260, 100),
        ("4510", "", ""): (90, 90),
        ("4520", "", ""): (123, 100),    # 叶子超 cap → 二分(该键重复查一次)
        ("4520", "", "30"): (105, 100),  # 开下界半区仍超 cap → 切点计算(修复点)
        ("4520", "", "12"): (55, 55),
        ("4520", "12", "30"): (50, 50),
        ("4520", "30", ""): (18, 18),    # 网格切点后的上半区
    }
    _router(monkeypatch, _materialize(spec))

    result = stock_dividend.fetch_dividend_snapshot("ck", tree_full, min_total_value=0)

    assert len(result["rows"]) == 3 + 90 + 55 + 50 + 18
    assert result["meta"]["tree_drift"] is False


# ---------------------------------------------------------------------------
# fetcher: 行业树解析
# ---------------------------------------------------------------------------

def test_parse_industry_options_extracts_tree():
    html = """
    <select id="select_industry" class="form-control">
      <option value="">全部</option>
      <option value="10" data-idx="801010" data-level="1" data-cnts="3" data-nm="一级A">一级A</option>
      <option value="4510" data-idx="801451" data-level="2" data-cnts="90" data-nm="一级B/二极B1">二极B1</option>
      <option value="451011" data-level="3" data-cnts="12">无属性名回退</option>
    </select>"""
    nodes = stock_dividend.parse_industry_options(html)
    assert nodes[0] == {"val": "10", "sw": "801010", "nm": "一级A", "level": 1, "cnts": 3}
    assert nodes[1]["nm"] == "一级B/二极B1" and nodes[1]["level"] == 2
    assert nodes[2]["nm"] == "无属性名回退" and nodes[2]["cnts"] == 12
    assert stock_dividend.parse_industry_options("<html>没有下拉</html>") == []


def test_fetch_industry_tree_raises_without_options(monkeypatch):
    """页面结构变更(无行业树下拉)必须抛错, 不能静默返回空树。"""
    request = httpx.Request("GET", "https://test/x")
    monkeypatch.setattr(
        stock_dividend.httpx, "get",
        lambda *a, **k: httpx.Response(
            200, text='<select id="select_industry"></select>', request=request
        ),
    )
    with pytest.raises(ValueError, match="未解析到行业树"):
        stock_dividend.fetch_industry_tree("ck")


# ---------------------------------------------------------------------------
# 任务: 双重闸门 / 漂移重试 / 结果契约
# ---------------------------------------------------------------------------

def _patch_task_env(monkeypatch, test_artifact_dir) -> Path:
    """任务环境三件套: 交易日 True + 固定 cookie + 有效树缓存。"""
    monkeypatch.setattr(stock_dividend_tasks, "is_trading_day", lambda _d: True)
    monkeypatch.setattr(stock_dividend_tasks, "get_cookie", lambda: "ck")
    cache = test_artifact_dir / "tree.json"
    stock_dividend_tasks._save_tree_cache(TREE, cache)
    return cache


def _snapshot(rows: list[dict], *, trade_date: str | None, drift: bool = False,
              warnings: list | None = None) -> dict:
    return {
        "rows": rows,
        "meta": {"trade_date": trade_date, "node_count": 1, "request_count": 1,
                 "warnings": warnings or [], "tree_drift": drift},
    }


def test_non_trading_day_skips_with_zero_requests(monkeypatch, test_artifact_dir):
    """闸门①: 非交易日零请求(连登录都不该发生)。"""
    monkeypatch.setattr(stock_dividend_tasks, "is_trading_day", lambda _d: False)
    monkeypatch.setattr(
        stock_dividend_tasks, "get_cookie",
        lambda: pytest.fail("非交易日不应触发登录"),
    )
    monkeypatch.setattr(
        stock_dividend.httpx, "post",
        lambda *a, **k: pytest.fail("非交易日不应发出列表请求"),
    )
    monkeypatch.setattr(
        stock_dividend.httpx, "get",
        lambda *a, **k: pytest.fail("非交易日不应发出页面请求"),
    )

    result = stock_dividend_tasks.run_stock_dividend_daily(
        cache_path=test_artifact_dir / "tree.json"
    )

    assert result == {"status": "skipped", "success_count": 0, "fail_count": 0}


def test_data_gate_skips_storing_when_last_dt_not_today(monkeypatch, test_artifact_dir):
    """闸门②: 多数 last_dt≠今天(节假日表缺漏场景) → 跳过落库。"""
    cache = _patch_task_env(monkeypatch, test_artifact_dir)
    stale = (datetime.now(_CST) - timedelta(days=1)).date().isoformat()
    monkeypatch.setattr(
        stock_dividend_tasks, "fetch_dividend_snapshot",
        lambda *a, **k: _snapshot(
            [_cell("s1", "100011", last_dt=stale)], trade_date=stale
        ),
    )
    db = MagicMock()
    monkeypatch.setattr(stock_dividend_tasks, "SessionLocal", lambda: db)

    result = stock_dividend_tasks.run_stock_dividend_daily(cache_path=cache)

    assert result == {"status": "skipped", "success_count": 0, "fail_count": 0}
    db.commit.assert_not_called()


def test_tree_drift_triggers_refetch_and_single_rerun(monkeypatch, test_artifact_dir):
    """漂移自愈: 重取树、整轮重跑一次; 重跑正常则以重跑结果落库。"""
    cache = _patch_task_env(monkeypatch, test_artifact_dir)
    snapshots = [
        _snapshot([_cell("s1", "100011")], trade_date=_today_cst(),
                  drift=True, warnings=["树计数漂移"]),
        _snapshot([_cell("s1", "100011"), _cell("s2", "100011")],
                  trade_date=_today_cst()),
    ]
    monkeypatch.setattr(
        stock_dividend_tasks, "fetch_dividend_snapshot",
        lambda *a, **k: snapshots.pop(0),
    )
    refetch = MagicMock(return_value=TREE)
    monkeypatch.setattr(stock_dividend_tasks, "fetch_industry_tree", refetch)
    db = MagicMock()
    monkeypatch.setattr(stock_dividend_tasks, "SessionLocal", lambda: db)
    saved = MagicMock(return_value=2)
    monkeypatch.setattr(stock_dividend_tasks, "save_stock_dividend_snapshot", saved)

    result = stock_dividend_tasks.run_stock_dividend_daily(cache_path=cache)

    assert result == {"success_count": 1, "fail_count": 0}
    refetch.assert_called_once()  # 重跑仅一次, 不会循环
    assert len(saved.call_args[0][1]) == 2  # 保存的是重跑后的行
    db.close.assert_called_once()


def test_empty_rows_is_reported_failed(monkeypatch, test_artifact_dir):
    """空返回(会话失效/接口变更)必须判失败, 不能静默记成功。"""
    cache = _patch_task_env(monkeypatch, test_artifact_dir)
    monkeypatch.setattr(
        stock_dividend_tasks, "fetch_dividend_snapshot",
        lambda *a, **k: _snapshot([], trade_date=None),
    )
    db = MagicMock()
    monkeypatch.setattr(stock_dividend_tasks, "SessionLocal", lambda: db)
    save = MagicMock()
    monkeypatch.setattr(stock_dividend_tasks, "save_stock_dividend_snapshot", save)

    result = stock_dividend_tasks.run_stock_dividend_daily(cache_path=cache)

    assert result == {"success_count": 0, "fail_count": 1}
    save.assert_not_called()
    db.commit.assert_not_called()


def test_database_failure_rolls_back_and_reports_failed(monkeypatch, test_artifact_dir):
    """落库失败必须 rollback 并返回失败计数。"""
    cache = _patch_task_env(monkeypatch, test_artifact_dir)
    monkeypatch.setattr(
        stock_dividend_tasks, "fetch_dividend_snapshot",
        lambda *a, **k: _snapshot([_cell("s1", "100011")], trade_date=_today_cst()),
    )
    db = MagicMock()
    monkeypatch.setattr(stock_dividend_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        stock_dividend_tasks, "save_stock_dividend_snapshot",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    result = stock_dividend_tasks.run_stock_dividend_daily(cache_path=cache)

    assert result == {"success_count": 0, "fail_count": 1}
    db.rollback.assert_called_once()
    db.close.assert_called_once()


# ---------------------------------------------------------------------------
# 树缓存: 月度刷新
# ---------------------------------------------------------------------------

def test_tree_cache_fresh_within_30_days_and_expired_after(test_artifact_dir):
    cache = test_artifact_dir / "tree.json"
    stock_dividend_tasks._save_tree_cache(TREE, cache)
    assert stock_dividend_tasks._load_tree_cache(cache) == TREE

    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["fetched_at"] = (
        datetime.now(_CST) - timedelta(days=31)
    ).isoformat(timespec="seconds")
    cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert stock_dividend_tasks._load_tree_cache(cache) is None


def test_tree_cache_corrupt_file_is_ignored(test_artifact_dir):
    """缓存文件损坏 → 当缺失处理(重取), 不抛错。"""
    cache = test_artifact_dir / "tree.json"
    cache.write_text("not-json", encoding="utf-8")
    assert stock_dividend_tasks._load_tree_cache(cache) is None


# ---------------------------------------------------------------------------
# store: 落库幂等与字段防御
# ---------------------------------------------------------------------------

def test_save_snapshot_idempotent_per_day_and_isolated_across_days(db):
    """同日重跑删旧插新; 其他日期历史不受影响。"""
    from backend.models.jisilu_stock import StockDividendDaily
    from backend.services.stock_dividend_store import save_stock_dividend_snapshot

    d1, d2 = date(2026, 9, 10), date(2026, 9, 11)

    assert save_stock_dividend_snapshot(
        db, [_cell("s1", "100011"), _cell("s2", "100011")], d1
    ) == 2
    # 同日重跑只留新集合
    assert save_stock_dividend_snapshot(db, [_cell("s1", "100011")], d1) == 1
    rows = db.query(StockDividendDaily).filter_by(trade_date=d1).all()
    assert [r.stock_id for r in rows] == ["s1"]

    # 写另一日, 此前日期不受影响
    save_stock_dividend_snapshot(db, [_cell("s3", "100011")], d2)
    assert db.query(StockDividendDaily).count() == 2
    assert db.query(StockDividendDaily).filter_by(trade_date=d1).count() == 1


def test_row_mapping_is_defensive_and_keeps_raw_json(db):
    """数值列防御('buy'/''→None), stdevry 存原样, raw_json 保留完整 cell。"""
    from backend.models.jisilu_stock import StockDividendDaily
    from backend.services.stock_dividend_store import save_stock_dividend_snapshot

    cell = _cell("s1", "100011")
    cell.update({
        "pledge_rt": "buy",   # 徽标串 → None
        "stdevry": "buy",     # 徽标串 → String 列存原样
        "pe": "",             # 空串 → None
        "owned": "1",         # 字符串整数 → 1
        "holded": "",         # 空串 → None
    })
    save_stock_dividend_snapshot(db, [cell], date(2026, 9, 11))

    row = db.query(StockDividendDaily).one()
    assert row.price == 10.5 and row.total_value == 300.0
    assert row.pledge_rt is None and row.pe is None
    assert row.stdevry == "buy"
    assert row.owned == 1 and row.holded is None
    assert json.loads(row.raw_json)["pledge_rt"] == "buy"  # 原样兜底


def test_dirty_rows_without_stock_id_are_skipped(db):
    """无 stock_id 的脏行跳过, 不产生半空行。"""
    from backend.models.jisilu_stock import StockDividendDaily
    from backend.services.stock_dividend_store import save_stock_dividend_snapshot

    cells = [{"stock_id": "", "stock_nm": "脏行"}, _cell("s1", "100011"), {"stock_nm": "无代码"}]
    assert save_stock_dividend_snapshot(db, cells, date(2026, 9, 11)) == 1
    assert db.query(StockDividendDaily).count() == 1


# ---------------------------------------------------------------------------
# 注册表: 调度接线
# ---------------------------------------------------------------------------

def test_job_registered_at_1508_and_not_everyday():
    from backend.tasks.registry import DAILY_JOBS, EVERYDAY_JOB_IDS

    entry = next(j for j in DAILY_JOBS if j[0] == "stock_dividend_daily")
    assert entry[1] is stock_dividend_tasks.run_stock_dividend_daily
    assert entry[2] == "高股息股票快照抓取"
    assert (entry[3], entry[4]) == (15, 8)
    assert "stock_dividend_daily" not in EVERYDAY_JOB_IDS


# ---------------------------------------------------------------------------
# 状态页/巡检接线: 目录纳管与完整性注册(评审 P1-1 / P2-1 的回归锁)
# ---------------------------------------------------------------------------

def test_status_page_group_is_catalog_managed(db):
    """高股息分组必须被数据目录纳管(managed + job_id), 不能永远 no_data。"""
    from backend.services.data_status import build_data_status

    status = build_data_status(db)
    group = next(g for g in status["datasets"] if g["name"] == "高股息股票快照")
    assert group["unmanaged_entities"] == []
    entity = group["entities"][0]
    assert entity["managed"] is True
    assert entity["job_id"] == "stock_dividend_daily"
    assert entity["source"] == "jisilu"


def test_integrity_registry_covers_stock_dividend():
    from backend.models.jisilu_stock import StockDividendDaily
    from backend.services.data_integrity import DAILY_TABLE_REGISTRY

    entry = next(
        r for r in DAILY_TABLE_REGISTRY if r["name"] == "高股息股票快照"
    )
    assert entry["model"] is StockDividendDaily
    assert entry["mode"] == "global"
