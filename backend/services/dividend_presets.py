# -*- coding: utf-8 -*-
"""高股息筛选预设配置读写(data/stock_dividend_presets.json)。

镜像 cb_factors 的落盘形态(原子替换 + version 字段), 但规模小得多:
单一 version=1, 无迁移链路。读路径缺文件/损坏一律回退内置默认预设
(market-daily 高股息邮件漏斗口径, 含默认勾选国资白名单), 不抛异常、
不写盘; 写路径为全量替换, 形状由 pydantic extra=forbid 保证。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.api.schemas.stock_dividend import DividendPresetsConfig

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PRESETS_PATH = DATA_DIR / "stock_dividend_presets.json"

# 内置默认预设 = market-daily 高股息邮件漏斗:
# PE≤15 / 股息率≥3 / PE温度≤40 / PB温度≤40 / 5年平均ROE≥5 / 总市值≥200亿,
# 最后一步仅保留国资白名单内标的(默认勾选国资)。
DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "active_id": "default",
    "presets": [
        {
            "id": "default",
            "name": "邮件口径",
            "form": {
                "markets": [],
                "industries": [],
                "excludeIndustries": [],
                "province": "",
                "peMax": 15,
                "peTMax": 40,
                "pbTMax": 40,
                "dividendMin": 3,
                "roeAverageMin": 5,
                "totalValueMin": 200,
                "soeOnly": True,
            },
        }
    ],
}


def load_presets() -> dict[str, Any]:
    """读取预设配置; 缺文件/损坏/形状不符回退默认配置(不写盘)。"""
    try:
        raw = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
        config = DividendPresetsConfig.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError):
        return copy.deepcopy(DEFAULT_CONFIG)
    return config.model_dump(mode="json")


def save_presets(config: dict[str, Any]) -> dict[str, Any]:
    """校验并全量替换写盘(原子替换), 返回规范化后的配置。"""
    normalized = DividendPresetsConfig.model_validate(config).model_dump(mode="json")
    payload = json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8")

    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(PRESETS_PATH.parent), prefix=".presets-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, PRESETS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return normalized
