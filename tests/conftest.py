# -*- coding: utf-8 -*-
"""pytest 共享 fixtures。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
