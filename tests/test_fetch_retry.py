# -*- coding: utf-8 -*-
"""fetch_with_retry 重试逻辑单元测试(httpx MockTransport)。"""
from __future__ import annotations

import httpx
import pytest

from backend.utils import fetch_with_retry


def _transport_with_status(codes: list[int]):
    """按请求次数依次返回状态码的 mock transport。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(counter["n"], len(codes) - 1)
        counter["n"] += 1
        return httpx.Response(codes[idx], text="ok")

    return httpx.MockTransport(handler), counter


def test_success_first_try():
    transport, counter = _transport_with_status([200])
    resp = fetch_with_retry(
        "GET", "http://test/x",
        retries=3,
        _transport=transport,
    )
    assert resp.status_code == 200
    assert counter["n"] == 1


def test_retries_on_500_then_success(monkeypatch):
    monkeypatch.setattr("backend.utils.time.sleep", lambda s: None)
    transport, counter = _transport_with_status([500, 502, 200])
    resp = fetch_with_retry("GET", "http://test/x", retries=3, _transport=transport)
    assert resp.status_code == 200
    assert counter["n"] == 3


def test_exhausted_retries_raises(monkeypatch):
    monkeypatch.setattr("backend.utils.time.sleep", lambda s: None)
    transport, counter = _transport_with_status([500])
    with pytest.raises(httpx.HTTPStatusError):
        fetch_with_retry("GET", "http://test/x", retries=3, _transport=transport)
    assert counter["n"] == 3


def test_no_retry_on_404(monkeypatch):
    monkeypatch.setattr("backend.utils.time.sleep", lambda s: None)
    transport, counter = _transport_with_status([404])
    # 4xx 直接返回(调用方自行 raise_for_status), 不消耗重试次数
    resp = fetch_with_retry("GET", "http://test/x", retries=3, _transport=transport)
    assert resp.status_code == 404
    assert counter["n"] == 1


def test_retries_on_timeout(monkeypatch):
    monkeypatch.setattr("backend.utils.time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(*args, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(200, text="ok")

    monkeypatch.setattr("backend.utils.httpx.request", flaky)
    resp = fetch_with_retry("GET", "http://test/x", retries=3)
    assert resp.status_code == 200
