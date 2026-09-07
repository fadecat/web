# -*- coding: utf-8 -*-
"""可转债黑名单存储层。

职责: 黑名单 CRUD, 幂等拉黑(已存在则更新 reason), 列表/纯 ID 集合查询。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.models.valuation import CbBlacklist


def add_to_blacklist(
    db: Session,
    bond_id: str,
    bond_nm: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """拉黑一只转债(幂等: 已存在则更新 bond_nm/reason/created_at)。

    返回拉黑后的记录 dict。
    """
    bond_id = str(bond_id).strip()
    existing = db.query(CbBlacklist).filter_by(bond_id=bond_id).first()
    if existing:
        existing.bond_nm = bond_nm or existing.bond_nm
        if reason is not None:
            existing.reason = reason.strip() if reason.strip() else None
        existing.created_at = datetime.utcnow()
        db.commit()
        return _to_dict(existing)

    entry = CbBlacklist(
        bond_id=bond_id,
        bond_nm=bond_nm,
        reason=reason.strip() if reason and reason.strip() else None,
    )
    db.add(entry)
    db.commit()
    return _to_dict(entry)


def remove_from_blacklist(db: Session, bond_id: str) -> bool:
    """取消拉黑。返回是否删除了记录(不存在返回 False)。"""
    bond_id = str(bond_id).strip()
    existing = db.query(CbBlacklist).filter_by(bond_id=bond_id).first()
    if not existing:
        return False
    db.delete(existing)
    db.commit()
    return True


def get_blacklist(db: Session) -> list[dict[str, Any]]:
    """返回全部黑名单列表, 按拉黑时间倒序。"""
    rows = db.query(CbBlacklist).order_by(CbBlacklist.created_at.desc()).all()
    return [_to_dict(r) for r in rows]


def get_blacklist_ids(db: Session) -> set[str]:
    """返回黑名单转债代码集合(给筛选接口剔除用, 单条 SQL)。"""
    return {r.bond_id for r in db.query(CbBlacklist).all()}


def _to_dict(entry: CbBlacklist) -> dict[str, Any]:
    return {
        "bond_id": entry.bond_id,
        "bond_nm": entry.bond_nm,
        "reason": entry.reason,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
