# -*- coding: utf-8 -*-
"""Alembic 迁移基线测试(第四阶段/T5-T6).

只对临时库执行: 空库升级、重复升级、结构比对、漂移拒绝。
绝不触碰 data/web.db(日常库)。
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from backend.models.database import Base
from backend.models import app_setting, data_status, valuation  # noqa: F401

EXPECTED_TABLES = {
    "app_setting", "task_run_log", "index_valuation_snapshot",
    "index_dividend_yield", "cn_bond_yield", "index_daily_quote",
    "cb_index_daily", "cb_daily_snapshot", "cb_redeem_daily", "cb_blacklist",
}

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _config(db_path: Path) -> Config:
    """构造指向临时库的 Alembic Config(不读 data/web.db)。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def _upgrade(db_path: Path) -> None:
    command.upgrade(_config(db_path), "head")


def _downgrade_base(db_path: Path) -> None:
    command.downgrade(_config(db_path), "base")


def _stamp(db_path: Path, rev: str) -> None:
    command.stamp(_config(db_path), rev)


class TestEmptyDatabaseUpgrade:
    def test_empty_database_upgrades_to_current_schema(self, test_artifact_dir):
        db_path = test_artifact_dir / "empty.db"
        _upgrade(db_path)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
        finally:
            engine.dispose()
        assert table_names == EXPECTED_TABLES | {"alembic_version"}

    def test_upgrade_head_is_idempotent(self, test_artifact_dir):
        db_path = test_artifact_dir / "repeat.db"
        _upgrade(db_path)
        _upgrade(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)

    def test_downgrade_to_base_removes_business_tables(self, test_artifact_dir):
        """downgrade 只在临时库测试; 日常回退用备份恢复(见 runbook)。

        Alembic 的 downgrade base 会保留空的 alembic_version 表(正常行为),
        业务表必须全删。
        """
        db_path = test_artifact_dir / "downgrade.db"
        _upgrade(db_path)
        _downgrade_base(db_path)
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
        finally:
            engine.dispose()
        assert EXPECTED_TABLES.isdisjoint(tables)  # 业务表全删
        assert "alembic_version" in tables  # 版本表保留(空)是 Alembic 行为
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("select count(*) from alembic_version").fetchone()[0] == 0

    def test_stamp_then_upgrade_on_matching_schema(self, test_artifact_dir):
        """已有 ORM 结构的库: 先 stamp 0001, 再 upgrade head 无操作(幂等)。"""
        db_path = test_artifact_dir / "match.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        _stamp(db_path, "0001")
        _upgrade(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            assert conn.execute("select version_num from alembic_version").fetchone() == ("0001",)


class TestSchemaBaseline:
    def test_current_unversioned_database_matches_baseline(self, test_artifact_dir):
        """ORM create_all 的库与初始 revision 结构一致(比对为空)。"""
        db_path = test_artifact_dir / "current.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        from scripts.check_db_baseline import compare_schema

        assert compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata) == []

    def test_schema_drift_is_reported_without_modifying_database(self, test_artifact_dir):
        """额外表被报告, 且数据库文件未被修改(fail closed)。"""
        db_path = test_artifact_dir / "drift.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("create table unexpected_table (id integer primary key)")
        before = db_path.read_bytes()
        from scripts.check_db_baseline import compare_schema

        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        after = db_path.read_bytes()
        assert any("unexpected_table" in item for item in differences)
        assert after == before

    def test_missing_column_reported(self, test_artifact_dir):
        """缺列被报告且不改库。"""
        db_path = test_artifact_dir / "missing_col.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("create table app_setting (key varchar(64) primary key)")
        from scripts.check_db_baseline import compare_schema

        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        assert any("app_setting" in item and ("缺列" in item or "缺失" in item) for item in differences)

    def test_wrong_nullable_reported(self, test_artifact_dir):
        db_path = test_artifact_dir / "nullable.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        # 把 app_setting.updated_at 改成可空(nullable 漂移)
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("alter table app_setting alter column updated_at drop not null")
        from scripts.check_db_baseline import compare_schema

        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        assert any("updated_at" in item for item in differences)


class TestReadonlySafety:
    """R5-01/R5-08: 只读语义与结构等价反例。"""

    def test_compare_schema_rejects_missing_database_without_creating_it(self, test_artifact_dir):
        """不存在的路径直接报错, 且不创建空库文件。"""
        import pytest as _pytest

        from scripts.check_db_baseline import compare_schema

        path = test_artifact_dir / "missing.db"
        with _pytest.raises((FileNotFoundError, ValueError)):
            compare_schema(f"sqlite:///{path.as_posix()}", Base.metadata)
        assert not path.exists()

    def test_compare_schema_releases_database_handle(self, test_artifact_dir):
        """比对后句柄释放: 文件可被重命名(无占用)。"""
        from scripts.check_db_baseline import compare_schema

        path = test_artifact_dir / "closed.db"
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        assert compare_schema(f"sqlite:///{path.as_posix()}", Base.metadata) == []
        # 句柄未释放时 Windows 会拒绝重命名
        path.rename(test_artifact_dir / "renamed.db")

    def test_migration_created_database_matches_orm(self, test_artifact_dir):
        """Alembic 产物与 ORM 结构完全等价(列/类型/nullable/主键/唯一/索引)。

        这是 R5-08 的核心: 不再用 metadata 自比较, 而是对 Alembic 创建的
        真实结构执行 compare_schema + alembic check。
        """
        from alembic import command as alembic_command

        from scripts.check_db_baseline import compare_schema

        db_path = test_artifact_dir / "migrated.db"
        _upgrade(db_path)
        assert compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata) == []
        # alembic check: 没有新的 upgrade operation(捕获列/索引/约束漂移)
        alembic_command.check(_config(db_path))

    def test_missing_unique_index_detected(self, test_artifact_dir):
        """从 Alembic 库删除唯一索引后 compare_schema 必须报差异。

        反例直接改 migration 产物(不能用 Base.metadata.create_all 自比较)。
        """
        from scripts.check_db_baseline import compare_schema

        db_path = test_artifact_dir / "no_uq.db"
        _upgrade(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            # 删掉 cb_daily_snapshot 的唯一约束对应的自动索引
            conn.execute("DROP INDEX ix_cb_snapshot_bond")
        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        assert any("索引" in item or "ix_cb_snapshot_bond" in item for item in differences)

    def test_index_column_order_detected(self, test_artifact_dir):
        """同名索引列序反转必须报差异。"""
        from scripts.check_db_baseline import compare_schema

        db_path = test_artifact_dir / "idxorder.db"
        _upgrade(db_path)
        with closing(sqlite3.connect(db_path)) as conn:
            # ix_cb_snapshot_bond 是 bond_id 单列索引, 换成 date 单列同名索引
            conn.execute("DROP INDEX ix_cb_snapshot_bond")
            conn.execute("CREATE INDEX ix_cb_snapshot_bond ON cb_daily_snapshot (trade_date)")
        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        assert any("ix_cb_snapshot_bond" in item for item in differences)
