# -*- coding: utf-8 -*-
"""SQLite 只读访问入口(第五阶段/T2-T3).

统一:
- URL 解析(sqlite:///... → 路径)
- 存在性检查(不存在/非普通文件直接报错, 绝不创建空库)
- SQLAlchemy 只读 engine(NullPool, 每次新连接)
- 裸 sqlite3 只读连接(contextlib.closing 配套使用)

设计约束:
1. 只读连接用 `file:...?mode=ro` URI, 打开即失败而非创建文件。
2. 所有连接由调用方用 with/closing/finally 显式关闭, 不留句柄。
3. inspector 的全部调用必须在 engine.dispose() 之前完成。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


def sqlite_path_from_url(url: str) -> Path:
    """从 sqlite:///... URL 解析出文件路径; 非 SQLite 文件 URL 报错。"""
    if not url.startswith("sqlite:///"):
        raise ValueError(f"仅支持 SQLite 文件 URL: {url}")
    path = Path(url[len("sqlite:///"):]).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"SQLite 目标不是普通文件: {path}")
    return path


def readonly_engine(url: str):
    """只读 SQLAlchemy engine(NullPool: 每次新连接, 用完必须 dispose)。"""
    path = sqlite_path_from_url(url)

    def connect():
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def readonly_connection(path: Path) -> sqlite3.Connection:
    """裸 sqlite3 只读连接; 调用方必须用 contextlib.closing 关闭。"""
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"SQLite 目标不是普通文件: {resolved}")
    return sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
