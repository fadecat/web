"""Add fund_nav_daily (场外基金净值日线, 组合实验室 P1).

仅 CREATE TABLE: unit_nav/daily_return_pct 为事实, adj_nav 为链式派生值;
唯一键 (symbol, nav_date) 支持幂等覆盖写。不动既有表。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fund_nav_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("nav_date", sa.Date(), nullable=False),
        sa.Column("unit_nav", sa.Float(), nullable=False),
        sa.Column("daily_return_pct", sa.Float(), nullable=True),
        sa.Column("adj_nav", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "nav_date", name="uq_fund_nav_daily_key"),
    )
    op.create_index(
        "ix_fund_nav_daily_symbol_date", "fund_nav_daily", ["symbol", "nav_date"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fund_nav_daily_symbol_date", table_name="fund_nav_daily")
    op.drop_table("fund_nav_daily")
