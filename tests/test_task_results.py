# -*- coding: utf-8 -*-
"""日频任务结构化结果契约测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.tasks import (
    cb_index_tasks,
    cb_list_tasks,
    cb_redeem_tasks,
    index_eod_tasks,
    style_rotation_tasks,
    valuation_tasks,
)


def _uni_index(code, name="创成长", **ds_kwargs):
    """构造统一名单 (index, dataset) 元组, 供 monkeypatch datasets_for_job 使用。"""
    ds = {"source": "efunds", "storage_code": code, "symbol": code, "enabled": True}
    ds.update(ds_kwargs)
    return ({"code": code, "name": name, "enabled": True, "datasets": {}}, ds)


@pytest.mark.parametrize(
    "module,func_name",
    [
        (style_rotation_tasks, "run_style_rotation_daily"),
        (cb_list_tasks, "run_cb_list_daily"),
        (cb_index_tasks, "run_cb_index_daily"),
        (cb_redeem_tasks, "run_cb_redeem_daily"),
    ],
)
def test_market_snapshot_tasks_report_non_trading_day_as_skipped(monkeypatch, module, func_name):
    """当日快照任务非交易日必须 skipped，不能返回 None 被误记 success。"""
    monkeypatch.setattr(module, "is_trading_day", lambda _day: False)

    result = getattr(module, func_name)()

    assert result == {"status": "skipped", "success_count": 0, "fail_count": 0}


@pytest.mark.parametrize(
    "module,func_name,fetch_name",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list"),
        (cb_index_tasks, "run_cb_index_daily", "fetch_cb_index_history"),
        (cb_redeem_tasks, "run_cb_redeem_daily", "fetch_redeem_list"),
    ],
)
def test_single_stream_fetch_failure_is_reported_failed(monkeypatch, module, func_name, fetch_name):
    """转债单流任务抓取失败必须返回 fail_count，不能吞异常后假绿。"""
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, fetch_name, lambda: (_ for _ in ()).throw(RuntimeError("source down")))

    result = getattr(module, func_name)()

    assert result == {"success_count": 0, "fail_count": 1}


def test_dividend_failure_marks_valuation_task_partial_and_rolls_back(monkeypatch):
    """PE成功但股息率失败属于 partial，失败后必须回滚 Session。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        valuation_tasks,
        "datasets_for_job",
        lambda _job: [_uni_index("399296")],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_args, **_kwargs: {
            "index_name": "创成长",
            "index_valuation_percentile_url": "percentile",
            "index_dividend_yield_url": "dividend",
        },
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_valuation_percentile",
        lambda *_args, **_kwargs: [{"trade_date": "2026-09-07", "metrics": {}}],
    )
    monkeypatch.setattr(valuation_tasks, "save_valuation_snapshots", lambda *_args: 1)
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_dividend_yield",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dividend down")),
    )
    # 国债子流要真的成功: 必须返回非空历史(空返回按新契约算异常空->失败)
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-07", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)

    result = valuation_tasks.run_valuation_daily()

    # PE 成功 + 股息率失败 + 国债成功 = 2 个成功子流 / 1 个失败子流
    assert result == {"success_count": 2, "fail_count": 1}
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_bond_success_counts_as_valuation_substream(monkeypatch):
    """无指数标的但国债成功时不能被误判为零成功/全失败。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(valuation_tasks, "datasets_for_job", lambda _job: [])
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-07", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 1)

    result = valuation_tasks.run_valuation_daily()

    assert result == {"success_count": 1, "fail_count": 0}
    db.close.assert_called_once()


@pytest.mark.parametrize(
    "module,func_name,fetch_name,save_name,sample",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list", "save_cb_snapshots", [{}]),
        (cb_index_tasks, "run_cb_index_daily", "fetch_cb_index_history", "save_cb_index_records", [{"date": "2026-09-07"}]),
        (cb_redeem_tasks, "run_cb_redeem_daily", "fetch_redeem_list", "save_cb_redeem", [{}]),
    ],
)
def test_single_stream_database_failure_rolls_back_and_reports_failed(
    monkeypatch, module, func_name, fetch_name, save_name, sample
):
    """转债任务落库失败必须 rollback 并返回 failed 计数。"""
    db = MagicMock()
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, fetch_name, lambda: sample)
    monkeypatch.setattr(
        module,
        save_name,
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    result = getattr(module, func_name)()

    assert result == {"success_count": 0, "fail_count": 1}
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_real_sqlite_rollback_clears_failed_transaction_and_allows_next_write(db):
    """真实 SQLite：失败事务 rollback 后，同一 Session 能继续写下一条。"""
    from datetime import date

    from sqlalchemy.exc import IntegrityError

    from backend.models.valuation import IndexValuationSnapshot

    # nullable=False 的 index_name 触发真实 SQL 约束失败
    db.add(
        IndexValuationSnapshot(
            index_code="BAD",
            index_name=None,
            trade_date=date(2026, 9, 7),
            pe=1.0,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        IndexValuationSnapshot(
            index_code="399296",
            index_name="创成长",
            trade_date=date(2026, 9, 7),
            pe=34.0,
        )
    )
    db.commit()

    rows = db.query(IndexValuationSnapshot).all()
    assert [(r.index_code, r.pe) for r in rows] == [("399296", 34.0)]


def test_historical_sync_jobs_run_without_trading_day_gate(monkeypatch):
    """易方达历史同步不应被周末挡住；返回日期由源数据决定。"""
    val_db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: val_db)
    monkeypatch.setattr(valuation_tasks, "datasets_for_job", lambda _job: [])
    # 本测试只验证「不被非交易日挡住」, 国债子流给正常非空数据;
    # 空返回的语义由 test_*_empty_* 系列覆盖(异常空 -> 失败)。
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-05", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)
    assert valuation_tasks.run_valuation_daily() == {"success_count": 1, "fail_count": 0}

    eod_db = MagicMock()
    monkeypatch.setattr(index_eod_tasks, "SessionLocal", lambda: eod_db)
    monkeypatch.setattr(index_eod_tasks, "datasets_for_job", lambda _job: [])
    assert index_eod_tasks.run_index_eod_daily() == {"success_count": 0, "fail_count": 0}


# ---------------------------------------------------------------------------
# 异常空数据契约: 本应非空的返回为空 -> 判失败(不能静默记成功)
# 合法可空的返回为空 -> 仍算成功; 非空但新增 0 条 -> 仍算成功
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module,func_name,fetch_name,save_name",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list", "save_cb_snapshots"),
        (cb_index_tasks, "run_cb_index_daily", "fetch_cb_index_history", "save_cb_index_records"),
    ],
)
def test_single_stream_empty_fetch_is_reported_failed(
    monkeypatch, module, func_name, fetch_name, save_name
):
    """转债全量快照/等权指数返回空列表必须判失败,不能因为「没抛异常」就记成功。"""
    db = MagicMock()
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, fetch_name, lambda: [])
    monkeypatch.setattr(module, save_name, lambda *_args: 0)

    result = getattr(module, func_name)()

    assert result == {"success_count": 0, "fail_count": 1}
    # 空返回在落库前就被拦下, 不应建立会话/写库
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "module,func_name,fetch_name,save_name,sample",
    [
        (cb_list_tasks, "run_cb_list_daily", "fetch_cb_list", "save_cb_snapshots", [{"bond_id": "113050"}]),
        (
            cb_index_tasks,
            "run_cb_index_daily",
            "fetch_cb_index_history",
            "save_cb_index_records",
            [{"date": "2026-09-07", "index_value": "180.1"}],
        ),
    ],
)
def test_single_stream_non_empty_with_zero_inserted_is_success(
    monkeypatch, module, func_name, fetch_name, save_name, sample
):
    """正常非空返回但新增 0 条(当日已写过/幂等命中)必须算成功。"""
    db = MagicMock()
    monkeypatch.setattr(module, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, fetch_name, lambda: sample)
    monkeypatch.setattr(module, save_name, lambda *_args: 0)

    assert getattr(module, func_name)() == {"success_count": 1, "fail_count": 0}


def test_redeem_legal_empty_list_is_success(monkeypatch):
    """强赎列表合法为空(当日无强赎转债)不能一刀切判失败。"""
    db = MagicMock()
    monkeypatch.setattr(cb_redeem_tasks, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(cb_redeem_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(cb_redeem_tasks, "fetch_redeem_list", lambda: [])
    monkeypatch.setattr(cb_redeem_tasks, "save_cb_redeem", lambda *_args: 0)

    assert cb_redeem_tasks.run_cb_redeem_daily() == {"success_count": 1, "fail_count": 0}
    db.rollback.assert_not_called()


def test_empty_valuation_percentile_fails_but_keeps_bond_success(monkeypatch):
    """估值分位空返回判该标的失败,已成功的国债子流计数保留。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        valuation_tasks,
        "datasets_for_job",
        lambda _job: [_uni_index("399296")],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_a, **_k: {"index_name": "创成长", "index_valuation_percentile_url": "p"},
    )
    monkeypatch.setattr(valuation_tasks, "fetch_index_valuation_percentile", lambda *_a, **_k: [])
    monkeypatch.setattr(
        valuation_tasks,
        "save_valuation_snapshots",
        lambda *_args: pytest.fail("空估值分位不应进入落库"),
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-07", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)

    # 估值分位失败 -> 该标的 fail; 国债正常非空(新增 0) -> success
    assert valuation_tasks.run_valuation_daily() == {"success_count": 1, "fail_count": 1}


def test_empty_dividend_history_fails_without_losing_pe_success(monkeypatch):
    """股息率历史空序列判股息子流失败,PE 与国债的成功不受影响。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        valuation_tasks,
        "datasets_for_job",
        lambda _job: [_uni_index("399296")],
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_detail",
        lambda *_a, **_k: {
            "index_name": "创成长",
            "index_valuation_percentile_url": "p",
            "index_dividend_yield_url": "dividend",
        },
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_valuation_percentile",
        lambda *_a, **_k: [{"trade_date": "2026-09-07", "metrics": {"PE(TTM)": {}}}],
    )
    # 非空历史 + 新增 0 条: PE 子流仍应成功
    monkeypatch.setattr(valuation_tasks, "save_valuation_snapshots", lambda *_args: 0)
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_index_dividend_yield",
        lambda *_a, **_k: {
            "index_code": "399296",
            "index_dividend_yield": 2.1,
            "index_dividend_yield_date": "2026-09-07",
            "history": [],
        },
    )
    monkeypatch.setattr(
        valuation_tasks,
        "save_dividend_yield",
        lambda *_args: pytest.fail("空股息历史不应进入落库"),
    )
    monkeypatch.setattr(
        valuation_tasks,
        "fetch_cn_10y_bond_yield",
        lambda: [{"trade_date": "2026-09-07", "cn_10y_bond_yield": 1.8}],
    )
    monkeypatch.setattr(valuation_tasks, "save_bond_yields", lambda *_args: 0)

    # PE 成功 + 国债成功 = 2, 股息历史空 = 1 失败
    assert valuation_tasks.run_valuation_daily() == {"success_count": 2, "fail_count": 1}


def test_empty_bond_history_is_reported_failed(monkeypatch):
    """国债收益率空返回必须判失败(此前会被记成一个成功子流)。"""
    db = MagicMock()
    monkeypatch.setattr(valuation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(valuation_tasks, "datasets_for_job", lambda _job: [])
    monkeypatch.setattr(valuation_tasks, "fetch_cn_10y_bond_yield", lambda: [])
    monkeypatch.setattr(
        valuation_tasks,
        "save_bond_yields",
        lambda *_args: pytest.fail("空国债历史不应进入落库"),
    )

    assert valuation_tasks.run_valuation_daily() == {"success_count": 0, "fail_count": 1}


def test_empty_index_eod_records_is_reported_failed(monkeypatch):
    """易方达 eod 空返回必须判该标的失败。"""
    db = MagicMock()
    monkeypatch.setattr(index_eod_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        index_eod_tasks,
        "datasets_for_job",
        lambda _job: [_uni_index("930955", "红利低波动100")],
    )
    monkeypatch.setattr(index_eod_tasks, "fetch_index_eod_price", lambda _code: [])
    monkeypatch.setattr(
        index_eod_tasks,
        "save_index_quotes",
        lambda *_args: pytest.fail("空 eod 历史不应进入落库"),
    )

    assert index_eod_tasks.run_index_eod_daily() == {"success_count": 0, "fail_count": 1}


def test_index_eod_non_empty_with_zero_inserted_is_success(monkeypatch):
    """eod 正常非空但新增 0 条(全历史已同步)必须算成功。"""
    db = MagicMock()
    monkeypatch.setattr(index_eod_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        index_eod_tasks,
        "datasets_for_job",
        lambda _job: [_uni_index("930955", "红利低波动100")],
    )
    monkeypatch.setattr(
        index_eod_tasks,
        "fetch_index_eod_price",
        lambda _code: [{"date": "2026-09-07", "close": 11412.7}],
    )
    monkeypatch.setattr(index_eod_tasks, "save_index_quotes", lambda *_args: 0)

    assert index_eod_tasks.run_index_eod_daily() == {"success_count": 1, "fail_count": 0}


def test_style_rotation_partial_empty_keeps_other_index_success(monkeypatch):
    """风格轮动一只指数空返回判失败,另一只成功的计数必须保留。"""
    db = MagicMock()
    monkeypatch.setattr(style_rotation_tasks, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(style_rotation_tasks, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        style_rotation_tasks,
        "datasets_for_job",
        lambda _job: [
            _uni_index("399376", "国证小盘成长", source="tencent"),
            _uni_index("399373", "国证大盘价值", source="tencent"),
        ],
    )
    monkeypatch.setattr(
        style_rotation_tasks,
        "fetch_index_kline",
        lambda symbol: [] if symbol == "399376"
        else [{"date": "2026-09-07", "close": 1.0}],
    )
    monkeypatch.setattr(style_rotation_tasks, "save_index_quotes", lambda *_args: 0)

    # 左指数空 -> 1 失败; 右指数非空(新增 0) -> 1 成功
    assert style_rotation_tasks.run_style_rotation_daily() == {
        "success_count": 1,
        "fail_count": 1,
    }


# ---------------------------------------------------------------------------
# 解析层: 区分「结构正常的空」与「格式不对/全部无效」
# 后者必须抛错, 否则任务层只看到空列表, 无法判断该不该算失败
# ---------------------------------------------------------------------------

def _fake_response(*, json_body=None, text=None, method="GET", url="http://test/x"):
    """构造带 request 的 httpx.Response(raise_for_status 需要 request)。"""
    import httpx

    request = httpx.Request(method, url)
    if json_body is not None:
        return httpx.Response(200, json=json_body, request=request)
    return httpx.Response(200, text=text or "", request=request)


def test_cb_index_page_with_unmapped_fields_raises():
    """等权指数页面字段全部改名 -> 必须抛错,不能产出「只有日期」的空壳记录。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07'];\n"
        "var __data = {'brand_new_field': [180.1]};\n"
    )
    with pytest.raises(ValueError, match="字段全部无法映射"):
        cb_index.parse_cb_index_page(html)


def test_cb_index_page_with_known_fields_parses_values():
    """字段正常时照旧解析出指标值(证明上面的校验不是一刀切)。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07'];\n"
        "var __data = {'price': [180.1], 'count': [560]};\n"
    )
    records = cb_index.parse_cb_index_page(html)
    assert records == [{"date": "2026-09-07", "index_value": "180.1", "count": "560"}]


# ---------------------------------------------------------------------------
# T1: 锁定并修复数组错位 bug
# 源数组形如 mid_price:[100,,102](中间有空位) 时, 按日期下标取数不能错位。
# ---------------------------------------------------------------------------

def test_cb_index_mid_price_gap_preserves_date_alignment():
    """三日 mid_price:[100,,102] 必须保持日期对齐: d2 缺失, d3=102。

    证明当前代码 bug: `if v.strip()` 会过滤空串,把 [100,,102] 变成
    [100,102],导致 102 错位到 d2。
    """
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,,102]};\n"
    )
    records = cb_index.parse_cb_index_page(html)

    assert len(records) == 3
    # d1 = 2026-09-07 -> 100
    assert records[0] == {"date": "2026-09-07", "median_price": "100"}
    # d2 = 2026-09-08 -> 中间空位, 缺失(空串, store 层 parse_float 转 None)
    assert records[1] == {"date": "2026-09-08", "median_price": ""}
    # d3 = 2026-09-09 -> 102, 不能错位到 d2
    assert records[2] == {"date": "2026-09-09", "median_price": "102"}


def test_cb_index_mid_price_explicit_null_preserved():
    """三日 mid_price:[100,null,102] 中间空位保留, null 由 parse_float 转缺失。"""
    from backend.services.fetchers import cb_index
    from backend.utils import parse_float

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,null,102]};\n"
    )
    records = cb_index.parse_cb_index_page(html)

    assert records[1] == {"date": "2026-09-08", "median_price": "null"}
    assert records[2] == {"date": "2026-09-09", "median_price": "102"}
    # 显式 null 在 store 层经 parse_float 转缺失, 不移动后续元素
    assert parse_float(records[1]["median_price"]) is None
    assert parse_float(records[2]["median_price"]) == 102.0


def test_cb_index_trailing_comma_dropped_without_shift():
    """尾随逗号 [100,102,] 只移除尾 token, 不产生错位。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,102,]};\n"
    )
    records = cb_index.parse_cb_index_page(html)

    assert len(records) == 2
    assert records[0] == {"date": "2026-09-08", "median_price": "100"}
    assert records[1] == {"date": "2026-09-09", "median_price": "102"}


def test_cb_index_trailing_comma_with_whitespace_dropped_without_shift():
    """尾随逗号后的空白仍是 JS 语法尾逗号, 不应制造额外元素。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,102, ]};\n"
    )
    records = cb_index.parse_cb_index_page(html)

    assert [r["median_price"] for r in records] == ["100", "102"]


def test_cb_index_single_hole_array_preserves_js_position():
    """[,] 是长度为 1 的 JS 数组, 空位必须保留而不能压成空数组。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-08'];\n"
        "var __data = {'mid_price': [,]};\n"
    )
    records = cb_index.parse_cb_index_page(html)

    assert records == [{"date": "2026-09-08", "median_price": ""}]


def test_cb_index_only_unknown_fields_raises():
    """只有未知字段 -> 走已有 ValueError 分支(字段全部无法映射)。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07'];\n"
        "var __data = {'brand_new_field': [180.1]};\n"
    )
    with pytest.raises(ValueError, match="字段全部无法映射"):
        cb_index.parse_cb_index_page(html)


def test_cb_index_short_array_raises_length_mismatch():
    """三日日期但某已知字段数组只有 2 个元素 -> 抛 ValueError(防止错位)。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,102]};\n"
    )
    with pytest.raises(ValueError, match="数组长度 2 与日期数 3 不一致"):
        cb_index.parse_cb_index_page(html)


def test_cb_index_extra_values_raises_length_mismatch():
    """三日日期但某已知字段数组有 4 个元素 -> 抛 ValueError(防止多余值错位)。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,102,103,104]};\n"
    )
    with pytest.raises(ValueError, match="数组长度 4 与日期数 3 不一致"):
        cb_index.parse_cb_index_page(html)


def test_cb_index_legal_array_aligned_without_error():
    """合法等长数组按日期对齐解析, 不抛错。"""
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'mid_price': [100,101,102]};\n"
    )
    records = cb_index.parse_cb_index_page(html)
    assert [r["median_price"] for r in records] == ["100", "101", "102"]


def test_cb_index_malformed_array_does_not_partially_persist():
    """错误输入(部分字段长度不符)必须在返回前抛错, 不能部分入库。

    一个字段长度正确、另一个字段短一截时, 仍应整体抛错, 不应返回
    已经对齐的那部分记录被误落库。
    """
    from backend.services.fetchers import cb_index

    html = (
        "var __date = ['2026-09-07', '2026-09-08', '2026-09-09'];\n"
        "var __data = {'price': [1,2,3], 'mid_price': [100,102]};\n"
    )
    with pytest.raises(ValueError, match="数组长度"):
        cb_index.parse_cb_index_page(html)


def test_redeem_list_empty_rows_is_legal_empty(monkeypatch):
    """强赎接口结构正常但 rows 为空 -> 合法空,返回 [] 不抛错。"""
    from backend.services.fetchers import cb_redeem

    monkeypatch.setattr(cb_redeem, "get_cookie", lambda: "cookie")
    monkeypatch.setattr(
        cb_redeem.httpx, "post",
        lambda url, **_k: _fake_response(json_body={"rows": []}, method="POST", url=url),
    )

    assert cb_redeem.fetch_redeem_list() == []


def test_redeem_list_missing_rows_key_raises(monkeypatch):
    """强赎接口缺少 rows(未登录/接口变更) -> 解析失败必须抛错,不能伪装成合法空。"""
    from backend.services.fetchers import cb_redeem

    monkeypatch.setattr(cb_redeem, "get_cookie", lambda: "cookie")
    monkeypatch.setattr(
        cb_redeem.httpx, "post",
        lambda url, **_k: _fake_response(
            json_body={"code": 401, "msg": "请登录"}, method="POST", url=url
        ),
    )

    with pytest.raises(ValueError, match="缺少 rows 列表"):
        cb_redeem.fetch_redeem_list()


def test_cb_list_rows_without_cell_raises(monkeypatch):
    """转债列表行数够但一条 cell 都没有 -> 抛错,不能返回空列表。"""
    from backend.services.fetchers import cb_list

    monkeypatch.setattr(cb_list, "get_cookie", lambda: "cookie")
    monkeypatch.setattr(
        cb_list.httpx, "post",
        lambda url, **_k: _fake_response(
            json_body={"rows": [{"id": i} for i in range(40)]}, method="POST", url=url
        ),
    )

    with pytest.raises(ValueError, match="均无 cell 字段"):
        cb_list.fetch_cb_list()


def test_index_kline_all_rows_malformed_raises(monkeypatch):
    """腾讯K线返回了行但行格式全不合规 -> 抛错,不能返回空列表被记成功。"""
    from backend.services.fetchers import style_rotation

    body = 'kline_day={"data": {"sz399376": {"day": [["2026-09-07"], [1, 2]]}}}'
    monkeypatch.setattr(
        style_rotation, "fetch_with_retry",
        lambda *_a, **_k: _fake_response(text=body),
    )

    with pytest.raises(ValueError, match="行格式异常"):
        style_rotation.fetch_index_kline("399376")


def test_index_eod_all_rows_invalid_raises(monkeypatch):
    """易方达 eod 行缺日期/收盘价被逐行过滤后全空 -> 抛错(已有行为,加测试锁定)。"""
    from backend.services.fetchers import index_eod

    monkeypatch.setattr(
        index_eod, "fetch_with_retry",
        lambda *_a, **_k: _fake_response(json_body=[{"trdDt": "", "pxClose": None}]),
    )

    with pytest.raises(ValueError, match="未返回有效数据"):
        index_eod.fetch_index_eod_price("930955")
