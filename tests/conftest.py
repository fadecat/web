# -*- coding: utf-8 -*-
"""pytest 共享 fixtures。"""
from __future__ import annotations

import pathlib
import shutil
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config import DATA_DIR
from backend.models.database import Base


@pytest.fixture()
def db():
    """内存 SQLite 会话(每次独立, 建全量表结构)。"""
    # 确保全部模型已注册到 metadata(生产由 init_db 的延迟导入负责)
    from backend.models import app_setting, data_status, valuation  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def tmp_universe(monkeypatch):
    """在 DATA_DIR 下建临时目录承载 index_universe.json, 测试后清理。

    Windows 下 pytest 的 tmp_path 基目录偶发 PermissionError, 故用项目可写目录。
    """
    from backend.services import index_universe as iu

    d = pathlib.Path(tempfile.mkdtemp(dir=str(DATA_DIR), prefix=".test_universe_"))
    f = d / "index_universe.json"
    monkeypatch.setattr(iu, "_UNIVERSE_FILE", f)
    yield f
    shutil.rmtree(d, ignore_errors=True)
