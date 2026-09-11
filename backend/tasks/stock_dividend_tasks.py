# -*- coding: utf-8 -*-
"""股票高股息日频快照任务: 集思录 dividend_rate_list → stock_dividend_daily 表。

调度: 交易日 15:08 CST(紧跟转债任务之后)。
也可手动调用: python -m backend.tasks.stock_dividend_tasks

节假日双重闸门(设计 §5):
1. 前置闸门(省请求): is_trading_day 判非交易日直接跳过, 零请求;
2. 数据闸门(安全网): 抓取完成后多数 last_dt ≠ 今天(东八区) → 跳过落库,
   防止节假日表缺漏时用旧数据覆盖(最坏浪费一轮 ~38 请求)。

行业树缓存(月度)与漂移重试归本层: fetcher 是纯函数, 不写 data/state。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from backend.config import DATA_DIR
from backend.models.database import SessionLocal
from backend.services.fetchers.stock_dividend import (
    fetch_dividend_snapshot,
    fetch_industry_tree,
)
from backend.services.jisilu import get_cookie
from backend.services.stock_dividend_store import save_stock_dividend_snapshot
from backend.utils import is_trading_day

# 行业树缓存: 节点列表 + fetched_at(路径可注入便于测试)
TREE_CACHE_FILE = DATA_DIR / "state" / "stock_dividend_industry_tree.json"
TREE_MAX_AGE_DAYS = 30

_CST = ZoneInfo("Asia/Shanghai")


def _load_tree_cache(cache_path: Path) -> list[dict] | None:
    """读取树缓存; 缺失/超 30 天/内容异常 → None(调用方重取)。"""
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(str(payload.get("fetched_at") or ""))
        nodes = payload.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            return None
        if (datetime.now(_CST) - fetched_at).days > TREE_MAX_AGE_DAYS:
            return None
        return nodes
    except (OSError, ValueError, TypeError):
        return None


def _save_tree_cache(nodes: list[dict], cache_path: Path) -> None:
    """树缓存落盘(月度刷新, 日常轮次零页面依赖)。"""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "nodes": nodes,
        "fetched_at": datetime.now(_CST).isoformat(timespec="seconds"),
    }
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _get_tree(cookie: str, cache_path: Path) -> list[dict]:
    """缓存优先取行业树; 缺失或过期才抓页面并写缓存。"""
    tree = _load_tree_cache(cache_path)
    if tree:
        logger.info(f"复用行业树缓存({len(tree)} 节点): {cache_path}")
        return tree
    logger.info("行业树缓存缺失或超过 30 天, 重新抓取页面")
    tree = fetch_industry_tree(cookie)
    _save_tree_cache(tree, cache_path)
    return tree


def run_stock_dividend_daily(cache_path: Path | None = None) -> dict:
    """股票高股息日频快照任务。

    1. 交易日双重闸门(前置: 非交易日零请求跳过)
    2. 集思录登录(会话复用) → 行业树(月度缓存) → 自适应覆盖抓取
    3. 树漂移时重取树整轮重跑一次(防循环); 数据闸门通过后按日追加落库
    """
    cache_path = cache_path or TREE_CACHE_FILE
    today = date.today()

    # ---- 闸门①: 非交易日直接跳过(零请求) ----
    if not is_trading_day(today):
        logger.info(f"非交易日({today}),跳过高股息股票快照任务")
        return {"status": "skipped", "success_count": 0, "fail_count": 0}

    logger.info(f"=== 高股息股票快照任务开始 ({today}) ===")

    try:
        cookie = get_cookie()
        tree = _get_tree(cookie, cache_path)

        snapshot = fetch_dividend_snapshot(cookie, tree)
        meta = snapshot["meta"]

        # ---- 漂移自愈①: 重取树并整轮重跑一次(单轮最多一次, 防循环) ----
        if meta["tree_drift"]:
            logger.warning(f"行业树漂移({meta['warnings']}), 重取树后整轮重跑一次")
            tree = fetch_industry_tree(cookie)
            _save_tree_cache(tree, cache_path)
            snapshot = fetch_dividend_snapshot(cookie, tree)
            meta = snapshot["meta"]
            if meta["tree_drift"]:
                logger.warning("重跑后仍漂移, 照常落库(warnings 已记录)")

        for warning in meta["warnings"]:
            logger.warning(f"抓取警告: {warning}")

        rows = snapshot["rows"]
        # 全市场 ≥200 亿不可能为空, 空返回 = 会话失效/接口变更, 判失败
        if not rows:
            raise ValueError("高股息快照接口返回空数据")

        # ---- 闸门②: 多数 last_dt ≠ 今天(东八区) → 跳过落库 ----
        trade_date = meta.get("trade_date")
        today_cst = datetime.now(_CST).date().isoformat()
        if trade_date != today_cst:
            logger.warning(
                f"数据闸门: 多数 last_dt={trade_date} ≠ 今天({today_cst}), "
                "判定节假日表缺漏或数据未更新, 跳过落库"
            )
            return {"status": "skipped", "success_count": 0, "fail_count": 0}

        db = SessionLocal()
        try:
            inserted = save_stock_dividend_snapshot(
                db, rows, date.fromisoformat(trade_date)
            )
            logger.info(f"落库完成: {inserted} 条新写入 (共 {len(rows)} 只)")
        except Exception as exc:
            db.rollback()
            logger.error(f"落库失败: {exc}")
            return {"success_count": 0, "fail_count": 1}
        finally:
            db.close()
    except Exception as exc:
        logger.error(f"高股息股票快照任务失败: {exc}")
        return {"success_count": 0, "fail_count": 1}

    logger.info("=== 高股息股票快照任务完成 ===")
    return {"success_count": 1, "fail_count": 0}


if __name__ == "__main__":
    run_stock_dividend_daily()
