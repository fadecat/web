# -*- coding: utf-8 -*-
"""Alembic 迁移基线测试(第四阶段/T5-T6).

只对临时库执行: 空库升级、重复升级、结构比对、漂移拒绝。
绝不触碰 data/web.db(日常库)。
"""
from __future__ import annotations

import sqlite3
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
        finally:
            engine.dispose()
        assert set(inspector.get_table_names()) == EXPECTED_TABLES | {"alembic_version"}

    def test_upgrade_head_is_idempotent(self, test_artifact_dir):
        db_path = test_artifact_dir / "repeat.db"
        _upgrade(db_path)
        _upgrade(db_path)
        with sqlite3.connect(db_path) as conn:
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
        finally:
            engine.dispose()
        tables = set(inspector.get_table_names())
        assert EXPECTED_TABLES.isdisjoint(tables)  # 业务表全删
        assert "alembic_version" in tables  # 版本表保留(空)是 Alembic 行为
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("select count(*) from alembic_version").fetchone()[0] == 0

    def test_stamp_then_upgrade_on_matching_schema(self, test_artifact_dir):
        """已有 ORM 结构的库: 先 stamp 0001, 再 upgrade head 无操作(幂等)。"""
        db_path = test_artifact_dir / "match.db"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()
        _stamp(db_path, "0001")
        _upgrade(db_path)
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
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
        with sqlite3.connect(db_path) as conn:
            conn.execute("alter table app_setting alter column updated_at drop not null")
        from scripts.check_db_baseline import compare_schema

        differences = compare_schema(f"sqlite:///{db_path.as_posix()}", Base.metadata)
        assert any("updated_at" in item for item in differences)
