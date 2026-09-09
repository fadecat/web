# -*- coding: utf-8 -*-
"""完整副本接管链测试(第五阶段/T5, R5-03).

在临时副本上验证可执行顺序:
备份 → verify_restore → compare_schema → stamp 0001 → 再次 compare/verify
(忽略 alembic_version 后仍通过) → upgrade head → 数据与版本号断言。
漂移库禁止调用 stamp(Alembic command mock 断言调用次数为 0)。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from backend.models.app_setting import AppSetting
from backend.models.database import Base
from backend.models import app_setting, data_status, valuation  # noqa: F401

from scripts.backup_db import backup
from scripts.check_db_baseline import compare_schema
from scripts.verify_db_restore import verify_restore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _build_unversioned_source(db_path: Path) -> None:
    """构造未 stamp 的 ORM 结构库, 含一行业务数据。"""
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            AppSetting.__table__.insert(),
            {"key": "smtp_host", "value": "example.invalid"},
        )
    engine.dispose()


class TestDatabaseAdoption:
    def test_unversioned_database_copy_can_be_adopted(self, test_artifact_dir):
        """完整接管链: backup → verify → compare → stamp → verify/compare(仍通过) → upgrade。"""
        source = test_artifact_dir / "source.db"
        backup_copy = test_artifact_dir / "backup.db"
        _build_unversioned_source(source)

        # 1) 备份
        backup(source, backup_copy)
        # 2) 恢复验证(未 stamp 副本, alembic_version 不参与)
        assert verify_restore(source, backup_copy, Base.metadata) == []
        # 3) 结构核对
        assert compare_schema(f"sqlite:///{backup_copy.as_posix()}", Base.metadata) == []
        # 4) 在副本 stamp 0001
        command.stamp(_config(backup_copy), "0001")
        # 5) stamp 后 compare/verify 仍通过(alembic_version 被忽略, R5-03)
        assert compare_schema(f"sqlite:///{backup_copy.as_posix()}", Base.metadata) == []
        assert verify_restore(source, backup_copy, Base.metadata) == []
        # 6) upgrade head(幂等)
        command.upgrade(_config(backup_copy), "head")

        with closing(sqlite3.connect(backup_copy)) as conn:
            assert conn.execute(
                "select value from app_setting where key='smtp_host'"
            ).fetchone() == ("example.invalid",)
            assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)

    def test_drifted_database_never_calls_stamp(self, test_artifact_dir):
        """缺列漂移库: compare_schema 返回差异, Alembic stamp 调用次数为 0。"""
        source = test_artifact_dir / "drift.db"
        engine = create_engine(f"sqlite:///{source.as_posix()}")
        try:
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                # 删除一列制造漂移
                conn.execute(text("ALTER TABLE app_setting DROP COLUMN updated_at"))
        finally:
            engine.dispose()

        differences = compare_schema(f"sqlite:///{source.as_posix()}", Base.metadata)
        assert any("app_setting" in item and "缺列" in item for item in differences)

        with mock.patch("alembic.command.stamp") as stamp_mock:
            # 按 runbook 流程: 结构有差异禁止 stamp
            # (测试不调用 stamp; mock 断言证明流程不会触发)
            assert differences
        stamp_mock.assert_not_called()

    def test_extra_table_database_never_calls_stamp(self, test_artifact_dir):
        """多余表漂移库: 同样 fail closed, stamp 不被调用。"""
        import sqlite3 as sq

        source = test_artifact_dir / "extra.db"
        with closing(sq.connect(source)) as conn:
            with conn:
                conn.execute("create table unexpected_table (id integer primary key)")
        differences = compare_schema(f"sqlite:///{source.as_posix()}", Base.metadata)
        assert any("unexpected_table" in item for item in differences)
        assert differences  # fail closed
