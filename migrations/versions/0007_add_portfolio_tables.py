"""Add portfolio / portfolio_asset (组合定义, 组合实验室 P2).

仅 CREATE TABLE 两张表:
- portfolio       组合身份 + L1 列表页三格缓存(cached_*)
- portfolio_asset 组合成员(初始比例 + added_at + sort_order), 唯一键 (portfolio_id, symbol)

再平衡/基准/区间**不在本表**(属 backtest_run, P3)。不动既有表。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "portfolio",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("default_rebalance", sa.String(length=16), nullable=True),
        sa.Column("cached_day_return", sa.Float(), nullable=True),
        sa.Column("cached_month_return", sa.Float(), nullable=True),
        sa.Column("cached_ytd_return", sa.Float(), nullable=True),
        sa.Column("cached_asof_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portfolio_status", "portfolio", ["status"], unique=False)

    op.create_table(
        "portfolio_asset",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("target_weight", sa.Float(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "symbol", name="uq_portfolio_asset_key"),
    )
    op.create_index(
        "ix_portfolio_asset_portfolio", "portfolio_asset", ["portfolio_id", "sort_order"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_asset_portfolio", table_name="portfolio_asset")
    op.drop_table("portfolio_asset")
    op.drop_index("ix_portfolio_status", table_name="portfolio")
    op.drop_table("portfolio")
