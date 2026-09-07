# -*- coding: utf-8 -*-
"""数据状态服务: 数据新鲜度 + 任务运行记录。

新鲜度不单独建表,直接查各业务表的 max(trade_date) —— 数据表本身就是事实来源。
滞后量按「交易日」计,避免节假日误报(春节 8 天假期≠数据坏了 8 天)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.data_status import TaskRunLog
from backend.models.valuation import (
    CbDailySnapshot,
    CbIndexDaily,
    CbRedeemDaily,
    CnBondYield,
    IndexDailyQuote,
    IndexDividendYield,
    IndexValuationSnapshot,
)
from backend.utils import INDEX_DISPLAY_NAMES, is_trading_day, latest_trading_day

# 数据新鲜度判定: 0 个交易日滞后 = 新鲜, <=5 = 滞后, >5 = 异常
_FRESH_MAX_LAG = 0
_STALE_MAX_LAG = 5

# 已接入监控的定时任务(job_id → 展示信息)
JOBS: dict[str, dict[str, str]] = {
    "cb_redeem_daily": {"name": "强赎列表", "schedule": "交易日 15:03"},
    "cb_index_daily": {"name": "转债等权指数", "schedule": "交易日 15:04"},
    "cb_list_daily": {"name": "转债全量快照", "schedule": "交易日 15:06"},
    "style_rotation_daily": {"name": "风格轮动日线", "schedule": "交易日 22:03"},
    "valuation_daily": {"name": "估值截面(易方达分位/股息率 + 东财国债)", "schedule": "交易日 22:06"},
    "index_eod_daily": {"name": "指数日线(易方达·轮动K线)", "schedule": "交易日 22:09"},
}

# 成功率统计窗口(最近 N 次运行)
_SUCCESS_RATE_WINDOW = 30


def _trading_days_between(start: date, end: date) -> int:
    """start(含)到 end(含)之间的交易日天数。start > end 时返回 0。"""
    if start > end:
        return 0
    count = 0
    cur = start
    while cur <= end:
        if is_trading_day(cur):
            count += 1
        cur = date.fromordinal(cur.toordinal() + 1)
    return count


def _expected_date() -> date:
    """数据「应该」更新的日期。

    今天是交易日且已过 15:00(最早的任务 15:03 跑)→ 预期今天有数据;
    交易日上午任务还没轮到跑,预期仍是上一交易日,避免满屏黄灯误报;
    非交易日 → 最近一个交易日(周末/节假日数据停更是正常的)。
    """
    today = date.today()
    if is_trading_day(today) and datetime.now().hour >= 15:
        return today
    return latest_trading_day(today - timedelta(days=1))


def _freshness_state(latest: date | None, expected: date) -> str:
    if latest is None:
        return "no_data"
    lag = _trading_days_between(latest, expected) - 1
    if lag <= _FRESH_MAX_LAG:
        return "fresh"
    if lag <= _STALE_MAX_LAG:
        return "stale"
    return "lagging"


def get_dataset_freshness(db: Session) -> list[dict]:
    """分组返回数据新鲜度,每组展开到具体指数/表的逐实体明细。

    返回结构:
        [{name, state, entities: [{label, latest_date, first_date, count, unit, state}]}]

    - 指数类数据(估值/股息率/K线)逐只指数一行, 带条目数与起止日期
    - 转债类表(快照/强赎)条目按「天数」计(每天几百只债, 行数无意义)
    - group state = 组内最差实体状态
    - expected = _expected_date(): 交易日为今天,非交易日为最近一个交易日
      (周末/节假日数据不更新是正常的,不应算滞后)。
    """
    expected = _expected_date()

    def make_entity(label, latest, first=None, count=None, unit="条"):
        return {
            "label": label,
            "latest_date": latest.isoformat() if latest else None,
            "first_date": first.isoformat() if first else None,
            "count": count,
            "unit": unit,
            "state": _freshness_state(latest, expected),
        }

    def group(name, entities):
        order = {"lagging": 3, "stale": 2, "fresh": 1}
        worst = "no_data"
        for e in entities:
            if worst == "no_data" or order.get(e["state"], 0) > order.get(worst, -1):
                worst = e["state"] if e["state"] != "no_data" else worst
        return {"name": name, "state": worst, "entities": entities}

    # 指数名称: 估值/股息率表内自带, 日线表用共享映射, 兜底用代码
    name_map = {
        code: name
        for code, name in db.execute(
            select(IndexValuationSnapshot.index_code, IndexValuationSnapshot.index_name)
            .distinct()
        ).all()
    }
    name_map.update(INDEX_DISPLAY_NAMES)

    groups: list[dict] = []

    # 1) 指数估值快照(PE/PB/PS) —— 逐只指数
    rows = db.execute(
        select(
            IndexValuationSnapshot.index_code,
            func.max(IndexValuationSnapshot.trade_date),
            func.min(IndexValuationSnapshot.trade_date),
            func.count(),
        ).group_by(IndexValuationSnapshot.index_code)
    ).all()
    groups.append(
        group(
            "指数估值(PE/PB)",
            [
                make_entity(
                    f"{name_map.get(code, code)} {code}",
                    latest,
                    first,
                    count,
                )
                for code, latest, first, count in sorted(rows)
            ],
        )
    )

    # 2) 指数股息率 —— 逐只指数
    rows = db.execute(
        select(
            IndexDividendYield.index_code,
            func.max(IndexDividendYield.trade_date),
            func.min(IndexDividendYield.trade_date),
            func.count(),
        ).group_by(IndexDividendYield.index_code)
    ).all()
    groups.append(
        group(
            "指数股息率",
            [
                make_entity(f"{name_map.get(code, code)} {code}", latest, first, count)
                for code, latest, first, count in sorted(rows)
            ],
        )
    )

    # 3) 指数日线(K线) —— 逐只指数(轮动 4 只共用一表)
    rows = db.execute(
        select(
            IndexDailyQuote.index_code,
            func.max(IndexDailyQuote.trade_date),
            func.min(IndexDailyQuote.trade_date),
            func.count(),
        ).group_by(IndexDailyQuote.index_code)
    ).all()
    groups.append(
        group(
            "指数日线(K线)",
            [
                make_entity(f"{name_map.get(code, code)} {code}", latest, first, count)
                for code, latest, first, count in sorted(rows)
            ],
        )
    )

    # 4) 国债收益率 —— 单实体
    latest, first, count = db.execute(
        select(func.max(CnBondYield.trade_date), func.min(CnBondYield.trade_date), func.count())
    ).one()
    groups.append(
        group(
            "国债收益率",
            [make_entity("10Y国债(含2/5/30Y)", latest, first, count)],
        )
    )

    # 5-7) 转债类表 —— 单实体, 条目按「天数」计
    for name, model in (
        ("转债全量快照", CbDailySnapshot),
        ("强赎列表", CbRedeemDaily),
        ("转债等权指数", CbIndexDaily),
    ):
        latest, first, days = db.execute(
            select(
                func.max(model.trade_date),
                func.min(model.trade_date),
                func.count(func.distinct(model.trade_date)),
            )
        ).one()
        groups.append(
            group(name, [make_entity(name, latest, first, days, unit="天")])
        )

    return groups


def get_job_runs(db: Session) -> list[dict]:
    """逐任务返回最近一次运行记录 + 最近 N 次成功率。

    未运行过(如刚部署)返回 status=never。
    """
    result = []
    for job_id, meta in JOBS.items():
        latest = db.execute(
            select(TaskRunLog)
            .where(TaskRunLog.job_id == job_id)
            .order_by(TaskRunLog.started_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        window = db.execute(
            select(TaskRunLog.status).where(
                TaskRunLog.job_id == job_id,
                TaskRunLog.status.in_(["success", "partial", "failed"]),
            ).order_by(TaskRunLog.started_at.desc()).limit(_SUCCESS_RATE_WINDOW)
        ).scalars().all()
        ok_count = sum(1 for s in window if s == "success")
        partial_count = sum(1 for s in window if s == "partial")
        success_rate = round(ok_count / len(window), 4) if window else None

        if latest is None:
                result.append(
                    {
                        "job_id": job_id,
                        "name": meta["name"],
                        "schedule": meta["schedule"],
                        "status": "never",
                        "started_at": None,
                        "finished_at": None,
                        "duration_sec": None,
                        "summary": None,
                        "error": None,
                        "success_rate": success_rate,
                        "run_count": len(window),
                        "next_run_at": None,
                    }
                )
                continue

        result.append(
            {
                "job_id": job_id,
                "name": meta["name"],
                "schedule": meta["schedule"],
                "status": latest.status,
                "started_at": latest.started_at.isoformat(timespec="seconds") if latest.started_at else None,
                "finished_at": latest.finished_at.isoformat(timespec="seconds") if latest.finished_at else None,
                "duration_sec": latest.duration_sec,
                "summary": latest.summary,
                "error": latest.error,
                "success_rate": success_rate,
                "run_count": len(window),
                "next_run_at": None,
            }
        )

    # 下次计划时间: 从 registry 的调度定义换算(单一事实源, 状态页不再手抄时刻)
    next_map = _next_run_times(datetime.now())
    for item in result:
        item["next_run_at"] = next_map.get(item["job_id"])
    return result


def _next_run_times(now: datetime) -> dict[str, str]:
    """按 registry 调度时刻计算各任务下一次触发时间(本地 ISO)。

    今天已过触发时刻或非交易日 → 顺延到下一交易日(与调度器 mon-fri 语义一致,
    节假日误差可容忍: 至少不早于下一个工作日)。
    """
    from datetime import timedelta

    from backend.tasks.registry import DAILY_JOBS

    out: dict[str, str] = {}
    for job_id, _func, _name, hour, minute in DAILY_JOBS:
        day = now.date()
        candidate = datetime.combine(day, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        if candidate <= now or not is_trading_day(day):
            candidate += timedelta(days=1)
            while candidate <= now or not is_trading_day(candidate.date()):
                candidate += timedelta(days=1)
        out[job_id] = candidate.isoformat(timespec="seconds")
    return out


def build_data_status(db: Session) -> dict:
    """状态页完整数据: 数据新鲜度 + 任务运行记录 + 生成时间。

    新鲜度按数据目录的来源规则(发布偏移/到期时刻)逐流判定;
    目录未纳管的历史遗留实体单列 unmanaged_entities, 不参与组状态。
    """
    from backend.services.data_catalog import apply_catalog

    datasets = apply_catalog(get_dataset_freshness(db), _freshness_state)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "expected_date": _expected_date().isoformat(),
        "datasets": datasets,
        "jobs": get_job_runs(db),
    }
