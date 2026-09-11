"""Read-only catalog adapter. All publication deadlines are provisional, not SLA.
Legacy configuration stays authoritative for instruments; no historical key rewrite.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from backend.utils import is_trading_day, latest_trading_day, load_valuation_targets, load_index_eod_targets

@dataclass(frozen=True)
class Policy:
    source: str
    job_id: str
    hour: int
    minute: int = 0
    trading_day_offset: int = 0

    def due_at(self, trade_date):
        day = trade_date
        for _ in range(self.trading_day_offset):
            day += timedelta(days=1)
            while not is_trading_day(day):
                day += timedelta(days=1)
        return datetime.combine(day, time(self.hour, self.minute))

    def expected(self, now=None):
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.tzinfo:
            now = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        day = latest_trading_day(now.date())
        while self.due_at(day) > now:
            day = latest_trading_day(day - timedelta(days=1))
        next_day = day + timedelta(days=1)
        while not is_trading_day(next_day):
            next_day += timedelta(days=1)
        return day, self.due_at(next_day)


def catalog():
    valuation = {str(t["code"]): t.get("name", t["code"]) for t in load_valuation_targets()}
    eod = {str(t["code"]): t.get("name", t["code"]) for t in load_index_eod_targets()}
    from backend.services.fetchers.style_rotation import LEFT_SYMBOL, LEFT_NAME, RIGHT_SYMBOL, RIGHT_NAME
    tencent = {LEFT_SYMBOL: LEFT_NAME, RIGHT_SYMBOL: RIGHT_NAME}
    pe = Policy("efunds", "valuation_daily", 12, trading_day_offset=1)
    dy = Policy("efunds", "valuation_daily", 12, trading_day_offset=1)
    close = Policy("efunds", "index_eod_daily", 23)
    tx = Policy("tencent", "style_rotation_daily", 16, 30)
    return {
        "指数估值(PE/PB)": {c: (n, pe) for c, n in valuation.items()},
        "指数股息率": {c: (n, dy) for c, n in valuation.items()},
        "指数日线(K线)": {**{c: (n, close) for c, n in eod.items()}, **{c: (n, tx) for c, n in tencent.items()}},
        "国债收益率": {"": ("10Y国债(含2/5/30Y)", Policy("eastmoney", "valuation_daily", 23))},
        "转债全量快照": {"": ("转债全量快照", Policy("jisilu", "cb_list_daily", 15, 30))},
        "强赎列表": {"": ("强赎列表", Policy("jisilu", "cb_redeem_daily", 15, 30))},
        "转债等权指数": {"": ("转债等权指数", Policy("jisilu", "cb_index_daily", 15, 30))},
        "高股息股票快照": {"": ("高股息股票快照", Policy("jisilu", "stock_dividend_daily", 15, 30))},
    }


def apply_catalog(groups, freshness, now=None):
    definitions = catalog()
    for group in groups:
        entries = definitions.get(group["name"], {})
        found = {}
        legacy = []
        for entity in group["entities"]:
            code = entity["label"].rsplit(" ", 1)[-1] if "" not in entries else ""
            if code in entries:
                found[code] = entity
            else:
                legacy.append({**entity, "managed": False})
        entities = []
        for code, (name, policy) in entries.items():
            entity = found.get(code, {"label": f"{name} {code}".strip(), "latest_date": None,
                                     "first_date": None, "count": 0, "unit": "条"})
            expected, next_due = policy.expected(now)
            from datetime import date
            latest = date.fromisoformat(entity["latest_date"]) if entity["latest_date"] else None
            entity.update(index_code=code, managed=True, source=policy.source, job_id=policy.job_id,
                          expected_date=expected.isoformat(), next_due_at=next_due.isoformat(),
                          policy_provisional=True, state=freshness(latest, expected))
            entities.append(entity)
        group["entities"] = entities
        group["unmanaged_entities"] = legacy
        priority = {"fresh": 0, "stale": 1, "lagging": 2, "no_data": 3}
        group["state"] = max((e["state"] for e in entities), key=lambda s: priority[s], default="no_data")
    return groups
