# -*- coding: utf-8 -*-
"""系统配置存储与邮件通知服务。

配置存 SQLite app_setting 表(键值对), 不进代码/.env, 网页上改。
SMTP 敏感值(授权码)只写不读——读取接口返回「已配置」布尔, 不回明文。

QQ 邮箱默认值: smtp.qq.com:465(SSL), 授权码在邮箱「设置-账户-POP3/SMTP」生成。
"""
from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from sqlalchemy.orm import Session

from backend.models.app_setting import AppSetting

# 配置项定义: key -> (展示名, 默认值, 是否敏感)
SMTP_KEYS: dict[str, tuple[str, str, bool]] = {
    "smtp_host": ("SMTP 服务器", "smtp.qq.com", False),
    "smtp_port": ("SMTP 端口", "465", False),
    "smtp_ssl": ("使用 SSL", "true", False),
    "smtp_user": ("发件邮箱", "", False),
    "smtp_password": ("SMTP 授权码", "", True),
    "notify_emails": ("通知邮箱(逗号分隔)", "", False),
}

_SENSITIVE_KEYS = {k for k, (_, _, sensitive) in SMTP_KEYS.items() if sensitive}


def get_settings_dict(db: Session) -> dict[str, str]:
    """读全部配置(库里有值用库里的, 否则用默认值)。"""
    rows = db.query(AppSetting).filter(AppSetting.key.in_(SMTP_KEYS)).all()
    stored = {r.key: (r.value or "") for r in rows}
    return {key: stored.get(key, default) for key, (_, default, _) in SMTP_KEYS.items()}


def get_settings_masked(db: Session) -> dict[str, dict]:
    """读取接口专用: 敏感值脱敏为布尔, 其余返回明文。"""
    values = get_settings_dict(db)
    out: dict[str, dict] = {}
    for key, (label, _, sensitive) in SMTP_KEYS.items():
        value = values.get(key, "")
        out[key] = {
            "label": label,
            "value": "" if sensitive else value,
            "configured": bool(value.strip()),
            "sensitive": sensitive,
        }
    return out


def save_settings(db: Session, payload: dict[str, str]) -> None:
    """批量写入配置。值为 None/空串且是敏感项时跳过(前端留空=不修改)。"""
    for key, value in payload.items():
        if key not in SMTP_KEYS:
            continue
        is_sensitive = key in _SENSITIVE_KEYS
        if is_sensitive and not (value or "").strip():
            continue  # 敏感项留空 = 保持原值
        row = db.query(AppSetting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.add(AppSetting(key=key, value=value))
    db.commit()


def send_mail(db: Session, subject: str, body: str, to_emails: list[str] | None = None) -> None:
    """按配置发邮件。收件人缺省用 notify_emails。配置不全或发送失败抛异常。"""
    cfg = get_settings_dict(db)
    user = (cfg.get("smtp_user") or "").strip()
    password = (cfg.get("smtp_password") or "").strip()
    if not user or not password:
        raise ValueError("SMTP 未配置完整(需要发件邮箱与授权码)")

    recipients = to_emails or [
        e.strip() for e in (cfg.get("notify_emails") or "").split(",") if e.strip()
    ]
    if not recipients:
        raise ValueError("未配置通知邮箱")

    host = (cfg.get("smtp_host") or "smtp.qq.com").strip()
    port = int((cfg.get("smtp_port") or "465").strip() or 465)
    use_ssl = (cfg.get("smtp_ssl") or "true").strip().lower() != "false"

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("Web 数据平台", user))
    msg["To"] = ", ".join(recipients)

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=20) as server:
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, recipients, msg.as_string())
