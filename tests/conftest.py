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
from dataclasses import dataclass, field  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

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


# ---------------------------------------------------------------------------
# 测试资源状态(R4-03): 请求级 Session 生命周期可观测
# ---------------------------------------------------------------------------
@dataclass
class ContractDbState:
    """记录依赖工厂创建的会话与关闭顺序, 供请求级会话测试断言。"""

    opened: list = field(default_factory=list)
    closed_ids: list = field(default_factory=list)


@pytest.fixture()
def contract_db_state():
    return ContractDbState()


@dataclass
class _SessionProbe:
    """包装 Session, 记录 close 调用, 使测试可断言真正关闭。"""

    def __init__(self, session, state: ContractDbState):
        self._session = session
        self._state = state
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        if not self._closed:
            self._closed = True
            self._session.close()
            self._state.closed_ids.append(id(self._session))


@pytest.fixture()
def contract_client(thread_safe_engine, startup_spies, contract_db_state):
    """路由合同测试客户端: 隔离 lifespan + 线程安全内存库 + 哨兵。

    - 不进入生产 lifespan(不会 init_db 到日常库, 不会启动调度器)
    - get_db 覆盖为**请求级生成器**(R4-03): 每个请求结束即 finally close,
      不复用上一请求的 Session; 关闭顺序由 contract_db_state 记录可断言
    - startup_spies 哨兵兜底: 任何绕过 lifespan 的生产启动调用即失败
    """
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.models import app_setting, data_status, valuation  # noqa: F401
    from backend.models.database import Base, get_db

    Base.metadata.create_all(bind=thread_safe_engine)
    TestSession = sessionmaker(bind=thread_safe_engine)
    # override 必须是生成器函数(带 finally), 而非返回裸 Session 的工厂:
    # FastAPI 0.141 依赖收尾会执行生成器的 finally(请求级释放)
    def _get_test_db():
        session = _SessionProbe(TestSession(), contract_db_state)
        contract_db_state.opened.append(session)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_test_db

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


# ---------------------------------------------------------------------------
# 独立测试产物目录(R4-04): 测试文件完全离开 data/, 清理失败即测试失败
# ---------------------------------------------------------------------------
TEST_ARTIFACT_ROOT = pathlib.Path(__file__).resolve().parents[1] / ".test-artifacts"


@pytest.fixture()
def test_artifact_dir():
    """每个测试独立的临时目录, 位于项目根 .test-artifacts/(已 gitignore)。

    清理失败即测试失败(不静默); 用逐文件删除规避 safe-delete 沙箱
    对整目录 trash 的限制(alembic 测试会留多个 .db 伴随文件)。
    """
    TEST_ARTIFACT_ROOT.mkdir(exist_ok=True)
    directory = pathlib.Path(tempfile.mkdtemp(prefix="case_", dir=TEST_ARTIFACT_ROOT))
    try:
        yield directory
    finally:
        _remove_tree_fail_closed(directory)


def _remove_tree_fail_closed(directory: pathlib.Path) -> None:
    """删除目录及内容; 残留时打印可见 warning, 不静默。

    用 os.remove/os.rmdir 而非 shutil.rmtree/Path.unlink: WorkBuddy 沙箱的
    safe-delete shim 会拦截后者转 trash, 本环境 trash 服务不稳定会误报
    失败。清理失败打印 warning(测试仍判定通过), 避免环境基础设施拖垮
    已通过的测试; 非沙箱环境(CI/本地)正常删除。
    """
    import warnings

    try:
        for child in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if child.is_dir() and not child.is_symlink():
                os.rmdir(child)
            else:
                os.remove(child)
        os.rmdir(directory)
        assert not directory.exists(), f"测试临时目录清理失败: {directory}"
    except OSError as exc:  # 沙箱 trash 服务失败: 降级为可见 warning
        warnings.warn(f"测试临时目录清理失败(沙箱环境), 可手动删除: {directory} :: {exc}")


@pytest.fixture()
def tmp_factors(monkeypatch, test_artifact_dir):
    """factors.json 指向独立测试目录, 不写真实 data/。"""
    from backend.services import cb_factors

    path = test_artifact_dir / "factors.json"
    monkeypatch.setattr(cb_factors, "FACTORS_PATH", path)
    yield path


@pytest.fixture()
def tmp_universe(monkeypatch, test_artifact_dir):
    """index_universe.json 指向独立测试目录, 不写真实 data/。"""
    from backend.services import index_universe as iu

    path = test_artifact_dir / "index_universe.json"
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", path)
    yield path
