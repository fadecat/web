"""Normalize Jisilu redeem-list fields into stable business semantics."""
from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def normalize_redeem_state(cell: dict[str, Any] | None) -> dict[str, Any]:
    """Return one normalized state for live and DB redeem cells.

    ``redeem_icon`` is retained only as source evidence.  Announcement state
    is determined from flag/date fields first, so R+X (near maturity) cannot
    be mistaken for R+Y (announced redemption).
    """
    cell = cell or {}
    icon = str(cell.get("redeem_icon") or "").strip().upper()
    flag = str(cell.get("redeem_flag") or "").strip().upper()
    remain = _int(cell.get("redeem_remain_days"))
    state: str
    if flag == "Y":
        state = "ANNOUNCED_REDEEM"
    elif icon == "R" and cell.get("delist_dt") and not cell.get("redeem_dt"):
        state = "NEAR_MATURITY"
    elif icon == "B":
        state = "TRIGGER_MET"
    elif icon == "O":
        state = "ANNOUNCED_INTENT"
    elif icon == "G" or flag == "N":
        state = "NO_REDEEM_ANNOUNCED"
    elif remain is not None and remain >= 0:
        state = "TRIGGER_COUNTING"
    else:
        state = "MONITORING"
    labels = {
        "ANNOUNCED_REDEEM": "已公告强赎",
        "NEAR_MATURITY": "临近到期",
        "TRIGGER_MET": "已满足强赎条件",
        "ANNOUNCED_INTENT": "公告拟强赎",
        "NO_REDEEM_ANNOUNCED": "公告不强赎",
        "TRIGGER_COUNTING": "强赎计数中",
        "MONITORING": "",
    }
    return {
        "status_code": state,
        "status_label": labels[state],
        "trigger_days_remaining": remain if remain is not None and remain >= 0 else None,
        "trigger_days_met": _int(cell.get("redeem_real_days")),
        "trigger_days_required": _int(cell.get("redeem_count_days")),
        "trigger_window_days": _int(cell.get("redeem_total_days")),
        "redeem_date": cell.get("redeem_dt"),
        "last_trade_date": cell.get("delist_dt"),
        "maturity_redeem_price": _number(cell.get("redeem_price")),
        "announced_redeem_price": _number(cell.get("real_force_redeem_price")),
        "source_redeem_icon": icon or None,
        "source_redeem_flag": flag or None,
    }

