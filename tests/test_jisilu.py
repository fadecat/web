# -*- coding: utf-8 -*-
"""jisilu 认证层节流逻辑测试。

不触网(conftest 拦截 + httpx 全 mock), 覆盖:
- 进程内 TTL 缓存: 批量调用只探活一次
- 探活网络异常短重试后成功 → 不登录
- cookie 失效 → 登录一次并落盘 + 计数
- 每日登录预算: 超限拒绝登录
- 登录失败冷却: 退避期内不再尝试
- 计数跨天自动归零
- invalidate_cookie 主动失效缓存
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.services import jisilu


class _Resp:
    """httpx.Response 最小桩: 探活用 text, 登录用 json/cookies。"""

    def __init__(self, text: str = "", json_data: dict | None = None, cookies: dict | None = None):
        self.text = text
        self._json = json_data or {}
        self.cookies = cookies or {}
        self.headers = {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json


_LOGGED_IN = "<a href='https://www.jisilu.cn/logout/'>退出</a>"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每个测试独立: 状态文件指到临时目录 + 重置进程内缓存。"""
    monkeypatch.setattr(jisilu, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(jisilu, "SESSION_FILE", tmp_path / "jisilu_session.json")
    monkeypatch.setattr(jisilu, "_cached_cookie", "")
    monkeypatch.setattr(jisilu, "_cached_at", 0.0)
    monkeypatch.setattr(jisilu, "_last_login_fail_at", -1e9)
    monkeypatch.setattr(jisilu.time, "sleep", lambda _s: None)  # 探活重试不真等
    monkeypatch.setenv("JISILU_USERNAME", "user")
    monkeypatch.setenv("JISILU_PASSWORD", "pass")
    yield


def _install_http(monkeypatch, *, probe_ok=True, probe_error_times=0, login_ok=True):
    """替换 httpx.get/post, 返回调用计数与可控失败注入。"""
    calls = {"get": 0, "post": 0}
    errors_left = {"n": probe_error_times}

    def fake_get(url, **kw):
        calls["get"] += 1
        if errors_left["n"] > 0:
            errors_left["n"] -= 1
            raise ConnectionError("网络抖动")
        return _Resp(text=_LOGGED_IN if probe_ok else "请登录")

    def fake_post(url, **kw):
        calls["post"] += 1
        if login_ok:
            return _Resp(json_data={"code": 200}, cookies={"kbzw__Session": "NEW"})
        return _Resp(json_data={"code": 401, "msg": "登录或者刷新过快"})

    monkeypatch.setattr(jisilu.httpx, "get", fake_get)
    monkeypatch.setattr(jisilu.httpx, "post", fake_post)
    return calls


def _write_session(cookie: str = "kbzw__Session=CACHED") -> None:
    jisilu.SESSION_FILE.write_text(
        json.dumps({"cookie": cookie, "saved_at": "2026-09-14T00:00:00+08:00"}), encoding="utf-8"
    )


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _write_count(date: str, count: int) -> None:
    jisilu._login_count_file().write_text(
        json.dumps({"date": date, "count": count}), encoding="utf-8"
    )


def _read_count() -> dict:
    return json.loads(jisilu._login_count_file().read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_ttl_cache_batch_calls_probe_once(monkeypatch):
    _write_session()
    calls = _install_http(monkeypatch)

    for _ in range(5):
        assert jisilu.get_cookie() == "kbzw__Session=CACHED"

    assert calls["get"] == 1  # 5 次调用只探活 1 次
    assert calls["post"] == 0  # 不登录


def test_probe_network_error_retry_then_reuse(monkeypatch):
    _write_session()
    calls = _install_http(monkeypatch, probe_error_times=1)  # 首次探活网络抖动

    assert jisilu.get_cookie() == "kbzw__Session=CACHED"
    assert calls["get"] == 2  # 异常重试了一次
    assert calls["post"] == 0  # 没有误判失效去登录


def test_dead_cookie_login_once_then_cached(monkeypatch):
    _write_session("kbzw__Session=STALE")
    calls = _install_http(monkeypatch, probe_ok=False)  # 落盘 cookie 已失效

    cookie = jisilu.get_cookie()
    assert cookie == "kbzw__Session=NEW"
    assert calls["post"] == 1

    # 会话已落盘 + 进程内缓存, 再调不再探活/登录
    assert jisilu.get_cookie() == "kbzw__Session=NEW"
    assert calls["post"] == 1
    assert calls["get"] == 1

    saved = json.loads(jisilu.SESSION_FILE.read_text(encoding="utf-8"))
    assert "kbzw__Session=NEW" in saved["cookie"]
    assert _read_count() == {"date": _today(), "count": 1}


def test_daily_budget_blocks_login(monkeypatch):
    _write_count(_today(), jisilu._DAILY_LOGIN_BUDGET)  # 今日额度已用完
    calls = _install_http(monkeypatch, probe_ok=False)

    with pytest.raises(RuntimeError, match="上限"):
        jisilu.get_cookie()
    assert calls["post"] == 0  # 直接拒绝, 不再尝试登录


def test_login_failure_enters_cooldown(monkeypatch):
    calls = _install_http(monkeypatch, probe_ok=False, login_ok=False)  # 登录被拒(如刷新过快)

    with pytest.raises(RuntimeError, match="登录失败"):
        jisilu.get_cookie()
    assert calls["post"] == 1

    # 退避期内再调: 明确报冷却, 且不再发登录请求
    with pytest.raises(RuntimeError, match="退避"):
        jisilu.get_cookie()
    assert calls["post"] == 1


def test_login_count_resets_next_day(monkeypatch):
    yesterday = (
        datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    _write_count(yesterday, jisilu._DAILY_LOGIN_BUDGET)  # 昨天用完额度
    calls = _install_http(monkeypatch, probe_ok=False)

    assert jisilu.get_cookie() == "kbzw__Session=NEW"  # 今天额度已重置
    assert _read_count() == {"date": _today(), "count": 1}


def test_invalidate_cookie_forces_reprobe(monkeypatch):
    _write_session()
    calls = _install_http(monkeypatch)

    first = jisilu.get_cookie()
    assert calls["get"] == 1

    # 调用方发现 cookie 中途失效 → 主动清缓存 → 下次调用重新探活
    jisilu.invalidate_cookie(first)
    assert jisilu.get_cookie() == "kbzw__Session=CACHED"
    assert calls["get"] == 2

    # 传入别的 cookie 不误伤当前缓存
    jisilu.get_cookie()
    jisilu.invalidate_cookie("kbzw__Session=OTHER")
    assert jisilu.get_cookie() == "kbzw__Session=CACHED"
    assert calls["get"] == 2
