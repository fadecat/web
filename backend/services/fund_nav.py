# -*- coding: utf-8 -*-
"""场外基金净值链路(组合实验室 P1, 数据源蛋卷 danjuanfunds.com)。

接口契约(2026-09-19 ECS 实测, docs/data-source-ecs-probe-report.md):
- 历史净值: GET /djapi/fund/nav/history/{code}?page=1&size=6000
  * size 可达 6000, 老基金全历史 1 个请求(100018: 5545 行/411KB/0.6s);
  * 无 Referer、免登录; 响应 data.items 按 nav_date **降序**(最新在前);
  * 字段: date / nav / percentage; ⚠ 成立首日无 percentage(必须 .get() 兜底);
    ⚠ value 恒等于 nav, 不是累计净值, 一律忽略;
  * total_items > size 时按 total_pages 翻页(尚无实例, 代码须支持)。
- 基金详情: GET /djapi/fund/{code} → fd_name / type_desc / manager_name / found_date
  ⚠ 对场内 ETF 不可用("该基金暂不销售"), 调用方必须容错。

口径设计(docs/portfolio-lab-data-maintenance.md §七):
- unit_nav + daily_return_pct 是不可变事实; adj_nav 是 daily_return_pct 链式推出的
  派生值(首日=unit_nav, 之后 prev_adj×(1+pct/100)), 每次全量重算覆盖写。
- 分红/份额折算只改 unit_nav 不改 daily_return_pct → 以 pct 为准历史自洽。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.models.research import FundNavDaily

logger = logging.getLogger(__name__)

# 蛋卷单请求行数上限(实测 6000 可用)
DANJUAN_PAGE_SIZE = 6000
# 该 Provider 在 research_security.source / fund_nav_daily.source 上的取值
DANJUAN_SOURCE = "danjuan"
# 防御性翻页上限(尚无超 6000 行的实例; 5 页 = 3 万行, 足够全历史)
_DANJUAN_MAX_PAGES = 5

_HEADERS = {"User-Agent": "Mozilla/5.0"}

NavHistoryFetchFn = Callable[[str, int, int], dict]
FundDetailFetchFn = Callable[[str], dict]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class FundNavRow:
    """单日净值事实 + 链式派生的复权净值。"""

    nav_date: date
    unit_nav: float
    daily_return_pct: float | None  # 成立首日为 None
    adj_nav: float = 0.0


def _default_nav_history_fetch(code: str, size: int, page: int) -> dict:
    import requests  # noqa: PLC0415

    url = f"https://danjuanfunds.com/djapi/fund/nav/history/{code}"
    response = requests.get(url, timeout=20, headers=_HEADERS, params={"page": page, "size": size})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"danjuan nav/history 响应非对象: {str(payload)[:80]!r}")
    return payload


def _default_fund_detail_fetch(code: str) -> dict:
    import requests  # noqa: PLC0415

    url = f"https://danjuanfunds.com/djapi/fund/{code}"
    response = requests.get(url, timeout=15, headers=_HEADERS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"danjuan fund 详情响应非对象: {str(payload)[:80]!r}")
    return payload


def _parse_percentage(raw: Any) -> float | None:
    """percentage → float(%); 空值/空串返回 None(成立首日合法缺失)。"""
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_nav_history(payload: dict) -> list[dict[str, Any]]:
    """蛋卷 nav/history 响应 → 升序净值事实列表 [{nav_date, unit_nav, daily_return_pct}]。

    只认 data.items 数组; 其余结构一律报错(不静默吞)。
    """
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        raise ValueError("danjuan nav/history 响应缺少 data 对象")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("danjuan nav/history 响应缺少 data.items 数组")
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"invalid nav item: {item!r}")
        raw_date = str(item.get("date") or "").strip()
        try:
            nav_date = date.fromisoformat(raw_date)
        except ValueError:
            raise ValueError(f"invalid nav date: {raw_date!r}") from None
        raw_nav = item.get("nav")
        try:
            unit_nav = float(raw_nav)
        except (TypeError, ValueError):
            raise ValueError(f"invalid nav value: {raw_nav!r} @ {raw_date}") from None
        rows.append({
            "nav_date": nav_date,
            "unit_nav": unit_nav,
            # ⚠ 成立首日无 percentage; value 恒等于 nav 不使用
            "daily_return_pct": _parse_percentage(item.get("percentage")),
        })
    rows.sort(key=lambda row: row["nav_date"])
    return rows


def chain_adj_nav(rows: list[dict[str, Any]]) -> list[FundNavRow]:
    """净值事实 → 链式分红再投复权净值(升序)。

    仅首行(链式起点)取 adj_nav = unit_nav; 之后的行
    adj_nav = prev_adj × (1 + pct/100)。
    ⚠ 中间行 pct 缺失按 0 处理(蛋卷仅成立首日缺失, 防御性容忍)——
    绝不能重置为 unit_nav, 否则一次缺失就掐断整条复权链。
    """
    chained: list[FundNavRow] = []
    prev_adj: float | None = None
    for row in rows:
        if prev_adj is None:
            adj = row["unit_nav"]
        else:
            pct = row["daily_return_pct"] or 0.0
            adj = prev_adj * (1.0 + pct / 100.0)
        chained.append(FundNavRow(
            nav_date=row["nav_date"],
            unit_nav=row["unit_nav"],
            daily_return_pct=row["daily_return_pct"],
            adj_nav=adj,
        ))
        prev_adj = adj
    return chained


def fetch_nav_history(
    code: str,
    *,
    fetch_fn: NavHistoryFetchFn | None = None,
    page_size: int = DANJUAN_PAGE_SIZE,
) -> list[FundNavRow]:
    """拉全历史净值并链式复权。构造器注入 fetch_fn 供测试隔离(测试禁止触网)。"""
    fetch = fetch_fn or _default_nav_history_fetch
    rows: list[dict[str, Any]] = []
    page = 1
    while page <= _DANJUAN_MAX_PAGES:
        payload = fetch(code, page_size, page)
        rows.extend(parse_nav_history(payload))
        data = payload.get("data") or {}
        total_items = int(data.get("total_items") or 0)
        if total_items <= page * page_size or not data.get("items"):
            break
        page += 1
    if not rows:
        raise ValueError(f"danjuan nav/history 无数据: {code}")
    # 跨页合并后按日期去重(同日取后拉的页, 即更新的观察)
    merged = {row["nav_date"]: row for row in rows}
    return chain_adj_nav([merged[key] for key in sorted(merged)])


def parse_fund_detail(payload: dict) -> dict[str, Any]:
    """蛋卷 /djapi/fund/{code} 响应 → {name, type_desc, found_date}。

    ⚠ 对场内 ETF 该接口返回错误体 → 由调用方 try/except 容错。
    """
    data = (payload or {}).get("data")
    if not isinstance(data, dict) or not data:
        raise ValueError("danjuan fund 详情响应缺少 data")
    return {
        "name": str(data.get("fd_name") or "").strip() or None,
        "type_desc": str(data.get("type_desc") or "").strip() or None,
        "found_date": str(data.get("found_date") or "").strip() or None,
    }


def fetch_fund_detail(code: str, *, fetch_fn: FundDetailFetchFn | None = None) -> dict[str, Any]:
    """场外基金详情(名称/类型/成立日); 失败向上抛, 由调用方决定降级。"""
    fetch = fetch_fn or _default_fund_detail_fetch
    return parse_fund_detail(fetch(code))


def upsert_fund_nav(
    db: Session, symbol: str, rows: list[FundNavRow], *, source: str = "danjuan",
) -> dict[str, int]:
    """幂等覆盖写 fund_nav_daily; 返回 {total(库里现有行数), rows_written(本次写入行数)}。

    SQLite 方言 INSERT ... ON CONFLICT (symbol, nav_date) DO UPDATE;
    adj_nav 为派生值, 每次随事实一起全量覆盖, 不依赖增量递推。
    """
    now = _utcnow()
    for row in rows:
        stmt = sqlite_insert(FundNavDaily).values(
            symbol=symbol, nav_date=row.nav_date, unit_nav=row.unit_nav,
            daily_return_pct=row.daily_return_pct, adj_nav=row.adj_nav,
            source=source, created_at=now, updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[FundNavDaily.symbol, FundNavDaily.nav_date],
            set_={
                "unit_nav": stmt.excluded.unit_nav,
                "daily_return_pct": stmt.excluded.daily_return_pct,
                "adj_nav": stmt.excluded.adj_nav,
                "source": stmt.excluded.source,
                "updated_at": now,
            },
        )
        db.execute(stmt)
    db.commit()
    total = db.scalar(
        select(func.count()).select_from(FundNavDaily).where(FundNavDaily.symbol == symbol),
    ) or 0
    return {"total": int(total), "rows_written": len(rows)}


def fund_nav_stats(db: Session, symbol: str) -> tuple[int, str | None, str | None]:
    """(行数, 最早净值日, 最晚净值日); 未入库返回 (0, None, None)。"""
    row = db.execute(
        select(
            func.count(),
            func.min(FundNavDaily.nav_date),
            func.max(FundNavDaily.nav_date),
        ).where(FundNavDaily.symbol == symbol)
    ).one()
    count = int(row[0] or 0)
    if count == 0:
        return 0, None, None
    return count, row[1].isoformat(), row[2].isoformat()
