"""集思录账号池状态与凭据管理。

锁只覆盖单进程多线程；多 worker 部署需要数据库或 Redis 租约协调。
"""
from __future__ import annotations

import threading
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.models.jisilu_account import JisiluAccount

SHANGHAI = ZoneInfo("Asia/Shanghai")
_POOL_LOCK = threading.RLock()
_FIELDS = {"name", "username", "password", "enabled", "status", "cookie"}
_STATUSES = {"ready", "unknown", "cooldown", "invalid", "disabled"}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone(SHANGHAI).replace(tzinfo=None)


def sanitize_error(error: str) -> str:
    """Remove common credential/header values from an error message."""
    if not isinstance(error, str):
        raise ValueError("error must be a string")
    cleaned = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", error)
    cleaned = re.sub(r'(?i)(?:"|\b)(cookie|set-cookie|password|authorization|token|access_token|refresh_token|session)(?:"|\b)\s*[:=]\s*(?:"[^"]*"|[^,;}\s]+)', r'\1="[REDACTED]"', cleaned)
    cleaned = re.sub(r"(?i)([?&](?:cookie|password|authorization|token|access_token|refresh_token|session)=[^&#\s]+)", lambda m: m.group(1).split("=", 1)[0] + "=[REDACTED]", cleaned)
    return cleaned[:500]


def validate_secret_string(value: Any, max_len: int, allow_empty: bool) -> str:
    if not isinstance(value, str) or len(value) > max_len or (not allow_empty and not value):
        raise ValueError("invalid secret value")
    return value


class JisiluAccountPool:
    def __init__(self, db: Session, *, failure_threshold: int = 3, cooldown_seconds: int = 60):
        if failure_threshold <= 0 or cooldown_seconds < 0:
            raise ValueError("invalid pool limits")
        self.db = db
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._lock = _POOL_LOCK

    def create(self, *, name: str, username: str, password: str, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            values = {"name": name, "username": username, "password": password, **kwargs}
            self._validate(values, creating=True)
            if values.get("enabled") is False and "status" not in values:
                values["status"] = "disabled"
            kwargs = {key: value for key, value in values.items() if key not in {"name", "username", "password"}}
            row = JisiluAccount(name=name, username=username, password=password, **kwargs)
            try:
                self.db.add(row)
                self._commit_or_rollback()
            except IntegrityError as exc:
                self.db.rollback()
                raise ValueError("username already exists") from exc
            self.db.refresh(row)
            return self.serialize(row)

    def get(self, account_id: int) -> dict[str, Any] | None:
        return self._result(self.db.get(JisiluAccount, account_id))

    def list(self) -> list[dict[str, Any]]:
        return [self.serialize(row) for row in self.db.query(JisiluAccount).order_by(JisiluAccount.id).all()]

    def update(self, account_id: int, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return None
            if not changes.get("password"):
                changes.pop("password", None)
            self._validate(changes, creating=False)
            for key, value in changes.items():
                if hasattr(row, key) and key not in {"id", "created_at", "updated_at"}:
                    setattr(row, key, value)
            if "enabled" in changes and changes["enabled"] is False:
                row.status = "disabled"
            elif "enabled" in changes and changes["enabled"] is True and row.status == "disabled":
                row.status = "ready"
            row.updated_at = _now()
            try:
                self._commit_or_rollback()
            except IntegrityError as exc:
                self.db.rollback()
                raise ValueError("username already exists") from exc
            return self.serialize(row)

    def delete(self, account_id: int) -> bool:
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return False
            self.db.delete(row)
            self._commit_or_rollback()
            return True

    def select_available(self, now: datetime | None = None) -> dict[str, Any] | None:
        with self._lock:
            now = now or _now()
            rows = self.db.query(JisiluAccount).filter(JisiluAccount.enabled.is_(True)).all()
            available = []
            for row in rows:
                if row.status in {"invalid", "disabled"}:
                    continue
                if row.cooldown_until and row.cooldown_until > now:
                    continue
                if row.status == "cooldown":
                    row.status = "ready"
                self._reset_request(row, now.date())
                available.append(row)
            if not available:
                return None
            available.sort(key=lambda r: (r.daily_request_count, r.last_request_at or datetime.min, r.id))
            self._commit_or_rollback()
            return self.serialize(available[0])

    def acquire_account(self, now: datetime | None = None) -> dict[str, Any] | None:
        """在共享进程锁内选择并立即占用一个账号。"""
        with self._lock:
            selected = self.select_available(now)
            if selected is None:
                return None
            return self.mark_request(selected["id"], now)

    def mark_request(self, account_id: int, now: datetime | None = None) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return None
            now = now or _now()
            if not row.enabled or row.status in {"invalid", "disabled"}:
                return None
            if row.cooldown_until and row.cooldown_until > now:
                return None
            if row.status == "cooldown":
                row.status = "ready"
            self._reset_request(row, now.date())
            row.daily_request_count += 1
            row.last_request_at = now
            row.updated_at = _now()
            self._commit_or_rollback()
            return self.serialize(row)

    def record_request(self, account_id: int, now: datetime | None = None, *, allow_unavailable: bool = False) -> dict[str, Any] | None:
        """Record one upstream request, optionally allowing cooldown/invalid rows.

        Manual checks use this path because probing is itself an upstream request;
        disabled or deleted accounts are never allowed to be recorded.
        """
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None or not row.enabled:
                return None
            now = now or _now()
            if not allow_unavailable and (row.status in {"invalid", "disabled"} or (row.cooldown_until and row.cooldown_until > now)):
                return None
            self._reset_request(row, now.date())
            row.daily_request_count += 1
            row.last_request_at = now
            row.updated_at = _now()
            self._commit_or_rollback()
            return self.serialize(row)

    def mark_success(self, account_id: int, cookie: str | None = None, now: datetime | None = None) -> dict[str, Any] | None:
        with self._lock:
            if cookie is not None:
                validate_secret_string(cookie, 4096, allow_empty=False)
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return None
            now = now or _now()
            if row.status not in {"invalid", "disabled"}:
                row.status, row.consecutive_failures, row.last_error = "ready", 0, None
                row.cooldown_until = None
            row.last_success_at = now
            if cookie is not None:
                row.cookie, row.cookie_saved_at = cookie, now
            row.updated_at = _now()
            self._commit_or_rollback()
            return self.serialize(row)

    def mark_cooldown(self, account_id: int, duration: timedelta | int | float | datetime | None = None, *, now: datetime | None = None) -> dict[str, Any] | None:
        """将账号暂时移出选择范围。"""
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return None
            now = now or _now()
            if isinstance(duration, timedelta) and duration.total_seconds() < 0:
                raise ValueError("cooldown duration must be non-negative")
            if isinstance(duration, (int, float)) and duration < 0:
                raise ValueError("cooldown duration must be non-negative")
            row.status = "cooldown"
            if isinstance(duration, datetime):
                row.cooldown_until = duration
            elif isinstance(duration, (int, float)):
                row.cooldown_until = now + timedelta(seconds=duration)
            else:
                row.cooldown_until = now + (duration if duration is not None else timedelta(seconds=self.cooldown_seconds))
            row.updated_at = _now()
            self._commit_or_rollback()
            return self.serialize(row)

    def mark_failure(self, account_id: int, error: str, *, cooldown_seconds: int | None = None, now: datetime | None = None) -> dict[str, Any] | None:
        with self._lock:
            if not isinstance(error, str):
                raise ValueError("error must be a string")
            if cooldown_seconds is not None and cooldown_seconds < 0:
                raise ValueError("cooldown_seconds must be non-negative")
            row = self.db.get(JisiluAccount, account_id)
            if row is None:
                return None
            now = now or _now()
            row.consecutive_failures += 1
            row.last_failure_at, row.last_error = now, sanitize_error(error)
            if row.consecutive_failures >= self.failure_threshold:
                row.status, row.cooldown_until = "invalid", None
            else:
                row.status = "cooldown"
                row.cooldown_until = now + timedelta(seconds=cooldown_seconds if cooldown_seconds is not None else self.cooldown_seconds)
            row.updated_at = _now()
            self._commit_or_rollback()
            return self.serialize(row)

    def can_login(self, account_id: int, daily_limit: int = 5, now: datetime | None = None) -> bool:
        """只读展示用途；执行登录必须使用 reserve_login。"""
        with self._lock:
            row = self.db.get(JisiluAccount, account_id)
            if row is None or not row.enabled or row.status in {"invalid", "disabled"}:
                return False
            now = now or _now()
            if daily_limit <= 0:
                return False
            count = row.daily_login_count if row.daily_login_date == now.date() else 0
            return count < daily_limit

    login_budget_available = can_login

    def mark_login(self, account_id: int, now: datetime | None = None, daily_limit: int = 5) -> bool:
        """兼容入口；新调用方应直接使用 reserve_login。"""
        return self.reserve_login(account_id, daily_limit=daily_limit, now=now)

    def reserve_login(self, account_id: int, daily_limit: int = 5, now: datetime | None = None) -> bool:
        with self._lock:
            if not isinstance(daily_limit, int) or daily_limit <= 0:
                return False
            row = self.db.get(JisiluAccount, account_id)
            if row is None or not row.enabled or row.status in {"invalid", "disabled"}:
                return False
            now = now or _now()
            self._reset_login(row, now.date())
            if row.daily_login_count >= daily_limit:
                self._commit_or_rollback()
                return False
            row.daily_login_count += 1
            row.updated_at = _now()
            self._commit_or_rollback()
            return True

    @staticmethod
    def serialize(row: JisiluAccount) -> dict[str, Any]:
        result = {}
        for c in row.__table__.columns:
            if c.name in {"password", "cookie"}:
                continue
            value = getattr(row, c.name)
            result[c.name] = sanitize_error(value) if c.name == "last_error" and value else (value.isoformat() if isinstance(value, (datetime, date)) else value)
        result["password_configured"] = bool(row.password)
        result["cookie_configured"] = bool(row.cookie)
        return result

    @staticmethod
    def _validate(values: dict[str, Any], *, creating: bool) -> None:
        unknown = set(values) - _FIELDS
        if unknown:
            raise ValueError(f"unknown account fields: {sorted(unknown)}")
        for field in ("name", "username"):
            if field in values and (not isinstance(values[field], str) or not values[field].strip() or len(values[field]) > 128):
                raise ValueError(f"{field} must be non-empty")
        if creating and (not isinstance(values.get("password"), str) or not values["password"].strip()):
            raise ValueError("password must be non-empty")
        if "password" in values:
            validate_secret_string(values["password"], 512, allow_empty=not creating)
        if "password" in values and values["password"] and (not isinstance(values["password"], str) or not values["password"].strip()):
            raise ValueError("password must be non-empty")
        if "cookie" in values and values["cookie"] is not None and not isinstance(values["cookie"], str):
            raise ValueError("cookie must be a string")
        if "cookie" in values and values["cookie"] is not None:
            validate_secret_string(values["cookie"], 4096, allow_empty=True)
        if "status" in values and values["status"] not in _STATUSES:
            raise ValueError("invalid account status")
        if "enabled" in values and type(values["enabled"]) is not bool:
            raise ValueError("enabled must be bool")

    def _commit_or_rollback(self) -> None:
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def _result(self, row: JisiluAccount | None) -> dict[str, Any] | None:
        return self.serialize(row) if row else None

    @staticmethod
    def _reset_request(row: JisiluAccount, day: date) -> None:
        if row.daily_request_date != day:
            row.daily_request_date, row.daily_request_count = day, 0

    @staticmethod
    def _reset_login(row: JisiluAccount, day: date) -> None:
        if row.daily_login_date != day:
            row.daily_login_date, row.daily_login_count = day, 0


# Functional aliases keep the service convenient for dependency-injected callers.
def create_account(db: Session, **kwargs: Any) -> dict[str, Any]:
    return JisiluAccountPool(db).create(**kwargs)


def list_accounts(db: Session) -> list[dict[str, Any]]:
    return JisiluAccountPool(db).list()


def get_account(db: Session, account_id: int) -> dict[str, Any] | None:
    return JisiluAccountPool(db).get(account_id)


def update_account(db: Session, account_id: int, **changes: Any) -> dict[str, Any] | None:
    return JisiluAccountPool(db).update(account_id, **changes)


def delete_account(db: Session, account_id: int) -> bool:
    return JisiluAccountPool(db).delete(account_id)


def acquire_account(db: Session, now: datetime | None = None) -> dict[str, Any] | None:
    return JisiluAccountPool(db).acquire_account(now)


def reserve_login(db: Session, account_id: int, daily_limit: int = 5, now: datetime | None = None) -> bool:
    return JisiluAccountPool(db).reserve_login(account_id, daily_limit, now)
