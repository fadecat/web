# -*- coding: utf-8 -*-
"""研究回放表迁移测试(0003 研究表 + 0004 权益事件 + 0005 同步状态列 + 0006 基金净值表, 对齐 test_commodity_migration 模式)。"""
from contextlib import closing
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config


RESEARCH_TABLES = {
    "research_security",
    "research_data_snapshot",
    "research_daily_bar_raw",
    "research_daily_bar_adjusted",
    "research_data_revision",
    "research_trade_calendar",
    "research_replay_run",
    "research_replay_day",
    "research_corporate_event",
    "fund_nav_daily",
}


def _cfg(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    return cfg


def test_research_migration_creates_all_tables_head_0008(test_artifact_dir):
    db_path = test_artifact_dir / "research-migration.db"
    command.upgrade(_cfg(db_path), "head")

    with closing(sqlite3.connect(db_path)) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert RESEARCH_TABLES <= names
    assert version == "0008"


def test_research_migration_downgrade_0003_to_0002(test_artifact_dir):
    db_path = test_artifact_dir / "research-downgrade.db"
    cfg = _cfg(db_path)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")

    with closing(sqlite3.connect(db_path)) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert names.isdisjoint(RESEARCH_TABLES)
    assert version == "0002"


def test_research_migration_rebuilds_empty_existing_table(test_artifact_dir):
    """已存在的空 research 表 → drop 重建(带约束); 非空不匹配 → raise。"""
    db_path = test_artifact_dir / "research-empty-rebuild.db"
    cfg = _cfg(db_path)
    command.upgrade(cfg, "0002")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE research_security (symbol VARCHAR(16) PRIMARY KEY)")
        conn.commit()

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_security)")}
        constraints = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='research_security'"
        ).fetchone()[0]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert "selection_list" in columns
    assert "uq_research_security_symbol" in constraints
    assert {"last_sync_at", "last_sync_status", "last_sync_error", "last_sync_rows"} <= columns
    assert version == "0008"


def test_research_migration_rejects_nonempty_existing_table(test_artifact_dir):
    db_path = test_artifact_dir / "research-nonempty-reject.db"
    cfg = _cfg(db_path)
    command.upgrade(cfg, "0002")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE research_security (symbol VARCHAR(16) PRIMARY KEY)")
        conn.execute("INSERT INTO research_security VALUES ('KEEP.SH')")
        conn.commit()

    import pytest

    with pytest.raises(Exception, match="non-empty"):
        command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT symbol FROM research_security").fetchone() == ("KEEP.SH",)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0002"


# ---------------------------------------------------------------------------
# 0004: 权益事件表(腾讯换源补表)
# ---------------------------------------------------------------------------


def test_corporate_event_migration_upgrade_downgrade(test_artifact_dir):
    """0003 → 0004 建 research_corporate_event; 降回 0003 删除。"""
    db_path = test_artifact_dir / "corporate-event-migration.db"
    cfg = _cfg(db_path)
    command.upgrade(cfg, "0003")
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'research_corporate_event'"
        ).fetchone()[0] == 0

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'research_corporate_event'"
        ).fetchone()[0] == 1
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_corporate_event)")}
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='research_corporate_event'"
        ).fetchone()[0]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert {"symbol", "event_date", "factor", "cumulative_dividend", "source", "fetched_at"} <= columns
    assert "uq_research_corporate_event_key" in sql
    assert version == "0008"

    command.downgrade(cfg, "0003")
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'research_corporate_event'"
        ).fetchone()[0] == 0
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"


def test_corporate_event_migration_rejects_nonempty_existing_table(test_artifact_dir):
    db_path = test_artifact_dir / "corporate-event-nonempty.db"
    cfg = _cfg(db_path)
    command.upgrade(cfg, "0003")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE research_corporate_event (symbol VARCHAR(16), event_date DATE)"
        )
        conn.execute("INSERT INTO research_corporate_event VALUES ('600900.SH', '2026-07-17')")
        conn.commit()

    import pytest

    with pytest.raises(Exception, match="non-empty"):
        command.upgrade(cfg, "head")
