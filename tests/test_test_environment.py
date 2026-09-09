# -*- coding: utf-8 -*-
"""测试环境隔离哨兵(R3-01/T1)。

验证:
1. contract_client 不触发生产启动(真实 init_db / start_scheduler 未被调用)
2. 请求会话被正确关闭(无泄漏)
3. 外部网络默认被拦截, 意外调用即失败
4. 测试配置生效: settings 读到内存库 + 调度关闭(即使工作目录有日常 .env)
"""
from __future__ import annotations

import socket

import pytest


def test_settings_point_to_isolated_resources():
    """测试进程的 settings 必须指向内存库且调度关闭(与日常 .env 无关)。"""
    from backend.config import settings

    assert settings.database_url.startswith("sqlite:///:memory:")
    assert settings.scheduler_enabled is False


def test_contract_client_never_calls_production_startup(contract_client, startup_spies):
    """进入 TestClient 上下文并发请求后, 真实启动函数零调用。"""
    r = contract_client.get("/api/health")
    assert r.status_code == 200
    assert startup_spies["init_db"] == 0
    assert startup_spies["start_scheduler"] == 0


def test_request_session_is_closed(contract_client, contract_db_state):
    """每个 HTTP 请求结束即关闭其会话(R4-03): 真实断言, 非空断言。"""
    first = contract_client.get("/api/cb-list/factors/ratings")
    assert first.status_code == 200
    assert len(contract_db_state.opened) == 1
    assert contract_db_state.closed_ids == [id(contract_db_state.opened[0]._session)]

    second = contract_client.get("/api/cb-list/factors/ratings")
    assert second.status_code == 200
    assert len(contract_db_state.opened) == 2
    assert contract_db_state.opened[0]._session is not contract_db_state.opened[1]._session
    assert contract_db_state.closed_ids == [
        id(contract_db_state.opened[0]._session),
        id(contract_db_state.opened[1]._session),
    ]


def test_production_lifespan_wires_database_and_scheduler(monkeypatch):
    """R4-05: 真实 lifespan 接线顺序 —— init_db → start → stop, 用内存 spy。

    不能使用 contract_client(它会替换 lifespan); 三个 spy 必须在
    create_app() 之前完成, 且 TestClient 用新构造的 app。
    """
    from fastapi.testclient import TestClient

    from backend import main as main_mod

    events = []
    monkeypatch.setattr(main_mod, "init_db", lambda: events.append("init_db"))
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: events.append("stop"))
    monkeypatch.setattr(main_mod.settings, "scheduler_enabled", True)

    isolated_app = main_mod.create_app()
    with TestClient(isolated_app) as client:
        assert client.get("/api/health").status_code == 200
        assert events == ["init_db", "start"]
    assert events == ["init_db", "start", "stop"]


def test_production_lifespan_skips_scheduler_when_disabled(monkeypatch):
    """调度关闭时 lifespan 只 init_db, 不启动/停止 scheduler。"""
    from fastapi.testclient import TestClient

    from backend import main as main_mod

    events = []
    monkeypatch.setattr(main_mod, "init_db", lambda: events.append("init_db"))
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: events.append("start"))
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: events.append("stop"))
    monkeypatch.setattr(main_mod.settings, "scheduler_enabled", False)

    isolated_app = main_mod.create_app()
    with TestClient(isolated_app):
        assert events == ["init_db"]
    assert events == ["init_db"]


def test_external_io_is_blocked(contract_client):
    """非本机 socket 连接在测试中直接失败(T1: 意外外部调用即失败)。"""
    with pytest.raises(AssertionError, match="禁止访问外部网络"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("93.184.216.34", 80))  # example.com, 不应真正连出
        finally:
            s.close()


def test_localhost_connections_still_allowed():
    """拦截只针对外部地址; localhost(内存库/本地服务)不受影响。"""
    # 连一个必然拒绝的本机端口: connection refused 而非 AssertionError
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError) as exc_info:
            s.connect(("127.0.0.1", 1))  # 保留端口, 本机必拒
        assert not isinstance(exc_info.value, AssertionError)
    finally:
        s.close()
