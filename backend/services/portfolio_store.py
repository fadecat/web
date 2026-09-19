# -*- coding: utf-8 -*-
"""组合定义服务层(P2): 组合 CRUD / 成员管理 / 权重。

归属原则(docs/portfolio-lab-multi-portfolio.md §一):
- 组合只描述"**持有什么、各多少**"。
- **再平衡 / 基准 / 区间 不在本层** —— 它们属于 `backtest_run`(P3), 参数即身份;
  否则"看三种再平衡"要建 9 个组合。
- 「添加后的收益」是**纯展示列**(对回测零影响): 由 `added_at` + 序列层算出, 见 `since_added_return`。

删除与移除的语义(勿混):
- **删除组合** = 归档(软删 `status=archived`), 可恢复, **不级联删标的与 Run**;
- **移除成员** = 只从 `portfolio_asset` 删一行, **不停用 `research_security` 标的**
  (否则会影响 `/research` 模块与 17:30 的定时同步)。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.portfolio import Portfolio, PortfolioAsset
from backend.models.research import ResearchSecurity
from backend.services import series

ACTIVE = "active"
ARCHIVED = "archived"

# 允许的再平衡方式(仅作新建 Run 的预填; 韭圈儿只有这三种)
REBALANCE_MODES = ("none", "quarterly", "yearly")
# 权重合计容差(百分比): 浮点求和误差容忍
WEIGHT_SUM_TOLERANCE = 0.01


class PortfolioError(ValueError):
    """组合层业务错误(重名 / 未知组合 / 未知标的 / 权重非法)。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 组合身份
# ---------------------------------------------------------------------------

def default_name(db: Session) -> str:
    """默认名 `我的组合N`(N = 现有组合数 + 1, 重名则继续往后找)。"""
    existing = {
        row[0] for row in db.execute(select(Portfolio.name)).all()
    }
    index = len(existing) + 1
    while f"我的组合{index}" in existing:
        index += 1
    return f"我的组合{index}"


def _portfolio_payload(db: Session, portfolio: Portfolio) -> dict[str, Any]:
    """L1 卡片 + L2 头部所需的组合字段。"""
    asset_count = db.scalar(
        select(func.count()).select_from(PortfolioAsset).where(
            PortfolioAsset.portfolio_id == portfolio.id,
        ),
    ) or 0
    return {
        "id": portfolio.id,
        "name": portfolio.name,
        "note": portfolio.note,
        "status": portfolio.status,
        # ⭐ 「成立时间」= 创建时间; 「收益时间」= cached_asof_date(缓存对应的数据截止日)
        "created_at": portfolio.created_at.isoformat() if portfolio.created_at else None,
        "default_rebalance": portfolio.default_rebalance,
        "asset_count": int(asset_count),
        # L1 三格(不平衡 + 日/近一月/今年以来); 未算过为 None → 前端显示 —
        "cached_day_return": portfolio.cached_day_return,
        "cached_month_return": portfolio.cached_month_return,
        "cached_ytd_return": portfolio.cached_ytd_return,
        "cached_asof_date": (
            portfolio.cached_asof_date.isoformat() if portfolio.cached_asof_date else None
        ),
    }


def create_portfolio(
    db: Session, name: str | None = None, note: str | None = None,
    *, default_rebalance: str | None = None,
) -> dict[str, Any]:
    """新建组合; 名缺省为 `我的组合N`。"""
    final_name = str(name or "").strip() or default_name(db)
    if default_rebalance is not None and default_rebalance not in REBALANCE_MODES:
        raise PortfolioError(
            f"未知再平衡方式: {default_rebalance!r}(支持 {', '.join(REBALANCE_MODES)})",
        )
    portfolio = Portfolio(
        name=final_name, note=note, status=ACTIVE, default_rebalance=default_rebalance,
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return _portfolio_payload(db, portfolio)


def list_portfolios(db: Session, *, include_archived: bool = False) -> list[dict[str, Any]]:
    """L1 列表(按创建时间倒序; 默认不含已归档)。"""
    stmt = select(Portfolio)
    if not include_archived:
        stmt = stmt.where(Portfolio.status == ACTIVE)
    rows = db.scalars(stmt.order_by(Portfolio.created_at.desc(), Portfolio.id.desc())).all()
    return [_portfolio_payload(db, row) for row in rows]


def get_portfolio_row(db: Session, portfolio_id: int) -> Portfolio | None:
    return db.get(Portfolio, portfolio_id)


def rename_portfolio(db: Session, portfolio_id: int, name: str) -> dict[str, Any]:
    """就地改名(空名拒绝, 避免列表出现无名卡片)。"""
    portfolio = _require_portfolio(db, portfolio_id)
    final_name = str(name or "").strip()
    if not final_name:
        raise PortfolioError("组合名不得为空")
    portfolio.name = final_name
    db.commit()
    return _portfolio_payload(db, portfolio)


def set_note(db: Session, portfolio_id: int, note: str | None) -> dict[str, Any]:
    portfolio = _require_portfolio(db, portfolio_id)
    portfolio.note = note
    db.commit()
    return _portfolio_payload(db, portfolio)


def archive_portfolio(db: Session, portfolio_id: int) -> dict[str, Any]:
    """软删(归档): 可恢复, **不**级联删成员与 Run。"""
    portfolio = _require_portfolio(db, portfolio_id)
    portfolio.status = ARCHIVED
    db.commit()
    return _portfolio_payload(db, portfolio)


def restore_portfolio(db: Session, portfolio_id: int) -> dict[str, Any]:
    portfolio = _require_portfolio(db, portfolio_id)
    portfolio.status = ACTIVE
    db.commit()
    return _portfolio_payload(db, portfolio)


def copy_portfolio(
    db: Session, source_id: int, name: str | None = None,
) -> dict[str, Any]:
    """复制组合(含成员与权重); 默认名 `复制_原名`。"""
    source = _require_portfolio(db, source_id)
    new_name = str(name or "").strip() or f"复制_{source.name}"
    created = create_portfolio(db, new_name, source.note, default_rebalance=source.default_rebalance)
    members = db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == source_id)
        .order_by(PortfolioAsset.sort_order, PortfolioAsset.id)
    ).all()
    for member in members:
        db.add(PortfolioAsset(
            portfolio_id=created["id"], symbol=member.symbol,
            target_weight=member.target_weight, added_at=_utcnow(),
            sort_order=member.sort_order,
        ))
    db.commit()
    return _portfolio_payload(db, _require_portfolio(db, created["id"]))


def _require_portfolio(db: Session, portfolio_id: int) -> Portfolio:
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise PortfolioError(f"未知组合: {portfolio_id}")
    return portfolio


# ---------------------------------------------------------------------------
# 成员管理
# ---------------------------------------------------------------------------

def add_asset(
    db: Session, portfolio_id: int, symbol: str, *,
    target_weight: float | None = None, asset_type: str | None = None,
) -> dict[str, Any]:
    """把标的加入组合。

    - 标的必须**已注册**在 `research_security`(这是唯一标的入口); 未注册直接拒绝,
      不去隐式注册 —— 注册要走 `/api/portfolio/assets`(含抓取与同步状态)。
    - 重复添加**幂等**: 返回 `created=False`, 不重复插入(唯一键 `(portfolio_id, symbol)`）。
    - 新增标的的 `sort_order` 追加到末尾。
    """
    _require_portfolio(db, portfolio_id)
    canonical = str(symbol or "").strip().upper()
    security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == canonical))
    if security is None:
        raise PortfolioError(
            f"标的未注册, 请先经 /api/portfolio/assets 注册: {canonical!r}",
        )
    if asset_type is not None:
        expect = str(asset_type).strip().upper()
        if expect != str(security.security_type).strip().upper():
            raise PortfolioError(
                f"类型与注册表不一致: 传入 {expect}, 注册表为 {security.security_type}({canonical})",
            )
    existing = db.scalar(
        select(PortfolioAsset).where(
            PortfolioAsset.portfolio_id == portfolio_id, PortfolioAsset.symbol == canonical,
        ),
    )
    if existing is not None:
        return {**_asset_payload(db, existing), "created": False}

    max_order = db.scalar(
        select(func.max(PortfolioAsset.sort_order)).where(
            PortfolioAsset.portfolio_id == portfolio_id,
        ),
    )
    member = PortfolioAsset(
        portfolio_id=portfolio_id, symbol=canonical, target_weight=target_weight,
        added_at=_utcnow(), sort_order=(int(max_order) + 1) if max_order is not None else 0,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    # 名称/类型以 research_security 为权威, 此处不冗余存储
    return {**_asset_payload(db, member), "created": True}


def remove_asset(db: Session, portfolio_id: int, symbol: str) -> bool:
    """从组合移除成员; ⚠ **不停用标的**(否则影响 /research 与定时同步)。"""
    canonical = str(symbol or "").strip().upper()
    member = db.scalar(
        select(PortfolioAsset).where(
            PortfolioAsset.portfolio_id == portfolio_id, PortfolioAsset.symbol == canonical,
        ),
    )
    if member is None:
        return False
    db.delete(member)
    db.commit()
    return True


def set_weights(
    db: Session, portfolio_id: int, weights: dict[str, float | None],
) -> dict[str, Any]:
    """批量设置初始比例(**百分比**, 如 25.0)。

    ⚠ 允许"部分未设"(`None`)—— 这是编辑器里的正常中间态(列表底部提示"权重合计 100% 才能回测")。
    是否可回测由 `weight_sum` / `ready` 暴露给调用方, 不在写入时硬拒。
    """
    _require_portfolio(db, portfolio_id)
    members = db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id),
    ).all()
    by_symbol = {m.symbol: m for m in members}
    for symbol, weight in weights.items():
        canonical = str(symbol).strip().upper()
        member = by_symbol.get(canonical)
        if member is None:
            raise PortfolioError(f"标的不在该组合内: {canonical!r}")
        if weight is not None and float(weight) < 0:
            raise PortfolioError(f"权重不得为负: {canonical} = {weight}")
        member.target_weight = None if weight is None else float(weight)
    db.commit()
    return list_assets(db, portfolio_id)


def list_assets(db: Session, portfolio_id: int) -> dict[str, Any]:
    """组合成员列表 + 权重合计状态(L2 详情表用)。"""
    members = db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id)
        .order_by(PortfolioAsset.sort_order, PortfolioAsset.id)
    ).all()
    assets = [_asset_payload(db, m) for m in members]
    assigned = [a["target_weight"] for a in assets if a["target_weight"] is not None]
    weight_sum = float(sum(assigned)) if assigned else 0.0
    all_set = len(assigned) == len(assets) and len(assets) > 0
    return {
        "portfolio_id": portfolio_id,
        "assets": assets,
        "weight_sum": round(weight_sum, 6),
        # ready = 每个成员都设了权重, 且合计 = 100%(无现金腿)
        "ready": bool(all_set and abs(weight_sum - 100.0) <= WEIGHT_SUM_TOLERANCE),
    }


def _asset_payload(db: Session, member: PortfolioAsset) -> dict[str, Any]:
    security = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == member.symbol))
    return {
        "id": member.id,
        "symbol": member.symbol,
        "name": security.name if security is not None else None,
        "security_type": security.security_type if security is not None else None,
        "target_weight": member.target_weight,
        "added_at": member.added_at.isoformat() if member.added_at else None,
        "sort_order": member.sort_order,
        # ⚠ 「当前占比」是**不平衡持有至今的漂移权重**, 需要份额法账本 → P3 提供;
        #    这里显式给 None, 不用目标权重冒充(两者不是一回事)。
        "current_weight": None,
        "since_added_return": since_added_return(db, member),
    }


def since_added_return(db: Session, member: PortfolioAsset) -> dict[str, Any] | None:
    """「添加后的收益」: **该标的自身自其添加日的收益, 与权重无关**。

    口径(docs/portfolio-lab-verification.md §二-E): 纯展示列, **对回测零影响**。
    股票/ETF 用 HFQ 后复权价、场外基金用 NAV_ADJ 分红再投净值(均由序列层判定),
    所以这个数字与详情页的组合收益不是同一套口径, 不能相互推导。
    """
    if member.added_at is None:
        return None
    start = member.added_at.date()
    try:
        contract = series.get_series(db, member.symbol, start=start)
    except series.SeriesError:
        return None
    if contract.row_count < 2:
        return None
    first, last = contract.prices[0], contract.prices[-1]
    if not first:
        return None
    return {
        "symbol": member.symbol,
        "price_basis": contract.price_basis,
        "actual_start": contract.first_date.isoformat(),
        "end_date": contract.last_date.isoformat(),
        "value": round((last / first - 1.0) * 100.0, 4),  # 百分比数值
    }


def portfolio_detail(db: Session, portfolio_id: int) -> dict[str, Any] | None:
    """L2 详情 = 组合身份 + 成员 + 权重状态 + 数据就绪摘要。"""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        return None
    payload = _portfolio_payload(db, portfolio)
    members = list_assets(db, portfolio_id)
    payload.update(members)
    payload["data_readiness"] = data_readiness(db, portfolio_id)
    return payload


def data_readiness(db: Session, portfolio_id: int) -> dict[str, Any]:
    """回测前的数据就绪检查(docs/portfolio-lab-data-maintenance.md §九)。

    **不阻塞、不静默降级** —— 把问题摆出来, 前端给「立即同步」按钮。
    `common_start` = 各成员**首个可用日**的最大值(T0), 这正是"起点共同规则"的 UI 回显。
    """
    members = db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id)
        .order_by(PortfolioAsset.sort_order, PortfolioAsset.id)
    ).all()
    items: list[dict[str, Any]] = []
    contracts: list[series.SeriesContract] = []
    for member in members:
        try:
            contract = series.get_series(db, member.symbol)
        except series.SeriesError as exc:
            items.append({"symbol": member.symbol, "ok": False, "reason": str(exc)})
            continue
        contracts.append(contract)
        items.append({
            "symbol": member.symbol,
            "ok": contract.row_count > 0,
            "row_count": contract.row_count,
            "first_date": contract.first_date.isoformat() if contract.first_date else None,
            "last_date": contract.last_date.isoformat() if contract.last_date else None,
            "price_basis": contract.price_basis,
            "reason": None if contract.row_count else "无数据, 请先同步",
        })
    t0 = series.common_start(contracts)
    return {
        "assets": items,
        "all_ready": bool(items) and all(i["ok"] for i in items),
        "common_start": t0.isoformat() if t0 else None,
    }


def update_rebalance_default(db: Session, portfolio_id: int, mode: str) -> dict[str, Any]:
    """设置 `default_rebalance`(**仅作新建 Run 的预填**, 不参与任何计算)。"""
    if mode not in REBALANCE_MODES:
        raise PortfolioError(
            f"未知再平衡方式: {mode!r}(支持 {', '.join(REBALANCE_MODES)})",
        )
    portfolio = _require_portfolio(db, portfolio_id)
    portfolio.default_rebalance = mode
    db.commit()
    return _portfolio_payload(db, portfolio)


def write_cached_metrics(
    db: Session, portfolio_id: int, *,
    day_return: float | None, month_return: float | None, ytd_return: float | None,
    asof_date: date | None,
) -> None:
    """回写 L1 三格缓存(P3 的定时任务调用; `cached_asof_date` = 卡片上的「收益时间」)。

    ⚠ 三格与详情页收益条**必须算出同样的数字** → 两者共用同一条
    「区间解析 + 份额法账本」实现(P3), 本函数只负责落库。
    """
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        return
    portfolio.cached_day_return = day_return
    portfolio.cached_month_return = month_return
    portfolio.cached_ytd_return = ytd_return
    portfolio.cached_asof_date = asof_date
    db.commit()


def weight_vectors(db: Session, portfolio_id: int) -> dict[str, float]:
    """成员 → 初始比例(仅返回已设权重的成员), 供 P3 账本使用。"""
    members: Iterable[PortfolioAsset] = db.scalars(
        select(PortfolioAsset).where(PortfolioAsset.portfolio_id == portfolio_id),
    ).all()
    return {
        m.symbol: float(m.target_weight)
        for m in members if m.target_weight is not None
    }
