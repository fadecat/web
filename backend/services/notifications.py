# -*- coding: utf-8 -*-
"""任务失败通知服务。

任务执行 failed / partial 时主动发邮件, 让人工及时介入。
设计原则(对齐架构审查文档 operations/notifications/):
- 通知失败不拖累采集: 本模块任何异常只记日志, 绝不向上抛。
- 频率控制: 进程内按 job_id 去重, 同一任务一个窗口内只通知一次,
  避免手动连续触发失败时连环轰炸收件箱。
- 配置复用 app_settings 的 SMTP 存储, 不另建凭据来源。
"""
from __future__ import annotations

import threading
import time

from loguru import logger

from backend.models.database import SessionLocal
from backend.services import app_settings

# 同一任务在此窗口内只通知一次(秒)。日频任务每天至多一次,
# 该值主要拦手动重复触发; 若需"每天失败每天提醒", 保持默认即可。
_NOTIFY_WINDOW_SEC = 600

_lock = threading.Lock()
_last_notify: dict[str, float] = {}


def _job_display_name(job_id: str) -> str:
    """任务展示名(延迟导入, 避免启动期引入任务模块的副作用)。"""
    from backend.tasks.registry import DAILY_JOBS

    for jid, _, name, _, _ in DAILY_JOBS:
        if jid == job_id:
            return name
    return job_id


def notify_task_failure(job_id: str, status: str, error: str | None) -> None:
    """任务失败后发通知(幂等去重, 异常自吞)。

    仅处理 failed / partial; 其余状态直接返回。
    """
    if status not in ("failed", "partial"):
        return

    now = time.time()
    with _lock:
        last = _last_notify.get(job_id, 0)
        if now - last < _NOTIFY_WINDOW_SEC:
            return
        _last_notify[job_id] = now

    name = _job_display_name(job_id)
    label = "失败" if status == "failed" else "部分失败"
    subject = f"【Web数据平台】任务{label}: {name}"
    body = (
        f"任务 {name}({job_id}) 执行{label}。\n\n"
        f"状态: {status}\n"
        f"详情: {error or '详见服务器日志'}\n"
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"请登录数据状态页查看最近运行记录与错误详情。\n"
    )
    try:
        with SessionLocal() as db:
            app_settings.send_mail(db, subject, body)
        logger.info(f"任务失败通知已发送: {job_id} ({status})")
    except ValueError as exc:
        # SMTP 未配置完整 / 未配置收件人 → 静默跳过, 不算错误
        logger.debug(f"任务失败通知跳过(配置不全): {job_id} - {exc}")
    except Exception as exc:
        logger.warning(f"任务失败通知发送失败(不影响采集): {job_id} - {exc}")


def reset_notify_state() -> None:
    """清空去重状态(仅测试用)。"""
    with _lock:
        _last_notify.clear()
