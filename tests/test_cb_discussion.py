# -*- coding: utf-8 -*-
"""cb_discussion(集思录「相关讨论」按需代理)服务与路由合同测试。

全程 mock jisilu.get_cookie 与 httpx.get, 不触外网(conftest socket 哨兵兜底)。
"""
from __future__ import annotations

import pytest

from backend.services import cb_discussion

# ---------------------------------------------------------------------------
# 测试夹具: 真实详情页板块结构的浓缩样本
# ---------------------------------------------------------------------------

_DETAIL_HTML = """
<html><body>
<div id="tbl_questions" class="ref_tb"><div class="title_b">相关讨论</div>
<ul class="question_list">
<li><span>
<div class="title"><a target="_blank" href="/question/525018">盛路转债怎么玩</a></div>
<div class="text"><span class="num">11条回复</span><span class="num">2089次浏览</span> <span>2026-09-04</span></div>
</span></li>
<li><span>
<div class="title"><a target="_blank" href="/question/523001"> asking 关于下修 </a></div>
<div class="text"><span class="num">3条回复</span><span class="num">150次浏览</span></div>
</span></li>
</ul>
</div>
<div id="tbl_bonds" class="ref_tb"><ul class="question_list"></ul></div>
</body></html>
"""

_LOGIN_HTML = "<html><body><div>帐号密码登录</div></body></html>"


class _FakeResp:
    """仅覆盖本模块用到的 httpx.Response 接口(text / raise_for_status)。"""

    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个测试用干净的进程内缓存。"""
    cb_discussion._cache.clear()
    yield
    cb_discussion._cache.clear()


@pytest.fixture()
def fetch_state(monkeypatch):
    """mock cookie 与抓取, 记录调用轨迹(cookie 获取次数 / 请求 URL / 所用 cookie)。"""
    state = {
        "cookies": [],       # get_cookie 依次返回的 cookie(空则恒返回 "ck-ok")
        "pages": {},         # bond_id -> html(缺省 _DETAIL_HTML)
        "raise_ids": set(),  # 抓取直接抛异常的 bond_id
        "get_cookie_calls": 0,
        "requests": [],      # (bond_id, cookie)
    }

    def _fake_get_cookie():
        state["get_cookie_calls"] += 1
        return state["cookies"].pop(0) if state["cookies"] else "ck-ok"

    def _fake_get(url, headers=None, **kwargs):  # noqa: ANN001, ARG001
        bond_id = url.rsplit("/", 1)[-1]
        state["requests"].append((bond_id, headers.get("Cookie")))
        if bond_id in state["raise_ids"]:
            raise RuntimeError("network down")
        return _FakeResp(state["pages"].get(bond_id, _DETAIL_HTML))

    monkeypatch.setattr(cb_discussion.jisilu, "get_cookie", _fake_get_cookie)
    monkeypatch.setattr(cb_discussion.httpx, "get", _fake_get)
    return state


# ---------------------------------------------------------------------------
# 解析(纯函数)
# ---------------------------------------------------------------------------

def test_parse_extracts_and_absolutizes():
    items = cb_discussion._parse_discussions(_DETAIL_HTML)
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "盛路转债怎么玩"
    assert first["url"] == "https://www.jisilu.cn/question/525018"
    assert first["replies"] == "11条回复"
    assert first["views"] == "2089次浏览"
    assert first["date"] == "2026-09-04"
    # 无日期 span 的条目: date 为空串; 标题两侧空白被剥掉
    assert items[1]["date"] == ""
    assert items[1]["title"] == "asking 关于下修"


def test_parse_without_section_returns_empty():
    assert cb_discussion._parse_discussions("<html><body>无讨论板块</body></html>") == []


def test_parse_keeps_absolute_href():
    html = _DETAIL_HTML.replace(
        'href="/question/525018"', 'href="https://other.example/q/1"'
    )
    items = cb_discussion._parse_discussions(html)
    assert items[0]["url"] == "https://other.example/q/1"


def test_parse_stops_at_bonds_section():
    # tbl_bonds 之后即使残留同构条目也不该被采集(切片右边界生效)
    html = _DETAIL_HTML.replace(
        "</body>",
        '<div class="title"><a href="/question/999">不应出现</a></div>'
        '<div class="text"><span class="num">1条回复</span><span class="num">2次浏览</span></div></body>',
    )
    assert all(i["title"] != "不应出现" for i in cb_discussion._parse_discussions(html))


# ---------------------------------------------------------------------------
# 服务层: 单只(校验/缓存/登录墙重试)
# ---------------------------------------------------------------------------

def test_get_discussions_rejects_bad_bond_id(fetch_state):
    with pytest.raises(ValueError):
        cb_discussion.get_discussions("12703")
    with pytest.raises(ValueError):
        cb_discussion.get_discussions("127030x")
    assert fetch_state["requests"] == []


def test_get_discussions_caches_result(fetch_state):
    items1 = cb_discussion.get_discussions("127030")
    items2 = cb_discussion.get_discussions("127030")
    assert items1 == items2
    assert len(items1) == 2
    # 第二次命中缓存, 不再发请求, 也不再取 cookie
    assert len(fetch_state["requests"]) == 1
    assert fetch_state["get_cookie_calls"] == 1


def test_get_discussions_relogin_retry_on_login_wall(fetch_state, monkeypatch):
    # 第一次 cookie 失效(返回登录页), 重取 cookie 后成功
    fetch_state["cookies"] = ["ck-stale", "ck-fresh"]
    calls = {"n": 0}

    def _fake_get(url, headers=None, **kwargs):  # noqa: ANN001, ARG001
        calls["n"] += 1
        text = _LOGIN_HTML if calls["n"] == 1 else _DETAIL_HTML
        fetch_state["requests"].append((url.rsplit("/", 1)[-1], headers.get("Cookie")))
        return _FakeResp(text)

    monkeypatch.setattr(cb_discussion.httpx, "get", _fake_get)
    items = cb_discussion.get_discussions("127030")
    assert len(items) == 2
    assert fetch_state["get_cookie_calls"] == 2
    assert [r[1] for r in fetch_state["requests"]] == ["ck-stale", "ck-fresh"]


def test_get_discussions_propagates_fetch_error(fetch_state):
    fetch_state["raise_ids"].add("127030")
    with pytest.raises(RuntimeError, match="network down"):
        cb_discussion.get_discussions("127030")
    # 失败不缓存: 下次仍会重试
    fetch_state["raise_ids"].clear()
    assert len(cb_discussion.get_discussions("127030")) == 2


# ---------------------------------------------------------------------------
# 服务层: 批量预热
# ---------------------------------------------------------------------------

def test_batch_validates_and_dedupes(fetch_state):
    out = cb_discussion.get_discussions_batch(["127030", "127030", "113050"])
    assert set(out) == {"127030", "113050"}
    # 去重后每只只抓一次
    assert len(fetch_state["requests"]) == 2


def test_batch_rejects_empty_and_over_cap(fetch_state):
    with pytest.raises(ValueError):
        cb_discussion.get_discussions_batch([])
    with pytest.raises(ValueError):
        cb_discussion.get_discussions_batch([f"{i:06d}" for i in range(cb_discussion._MAX_BATCH + 1)])


def test_batch_cookie_fetched_once(fetch_state):
    cb_discussion.get_discussions_batch(["127030", "113050", "110045"])
    # 批量路径: 单线程先取一次 cookie, 线程内复用(不随债券数增长)
    assert fetch_state["get_cookie_calls"] == 1
    assert {c for _, c in fetch_state["requests"]} == {"ck-ok"}


def test_batch_cache_hit_skips_refetch(fetch_state):
    cb_discussion.get_discussions("127030")  # 先单只填充缓存
    before = len(fetch_state["requests"])
    out = cb_discussion.get_discussions_batch(["127030", "113050"])
    assert set(out) == {"127030", "113050"}
    # 只为未命中的 113050 新增一次请求
    assert len(fetch_state["requests"]) == before + 1


def test_batch_tolerates_per_bond_failure(fetch_state):
    fetch_state["raise_ids"].add("113050")
    out = cb_discussion.get_discussions_batch(["127030", "113050"])
    # 单只失败跳过: 只返回成功部分, 不中断整批
    assert set(out) == {"127030"}
    # 失败未落缓存(不写空结果), 后续悬浮/下次批量可重试
    assert "113050" not in cb_discussion._cache


# ---------------------------------------------------------------------------
# 路由合同(contract_client: 隔离 lifespan + 内存库)
# ---------------------------------------------------------------------------

def test_route_get_ok(contract_client, fetch_state):
    resp = contract_client.get("/api/cb-discussion/127030")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bond_id"] == "127030"
    assert body["items"][0]["url"].startswith("https://www.jisilu.cn/question/")


def test_route_get_bad_id_400(contract_client, fetch_state):
    assert contract_client.get("/api/cb-discussion/abc").status_code == 400


def test_route_get_upstream_error_502(contract_client, fetch_state):
    fetch_state["raise_ids"].add("127030")
    assert contract_client.get("/api/cb-discussion/127030").status_code == 502


def test_route_batch_ok(contract_client, fetch_state):
    resp = contract_client.post("/api/cb-discussion/batch", json={"bond_ids": ["127030", "113050"]})
    assert resp.status_code == 200
    assert set(resp.json()["items"]) == {"127030", "113050"}


def test_route_batch_validation_422(contract_client, fetch_state):
    # 空列表 / 超上限 / 非法元素形状: pydantic 层直接拒绝
    assert contract_client.post("/api/cb-discussion/batch", json={"bond_ids": []}).status_code == 422
    too_many = [f"{i:06d}" for i in range(101)]
    assert contract_client.post("/api/cb-discussion/batch", json={"bond_ids": too_many}).status_code == 422
    assert contract_client.post("/api/cb-discussion/batch", json={}).status_code == 422
