# -*- coding: utf-8 -*-
"""数据库引擎与会话管理。

SQLAlchemy 2.0 风格: 使用 DeclarativeBase + sessionmaker。
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    echo=False,
)

# SQLite 并发防护: APScheduler 线程池(多个日频任务)与 FastAPI 请求并发写同一库,
# 默认 journal 模式下极易出现 "database is locked"。
# WAL 允许读写并发; busy_timeout 让锁竞争时等待而非立即报错。
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """创建所有表(骨架阶段;后续引入 Alembic 做迁移)。"""
    # 延迟导入,确保模型均已注册到 Base.metadata
    from backend.models import valuation  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖: 提供请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
