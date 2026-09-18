"""Add research replay tables (次日 T 价位研究 V1).

8 张表: research_security / research_data_snapshot / research_daily_bar_raw /
research_daily_bar_adjusted / research_data_revision / research_trade_calendar /
research_replay_run / research_replay_day。

与 ORM create_all 产物严格一致(建表守卫: 已存在的空表 drop 重建, 非空拒绝——
对齐 0002 先例)。名单不在此 seed(研究配置由任务按 yaml upsert)。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TARGET_TABLES = (
    "research_security",
    "research_data_snapshot",
    "research_daily_bar_raw",
    "research_daily_bar_adjusted",
    "research_data_revision",
    "research_trade_calendar",
    "research_replay_run",
    "research_replay_day",
)


def _reset_existing_empty_tables() -> None:
    """重建已存在的空表(接管链 create_all 产物场景); 非空拒绝。"""
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
            "research migration refuses non-empty target tables: " + ", ".join(nonempty)
        )
    # 子表先删(外键安全)
    for name in reversed(_TARGET_TABLES):
        if name in existing:
            op.drop_table(name)


def upgrade() -> None:
    _reset_existing_empty_tables()
    op.create_table(
        "research_security",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("security_type", sa.String(length=8), nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="akshare"),
        sa.Column("selection_list", sa.String(length=32), nullable=False, server_default="手动ETF"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", name="uq_research_security_symbol"),
    )
    op.create_index("ix_research_security_type", "research_security", ["security_type"])

    op.create_table(
        "research_data_snapshot",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("request_start", sa.Date(), nullable=False),
        sa.Column("request_end", sa.Date(), nullable=False),
        sa.Column("raw_rows", sa.Integer(), nullable=False),
        sa.Column("hfq_rows", sa.Integer(), nullable=False),
        sa.Column("raw_hash", sa.String(length=64), nullable=False),
        sa.Column("hfq_hash", sa.String(length=64), nullable=False),
        sa.Column("dates_match", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reject_reason", sa.String(length=500), nullable=True),
        sa.Column("first_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "fetched_at", "source", name="uq_research_snapshot_key"),
    )
    op.create_index("ix_research_snapshot_symbol", "research_data_snapshot", ["symbol"])

    op.create_table(
        "research_daily_bar_raw",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("volume_unit", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["research_data_snapshot.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "trade_date", "source", name="uq_research_bar_raw_key"),
    )
    op.create_index("ix_research_bar_raw_symbol_date", "research_daily_bar_raw", ["symbol", "trade_date"])

    op.create_table(
        "research_daily_bar_adjusted",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_mode", sa.String(length=8), nullable=False, server_default="HFQ"),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("volume_unit", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["research_data_snapshot.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "trade_date", "adjust_mode", "source", name="uq_research_bar_adj_key"),
    )
    op.create_index("ix_research_bar_adj_symbol_date", "research_daily_bar_adjusted", ["symbol", "trade_date"])

    op.create_table(
        "research_data_revision",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("old_hash", sa.String(length=64), nullable=False),
        sa.Column("new_hash", sa.String(length=64), nullable=False),
        sa.Column("old_payload", sa.String(), nullable=False),
        sa.Column("new_payload", sa.String(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["research_data_snapshot.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_name", "business_key", "new_hash", name="uq_research_revision_key"),
    )
    op.create_index("ix_research_revision_key", "research_data_revision", ["table_name", "business_key"])

    op.create_table(
        "research_trade_calendar",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exchange", sa.String(length=8), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exchange", "trade_date", "source", name="uq_research_calendar_key"),
    )
    op.create_index("ix_research_calendar_date", "research_trade_calendar", ["trade_date"])

    op.create_table(
        "research_replay_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("param_lambda", sa.Float(), nullable=False),
        sa.Column("quantile_window", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("train_end_date", sa.Date(), nullable=True),
        sa.Column("train_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("hfq_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol", "param_lambda", "quantile_window", "algorithm_version",
            "start_date", "end_date",
            name="uq_research_replay_run_key",
        ),
    )
    op.create_index("ix_research_replay_run_symbol", "research_replay_run", ["symbol"])

    op.create_table(
        "research_replay_day",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("eval_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_codes", sa.String(), nullable=True),
        sa.Column("z_value", sa.Float(), nullable=True),
        sa.Column("atr14", sa.Float(), nullable=True),
        sa.Column("ema20", sa.Float(), nullable=True),
        sa.Column("scale", sa.Float(), nullable=True),
        sa.Column("buy_levels_raw", sa.String(), nullable=True),
        sa.Column("sell_levels_raw", sa.String(), nullable=True),
        sa.Column("next_open", sa.Float(), nullable=True),
        sa.Column("next_high", sa.Float(), nullable=True),
        sa.Column("next_low", sa.Float(), nullable=True),
        sa.Column("next_close", sa.Float(), nullable=True),
        sa.Column("buy_hits", sa.String(), nullable=True),
        sa.Column("sell_hits", sa.String(), nullable=True),
        sa.Column("buy_open_invalid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sell_open_invalid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("day_category", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_replay_run.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "plan_date", name="uq_research_replay_day_key"),
    )
    op.create_index("ix_research_replay_day_run", "research_replay_day", ["run_id", "plan_date"])


def downgrade() -> None:
    op.drop_index("ix_research_replay_day_run", table_name="research_replay_day")
    op.drop_table("research_replay_day")
    op.drop_index("ix_research_replay_run_symbol", table_name="research_replay_run")
    op.drop_table("research_replay_run")
    op.drop_index("ix_research_calendar_date", table_name="research_trade_calendar")
    op.drop_table("research_trade_calendar")
    op.drop_index("ix_research_revision_key", table_name="research_data_revision")
    op.drop_table("research_data_revision")
    op.drop_index("ix_research_bar_adj_symbol_date", table_name="research_daily_bar_adjusted")
    op.drop_table("research_daily_bar_adjusted")
    op.drop_index("ix_research_bar_raw_symbol_date", table_name="research_daily_bar_raw")
    op.drop_table("research_daily_bar_raw")
    op.drop_index("ix_research_snapshot_symbol", table_name="research_data_snapshot")
    op.drop_table("research_data_snapshot")
    op.drop_index("ix_research_security_type", table_name="research_security")
    op.drop_table("research_security")
