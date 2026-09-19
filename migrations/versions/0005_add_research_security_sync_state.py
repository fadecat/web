"""Add research_security 最近一次同步状态列(场外基金/同步可观测性).

仅 ADD COLUMN 4 个可空字段(last_sync_at / last_sync_status /
last_sync_error / last_sync_rows), 不动既有列与数据。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_security", sa.Column("last_sync_at", sa.DateTime(), nullable=True))
    op.add_column("research_security", sa.Column("last_sync_status", sa.String(length=16), nullable=True))
    op.add_column("research_security", sa.Column("last_sync_error", sa.String(length=255), nullable=True))
    op.add_column("research_security", sa.Column("last_sync_rows", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("research_security", "last_sync_rows")
    op.drop_column("research_security", "last_sync_error")
    op.drop_column("research_security", "last_sync_status")
    op.drop_column("research_security", "last_sync_at")
