# -*- coding: utf-8 -*-
"""pytest 共享 fixtures 与测试环境隔离(R3-01/T1)。

隔离原则:
1. 本文件顶部(import backend 之前)先设置环境变量, 保证 backend.config.settings
   读到的是测试配置: 内存 SQLite + 关闭调度器。即使工作目录存在日常 .env,
   环境变量优先级高于 .env(pydantic-settings 语义), 隔离不依赖操作者记得配置。
2. 路由测试不进入生产 lifespan: 通过 app.router.lifespan_context 替换,
   使真实 init_db/start_scheduler 被调用即触发哨兵失败(见 test_test_environment.py)。
3. 外部网络/SMTP 默认禁止: socket 层拦截非本机连接, 意外调用直接失败。
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# 测试环境设置: 必须发生在任何 backend.* 导入之前(conftest 由 pytest 最先加载)
# 环境变量优先级高于 .env(pydantic-settings 语义), 与字段名同名(DATABASE_URL)
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SCHEDULER_ENABLED"] = "false"

import pathlib  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.config import DATA_DIR  # noqa: E402
from backend.models.database import Base  # noqa: E402


# ---------------------------------------------------------------------------
# 外部网络拦截: 非 localhost 连接直接失败(T1: test_external_io_is_blocked)
# ---------------------------------------------------------------------------
def _is_local_host(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1", "")

_real_socket_connect = socket.socket.connect


def _guarded_connect(self, address):  # noqa: ANN001
    host = address[0] if isinstance(address, tuple) else ""
    if not _is_local_host(str(host)):
        raise AssertionError(
            f"测试中禁止访问外部网络: {address!r}(如需真实数据源请显式 mock)"
        )
    return _real_socket_connect(self, address)


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """每个测试默认禁止外部网络; 需要 SMTP/HTTP mock 的测试天然不触网。"""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    yield


@pytest.fixture()
def db():
    """内存 SQLite 会话(每次独立, 建全量表结构); yield/finally 保证关闭。"""
    # 确保全部模型已注册到 metadata(生产由 init_db 的延迟导入负责)
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def startup_spies(monkeypatch):
    """哨兵: 真实 init_db/start_scheduler 被调用即抛错。

    生产 lifespan 已被 contract_client 替换; 此哨兵兜底捕获任何绕过
    lifespan 直接调用生产启动函数的代码路径。
    """
    from backend import main as main_mod
    from backend.models import database as database_mod
    from backend import scheduler as scheduler_mod

    calls = {"init_db": 0, "start_scheduler": 0, "stop_scheduler": 0}

    def _spy_init():
        calls["init_db"] += 1
        raise AssertionError("测试中调用了真实 init_db(生产库写入风险)")

    def _spy_start():
        calls["start_scheduler"] += 1
        raise AssertionError("测试中调用了真实 start_scheduler(调度器启动风险)")

    def _spy_stop():
        calls["stop_scheduler"] += 1

    monkeypatch.setattr(main_mod, "init_db", _spy_init, raising=False)
    monkeypatch.setattr(main_mod, "start_scheduler", _spy_start, raising=False)
    monkeypatch.setattr(main_mod, "stop_scheduler", _spy_stop, raising=False)
    monkeypatch.setattr(database_mod, "init_db", _spy_init, raising=False)
    monkeypatch.setattr(scheduler_mod, "start_scheduler", _spy_start, raising=False)
    monkeypatch.setattr(scheduler_mod, "stop_scheduler", _spy_stop, raising=False)
    yield calls


@pytest.fixture()
def thread_safe_engine():
    """TestClient 专用: 线程安全内存库引擎(StaticPool + check_same_thread=False)。"""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def contract_client(thread_safe_engine, startup_spies):
    """路由合同测试客户端: 隔离 lifespan + 线程安全内存库 + 哨兵。

    - 不进入生产 lifespan(不会 init_db 到日常库, 不会启动调度器)
    - get_db 覆盖为测试会话工厂, yield/finally 恢复并关闭全部会话
    - startup_spies 哨兵兜底: 任何绕过 lifespan 的生产启动调用即失败
    """
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.models import app_setting, data_status, valuation  # noqa: F401
    from backend.models.database import Base, get_db

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    # override 必须是函数而非 sessionmaker 类:
    # FastAPI 0.141 把类当依赖签名分析会生成 local_kw 必填参数导致 422
    opened = []

    def _make_session():
        s = TestSession()
        opened.append(s)
        return s

    app.dependency_overrides[get_db] = _make_session

    # 隔离 lifespan: TestClient 上下文不触发 init_db/start_scheduler
    from contextlib import asynccontextmanager

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _noop_lifespan(a):  # noqa: ANN001
        yield

    app.router.lifespan_context = _noop_lifespan
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.router.lifespan_context = original_lifespan
        app.dependency_overrides.pop(get_db, None)
        for s in opened:
            try:
                s.close()
            except Exception:
                pass


@pytest.fixture()
def tmp_universe(monkeypatch):
    """在 DATA_DIR 下建临时目录承载 index_universe.json, 测试后清理。

    Windows 下 pytest 的 tmp_path 基目录偶发 PermissionError, 故用项目可写目录。
    """
    from backend.services import index_universe as iu

    d = pathlib.Path(tempfile.mkdtemp(dir=str(DATA_DIR), prefix=".test_universe_"))
    f = d / "index_universe.json"
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", f)
    yield f
    shutil.rmtree(d, ignore_errors=True)
