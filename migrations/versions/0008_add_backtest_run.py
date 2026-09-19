"""Add backtest_run (回测 Run, 组合实验室 P3).

仅 CREATE TABLE 一张表:
- backtest_run: 一次回测 = 一个不可变 Run。组合快照(members_json) + 参数(rebalance/benchmark/
  区间) + 结果(result_json/nav_json) + input_hash 幂等键。

归属: 再平衡/基准/区间**属于 Run 不属于 portfolio**(docs/portfolio-lab-flow.md 第二节)。
不动既有表; downgrade 仅删本表。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rebalance", sa.String(length=16), nullable=False),
        sa.Column("benchmark_symbol", sa.String(length=32), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("actual_start", sa.Date(), nullable=True),
        sa.Column("actual_end", sa.Date(), nullable=True),
        sa.Column("t0_date", sa.Date(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("members_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("nav_json", sa.Text(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolio.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_run_portfolio_id", "backtest_run", ["portfolio_id"], unique=False)
    op.create_index("ix_backtest_run_input_hash", "backtest_run", ["input_hash"], unique=False)
    op.create_index(
        "ix_backtest_run_portfolio_created", "backtest_run", ["portfolio_id", "created_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_run_portfolio_created", table_name="backtest_run")
    op.drop_index("ix_backtest_run_input_hash", table_name="backtest_run")
    op.drop_index("ix_backtest_run_portfolio_id", table_name="backtest_run")
    op.drop_table("backtest_run")
