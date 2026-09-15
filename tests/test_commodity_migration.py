from pathlib import Path
import sqlite3
from contextlib import closing

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
