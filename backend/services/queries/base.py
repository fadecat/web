# -*- coding: utf-8 -*-
"""查询层公共语句构造: latest(自连接取最新) / history(全量)。

估值快照、股息率等带 (index_code, trade_date) 幂等键的日频表共用;
index_code 传入时先做 canonical_id → 历史存储 key 翻译(阶段2)。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.sql.selectable import Select

from backend.services.catalog_entities import to_storage


def _norm(index_code: str | None) -> str | None:
    return to_storage(index_code) if index_code else None


def latest_stmt(model, index_code: str | None = None) -> Select:
    """每只指数最新交易日那一行(自连接 max(trade_date))。"""
    latest_date = (
        select(
            model.index_code,
            func.max(model.trade_date).label("max_date"),
        )
        .group_by(model.index_code)
        .subquery()
    )
    stmt = (
        select(model)
        .join(
            latest_date,
            (model.index_code == latest_date.c.index_code)
            & (model.trade_date == latest_date.c.max_date),
        )
        .order_by(model.index_code)
    )
    if index_code:
        stmt = stmt.where(model.index_code == _norm(index_code))
    return stmt


def history_stmt(model, index_code: str | None = None) -> Select:
    """全量历史, 按 index_code 升序、trade_date 降序。"""
    stmt = select(model).order_by(model.index_code, model.trade_date.desc())
    if index_code:
        stmt = stmt.where(model.index_code == _norm(index_code))
    return stmt
