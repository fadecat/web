# -*- coding: utf-8 -*-
"""任务失败通知单元测试。

验证: failed/partial 触发通知、success/skipped 不触发、SMTP 未配置静默跳过、
同任务去重防轰炸、异常不向上抛。
"""
from __future__ import annotations

from backend.services import notifications


class _FakeSession:
    """替身 SessionLocal 上下文管理器, 避免测试触碰真实 SQLite。"""

    def __enter__(self):
        return object()

    def __exit__(self, *args):
        return False


def _mock_send(monkeypatch, sink):
    monkeypatch.setattr(notifications.app_settings, "send_mail",
                        lambda db, subject, body: sink.append((subject, body)))
    monkeypatch.setattr(notifications, "SessionLocal", lambda: _FakeSession())
    notifications.reset_notify_state()


def test_failed_notifies(monkeypatch):
    sent = []
    _mock_send(monkeypatch, sent)

    notifications.notify_task_failure("valuation_daily", "failed", "boom")

    assert len(sent) == 1
    subject, body = sent[0]
    assert "失败" in subject
    assert "valuation_daily" in body
    assert "boom" in body


def test_partial_notifies(monkeypatch):
    sent = []
    _mock_send(monkeypatch, sent)

    notifications.notify_task_failure("valuation_daily", "partial", "3 个标的失败")

    assert len(sent) == 1
    assert "部分失败" in sent[0][0]


def test_success_and_skipped_do_not_notify(monkeypatch):
    sent = []
    _mock_send(monkeypatch, sent)

    notifications.notify_task_failure("valuation_daily", "success", None)
    notifications.notify_task_failure("valuation_daily", "skipped", None)

    assert sent == []


def test_same_job_dedup_within_window(monkeypatch):
    sent = []
    _mock_send(monkeypatch, sent)

    notifications.notify_task_failure("valuation_daily", "failed", "first")
    notifications.notify_task_failure("valuation_daily", "failed", "second")

    # 同一任务窗口内只通知一次
    assert len(sent) == 1
    assert "first" in sent[0][1]


def test_value_error_swallowed(monkeypatch):
    """SMTP 未配置完整 → 静默跳过, 不抛异常。"""

    def raiser(db, subject, body):
        raise ValueError("SMTP 未配置完整")

    monkeypatch.setattr(notifications.app_settings, "send_mail", raiser)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: _FakeSession())
    notifications.reset_notify_state()

    # 不抛异常即为通过
    notifications.notify_task_failure("valuation_daily", "failed", "boom")


def test_send_exception_swallowed(monkeypatch):
    """发送时网络/登录异常 → 只记日志, 不抛异常。"""

    def raiser(db, subject, body):
        raise ConnectionError("timeout")

    monkeypatch.setattr(notifications.app_settings, "send_mail", raiser)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: _FakeSession())
    notifications.reset_notify_state()

    notifications.notify_task_failure("valuation_daily", "failed", "boom")
