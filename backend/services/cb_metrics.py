# -*- coding: utf-8 -*-
"""公共指标计算(转债选债 V3, 方案 §3.1)。

单一事实源:
- simple_maturity_yield_pct(简单到期收益率) = (到期赎回价 − 现价) / 现价 × 100
  未年化, 不含票息和税; 与集思录原生 YTM(ytm_rt) 口径不同, 互不兜底。
- 赎回价只能取 redeem_price(到期赎回价), 严禁用 force_redeem_price
  (强赎触发价)替代——两者语义不同。
- 计算、过滤、排序一律使用未格式化数值; 保留位数只发生在前端显示,
  不允许先四舍五入再比较阈值。

纯函数模块: 不读库、不触网、无副作用。
"""
from __future__ import annotations

import math
from typing import Any


def finite_number(value: Any) -> float | None:
    """任意来源的值 → 有限浮点数; 不可解析/非有限数返回 None。

    - None 与 bool 直接返回 None(bool 是 int 子类, 不得冒充数值 1/0);
    - 字符串去首尾空白并剔除千分位逗号后解析(集思录源数据可能是文本);
    - nan/inf/-inf 一律 None, 不让非有限数进入过滤与排序。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip().replace(',', ''))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def simple_maturity_yield_pct(price: Any, redeem_price: Any) -> float | None:
    """简单到期收益率(%) = (redeem_price - price) / price * 100。

    现价或赎回价缺失、非正数时返回 None(缺失是缺失, 不是 0);
    负值是合法业务结果(现价高于赎回价), 原样返回不截断。
    """
    p, r = finite_number(price), finite_number(redeem_price)
    if p is None or r is None or p <= 0 or r <= 0:
        return None
    return (r - p) / p * 100


def enrich_cell(
    cell: dict[str, Any],
    redeem_cell: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为条件过滤/评分统一补充字段, 返回新 dict, 不原地修改入参。

    写入字段:
    - redeem_price: 仅取 redeem_cell 的 redeem_price(到期赎回价);
      缺失记 None。即使原 cell 残留同名旧值也以本字段来源为准,
      防止 force_redeem_price 等异义字段混入计算。
    - simple_maturity_yield_pct: 由 cell.price 与上述 redeem_price
      经公共公式计算; 任一缺失为 None。

    抓取结果必须保持干净: 后续条件过滤、评分、DTO 都只读补充后的副本。
    """
    enriched = dict(cell)
    redeem_price = finite_number((redeem_cell or {}).get("redeem_price"))
    enriched["redeem_price"] = redeem_price
    enriched["simple_maturity_yield_pct"] = simple_maturity_yield_pct(
        cell.get("price"), redeem_price
    )
    return enriched
