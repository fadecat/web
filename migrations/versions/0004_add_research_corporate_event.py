"""Add research_corporate_event (次日 T 价位研究 V1 换源补表).

权益事件日历表(新浪 hfq.js), 是腾讯换源后权益事件的主检测来源:
腾讯 fqkline 的 raw/hfq 比值在非事件日有 0.1%~0.4% 抖动, r_t 阶跃启发式
降级为兜底, 事件判断以本表为准(设计变更记录见设计文档「数据源决策记录」)。

与 ORM create_all 产物严格一致(建表守卫: 已存在的空表 drop 重建, 非空拒绝——
对齐 0002/0003 先例)。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TARGET_TABLES = ("research_corporate_event",)


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
            "research corporate event migration refuses non-empty target tables: "
            + ", ".join(nonempty)
        )
    for name in reversed(_TARGET_TABLES):
        if name in existing:
            op.drop_table(name)


def upgrade() -> None:
    _reset_existing_empty_tables()
    op.create_table(
        "research_corporate_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("factor", sa.Float(), nullable=True),
        sa.Column("cumulative_dividend", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "event_date", "source", name="uq_research_corporate_event_key"),
    )
    op.create_index("ix_research_corporate_event_symbol", "research_corporate_event", ["symbol", "event_date"])


def downgrade() -> None:
    op.drop_index("ix_research_corporate_event_symbol", table_name="research_corporate_event")
    op.drop_table("research_corporate_event")
