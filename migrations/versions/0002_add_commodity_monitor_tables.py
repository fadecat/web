"""Add commodity monitor tables and the configured instrument universe."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The seed is intentionally captured in the revision.  A migration must remain
# reproducible if the source project's YAML changes later.
_SEED = [
    ("CL", "NYMEX原油", "foreign", "能源与化工"),
    ("NG", "NYMEX天然气", "foreign", "能源与化工"),
    ("OIL", "布伦特原油", "foreign", "能源与化工"),
    ("GC", "COMEX黄金", "foreign", "有色贵金属"),
    ("SI", "COMEX白银", "foreign", "有色贵金属"),
    ("HG", "COMEX铜", "foreign", "有色贵金属"),
    ("AHD", "LME铝", "foreign", "有色贵金属"),
    ("CAD", "LME铜", "foreign", "有色贵金属"),
    ("NID", "LME镍", "foreign", "有色贵金属"),
    ("PBD", "LME铅", "foreign", "有色贵金属"),
    ("SND", "LME锡", "foreign", "有色贵金属"),
    ("ZSD", "LME锌", "foreign", "有色贵金属"),
    ("C", "CBOT玉米", "foreign", "农产品"),
    ("S", "CBOT大豆", "foreign", "农产品"),
    ("W", "CBOT小麦", "foreign", "农产品"),
    ("BO", "CBOT豆油", "foreign", "农产品"),
    ("SM", "CBOT豆粕", "foreign", "农产品"),
    ("CT", "ICE棉花", "foreign", "农产品"),
    ("FCPO", "马棕榈油", "foreign", "农产品"),
    ("RSS3", "TOCOM橡胶", "foreign", "农产品"),
    ("SC0", "原油主连", "domestic", "能源与化工"),
    ("FU0", "燃油主连", "domestic", "能源与化工"),
    ("BU0", "沥青主连", "domestic", "能源与化工"),
    ("LU0", "低硫燃料油主连", "domestic", "能源与化工"),
    ("PG0", "液化石油气主连", "domestic", "能源与化工"),
    ("ZC0", "动力煤主连", "domestic", "黑色建材"),
    ("JM0", "焦煤主连", "domestic", "黑色建材"),
    ("J0", "焦炭主连", "domestic", "黑色建材"),
    ("I0", "铁矿石主连", "domestic", "黑色建材"),
    ("RB0", "螺纹钢主连", "domestic", "黑色建材"),
    ("HC0", "热卷主连", "domestic", "黑色建材"),
    ("CU0", "沪铜主连", "domestic", "有色贵金属"),
    ("AL0", "沪铝主连", "domestic", "有色贵金属"),
    ("ZN0", "沪锌主连", "domestic", "有色贵金属"),
    ("NI0", "沪镍主连", "domestic", "有色贵金属"),
    ("SN0", "沪锡主连", "domestic", "有色贵金属"),
    ("PB0", "沪铅主连", "domestic", "有色贵金属"),
    ("AU0", "沪金主连", "domestic", "有色贵金属"),
    ("AG0", "沪银主连", "domestic", "有色贵金属"),
    ("AO0", "氧化铝主连", "domestic", "有色贵金属"),
    ("SI0", "工业硅主连", "domestic", "有色贵金属"),
    ("LC0", "碳酸锂主连", "domestic", "有色贵金属"),
    ("M0", "豆粕主连", "domestic", "农产品"),
    ("Y0", "豆油主连", "domestic", "农产品"),
    ("P0", "棕榈油主连", "domestic", "农产品"),
    ("RM0", "菜粕主连", "domestic", "农产品"),
    ("OI0", "菜油主连", "domestic", "农产品"),
    ("C0", "玉米主连", "domestic", "农产品"),
    ("A0", "豆一主连", "domestic", "农产品"),
    ("SR0", "白糖主连", "domestic", "农产品"),
    ("CF0", "棉花主连", "domestic", "农产品"),
    ("TA0", "PTA主连", "domestic", "能源与化工"),
    ("MA0", "甲醇主连", "domestic", "能源与化工"),
    ("EG0", "乙二醇主连", "domestic", "能源与化工"),
    ("SA0", "纯碱主连", "domestic", "能源与化工"),
    ("UR0", "尿素主连", "domestic", "能源与化工"),
    ("AP0", "苹果主连", "domestic", "农产品"),
    ("PK0", "花生主连", "domestic", "农产品"),
    ("L0", "聚乙烯主连", "domestic", "能源与化工"),
    ("PP0", "聚丙烯主连", "domestic", "能源与化工"),
    ("V0", "PVC主连", "domestic", "能源与化工"),
    ("LH0", "生猪主连", "domestic", "其他"),
    ("JD0", "鸡蛋主连", "domestic", "其他"),
    ("FG0", "玻璃主连", "domestic", "其他"),
    ("RU0", "沪胶主连", "domestic", "其他"),
    ("SS0", "不锈钢主连", "domestic", "黑色建材"),
    ("EB0", "苯乙烯主连", "domestic", "其他"),
    ("SP0", "纸浆主连", "domestic", "其他"),
    ("EC0", "集运指数主连", "domestic", "其他"),
    ("NR0", "20号胶主连", "domestic", "其他"),
    ("BC0", "国际铜主连", "domestic", "其他"),
    ("CS0", "玉米淀粉主连", "domestic", "其他"),
    ("PF0", "短纤主连", "domestic", "其他"),
    ("SF0", "硅铁主连", "domestic", "黑色建材"),
    ("SM0", "锰硅主连", "domestic", "黑色建材"),
]

_TARGET_TABLES = (
    "commodity_instrument",
    "commodity_daily_price",
    "commodity_percentile_daily",
    "commodity_sync_state",
)


def _reset_existing_empty_tables() -> None:
    """Rebuild empty pre-existing tables; preserve any table containing data."""
    if context.is_offline_mode():
        return
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = [name for name in _TARGET_TABLES if inspector.has_table(name)]
    nonempty = [
        name
        for name in existing
        if bind.execute(sa.text(f"SELECT 1 FROM {name} LIMIT 1")).first() is not None
    ]
    if nonempty:
        raise RuntimeError(
            "commodity migration refuses non-empty target tables: "
            + ", ".join(nonempty)
        )
    # Child-to-parent order keeps SQLite foreign-key enforcement safe.
    for name in ("commodity_sync_state", "commodity_percentile_daily", "commodity_daily_price", "commodity_instrument"):
        if name in existing:
            op.drop_table(name)


def upgrade() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _reset_existing_empty_tables()
    op.create_table(
        "commodity_instrument",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "commodity_daily_price",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_code", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("open", sa.Float(), nullable=True),
        sa.Column("high", sa.Float(), nullable=True),
        sa.Column("low", sa.Float(), nullable=True),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ingest_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("close > 0", name="ck_commodity_price_close_positive"),
        sa.ForeignKeyConstraint(["instrument_code"], ["commodity_instrument.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_code", "trade_date", name="uq_commodity_price_code_date"),
    )
    op.create_index("ix_commodity_price_code_date", "commodity_daily_price", ["instrument_code", "trade_date"])
    op.create_index("ix_commodity_price_date", "commodity_daily_price", ["trade_date"])
    op.create_table(
        "commodity_percentile_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instrument_code", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("window_code", sa.String(length=8), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("algorithm_version", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("percentile IS NULL OR (percentile >= 0 AND percentile <= 100)", name="ck_commodity_percentile_range"),
        sa.ForeignKeyConstraint(["instrument_code"], ["commodity_instrument.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_code", "trade_date", "window_code", "algorithm_version", name="uq_commodity_percentile_key"),
    )
    op.create_index("ix_commodity_percentile_code_date", "commodity_percentile_daily", ["instrument_code", "trade_date"])
    op.create_index("ix_commodity_percentile_date", "commodity_percentile_daily", ["trade_date"])
    op.create_table(
        "commodity_sync_state",
        sa.Column("instrument_code", sa.String(length=16), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("source_latest_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["instrument_code"], ["commodity_instrument.code"]),
        sa.PrimaryKeyConstraint("instrument_code"),
    )
    op.bulk_insert(
        sa.table(
            "commodity_instrument",
            sa.column("code", sa.String(16)),
            sa.column("name", sa.String(64)),
            sa.column("market", sa.String(16)),
            sa.column("category", sa.String(32)),
            sa.column("source", sa.String(32)),
            sa.column("enabled", sa.Boolean()),
            sa.column("display_order", sa.Integer()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        ),
        [
            {"code": code, "name": name, "market": market, "category": category, "source": "akshare", "enabled": True, "display_order": order, "created_at": now, "updated_at": now}
            for order, (code, name, market, category) in enumerate(_SEED, 1)
        ],
    )


def downgrade() -> None:
    op.drop_table("commodity_sync_state")
    op.drop_index("ix_commodity_percentile_date", table_name="commodity_percentile_daily")
    op.drop_index("ix_commodity_percentile_code_date", table_name="commodity_percentile_daily")
    op.drop_table("commodity_percentile_daily")
    op.drop_index("ix_commodity_price_date", table_name="commodity_daily_price")
    op.drop_index("ix_commodity_price_code_date", table_name="commodity_daily_price")
    op.drop_table("commodity_daily_price")
    op.drop_table("commodity_instrument")
