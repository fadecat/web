# -*- coding: utf-8 -*-
"""风格轮动「最新估值」只读接口单元测试。

验证: 只读库不触发抓取、按对照组两侧返回独立数据日期、缺估值返回
available=False、同侧代码报 400。
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from backend.models.valuation import IndexValuationSnapshot
from backend.api.routes.style_rotation import style_rotation_valuation


def _seed_valuation(db, index_code, index_name, trade_date, pe=None, pb=None):
    db.add(IndexValuationSnapshot(
        index_code=index_code,
        index_name=index_name,
        trade_date=trade_date,
        pe=pe,
        pb=pb,
    ))
    db.commit()


def test_both_sides_available(db):
    _seed_valuation(db, "399296", "创成长", date(2026, 9, 4), pe=28.5, pb=4.2)
    _seed_valuation(db, "930955", "红利低波100", date(2026, 9, 4), pe=8.1, pb=1.1)

    result = style_rotation_valuation("399296", "930955", db)

    assert result["left"]["available"] is True
    assert result["left"]["index_code"] == "399296"
    assert result["left"]["trade_date"] == "2026-09-04"
    assert result["left"]["pe"] == 28.5
    assert result["right"]["available"] is True
    assert result["right"]["index_code"] == "930955"
    assert result["right"]["pe"] == 8.1


def test_missing_side_returns_unavailable(db):
    _seed_valuation(db, "399296", "创成长", date(2026, 9, 4), pe=28.5)

    result = style_rotation_valuation("399296", "399373", db)

    # 有估值侧
    assert result["left"]["available"] is True
    assert result["left"]["index_code"] == "399296"
    # 无估值侧(腾讯大小盘组未纳入估值任务)不报错, 显式标记 available=False
    assert result["right"]["available"] is False
    assert result["right"]["index_code"] == "399373"


def test_latest_row_is_selected(db):
    _seed_valuation(db, "399296", "创成长", date(2026, 9, 3), pe=27.0)
    _seed_valuation(db, "399296", "创成长", date(2026, 9, 4), pe=28.5)

    result = style_rotation_valuation("399296", "930955", db)

    # 同指数多条历史只取 trade_date 最新的一行
    assert result["left"]["trade_date"] == "2026-09-04"
    assert result["left"]["pe"] == 28.5


def test_same_symbol_rejected(db):
    with pytest.raises(HTTPException) as exc:
        style_rotation_valuation("399296", "399296", db)
    assert exc.value.status_code == 400
