# -*- coding: utf-8 -*-
"""组合实验室标的服务层(P0-2 + P1): 代码归一化 / 解析 / 注册 / 单标的同步 / 列表。

范围: 股票/ETF 走腾讯 raw+hfq 配对; **场外基金(FUND)走蛋卷净值**(P1 打通,
落 fund_nav_daily, 链式分红再投复权, 见 backend/services/fund_nav.py)。

三条硬约束(源于实测事实, 不是风格偏好):
1. **六位数字三义**: `000001` 同时可以是 平安银行(股票) / 华夏成长混合(场外基金) /
   上证指数(指数)。所以「裸六位 + 无 type_hint」必须把候选列出来交给调用方选,
   层里不替用户做决定; 候选上限 3(数据源克制: 每个候选最多 1 个请求)。
2. **数据源克制**: probe 对每个候选最多发 1 个 HTTP 请求, 且腾讯 fqkline 的 count
   必须 ≤ 640(实测更大会静默返回空数组, 不报错); sync 才按 640/页向后分页拉全。
   蛋券详情接口对场内 ETF 不可用 → FUND 候选解析失败必须降级, 不上抛。
3. **非法输入返回空列表而不是抛异常**(路由据此区分 422/404); 未命中就是未命中,
   不回填任何猜测值。

服务层不持有请求级会话: sync_one 每标的一个独立 Session(网络在事务外),
与 research_tasks.run_research_daily_sync 的范式一致。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.database import SessionLocal
from backend.models.research import ResearchDailyBarRaw, ResearchSecurity
from backend.services import fund_nav, research_store
from backend.services.market_data import AdjustMode, ResearchSourceError, provider_factory
from backend.services.market_data import _assert_symbol_type  # 私有但唯一的类型一致性断言源, 不复制一份
from backend.tasks import research_tasks
from backend.utils import load_research_settings

logger = logging.getLogger(__name__)

_SELECTION_LIST = "组合实验室"
# P0 只支持腾讯链路(东财 push2* 对 ECS IP 封禁, 见 config/research.yaml 换源决策)
_SOURCE_DEFAULT = "tencent"
_HISTORY_START_DEFAULT = "2013-01-01"
_MAX_CANDIDATES = 3
_MAX_SYNC_ERROR = 255
# probe 只需名称与最新交易日; count 上限 640, 取小值更快也够用
_PROBE_COUNT = 10
_FUND_NOTE = "场外基金链路 P1 实现"  # 兼容旧引用(测试断言文案); P1 已打通, 仅留作历史文案
_FUND_SOURCE = "danjuan"

STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

_PRICE_BASIS = {"STOCK": "HFQ", "ETF": "HFQ", "FUND": "NAV_ADJ"}

_PREFIX_RE = re.compile(r"^(SH|SZ)(\d{6})$")
_SUFFIX_RE = re.compile(r"^(\d{6})\.?(SH|SZ|OF)$")
_BARE_RE = re.compile(r"^\d{6}$")

_SH_FIRST = frozenset({"5", "6", "9"})
_SZ_FIRST = frozenset({"0", "1", "2", "3"})
# 交易所 → 首位 → 主候选类型(沪 5=ETF/6·9=股票; 深 1=ETF/0·2·3=股票)
_PRIMARY_TYPE: dict[str, dict[str, str]] = {
    "SH": {"5": "ETF", "6": "STOCK", "9": "STOCK"},
    "SZ": {"0": "STOCK", "1": "ETF", "2": "STOCK", "3": "STOCK"},
}
_TYPE_HINTS = {"stock": "STOCK", "etf": "ETF", "fund": "FUND"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 候选结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """一个可能的标的。
    
    symbol 用规范代码(带交易所后缀; 场外基金为 .OF), price_basis 显式标注三类口径,
    UI 可逐列展示; resolved 表示数据源是否真的查到了它。
    """

    symbol: str
    name: str | None
    security_type: str
    price_basis: str
    source: str
    resolved: bool
    latest_date: str | None
    registered: bool
    row_count: int | None
    first_date: str | None
    last_date: str | None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "security_type": self.security_type,
            "price_basis": self.price_basis,
            "source": self.source,
            "resolved": self.resolved,
            "latest_date": self.latest_date,
            "registered": self.registered,
            "row_count": self.row_count,
            "first_date": self.first_date,
            "last_date": self.last_date,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# 代码归一化
# ---------------------------------------------------------------------------

def _split_input(raw: str) -> tuple[str | None, str | None]:
    """接受的四种写法 → (六位码, 显式交易所 or None); 无法识别返回 (None, None)。

    接受: 600900 / sh600900 / 600900.SH / SH600900(大小写与内嵌空格不敏感)。
    """
    text = str(raw or "").strip().upper().replace(" ", "")
    matched = _SUFFIX_RE.match(text)
    if matched:
        return matched.group(1), matched.group(2)
    matched = _PREFIX_RE.match(text)
    if matched:
        return matched.group(2), matched.group(1)
    if _BARE_RE.match(text):
        return text, None
    return None, None


def _exchange_of(code: str, explicit: str | None) -> str | None:
    """首位推断交易所(5/6/9→SH, 0/1/2/3→SZ); 显式给了就用显式的; 其余不支持。"""
    if explicit:
        return explicit
    if code[0] in _SH_FIRST:
        return "SH"
    if code[0] in _SZ_FIRST:
        return "SZ"
    return None


def _types_for(code: str, exchange: str, type_hint: str | None, explicit_exchange: str | None = None) -> list[str]:
    """候选类型列表: 显式 hint 只返回该类型; 否则 主候选 + 场外基金(上限 3)。

    类型与代码矛盾时(如 510300 当 STOCK)返回空列表——组合不可能存在,
    继续请求只是白打一次数据源。一致性判断与数据源共用 `_assert_symbol_type`。
    """
    hint = str(type_hint or "").strip().lower()
    if hint:
        normalized = _TYPE_HINTS.get(hint)
        if normalized is None:
            return []
        if normalized == "FUND":
            return ["FUND"]
        try:
            _assert_symbol_type(f"{code}.{exchange}", code, normalized)
        except ResearchSourceError:
            return []
        return [normalized]
    primary = _PRIMARY_TYPE[exchange].get(code[0])
    types = [primary] if primary else []
    # 只在「未显式给交易所」时才并列场外基金候选: 显式 sh/sz 说明用户已指明场内标的,
    # 再列 .OF 只是噪音(六位数字三义冲突只发生在裸码场景)。
    if explicit_exchange is None:
        types.append("FUND")
    return types[:_MAX_CANDIDATES]


def normalize_code(raw: str, type_hint: str | None = None) -> list[Candidate]:
    """代码归一化 + 候选生成(不触网、不落库); 非法输入返回空列表。"""
    code, explicit_exchange = _split_input(raw)
    if code is None:
        return []
    exchange = _exchange_of(code, explicit_exchange)
    if exchange is None:
        return []  # 4/7/8 开头(北交所等) P0 不支持
    candidates: list[Candidate] = []
    seen: set[tuple[str, str]] = set()
    for security_type in _types_for(code, exchange, type_hint, explicit_exchange):
        if security_type == "FUND":
            symbol = f"{code}.OF"  # 与 docs/portfolio-lab-data-maintenance.md 的形态决策一致
        else:
            symbol = f"{code}.{exchange}"
        key = (symbol, security_type)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(Candidate(
            symbol=symbol,
            name=None,
            security_type=security_type,
            price_basis=_PRICE_BASIS[security_type],
            source=_SOURCE_DEFAULT,
            resolved=False,
            latest_date=None,
            registered=False,
            row_count=None,
            first_date=None,
            last_date=None,
        ))
    return candidates[:_MAX_CANDIDATES]


def describe_code_problem(raw: str) -> str | None:
    """入参是否连「代码写法」都不合法; None 表示写法合法(可能仍无候选)。

    路由据此区分 422(写法非法) 与 404(没找到该代码)。
    """
    code, _ = _split_input(raw)
    if code is None:
        return f"无法识别的代码: {raw!r}(支持 600900 / sh600900 / 600900.SH / SH600900)"
    if _exchange_of(code, None) is None:
        return f"首位 {code[0]} 不在支持的交易所范围(沪 5/6/9, 深 0/1/2/3): {raw!r}"
    return None


# ---------------------------------------------------------------------------
# probe: 只解析不落库
# ---------------------------------------------------------------------------

ProbeFetchFn = Callable[[str, int], tuple[str | None, str | None]]


def _default_probe_fetch(tencent_code: str, count: int) -> tuple[str | None, str | None]:
    """一次腾讯 fqkline 请求拿 (名称, 最新交易日); count ≤ 640 是硬上限。

    与 `_default_tencent_kline_fetch` 同源同 URL(count 参数在 >=640 时会被静默
    截断为空数组); 这里不复用它是因为 probe 还要取 `qt[1]` 名称。
    """
    import requests  # noqa: PLC0415

    param = f"{tencent_code},day,,,{min(int(count), 640)},"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
    response = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    payload = response.json() or {}
    data = (payload.get("data") or {}).get(tencent_code) or {}
    qt = data.get("qt") or {}
    name = str(qt[1]).strip() if len(qt) > 1 and qt[1] else None
    rows = data.get("day") or []
    latest = str(rows[-1][0])[:10] if rows else None
    return name, latest


def _local_stats(db: Session | None, symbol: str) -> tuple[bool, int | None, str | None, str | None]:
    """本库注册情况与已入库区间; 未注册一律 None(不用 0 冒充)。

    股票/ETF 读 research_daily_bar_raw; 场外基金(.OF)读 fund_nav_daily。
    """
    if db is None:
        return False, None, None, None
    exists = db.scalar(select(ResearchSecurity.id).where(ResearchSecurity.symbol == symbol))
    if exists is None:
        return False, None, None, None
    if str(symbol).strip().upper().endswith(".OF"):
        return (True, *fund_nav.fund_nav_stats(db, symbol))
    row_count, first_date, last_date = db.execute(
        select(
            func.count(ResearchDailyBarRaw.id),
            func.min(ResearchDailyBarRaw.trade_date),
            func.max(ResearchDailyBarRaw.trade_date),
        ).where(ResearchDailyBarRaw.symbol == symbol)
    ).one()
    return (
        True,
        int(row_count or 0),
        first_date.isoformat() if first_date else None,
        last_date.isoformat() if last_date else None,
    )


def probe(
    code: str,
    type_hint: str | None = None,
    *,
    db: Session | None = None,
    probe_fetch_fn: ProbeFetchFn | None = None,
    fund_detail_fetch_fn: fund_nav.FundDetailFetchFn | None = None,
) -> list[dict[str, Any]]:
    """解析代码 → 候选列表(每个候选最多 1 个 HTTP 请求), **不落库**。

    股票/ETF 走腾讯(名称+最新交易日); 场外基金走蛋卷详情(名称/类型/成立日,
    ⚠ 该接口对场内 ETF 不可用, 失败按未解析降级, 不上抛)。
    网络异常捕获为 resolved=False, 不上抛(路由不得因为单一数据源抖动变 500)。
    """
    candidates = normalize_code(code, type_hint)
    fetch = probe_fetch_fn or _default_probe_fetch
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        registered, row_count, first_date, last_date = _local_stats(db, candidate.symbol)
        base = {
            "registered": registered,
            "row_count": row_count,
            "first_date": first_date,
            "last_date": last_date,
        }
        if candidate.security_type == "FUND":
            fund_code = candidate.symbol[:6]
            name: str | None = None
            type_desc: str | None = None
            resolved = False
            try:
                detail = fund_nav.fetch_fund_detail(fund_code, fetch_fn=fund_detail_fetch_fn)
                name = detail.get("name")
                type_desc = detail.get("type_desc")
                resolved = bool(name)
            except Exception as exc:  # noqa: BLE001 蛋卷详情对场内 ETF 等不可用, 降级不抛
                logger.warning("probe 蛋卷详情失败(%s): %s", candidate.symbol, exc)
            results.append({
                **candidate.to_dict(),
                **base,
                "name": name,
                "type_desc": type_desc,
                "latest_date": last_date or None,  # 已入库时本库最晚净值日; 否则未知
                "resolved": resolved,
            })
            continue
        # 腾讯代码只需后缀: 600900.SH → sh600900(此处候选必带 .SH/.SZ)
        tencent_code = f"{candidate.symbol[-2:].lower()}{candidate.symbol[:6]}"
        try:
            name, latest_date = fetch(tencent_code, _PROBE_COUNT)
        except Exception as exc:  # noqa: BLE001 单个候选抓取失败不构成整体失败
            logger.warning("probe 解析失败(%s): %s", candidate.symbol, exc)
            name, latest_date, resolved = None, None, False
        else:
            resolved = latest_date is not None
        results.append({
            **candidate.to_dict(),
            **base,
            "name": name or None,
            "latest_date": latest_date,
            "resolved": resolved,
        })
    return results


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def get_asset(db: Session, security_id: int) -> dict[str, Any] | None:
    """单标的行(含本库行数与同步状态); 不存在返回 None。"""
    security = db.get(ResearchSecurity, security_id)
    return _security_payload(db, security) if security is not None else None


def _security_payload(db: Session, security: ResearchSecurity) -> dict[str, Any]:
    _, row_count, first_date, last_date = _local_stats(db, security.symbol)
    return {
        "id": security.id,
        "symbol": security.symbol,
        "name": security.name,
        "security_type": security.security_type,
        "exchange": security.exchange,
        "source": security.source,
        "selection_list": security.selection_list,
        "enabled": security.enabled,
        "price_basis": _PRICE_BASIS.get(security.security_type, "HFQ"),
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "last_sync_at": security.last_sync_at.isoformat() if security.last_sync_at else None,
        "last_sync_status": security.last_sync_status,
        "last_sync_error": security.last_sync_error,
        "last_sync_rows": security.last_sync_rows,
    }


def register(symbol: str, security_type: str, name: str, *, db: Session) -> dict[str, Any]:
    """写入 / 更新 research_security(只增不删), 返回标的行 + `created` 标记。

    - exchange: 股票/ETF 按代码后缀给 SSE/SZSE; FUND 给 OTC;
    - FUND 的 source 固定 danjuan(蛋卷净值链路), 不走 research.yaml 的行情源路由;
    - 幂等: 重复注册同一 symbol 返回已存在的行, created=False(调用方据此决定是否首抓)。
    """
    normalized_type = str(security_type or "").strip().upper()
    if normalized_type not in _PRICE_BASIS:
        raise ValueError(f"未知标的类型: {security_type!r}(支持 STOCK / ETF / FUND)")

    code, explicit_exchange = _split_input(symbol)
    if code is None:
        raise ValueError(f"无法识别的标的代码: {symbol!r}(支持 600900 / sh600900 / 600900.SH)")
    if normalized_type == "FUND":
        # 场外基金: 6 位纯数字, 无交易所概念; 规范形态 {code}.OF
        canonical = f"{code}.OF"
    else:
        exchange = _exchange_of(code, explicit_exchange)
        if exchange is None:
            raise ValueError(f"代码首位 {code[0]} 不在支持的交易所范围: {symbol!r}")
        canonical = f"{code}.{exchange}"
    _assert_symbol_type(canonical, code, normalized_type)  # 类型/代码矛盾 → ResearchSourceError(ValueError)

    if normalized_type == "FUND":
        source = _FUND_SOURCE
    else:
        source = str(load_research_settings().get("data_source") or _SOURCE_DEFAULT).strip().lower()
    targets: Sequence[dict[str, Any]] = [{
        "symbol": canonical,
        "name": str(name or "").strip(),
        "type": normalized_type.lower(),
        "source": source,
        "selection_list": _SELECTION_LIST,
    }]
    added = research_store.upsert_securities(db, targets)  # 只增不删 + 立即 commit
    security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == canonical))
    return {**_security_payload(db, security), "created": bool(added)}


# ---------------------------------------------------------------------------
# 单标的同步
# ---------------------------------------------------------------------------

def _write_sync_state(
    db: Session,
    security_id: int,
    status: str,
    *,
    error: str | None = None,
    rows: int | None = None,
) -> ResearchSecurity | None:
    """回写同步状态列; 标的不存在返回 None。"""
    security = db.get(ResearchSecurity, security_id)
    if security is None:
        return None
    security.last_sync_status = status
    security.last_sync_error = error
    if rows is not None:
        security.last_sync_rows = rows
    if status in (STATUS_SUCCESS, STATUS_FAILED):
        security.last_sync_at = _utcnow()
    db.commit()
    return security


def _resolve_history_start() -> date:
    history_start = research_tasks._resolve_history_start(load_research_settings())
    return history_start if isinstance(history_start, date) else datetime.strptime(
        _HISTORY_START_DEFAULT, "%Y-%m-%d",
    ).date()


def sync_one(
    security_id: int,
    *,
    db_factory: Callable[[], Session] | None = None,
    provider_factory_fn: Callable[[str], Any] | None = None,
    request_end: date | None = None,
    nav_history_fetch_fn: fund_nav.NavHistoryFetchFn | None = None,
) -> dict[str, Any]:
    """单标的: 置 running → 抓取 → 落库 → 回写同步状态。

    股票/ETF: 腾讯 raw/hfq 配对 → publish_paired_snapshot;
    场外基金(FUND): 蛋卷净值全历史(1 请求) → 链式复权 → 幂等覆盖写 fund_nav_daily。
    网络在事务外、每标的一个独立 Session(主流程与落库各一次), 与既有任务范式一致;
    失败写 failed + 原因(截断 255), 不向上抛(单标的失败不影响其余标的)。
    """
    create_session = db_factory or SessionLocal
    make_provider = provider_factory_fn or provider_factory
    today = request_end or date.today()

    with create_session() as db:  # type: Session
        security = db.get(ResearchSecurity, security_id)
        if security is None:
            return {
                "security_id": security_id, "symbol": None, "security_type": None,
                "status": STATUS_FAILED, "rows": 0, "inserted_rows": 0, "revised_rows": 0,
                "unchanged_rows": 0, "first_date": None, "last_date": None,
                "source": None, "error": f"未知标的 id: {security_id}",
                "last_sync_status": STATUS_FAILED, "last_sync_at": None,
                "last_sync_rows": None, "last_sync_error": f"未知标的 id: {security_id}",
            }
        target = {
            "symbol": security.symbol,
            "security_type": security.security_type,
            "source": security.source or _SOURCE_DEFAULT,
        }
        _write_sync_state(db, security_id, STATUS_RUNNING, error=None)

    history_start = _resolve_history_start()
    failure: str | None = None
    inserted = revised = unchanged = 0
    first_date: date | None = None
    last_date: date | None = None
    source_name: str | None = None

    if target["security_type"] == "FUND":
        # ------- P1 场外基金: 蛋卷净值全历史 → 链式复权 → 幂等覆盖写 -------
        try:
            rows = fund_nav.fetch_nav_history(
                target["symbol"][:6], fetch_fn=nav_history_fetch_fn,
            )
            with create_session() as db:  # type: Session
                stats = fund_nav.upsert_fund_nav(db, target["symbol"], rows, source=_FUND_SOURCE)
                first_date = rows[0].nav_date
                last_date = rows[-1].nav_date
                inserted = stats["rows_written"]  # 本次写入行数(幂等覆盖, 含未变更)
                _write_sync_state(
                    db, security_id, STATUS_SUCCESS,
                    error=None, rows=stats["rows_written"],
                )
        except Exception as exc:  # noqa: BLE001
            failure = f"{type(exc).__name__}: {exc}"[:_MAX_SYNC_ERROR]
            logger.warning("场外基金同步失败(id=%s, %s): %s", security_id, target["symbol"], exc)
            try:
                with create_session() as db:  # type: Session
                    _write_sync_state(db, security_id, STATUS_FAILED, error=failure, rows=0)
            except Exception:  # noqa: BLE001
                logger.exception("同步状态回写失败: security_id=%s", security_id)
    else:
        try:
            provider = make_provider(target["source"])
            source_name = getattr(provider, "name", target["source"])
            raw_bars = provider.get_daily_bars(
                target["symbol"], history_start, today, AdjustMode.RAW,
                security_type=target["security_type"],
            )
            hfq_bars = provider.get_daily_bars(
                target["symbol"], history_start, today, AdjustMode.HFQ,
                security_type=target["security_type"],
            )
            # 尾部发布滞后截齐(与定时任务同一份逻辑, 不复制)
            raw_bars, hfq_bars, common_last = research_tasks._align_trailing_publication_lag(raw_bars, hfq_bars)
            if common_last is not None:
                logger.warning("%s: raw/hfq 尾部发布滞后, 截齐到共同末日 %s 后发布", target["symbol"], common_last)
            with create_session() as db:  # type: Session
                result = research_store.publish_paired_snapshot(
                    db, target["symbol"], raw_bars, hfq_bars,
                    source=source_name, request_start=history_start, request_end=today,
                )
                if result.status == "success":
                    inserted, revised, unchanged = result.inserted_rows, result.revised_rows, result.unchanged_rows
                    if raw_bars:
                        first_date = min(bar.trade_date for bar in raw_bars)
                        last_date = max(bar.trade_date for bar in raw_bars)
                else:
                    failure = result.error or "配对快照被拒绝"
                rows = inserted + revised + unchanged
                _write_sync_state(
                    db, security_id,
                    STATUS_SUCCESS if failure is None else STATUS_FAILED,
                    error=failure,
                    rows=rows if failure is None else 0,
                )
        except Exception as exc:  # noqa: BLE001 单标的失败不中断(后台任务/刷新端点均据此展示)
            failure = f"{type(exc).__name__}: {exc}"[:_MAX_SYNC_ERROR]
            logger.warning("单一标的同步失败(id=%s, %s): %s", security_id, target["symbol"], exc)
            try:
                with create_session() as db:  # type: Session
                    _write_sync_state(db, security_id, STATUS_FAILED, error=failure, rows=0)
            except Exception:  # noqa: BLE001 状态回写失败只能记录, 不能改变主失败语义
                logger.exception("同步状态回写失败: security_id=%s", security_id)

    status = STATUS_FAILED if failure else STATUS_SUCCESS
    stored: dict[str, Any] = {"last_sync_status": status}
    with create_session() as db:  # type: Session
        security = db.get(ResearchSecurity, security_id)
        if security is not None:
            stored = {
                "last_sync_status": security.last_sync_status,
                "last_sync_at": security.last_sync_at.isoformat() if security.last_sync_at else None,
                "last_sync_rows": security.last_sync_rows,
                "last_sync_error": security.last_sync_error,
            }
    return {
        "security_id": security_id,
        "symbol": target["symbol"],
        "security_type": target["security_type"],
        "source": source_name,
        "request_start": history_start.isoformat(),
        "request_end": today.isoformat(),
        "status": status,
        # last_sync_rows 为本次两侧(raw+hfq)合计的行写入数(含未变更)
        "rows": inserted + revised + unchanged,
        "inserted_rows": inserted,
        "revised_rows": revised,
        "unchanged_rows": unchanged,
        "first_date": first_date.isoformat() if first_date else None,
        "last_date": last_date.isoformat() if last_date else None,
        "error": failure,
        **stored,
    }


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

def list_assets(db: Session) -> list[dict[str, Any]]:
    """已注册标的列表(按注册顺序), 含本库行数/区间与同步状态。"""
    securities = db.scalars(select(ResearchSecurity).order_by(ResearchSecurity.id)).all()
    return [_security_payload(db, security) for security in securities]
