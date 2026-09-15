"""统一集思录 HTTP 请求网关。

账号池存在时，所有请求都在池的原子租约上执行；没有可用账号时兼容旧的
``jisilu.get_cookie`` 路径。解析和业务错误由调用方处理。
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterator

import httpx

from backend.models.database import SessionLocal
from backend.models.jisilu_account import JisiluAccount
from backend.services import jisilu
from backend.services.jisilu_account_pool import JisiluAccountPool

logger = logging.getLogger(__name__)
_UA = jisilu.LOGIN_HEADERS["User-Agent"]
_LOGIN_MARKERS = ("帐号密码登录", "账号密码登录", "请先登录", "请登录")
_FAST_MARKERS = ("刷新过快", "验证码")
MIN_REQUEST_INTERVAL = 0.2
_last_request: dict[int, float] = {}
_throttle_lock = threading.Lock()
_login_locks: dict[int, threading.Lock] = {}
_login_locks_guard = threading.Lock()
LoginNetworkError = jisilu.LoginNetworkError
LoginRejectedError = jisilu.LoginRejectedError

def _login_lock(account_id: int) -> threading.Lock:
    with _login_locks_guard:
        return _login_locks.setdefault(account_id, threading.Lock())


@dataclass
class _PoolHandle:
    db: Any
    pool: JisiluAccountPool


def get_account_pool() -> _PoolHandle | None:
    """返回池句柄；数据库未初始化或池为空时返回 ``None``。"""
    try:
        db = SessionLocal()
        pool = JisiluAccountPool(db)
        try:
            empty = db.query(JisiluAccount).first() is None
        except Exception as exc:
            db.close()
            raise RuntimeError("account pool database query failed") from exc
        if empty:
            db.close()
            return None
        return _PoolHandle(db, pool)
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.close()  # type: ignore[name-defined]
        if isinstance(exc, RuntimeError) and str(exc) == "account pool database query failed":
            raise
        raise RuntimeError("account pool database unavailable") from exc


def _response_requires_login(response: httpx.Response) -> bool:
    if getattr(response, "status_code", 200) in (401, 403):
        return True
    text = (getattr(response, "text", "") or "")[:200000]
    if any(marker in text for marker in _LOGIN_MARKERS):
        return True
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        code = payload.get("code")
        msg = " ".join(str(payload.get(k, "")) for k in ("msg", "message", "error"))
        if code in (401, 403, "401", "403") or any(x in msg.lower() for x in ("login", "登录", "unauth")):
            return True
    return False


def _is_fast(response: httpx.Response) -> bool:
    return getattr(response, "status_code", 200) == 429 or any(x in (getattr(response, "text", "") or "") for x in _FAST_MARKERS)


class JisiluGateway:
    def __init__(self, *, login_daily_limit: int = 3):
        self.login_daily_limit = login_daily_limit

    def _send(self, method: str, url: str, headers: dict[str, str], **kwargs: Any) -> httpx.Response:
        if method.upper() == "GET":
            # GET 无请求体; httpx.get 签名不接受 data(即使传 None 也 TypeError)
            kwargs.pop("data", None)
            return httpx.get(url, headers=headers, **kwargs)
        if method.upper() == "POST":
            kwargs.pop("follow_redirects", None)
            return httpx.post(url, headers=headers, **kwargs)
        return httpx.request(method, url, headers=headers, **kwargs)

    @staticmethod
    def _throttle(account_id: int) -> None:
        with _throttle_lock:
            now = time.monotonic()
            wait = MIN_REQUEST_INTERVAL - (now - _last_request.get(account_id, 0.0))
            if wait > 0:
                time.sleep(wait)
            _last_request[account_id] = time.monotonic()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        params: Any = None,
        timeout: Any = 15,
        follow_redirects: bool = True,
        request_type: str = "api",
        _account_id: int | None = None,
        _already_acquired: bool = False,
        _lease_cookie_update: Any = None,
    ) -> httpx.Response:
        del request_type
        base_headers = dict(headers or {})
        handle = get_account_pool()
        if handle is None:
            cookie = base_headers.get("Cookie") or jisilu.get_cookie()
            base_headers.setdefault("User-Agent", _UA)
            base_headers["Cookie"] = cookie
            response = self._send(method, url, base_headers, data=data, params=params, timeout=timeout, follow_redirects=follow_redirects)
            if _response_requires_login(response):
                jisilu.invalidate_cookie(cookie)
                base_headers["Cookie"] = jisilu.get_cookie()
                response = self._send(method, url, base_headers, data=data, params=params, timeout=timeout, follow_redirects=follow_redirects)
            response.raise_for_status()
            return response

        try:
            pool = handle.pool if hasattr(handle, "pool") else handle
            db = getattr(handle, "db", None)
            tried: set[int] = set()
            for _ in range(2):
                lease = ({"id": _account_id} if _already_acquired else pool.mark_request(_account_id)) if _account_id is not None else pool.acquire_account()
                if not lease or (_account_id is None and lease["id"] in tried):
                    break
                account_id = int(lease["id"])
                tried.add(account_id)
                row = db.get(JisiluAccount, account_id) if db is not None else next(
                    (a for a in getattr(pool, "accounts", []) if a.get("id") == account_id), None
                )
                if row is None:
                    continue
                # 池模式始终使用该租约账号的 cookie，避免调用方把别的账号会话带入。
                cookie = ((row.cookie if hasattr(row, "cookie") else row.get("cookie")) or "")
                # 无 cookie 时也使用账号级 single-flight，避免并发线程重复登录。
                if not cookie:
                    with _login_lock(account_id):
                        if db is not None:
                            db.refresh(row)
                            cookie = row.cookie or ""
                        if not cookie:
                            if not pool.reserve_login(account_id, daily_limit=self.login_daily_limit):
                                pool.mark_failure(account_id, "login budget exhausted")
                                continue
                            try:
                                cookie = jisilu.login_account(row.username if hasattr(row, "username") else row["username"], row.password if hasattr(row, "password") else row["password"])
                            except jisilu.LoginNetworkError:
                                raise LoginNetworkError("login network error")
                            except jisilu.LoginRejectedError:
                                pool.mark_failure(account_id, "login rejected")
                                continue
                            if not cookie:
                                pool.mark_failure(account_id, "login failed")
                                continue
                            pool.mark_success(account_id, cookie)

                req_headers = dict(base_headers)
                req_headers.setdefault("User-Agent", _UA)
                req_headers["Cookie"] = cookie
                self._throttle(account_id)
                network_attempts = 0
                login_attempted = False
                while True:
                    try:
                        response = self._send(method, url, req_headers, data=data, params=params, timeout=timeout, follow_redirects=follow_redirects)
                    except (httpx.RequestError, OSError):
                        if network_attempts == 0:
                            network_attempts += 1
                            continue
                        raise
                    if _is_fast(response):
                        pool.mark_cooldown(account_id, timedelta(minutes=30))
                        break
                    if _response_requires_login(response) and not login_attempted:
                        login_attempted = True
                        with _login_lock(account_id):
                            if db is not None:
                                db.refresh(row)
                                current_cookie = row.cookie or ""
                            else:
                                current_cookie = next((a.get("cookie") for a in getattr(pool, "accounts", []) if a.get("id") == account_id), "")
                            if current_cookie and current_cookie != cookie:
                                cookie = current_cookie
                                req_headers["Cookie"] = cookie
                                self._throttle(account_id)
                                if _lease_cookie_update:
                                    _lease_cookie_update(cookie)
                                continue
                            if not pool.reserve_login(account_id, daily_limit=self.login_daily_limit):
                                new_cookie = ""
                            else:
                                try:
                                    new_cookie = jisilu.login_account(row.username if hasattr(row, "username") else row["username"], row.password if hasattr(row, "password") else row["password"])
                                except jisilu.LoginNetworkError:
                                    raise LoginNetworkError("login network error")
                                except jisilu.LoginRejectedError:
                                    new_cookie = ""
                            if new_cookie:
                                cookie = new_cookie
                                req_headers["Cookie"] = new_cookie
                                pool.mark_success(account_id, new_cookie)
                                if _lease_cookie_update:
                                    _lease_cookie_update(new_cookie)
                                self._throttle(account_id)
                                continue
                        pool.mark_failure(account_id, "authentication failed")
                        break
                    response.raise_for_status()
                    pool.mark_success(account_id)
                    return response
            raise RuntimeError("集思录账号池暂无可用账号")
        finally:
            if hasattr(handle, "db"):
                handle.db.close()

    def lease(self, cookie: str | None = None) -> "JisiluLease":
        handle = get_account_pool()
        if handle is None:
            return JisiluLease(self, cookie=cookie or jisilu.get_cookie())
        pool = handle.pool if hasattr(handle, "pool") else handle
        db = getattr(handle, "db", None)
        try:
            lease = pool.acquire_account()
            if not lease:
                raise RuntimeError("集思录账号池暂无可用账号")
            row = db.get(JisiluAccount, lease["id"]) if db is not None else next((a for a in getattr(pool, "accounts", []) if a["id"] == lease["id"]), None)
            cookie = (row.cookie if hasattr(row, "cookie") else row.get("cookie")) if row is not None else ""
            return JisiluLease(self, db, pool, int(lease["id"]), cookie)
        except Exception:
            if db is not None:
                db.close()
            raise


class JisiluLease:
    """批次请求客户端；接口与网关一致，便于抓取器复用请求上下文。"""
    def __init__(self, gateway: JisiluGateway, db: Any = None, pool: Any = None, account_id: int | None = None, cookie: str = ""):
        self.gateway = gateway
        self.db, self.pool = db, pool
        self.account_id = account_id
        self.cookie = cookie
        self._first = account_id is not None
        self.closed = False
        self._state_lock = threading.Lock()

    def __enter__(self) -> "JisiluLease":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        with self._state_lock:
            if self.closed:
                return
            self.closed = True
            db = self.db
        if db is not None:
            db.close()

    def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        with self._state_lock:
            if self.closed:
                raise RuntimeError("JisiluLease is closed")
            first = self._first
            self._first = False
            cookie = self.cookie
            account_id = self.account_id
        if cookie:
            headers = dict(kwargs.get("headers") or {})
            headers["Cookie"] = cookie
            kwargs["headers"] = headers
        if account_id is not None:
            kwargs["_account_id"] = account_id
            kwargs["_already_acquired"] = first
            kwargs["_lease_cookie_update"] = self._update_cookie
        return self.gateway.request(*args, **kwargs)

    def _update_cookie(self, cookie: str) -> None:
        with self._state_lock:
            if not self.closed:
                self.cookie = cookie


JisiluClient = JisiluLease


gateway = JisiluGateway()
