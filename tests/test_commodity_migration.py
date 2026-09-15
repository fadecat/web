from pathlib import Path
import sqlite3
from contextlib import closing
import importlib.util
import sys

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
        conn.commit()

    with pytest.raises(Exception, match="commodity_daily_price"):
        command.upgrade(cfg, "head")

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='commodity_daily_price'").fetchone()
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0001"


def test_migration_seed_categories_match_reference_reporting_algorithm():
    import yaml

    migration = importlib.util.spec_from_file_location(
        "commodity_migration",
        Path(__file__).resolve().parents[1] / "migrations/versions/0002_add_commodity_monitor_tables.py",
    )
    assert migration is not None and migration.loader is not None
    module = importlib.util.module_from_spec(migration)
    migration.loader.exec_module(module)

    reporting_path = Path(r"D:\gitub_codes\market-daily")
    sys.path.insert(0, str(reporting_path))
    try:
        from src.commodity.reporting import _normalize_code_root, _section_name
    finally:
        sys.path.pop(0)

    def reference_category(code):
        upper = code.upper()
        if upper == "SM":
            return "农产品"
        if upper == "SM0":
            return "黑色建材"
        return _section_name(_normalize_code_root(upper))

    configured = yaml.safe_load(
        open(r"D:\gitub_codes\market-daily\config\commodity.yaml", encoding="utf-8")
    )["symbols"]
    configured_by_code = {item["code"]: item for item in configured}
    assert len(module._SEED) == len(configured_by_code) == 75
    assert all(
        category == reference_category(code)
        for code, _name, _market, category in module._SEED
    )
    assert dict((code, category) for code, _name, _market, category in module._SEED)["BC0"] == "其他"
    assert dict((code, category) for code, _name, _market, category in module._SEED)["CS0"] == "其他"
