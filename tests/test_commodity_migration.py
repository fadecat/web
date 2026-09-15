from pathlib import Path
import sqlite3
from contextlib import closing
import importlib.util
import json

import pytest

from alembic import command
from alembic.config import Config


def test_commodity_migration_seeds_all_configured_instruments(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-migration.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_path)) as conn:
        count = conn.execute("SELECT count(*) FROM commodity_instrument").fetchone()[0]
        rows = conn.execute(
            "SELECT code, market, category, source, enabled, display_order "
            "FROM commodity_instrument ORDER BY display_order"
        ).fetchall()
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert count == 75
    assert len({row[0] for row in rows}) == 75
    assert {row[1] for row in rows} == {"domestic", "foreign"}
    assert {row[2] for row in rows} >= {"能源与化工", "黑色建材", "有色贵金属", "农产品"}
    assert all(row[3] == "akshare" and row[4] == 1 for row in rows)
    assert [row[5] for row in rows] == list(range(1, 76))
    assert version != "0001"


def test_commodity_migration_downgrade_removes_child_tables_first(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-downgrade.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0001")

    with closing(sqlite3.connect(db_path)) as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert names.isdisjoint({
        "commodity_instrument",
        "commodity_daily_price",
        "commodity_percentile_daily",
        "commodity_sync_state",
    })
    assert version == "0001"


def test_commodity_migration_rejects_incompatible_existing_table(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-incompatible.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "0001")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE commodity_daily_price ("
            "id INTEGER PRIMARY KEY, instrument_code VARCHAR(16) NOT NULL, "
            "trade_date DATE NOT NULL, close FLOAT NOT NULL, open FLOAT, high FLOAT, "
            "low FLOAT, volume FLOAT, source VARCHAR(32) NOT NULL, "
            "ingest_run_id INTEGER, created_at DATETIME NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO commodity_daily_price "
            "(id, instrument_code, trade_date, close, source, created_at) "
            "VALUES (1, 'BAD', '2026-01-01', 1, 'test', '2026-01-01')"
        )
        conn.commit()

    with pytest.raises(Exception, match="commodity_daily_price"):
        command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commodity_daily_price'").fetchone()
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"


def test_commodity_migration_rejects_same_columns_with_wrong_close_definition(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-wrong-close.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "0001")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE commodity_daily_price ("
            "id INTEGER PRIMARY KEY, instrument_code VARCHAR(16) NOT NULL, "
            "trade_date DATE NOT NULL, close VARCHAR(32), open FLOAT, high FLOAT, "
            "low FLOAT, volume FLOAT, source VARCHAR(32) NOT NULL, "
            "ingest_run_id INTEGER, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
            "CONSTRAINT uq_commodity_price_code_date UNIQUE (instrument_code, trade_date), "
            "FOREIGN KEY (instrument_code) REFERENCES commodity_instrument(code)"
            ")"
        )
        conn.execute("CREATE INDEX ix_commodity_price_code_date ON commodity_daily_price (instrument_code, trade_date)")
        conn.execute("CREATE INDEX ix_commodity_price_date ON commodity_daily_price (trade_date)")
        conn.execute(
            "INSERT INTO commodity_daily_price "
            "(id, instrument_code, trade_date, source, created_at, updated_at) "
            "VALUES (1, 'BAD', '2026-01-01', 'test', '2026-01-01', '2026-01-01')"
        )
        conn.commit()

    with pytest.raises(Exception, match="commodity_daily_price"):
        command.upgrade(cfg, "head")


def test_commodity_migration_supports_offline_sql_generation(tmp_path):
    import io

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{(tmp_path / 'offline.db').as_posix()}")
    cfg.output_buffer = io.StringIO()

    command.upgrade(cfg, "0001:head", sql=True)
    assert "CREATE TABLE commodity_instrument" in cfg.output_buffer.getvalue()


def test_migration_seed_categories_match_reference_reporting_algorithm():
    migration = importlib.util.spec_from_file_location(
        "commodity_migration",
        Path(__file__).resolve().parents[1] / "migrations/versions/0002_add_commodity_monitor_tables.py",
    )
    assert migration is not None and migration.loader is not None
    module = importlib.util.module_from_spec(migration)
    migration.loader.exec_module(module)

    fixture_path = Path(__file__).parent / "fixtures" / "commodity_seed.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(fixture) == 75
    assert module._SEED == [tuple(item) for item in fixture]
    categories = dict((code, category) for code, _name, _market, category in module._SEED)
    assert categories["BC0"] == "其他"
    assert categories["CS0"] == "其他"


def test_empty_incompatible_existing_table_is_rebuilt_with_constraints_and_seed(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-empty-rebuild.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "0001")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE commodity_instrument (code VARCHAR(16) PRIMARY KEY)")
        conn.execute("CREATE TABLE commodity_daily_price (id INTEGER PRIMARY KEY)")
        conn.commit()

    command.upgrade(cfg, "head")
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM commodity_instrument").fetchone() == (75,)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commodity_daily_price)")}
        assert "close" in columns
        checks = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='commodity_daily_price'"
        ).fetchone()[0]
        assert "ck_commodity_price_close_positive" in checks
        foreign_keys = conn.execute("PRAGMA foreign_key_list(commodity_daily_price)").fetchall()
        assert any(row[2] == "commodity_instrument" for row in foreign_keys)


def test_nonempty_existing_commodity_table_is_rejected_and_preserved(test_artifact_dir):
    db_path = test_artifact_dir / "commodity-nonempty-reject.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(cfg, "0001")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("CREATE TABLE commodity_instrument (code VARCHAR(16) PRIMARY KEY)")
        conn.execute("INSERT INTO commodity_instrument VALUES ('KEEP')")
        conn.commit()

    with pytest.raises(Exception, match="non-empty"):
        command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT code FROM commodity_instrument").fetchone() == ("KEEP",)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"
