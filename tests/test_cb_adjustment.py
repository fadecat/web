# -*- coding: utf-8 -*-
"""cb_adjustment(集思录转股价「下修记录」按需代理)服务与路由合同测试。

全程 mock httpx.get(公开接口, 不涉登录态), 不触外网(conftest socket 哨兵兜底);
另覆盖 cb_screen._to_dto 对 convert_price / adj_scnt 的透传。
"""
from __future__ import annotations

import pytest

from backend.services import cb_adjustment

# ---------------------------------------------------------------------------
# 测试夹具: 真实 adj_logs 接口返回的浓缩样本(D=下修记录)
# ---------------------------------------------------------------------------

_LOGS_HTML = (
    '<table class="tablesorter" style="width:440px;"><thead><tr><th>转债名称</th>'
    '<th>股东大会日</th><th>下修前<br>转股价</th><th>下修后<br>转股价</th>'
    '<th>新转股价<br>生效日期</th><th>下修底价</th></tr></thead><tbody>'
    '<tr><td>美锦转债</td><td>2026-07-16</td><td>5.260</td><td>3.470</td><td>2026-07-17</td><td>3.470</td></tr>'
    '<tr><td>美锦转债</td><td>2024-06-25</td><td>12.930</td><td>5.260</td><td>2024-06-26</td><td>5.257</td></tr>'
    '</tbody></table>'
)

# 无记录时接口返回的字面量
_EMPTY_HTML = "----"


class _FakeResp:
    """仅覆盖本模块用到的 httpx.Response 接口(text / raise_for_status)。"""

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试用干净的进程内缓存。"""
    cb_adjustment._cache.clear()
    yield
    cb_adjustment._cache.clear()


@pytest.fixture()
def fetch_state(monkeypatch):
    """mock 抓取, 记录请求参数(bond_id / adj_type / 是否带了 cookie)。"""
    state = {
        "pages": {},        # bond_id -> html(缺省 _LOGS_HTML)
        "raise_ids": set(),  # 抓取直接抛异常的 bond_id
        "requests": [],     # (params, has_cookie)
    }

    def _fake_get(url, params=None, headers=None, **kwargs):  # noqa: ANN001, ARG001
        state["requests"].append((params, bool(headers.get("Cookie")) if headers else False))
        bond_id = (params or {}).get("bond_id", "")
        if bond_id in state["raise_ids"]:
            raise RuntimeError("network down")
        return _FakeResp(state["pages"].get(bond_id, _LOGS_HTML))

    monkeypatch.setattr(cb_adjustment.httpx, "get", _fake_get)
    return state


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_parse_extracts_rows_in_order():
    items = cb_adjustment._parse_logs(_LOGS_HTML)
    assert len(items) == 2
    # 源序即新→旧, 原样保留; 名称列冗余不输出
    first = items[0]
    assert first == {
        "meeting_date": "2026-07-16",
        "price_before": 5.26,
        "price_after": 3.47,
        "effective_date": "2026-07-17",
        "floor_price": 3.47,
    }
    assert items[1]["meeting_date"] == "2024-06-25"
    assert items[1]["floor_price"] == 5.257  # 未下修到底: 底价 ≠ 下修后


def test_parse_empty_marker_returns_empty():
    assert cb_adjustment._parse_logs(_EMPTY_HTML) == []
    assert cb_adjustment._parse_logs("") == []


def test_parse_tolerates_missing_floor_price():
    # 列不足 6(缺下修底价): 前 5 列仍可用, 底价为 None
    html = "<tbody><tr><td>甲</td><td>2026-01-01</td><td>10.0</td><td>8.0</td><td>2026-01-02</td></tr></tbody>"
    items = cb_adjustment._parse_logs(html)
    assert items == [{
        "meeting_date": "2026-01-01", "price_before": 10.0, "price_after": 8.0,
        "effective_date": "2026-01-02", "floor_price": None,
    }]


def test_parse_skips_malformed_row():
    html = "<tbody><tr><td>甲</td><td>残行</td></tr>" \
           "<tr><td>乙</td><td>2026-01-01</td><td>10.0</td><td>8.0</td><td>2026-01-02</td></tr></tbody>"
    assert len(cb_adjustment._parse_logs(html)) == 1


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------

def test_bad_bond_id_raises_without_request(fetch_state):
    for bad in ("12703", "1270301", "abc", ""):
        with pytest.raises(ValueError, match="6 位数字"):
            cb_adjustment.get_adjustment_logs(bad)
    assert fetch_state["requests"] == []


def test_cache_hit_skips_refetch(fetch_state):
    first = cb_adjustment.get_adjustment_logs("127061")
    assert len(first) == 2
    assert len(fetch_state["requests"]) == 1
    cb_adjustment.get_adjustment_logs("127061")
    assert len(fetch_state["requests"]) == 1  # 7 天 TTL 内不重抓


def test_empty_result_is_cached(fetch_state):
    fetch_state["pages"]["110045"] = _EMPTY_HTML
    assert cb_adjustment.get_adjustment_logs("110045") == []
    assert len(fetch_state["requests"]) == 1
    assert cb_adjustment.get_adjustment_logs("110045") == []  # 空列表为有效终态, 命中缓存
    assert len(fetch_state["requests"]) == 1


def test_fetch_error_propagates_and_not_cached(fetch_state):
    fetch_state["raise_ids"].add("127061")
    with pytest.raises(RuntimeError, match="network down"):
        cb_adjustment.get_adjustment_logs("127061")
    assert "127061" not in cb_adjustment._cache  # 失败不落缓存, 可重试
    fetch_state["raise_ids"].clear()
    assert len(cb_adjustment.get_adjustment_logs("127061")) == 2  # 重试成功


def test_request_uses_public_endpoint_without_cookie(fetch_state):
    cb_adjustment.get_adjustment_logs("127061")
    params, has_cookie = fetch_state["requests"][0]
    # 公开接口: adj_type=D(下修记录), 不带登录 cookie(不碰会话配额)
    assert params == {"bond_id": "127061", "adj_type": "D"}
    assert has_cookie is False


# ---------------------------------------------------------------------------
# cb_screen DTO 透传(转股价 + 成功下修次数)
# ---------------------------------------------------------------------------

def test_to_dto_carries_convert_price_and_adj_scnt():
    from backend.services.cb_screen import _to_dto

    row = {"cell": {
        "bond_id": "127061", "bond_nm": "美锦转债",
        "convert_price": 3.47, "adj_scnt": "2",  # 集思录 int/str 混返
    }}
    dto = _to_dto(row, 1, scored_mode=False)
    assert dto["convert_price"] == 3.47
    assert dto["adj_scnt"] == 2

    # 缺字段: 均为 None, 前端按「无下修」渲染
    dto_missing = _to_dto({"cell": {"bond_id": "110002"}}, 2, scored_mode=False)
    assert dto_missing["convert_price"] is None
    assert dto_missing["adj_scnt"] is None


# ---------------------------------------------------------------------------
# 路由合同(contract_client: 隔离 lifespan + 内存库)
# ---------------------------------------------------------------------------

def test_route_get_ok(contract_client, fetch_state):
    resp = contract_client.get("/api/cb-adjustment/127061")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bond_id"] == "127061"
    assert body["items"][0]["meeting_date"] == "2026-07-16"
    assert body["items"][0]["price_after"] == 3.47


def test_route_get_bad_id_400(contract_client, fetch_state):
    assert contract_client.get("/api/cb-adjustment/abc").status_code == 400


def test_route_get_upstream_error_502(contract_client, fetch_state):
    fetch_state["raise_ids"].add("127061")
    assert contract_client.get("/api/cb-adjustment/127061").status_code == 502
