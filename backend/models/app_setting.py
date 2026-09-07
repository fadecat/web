# -*- coding: utf-8 -*-
"""系统配置 ORM 模型。

AppSetting: 单行键值对配置表。敏感项(SMTP 密码等)只在写入时保存、
读取接口不回传明文, 前端只显示「已配置」状态。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.database import Base


class AppSetting(Base):
    """系统配置(键值对)。

    - key: 配置项名, 如 smtp_host / smtp_port / notify_emails
    - value: 配置值(SMTP 授权码等敏感值也存这里, API 层脱敏)
    - updated_at: 最后修改时间
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
