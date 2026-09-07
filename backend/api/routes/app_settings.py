# -*- coding: utf-8 -*-
"""系统配置路由: 读写配置(敏感项脱敏) + 发送测试邮件。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services import app_settings

router = APIRouter(prefix="/settings")


class SettingsPayload(BaseModel):
    values: dict[str, str]


@router.get("")
def read_settings(db: Session = Depends(get_db)) -> dict:
    """返回配置项列表。敏感项(授权码)不回明文, 只返回 configured 布尔。"""
    return {"items": app_settings.get_settings_masked(db)}


@router.put("")
def update_settings(payload: SettingsPayload, db: Session = Depends(get_db)) -> dict:
    """批量保存配置。敏感项留空 = 保持原值。"""
    app_settings.save_settings(db, payload.values)
    return {"status": "saved"}


@router.post("/test-mail")
def send_test_mail(db: Session = Depends(get_db)) -> dict:
    """按当前配置发送测试邮件(收件人用已配置的 notify_emails)。"""
    try:
        app_settings.send_mail(
            db,
            subject="【Web数据平台】测试邮件",
            body="这是一封测试邮件。\n\n如果你收到了, 说明 SMTP 配置正确, 任务告警邮件将发送到该邮箱。\n",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"发送失败: {type(e).__name__}: {e}")
    return {"status": "sent"}
