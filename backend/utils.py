# -*- coding: utf-8 -*-
"""通用工具函数(解析、格式化、配置加载、交易日判断等)。

从旧 market-daily 提取并精简,移除 pandas 依赖改用标准库。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from backend.config import CONFIG_DIR


# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------

def parse_float(value: object) -> float | None:
    """宽松解析数值: 去逗号/去空白, '-' / 空串返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------

def parse_optional_date(value: object) -> date | None:
    """宽松解析日期,返回 date 对象;无法解析返回 None。

    支持 ISO 格式(2024-01-15 / 2024-01-15T00:00:00)及纯数字(20240115)。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None

    # 纯数字: 20240115 -> 2024-01-15
    if re.match(r"^\d{8}$", text):
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None

    # ISO 格式 / 带时间
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.date()
    except ValueError:
        pass

    # 尝试 fromisoformat 只取日期部分
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 指数代码提取
# ---------------------------------------------------------------------------

def extract_index_digits(code: str) -> str:
    """从任意格式(930955 / sh930955 / 930955.SH 等)提取 6 位数字代码。"""
    raw = code.strip().lower()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


# ---------------------------------------------------------------------------
# 通用 HTTP 工具
# ---------------------------------------------------------------------------

DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
}

# 易方达 CDN 对默认 python-requests UA 易限流,统一带浏览器头
DEFAULT_TIMEOUT: float = 15.0
DEFAULT_RETRIES: int = 3


# ---------------------------------------------------------------------------
# YAML 配置加载
# ---------------------------------------------------------------------------

def load_yaml_config(filename: str, config_dir: Path | None = None) -> dict[str, Any]:
    """加载 YAML 配置文件。

    参数:
        filename: 文件名(如 "valuation.yaml")
        config_dir: 配置目录,默认使用 CONFIG_DIR

    返回: 解析后的 dict;文件为空返回 {}。
    """
    directory = config_dir or CONFIG_DIR
    path = directory / filename
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def load_valuation_targets(config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """加载估值板块标的配置(config/valuation.yaml)。

    仅保留 type=valuation 的标的,与旧 load_valuation_config 行为一致。

    返回: targets 列表,每项含 name / code / index_detail_url 等字段。
    """
    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = load_yaml_config("valuation.yaml")

    targets = data.get("targets") or []
    targets = [t for t in targets if isinstance(t, dict) and t.get("type") == "valuation"]
    if not targets:
        raise ValueError("valuation 配置无 type=valuation 标的")
    return targets


# ---------------------------------------------------------------------------
# 交易日判断
# ---------------------------------------------------------------------------

# A 股固定节假日(月日格式,不随年份变化的部分)。
# 节假日安排每年由国务院发布,通常在前一年11月公布。
# 此处维护 2025-2027 年节假日;后续年份需更新。
# 格式: "MM-DD" 或 "YYYY-MM-DD"(用于跨年明确的节日)。
#
# 注意: 这只是粗筛,精确判断应接入交易所交易日历 API。
# 当前设计: 周末一定非交易日,节假日查表,其余视为交易日。

# 2025 年 A 股休市日(国务院公布)
_HOLIDAYS_2025: set[str] = {
    "2025-01-01",                          # 元旦
    "2025-01-28", "2025-01-29", "2025-01-30",  # 春节假
    "2025-01-31", "2025-02-01", "2025-02-02",
    "2025-02-03", "2025-02-04",              # 春节补休
    "2025-04-04", "2025-04-05", "2025-04-06",  # 清明
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",  # 劳动节
    "2025-05-31", "2025-06-01", "2025-06-02",  # 端午
    "2025-10-01", "2025-10-02", "2025-10-03",  # 国庆
    "2025-10-04", "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",  # 国庆+中秋
}

# 2026 年 A 股休市日(根据 2025 年 11 月公布的安排)
_HOLIDAYS_2026: set[str] = {
    "2026-01-01", "2026-01-02", "2026-01-03",  # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18",  # 春节
    "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22",  # 春节
    "2026-02-23",                              # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",  # 清明
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",  # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",  # 端午
    "2026-09-25", "2026-09-26", "2026-09-27",  # 中秋
    "2026-10-01", "2026-10-02", "2026-10-03",  # 国庆
    "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",  # 国庆
}

# 2027 年(预计,尚未公布,先按 2026 模式预留)
_HOLIDAYS_2027: set[str] = set()  # 待公布后补充

_ALL_HOLIDAYS: dict[int, set[str]] = {
    2025: _HOLIDAYS_2025,
    2026: _HOLIDAYS_2026,
    2027: _HOLIDAYS_2027,
}


def is_trading_day(d: date | None = None) -> bool:
    """判断给定日期是否为 A 股交易日。

    规则:
    1. 周六/周日 → 非交易日
    2. 在节假日表中 → 非交易日
    3. 其他 → 交易日

    参数:
        d: 日期,默认今天(北京时间)

    注意: 节假日表为手工维护,可能不完整。精确判断应接入交易所日历 API。
    """
    if d is None:
        d = date.today()

    # 周末
    if d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False

    # 节假日
    holidays = _ALL_HOLIDAYS.get(d.year)
    if holidays and d.isoformat() in holidays:
        return False

    return True


def latest_trading_day(d: date | None = None) -> date:
    """获取最近的交易日(含当天)。

    如果今天是交易日,返回今天;否则往前找最近的交易日。
    用于日频数据抓取: 收盘后跑时,latest_trading_day() 就是当天。
    """
    if d is None:
        d = date.today()

    # 最多往前找 30 天(覆盖最长假期)
    for i in range(31):
        check = d - timedelta(days=i)
        if is_trading_day(check):
            return check

    # 理论上不会走到这里
    raise ValueError(f"最近 30 天内无交易日(从 {d} 起)")

