from types import SimpleNamespace
import inspect
import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from backend.models.database import Base
from backend.models.jisilu_account import JisiluAccount
from backend.services.jisilu_account_pool import JisiluAccountPool

import pytest

from backend.services import jisilu_gateway


class FakeResponse:
    def __init__(self, status_code=200, text="ok", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakePool:
    def __init__(self, accounts):
        self.accounts = accounts
        self.requests = []
        self.logins = 0
        self.cooldowns = []
        self.failures = []
        self.request_counts = 0

    def acquire_account(self):
        for account in self.accounts:
            if account["id"] not in self.requests:
                self.requests.append(account["id"])
                return {"id": account["id"]}
        return None

    def mark_request(self, account_id):
        self.request_counts += 1
        return {"id": account_id}

    def reserve_login(self, account_id, daily_limit=3):
        self.logins += 1
        account = next(a for a in self.accounts if a["id"] == account_id)
        if account.get("login_count", 0) >= daily_limit:
            return False
        account["login_count"] = account.get("login_count", 0) + 1
        return True

    def mark_success(self, account_id, cookie=None):
        if cookie is not None:
            next(a for a in self.accounts if a["id"] == account_id)["cookie"] = cookie

    def mark_cooldown(self, account_id, duration):
        self.cooldowns.append((account_id, duration))

    def mark_failure(self, account_id, summary):
        self.failures.append((account_id, summary))


def test_valid_cookie_is_reused_without_login(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda *a: pytest.fail("must not login"))

    response = jisilu_gateway.gateway.request("GET", "https://www.jisilu.cn/data/x")

    assert response.status_code == 200
    assert pool.logins == 0
    assert pool.requests == [1]


def test_missing_cookie_logs_in_once_and_retries(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": ""}
    pool = FakePool([account])
    calls = []
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda u, p: "sid=new")
    monkeypatch.setattr(jisilu_gateway.httpx, "post", lambda *a, **k: calls.append(k) or FakeResponse())

    jisilu_gateway.gateway.request("POST", "https://www.jisilu.cn/data/x", data={"a": 1})

    assert pool.logins == 1
    assert len(calls) == 1
    assert calls[0]["headers"]["Cookie"] == "sid=new"
    assert account["cookie"] == "sid=new"


def test_429_cools_account_and_switches_once(monkeypatch):
    accounts = [
        {"id": 1, "username": "u1", "password": "p1", "cookie": "sid=1"},
        {"id": 2, "username": "u2", "password": "p2", "cookie": "sid=2"},
    ]
    pool = FakePool(accounts)
    responses = iter([FakeResponse(429, "刷新过快"), FakeResponse(200)])
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: next(responses))

    result = jisilu_gateway.gateway.request("GET", "https://www.jisilu.cn/data/x")

    assert result.status_code == 200
    assert pool.requests == [1, 2]
    assert pool.cooldowns and pool.failures == []


def test_network_error_does_not_disable_account(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    calls = {"n": 0}
    def request(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("network down")
        return FakeResponse()
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", request)

    assert jisilu_gateway.gateway.request("GET", "https://www.jisilu.cn/data/x").status_code == 200
    assert calls["n"] == 2 and pool.failures == []


def test_lease_reuses_one_account_and_counts_each_request(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    calls = []
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: calls.append(k) or FakeResponse())

    with jisilu_gateway.gateway.lease() as client:
        for _ in range(3):
            client.request("GET", "https://www.jisilu.cn/data/x")

    assert pool.requests == [1] and pool.request_counts == 2
    assert len(calls) == 3
    assert pool.closed if hasattr(pool, "closed") else True


def test_fixed_lease_auth_failure_does_not_reacquire_or_count_twice(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    responses = iter([FakeResponse(401, "请登录"), FakeResponse(200)])
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda *a: "sid=2")
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: next(responses))

    with jisilu_gateway.gateway.lease() as client:
        assert client.request("GET", "https://www.jisilu.cn/data/x").status_code == 200

    assert pool.requests == [1]


def test_login_network_error_is_not_account_failure(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": ""}
    pool = FakePool([account])
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda *a: (_ for _ in ()).throw(OSError("down")))

    with pytest.raises(jisilu_gateway.LoginNetworkError):
        jisilu_gateway.gateway.request("GET", "https://www.jisilu.cn/data/x")
    assert pool.failures == []


def test_auth_retry_throttles_again_after_successful_login(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=old"}
    pool = FakePool([account])
    responses = iter([FakeResponse(401, "请登录"), FakeResponse(200)])
    throttle_calls = []
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda *a: "sid=new")
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: next(responses))
    monkeypatch.setattr(jisilu_gateway.gateway, "_throttle", lambda aid: throttle_calls.append(aid))

    jisilu_gateway.gateway.request("GET", "https://www.jisilu.cn/data/x")

    assert throttle_calls == [1, 1]


def test_closed_lease_rejects_requests_without_counting(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: FakeResponse())

    client = jisilu_gateway.gateway.lease()
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.request("GET", "https://www.jisilu.cn/data/x")
    assert pool.request_counts == 0

    pool.requests.clear()
    with jisilu_gateway.gateway.lease() as exited:
        pass
    before = pool.request_counts
    with pytest.raises(RuntimeError, match="closed"):
        exited.request("GET", "https://www.jisilu.cn/data/x")
    assert pool.request_counts == before


def test_shared_lease_concurrent_requests_count_exactly_once_each(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=1"}
    pool = FakePool([account])
    barrier = threading.Barrier(8)
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: FakeResponse())
    client = jisilu_gateway.gateway.lease()
    errors = []

    def worker():
        try:
            barrier.wait()
            client.request("GET", "https://www.jisilu.cn/data/x")
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    client.close()

    assert not errors
    assert pool.request_counts + len(pool.requests) == 8


def test_lease_refresh_updates_cookie_for_following_requests(monkeypatch):
    account = {"id": 1, "username": "u", "password": "p", "cookie": "sid=old"}
    pool = FakePool([account])
    responses = iter([FakeResponse(401, "请登录"), FakeResponse(200), FakeResponse(200)])
    sent = []
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", lambda: pool)
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", lambda *a: "sid=new")
    monkeypatch.setattr(jisilu_gateway.httpx, "get", lambda *a, **k: sent.append(k["headers"]["Cookie"]) or next(responses))
    with jisilu_gateway.gateway.lease() as client:
        client.request("GET", "https://www.jisilu.cn/data/x")
        client.request("GET", "https://www.jisilu.cn/data/x")
    assert sent == ["sid=old", "sid=new", "sid=new"]
    assert pool.logins == 1


def test_pool_database_error_does_not_fallback_to_legacy(monkeypatch):
    class BrokenDB:
        def query(self, *args):
            raise RuntimeError("db unavailable")
        def close(self):
            self.closed = True
    db = BrokenDB()
    monkeypatch.setattr(jisilu_gateway, "SessionLocal", lambda: db)
    monkeypatch.setattr(jisilu_gateway.jisilu, "get_cookie", lambda: pytest.fail("legacy must not run"))
    with pytest.raises(RuntimeError, match="account pool database query failed"):
        jisilu_gateway.get_account_pool()


def test_production_handle_concurrent_empty_cookie_logs_in_once(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    seed = Session()
    JisiluAccountPool(seed).create(name="a", username="u", password="p")
    seed.close()

    def handle_factory():
        db = Session()
        return jisilu_gateway._PoolHandle(db, JisiluAccountPool(db))
    monkeypatch.setattr(jisilu_gateway, "get_account_pool", handle_factory)
    barrier = threading.Barrier(8)
    calls, logins = [], {"n": 0}
    login_lock = threading.Lock()
    def login(*args):
        with login_lock:
            logins["n"] += 1
        return "sid=new"
    def request(*args, **kwargs):
        barrier.wait(timeout=5)
        calls.append(kwargs["headers"]["Cookie"])
        return FakeResponse()
    monkeypatch.setattr(jisilu_gateway.jisilu, "_login", login)
    monkeypatch.setattr(jisilu_gateway.httpx, "get", request)

    with jisilu_gateway.gateway.lease() as client:
        errors = []
        def work():
            try:
                client.request("GET", "https://www.jisilu.cn/data/x")
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
    row = Session().query(JisiluAccount).one()
    assert not errors and logins["n"] == 1 and len(calls) == 8
    assert set(calls) == {"sid=new"} and row.daily_request_count == 8
    engine.dispose()


def test_send_get_forwards_only_httpx_get_supported_kwargs(monkeypatch):
    """回归: GET 分支曾把 data=None 透传给 httpx.get, 真实签名不接受 data 即 TypeError
    (既有测试把 httpx.get mock 成 **k 全吞, 掩盖了该错误)。"""
    allowed = set(inspect.signature(jisilu_gateway.httpx.get).parameters) - {"url"}
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(jisilu_gateway.httpx, "get", fake_get)
    jisilu_gateway.gateway._send(
        "GET", "https://www.jisilu.cn/data/x", {"k": "v"},
        data=None, params={}, timeout=5, follow_redirects=True,
    )
    assert set(captured) <= allowed
    assert "data" not in captured
