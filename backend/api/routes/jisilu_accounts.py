"""集思录账号池管理 API。"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.models.jisilu_account import JisiluAccount
from backend.services import jisilu
from backend.services.jisilu_account_pool import JisiluAccountPool

router = APIRouter(prefix="/jisilu/accounts")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_LOGIN_DAILY_LIMIT = 3


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=128)
    username: StrictStr = Field(min_length=1, max_length=128)
    password: StrictStr = Field(min_length=1, max_length=512)
    enabled: bool = True


class _UpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    username: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    password: StrictStr | None = Field(default=None, max_length=512)
    enabled: bool | None = None


def _account_or_404(db: Session, account_id: int) -> JisiluAccount:
    row = db.get(JisiluAccount, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return row


def _error(exc: ValueError) -> HTTPException:
    if "already exists" in str(exc):
        return HTTPException(status_code=409, detail="username already exists")
    return HTTPException(status_code=400, detail=str(exc))


def _summary(rows: list[JisiluAccount]) -> dict[str, int]:
    today = datetime.now(_SHANGHAI).date()
    counts = {"total": len(rows), "enabled": 0, "ready": 0, "cooldown": 0, "invalid": 0, "today_requests": 0, "today_logins": 0}
    for row in rows:
        if row.enabled:
            counts["enabled"] += 1
        if row.status in ("ready", "cooldown", "invalid"):
            counts[row.status] += 1
        if row.daily_request_date == today:
            counts["today_requests"] += row.daily_request_count or 0
        if row.daily_login_date == today:
            counts["today_logins"] += row.daily_login_count or 0
    return counts


@router.get("")
def list_accounts(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(JisiluAccount).order_by(JisiluAccount.id).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    normalized = False
    for row in rows:
        if row.status == "cooldown" and row.cooldown_until and row.cooldown_until <= now:
            row.status, row.cooldown_until = "ready", None
            normalized = True
    if normalized:
        db.commit()
    return {"items": [JisiluAccountPool.serialize(row) for row in rows], "summary": _summary(rows)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(payload: _Payload, db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    try:
        return JisiluAccountPool(db).create(**values)
    except ValueError as exc:
        raise _error(exc) from exc


@router.put("/{account_id}")
def update_account(payload: _UpdatePayload, account_id: int = Path(ge=1), db: Session = Depends(get_db)) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="至少提供一个更新字段")
    try:
        result = JisiluAccountPool(db).update(account_id, **values)
    except ValueError as exc:
        raise _error(exc) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return result


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def delete_account(account_id: int = Path(ge=1), db: Session = Depends(get_db)) -> None:
    if not JisiluAccountPool(db).delete(account_id):
        raise HTTPException(status_code=404, detail="账号不存在")


@router.post("/{account_id}/reset-status")
def reset_status(account_id: int = Path(ge=1), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _account_or_404(db, account_id)
    row.status = "ready" if row.enabled else "disabled"
    row.cooldown_until = None
    row.consecutive_failures = 0
    row.last_error = None
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="状态重置失败") from exc
    return {"status": row.status, "account": JisiluAccountPool.serialize(row)}


@router.post("/{account_id}/check")
def check_account(account_id: int = Path(ge=1), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _account_or_404(db, account_id)
    if not row.enabled:
        raise HTTPException(status_code=409, detail="账号已停用")
    pool = JisiluAccountPool(db)
    if row.cookie:
        probe_result = None
        for attempt in range(2):
            current = _account_or_404(db, account_id)
            if not pool.record_request(account_id, allow_unavailable=True):
                raise HTTPException(status_code=409, detail="账号已停用")
            try:
                probe_result = jisilu._probe_once(current.cookie)
            except Exception as exc:
                probe_result = (False, True)
            ok, network_error = probe_result
            if ok:
                result = pool.mark_success(account_id)
                latest = db.get(JisiluAccount, account_id)
                if latest is None:
                    raise HTTPException(status_code=404, detail="账号不存在")
                if not latest.enabled:
                    raise HTTPException(status_code=409, detail="账号已停用")
                if result is None:
                    raise HTTPException(status_code=404, detail="账号不存在")
                return {"status": "ready", "account": result}
            if not network_error:
                break
            if attempt == 0:
                time.sleep(2)
        if probe_result and probe_result[1]:
            raise HTTPException(status_code=502, detail="Cookie 探测失败")
    if not pool.reserve_login(account_id, daily_limit=_LOGIN_DAILY_LIMIT):
        raise HTTPException(status_code=429, detail="今日登录预算已用尽或账号不可用")
    current = _account_or_404(db, account_id)
    if not pool.record_request(account_id, allow_unavailable=True):
        raise HTTPException(status_code=409, detail="账号已停用")
    try:
        cookie = jisilu.login_account(current.username, current.password)
        if not cookie:
            raise RuntimeError("登录未返回 Cookie")
    except jisilu.LoginNetworkError as exc:
        raise HTTPException(status_code=502, detail="集思录登录失败") from exc
    except jisilu.LoginRejectedError as exc:
        pool.mark_failure(account_id, "manual check login failed")
        raise HTTPException(status_code=502, detail="集思录登录失败") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="集思录登录失败") from exc
    result = pool.mark_success(account_id, cookie=cookie)
    latest = db.get(JisiluAccount, account_id)
    if latest is None or result is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not latest.enabled:
        raise HTTPException(status_code=409, detail="账号已停用")
    return {"status": "ready", "account": result}
