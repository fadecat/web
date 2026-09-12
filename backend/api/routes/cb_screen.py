# -*- coding: utf-8 -*-
"""可转债筛选打分路由。

端点:
1. GET  /cb-list/factors/catalog  — 因子目录
2. GET  /cb-list/factors          — 策略模板配置
3. POST /cb-list/factors          — 保存策略模板配置
4. POST /cb-list/screen           — 按模板筛选打分(基于最新交易日快照)
5. GET  /cb-list/screen/active    — 按当前 active 模板筛选打分
6. GET  /cb-list/screen/intraday  — 盘中选债(实时拉集思录, 纯条件过滤)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.schemas.cb_screen import (
    FactorsConfigModel,
    IntradayFilterQuery,
    SelectionConfigModel,
    SelectionRunModel,
    normalize_ratings,
)
from backend.models.database import get_db
from backend.models.valuation import CbDailySnapshot
from backend.services.cb_blacklist_store import (
    add_to_blacklist,
    get_blacklist,
    get_blacklist_ids,
    remove_from_blacklist,
)
from backend.services.cb_factors import (
    FACTOR_CATALOG,
    ConfigConflictError,
    ConfigUnreadableError,
    default_config_normalized,
    get_active_template,
    load_current_config,
    save_config_v3,
    write_config,
)
from backend.services.cb_template_migration import migrate_config_to_v3
from backend.services.cb_intraday import screen_bonds_intraday
from backend.services.cb_screen import (
    RedeemDataUnavailableError,
    screen_bonds,
    screen_bonds_live,
)

router = APIRouter()


@router.get("/cb-list/factors/catalog")
def get_factor_catalog() -> list[dict[str, Any]]:
    """返回可用因子字段目录。"""
    from backend.services.cb_redeem_semantics import STATUS_LABELS
    return [
        {**item, **({"options": [{"value": key, "label": label} for key, label in STATUS_LABELS.items() if key != "UNKNOWN"]} if item["field"] == "redeem_status_code" else {})}
        for item in FACTOR_CATALOG if item["field"] not in {"redeem_icons", "redeem_remain_days"}
    ]


@router.get("/cb-list/factors/ratings")
def get_ratings_catalog(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """评级目录(唯一事实源, P2-R04): 规范等级 + 快照中发现的未知非空值。"""
    from sqlalchemy import distinct

    from backend.services.rating_catalog import get_rating_catalog

    discovered = [
        str(v) for (v,) in db.query(distinct(CbDailySnapshot.rating_cd)).all() if v
    ]
    return get_rating_catalog(discovered)


@router.get("/cb-list/factors")
def get_factors() -> dict[str, Any]:
    """返回 V3 模板配置 + revision + 每模板迁移问题(§5.3-1/§6.2)。

    旧版本磁盘文件只在内存迁移, 不改磁盘; 无文件时迁移默认配置。
    JSON 损坏/版本不支持 → 503 CONFIG_UNREADABLE, 绝不静默回默认(R9)。
    """
    try:
        revision, config = load_current_config()
    except ConfigUnreadableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_UNREADABLE", "message": str(exc)},
        )
    if config is None:
        config = default_config_normalized()
    migrated = migrate_config_to_v3(config)
    return {**migrated, "revision": revision}


def _validation_error_detail(exc: ValidationError) -> dict[str, Any]:
    """V3 校验错误 → {code/message/path} 结构(§4.3, path 如 templates.0.conditions.2.value)。"""
    errors = [
        {
            "code": str(e.get("type", "value_error")),
            "message": str(e.get("msg", "输入非法")),
            "path": ".".join(str(p) for p in e.get("loc", ())),
        }
        for e in exc.errors()
    ]
    first = errors[0]
    return {
        "code": "INVALID_CONFIG",
        "message": first["message"],
        "path": first["path"],
        "errors": errors,
    }


def _save_factors_v3(body: dict[str, Any]) -> dict[str, Any]:
    """V3 整份保存(§5.3): 强校验 → 锁内 revision 比对 → 备份 → 原子写。"""
    try:
        validated = SelectionConfigModel.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_error_detail(exc))
    try:
        outcome = save_config_v3(validated.model_dump(), validated.revision)
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CONFIG_CONFLICT",
                "message": str(exc),
                "current_revision": exc.current_revision,
            },
        )
    except ConfigUnreadableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_UNREADABLE", "message": str(exc)},
        )
    return {"ok": True, "data": {**outcome["data"], "revision": outcome["revision"]}}


def _save_factors_legacy(body: dict[str, Any]) -> dict[str, Any]:
    """V1/V2 旧客户端兼容保存(§5.3-7)。

    仅当磁盘仍为 V1/V2 或不存在时兼容; 磁盘已升级 V3 后, 旧客户端(不携带
    revision)写入返回 409 CLIENT_UPGRADE_REQUIRED, 防止旧结构覆盖新配置。
    """
    try:
        _, current = load_current_config()
    except ConfigUnreadableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_UNREADABLE", "message": str(exc)},
        )
    if current is not None and current.get("version") == 3:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CLIENT_UPGRADE_REQUIRED",
                "message": "配置已升级到 V3, 旧版客户端需升级后携带 revision 保存",
            },
        )
    try:
        validated = FactorsConfigModel.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0].get("msg", "配置结构非法"))
    validated_data = validated.model_dump()
    normalized = write_config(validated_data)
    return {"ok": True, "data": normalized}


@router.post("/cb-list/factors")
def save_factors(body: dict[str, Any]) -> dict[str, Any]:
    """保存策略模板配置(版本化整份保存, §5.3/§6.2)。

    - body.version == 3: V3 路径 — SelectionConfigModel 强校验(模板 id 唯一/
      active_id 存在/名称 trim 1~40 且 casefold 不重), revision 必填并与磁盘
      比对(409 CONFIG_CONFLICT), 首次落盘前独占备份, 原子替换;
      响应 {"ok": true, "data": {..., "revision": 新revision}}。
    - 其他(V1/V2 旧客户端): 走旧 FactorsConfigModel 校验与旧合同
      (ratings 缺省/[] = 不限; null/错误类型/空串/归一后重复 = 422;
      excluded_ratings 仅在读取旧文件时迁移, 新 POST 出现即 422),
      磁盘已是 V3 时返回 409 CLIENT_UPGRADE_REQUIRED。
    """
    if isinstance(body, dict) and body.get("version") == 3:
        return _save_factors_v3(body)
    return _save_factors_legacy(body)


def _load_rows(db: Session) -> tuple[list[CbDailySnapshot], Any]:
    """加载最新交易日 D 的全量转债快照, 返回 (rows, D); 无快照返回 ([], None)。"""
    latest = db.query(func.max(CbDailySnapshot.trade_date)).scalar()
    if not latest:
        return [], None
    rows = db.query(CbDailySnapshot).filter(
        CbDailySnapshot.trade_date == latest
    ).all()
    return rows, latest


def _load_redeem_map(db: Session, as_of_date: Any) -> tuple[dict[str, dict[str, Any]], Any]:
    """加载不晚于 as_of_date(D) 的最近强赎快照 R(§3.2), 返回 ({bond_id: cell}, R)。

    必须纳入 redeem_price(简单到期收益率的唯一合法赎回价来源, 不得用
    force_redeem_price 替代)。R 不存在返回 ({}, None)。
    """
    from backend.models.valuation import CbRedeemDaily

    if as_of_date is None:
        return {}, None
    latest = db.query(func.max(CbRedeemDaily.trade_date)).filter(
        CbRedeemDaily.trade_date <= as_of_date
    ).scalar()
    if not latest:
        return {}, None
    rows = db.query(CbRedeemDaily).filter(
        CbRedeemDaily.trade_date == latest
    ).all()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        try:
            raw_redeem = json.loads(r.raw_json) if r.raw_json else {}
        except (TypeError, ValueError):
            raw_redeem = {}
        result[r.bond_id] = {
            "redeem_icon": r.redeem_icon,
            "redeem_flag": r.redeem_flag,
            "redeem_remain_days": r.redeem_remain_days,
            "redeem_real_days": r.redeem_real_days,
            "redeem_count_days": r.redeem_count_days,
            "redeem_total_days": r.redeem_total_days,
            "redeem_price": r.redeem_price,
            "redeem_dt": r.redeem_dt,
            "recount_dt": r.recount_dt,
            "delist_dt": r.delist_dt,
            "force_redeem": r.force_redeem,
            "real_force_redeem_price": raw_redeem.get("real_force_redeem_price"),
        }
    return result, latest


# 东八区固定偏移(中国无夏令时; 不依赖系统 tzdata, Windows/容器行为一致)
_TZ_CN = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _cn_now() -> datetime:
    return datetime.now(_TZ_CN)


def _validate_run_template(body: dict[str, Any]) -> dict[str, Any]:
    """执行请求强校验(先校验再抓取, §4.3) + 迁移 pending 拒绝(§5.2)。"""
    try:
        validated = SelectionRunModel.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_validation_error_detail(exc))
    template = validated.model_dump()
    if any(c["field"] in {"redeem_icons", "redeem_remain_days"} for c in template["conditions"]):
        raise HTTPException(status_code=409, detail={"code": "TEMPLATE_REVIEW_REQUIRED", "message": "旧强赎条件需重新加载并迁移后执行"})
    pending = [
        issue for issue in (template.get("migration_issues") or [])
        if isinstance(issue, dict) and issue.get("status") == "pending"
    ]
    if pending:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TEMPLATE_REVIEW_REQUIRED",
                "message": "模板存在待确认的迁移项(如旧集思录收益率 ytm_rt),"
                           " 请先在模板编辑中确认后再执行",
                "issues": pending,
            },
        )
    return template


def _no_snapshot_result(template: dict[str, Any], source: str) -> dict[str, Any]:
    """DB 无转债快照: 200 + meta.data_status=no_snapshot, 区别于 0 只符合(§3.2)。"""
    return {
        "total_all": 0,
        "total_filtered": 0,
        "total_excluded": 0,
        "selection_mode": (
            "scored" if any(
                f.get("enabled", True) for f in (template.get("strategy_factors") or [])
            ) else "filter_only"
        ),
        "rows": [],
        "excluded_rows": [],
        "source": source,
        "template_id": template.get("id"),
        "template_name": template.get("name"),
        "meta": {
            "data_status": "no_snapshot",
            "trade_date": None,
            "redeem_trade_date": None,
            "fetched_at": None,
            "quote_time": None,
            "redeem_loaded": False,
            "missing_yield_count": 0,
            "blacklisted_count": 0,
            "warnings": [],
        },
    }


def _execute_db_screen(db: Session, template: dict[str, Any]) -> dict[str, Any]:
    """DB 路径执行(§3.2): D=最新转债快照日, R=不晚于 D 的最近强赎快照日。

    严格同日策略: R 不存在或 R≠D 时本轮赎回字段一律按缺失处理(redeem_loaded
    =False), 返回 D、R 与警告; 模板依赖收益率/赎回价时由服务层抛
    RedeemDataUnavailableError → 503。
    """
    rows, trade_date = _load_rows(db)
    if trade_date is None:
        return _no_snapshot_result(template, source="db")

    redeem_map, redeem_date = _load_redeem_map(db, trade_date)
    warnings: list[str] = []
    redeem_unusable = redeem_date != trade_date
    if redeem_date is None:
        warnings.append("无强赎快照: 赎回价/简单到期收益率缺失")
    elif redeem_date != trade_date:
        warnings.append(
            f"强赎快照({redeem_date.isoformat()})早于行情快照({trade_date.isoformat()}), "
            "按严格同日策略本轮赎回字段按缺失处理; 可改用实时行情"
        )
    try:
        result = screen_bonds(
            rows, template,
            redeem_map=redeem_map,
            as_of_date=trade_date,
            blacklist_ids=get_blacklist_ids(db),
            trade_date=trade_date.isoformat(),
            redeem_trade_date=redeem_date.isoformat() if redeem_date else None,
            redeem_loaded=(redeem_date == trade_date),
            redeem_unusable=redeem_unusable,
            warnings=warnings,
        )
    except RedeemDataUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "REDEEM_DATA_UNAVAILABLE", "message": str(exc)},
        )
    result["source"] = "db"
    result["template_id"] = template.get("id")
    result["template_name"] = template.get("name")
    return result


@router.post("/cb-list/screen")
def screen(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """按传入模板配置筛选打分(V3, §6.2)。

    保留平铺模板+source 请求形状, 模板携带 schema_version=3, 不要求先保存。
    执行顺序: 先强校验(失败 422 且不触发任何数据源), 再按 source 取数:
    - db: 最新交易日快照 + 同日强赎快照(严格同日) + 黑名单;
    - live: 实时拉集思录(强赎列表空 → 赎回数据不可用), trade_date 不冒充。
    迁移 pending 模板 409 TEMPLATE_REVIEW_REQUIRED; 上游失败 502;
    全市场赎回输入无效且模板依赖 503 REDEEM_DATA_UNAVAILABLE;
    DB 无快照 200 + meta.data_status=no_snapshot。
    """
    template = _validate_run_template(body)

    if template.get("source") == "live":
        try:
            from backend.services.queries.live import fetch_live_snapshot

            snapshot = fetch_live_snapshot()
            records, redeem_cells = snapshot
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"实时数据拉取失败: {exc}")
        try:
            result = screen_bonds_live(
                records, template,
                redeem_cells=redeem_cells,
                as_of_date=_cn_now().date(),
                blacklist_ids=get_blacklist_ids(db),
            )
        except RedeemDataUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "REDEEM_DATA_UNAVAILABLE", "message": str(exc)},
            )
        result["meta"]["fetched_at"] = _cn_now().isoformat(timespec="seconds")
        result["meta"]["redeem_fetch_status"] = getattr(snapshot, "redeem_fetch_status", "ok" if redeem_cells else "empty")
        if result["meta"]["redeem_fetch_status"] == "failed":
            result["meta"]["warnings"].append("强赎接口抓取失败，请稍后重试或检查数据源登录状态")
        result["source"] = "live"
        result["template_id"] = template.get("id")
        result["template_name"] = template.get("name")
        return result

    return _execute_db_screen(db, template)


@router.get("/cb-list/screen/active")
def screen_active(db: Session = Depends(get_db)) -> dict[str, Any]:
    """按当前 active 模板筛选打分(V3, §6.2): 与 run 同一校验、同一引擎。

    配置文件损坏 → 503 CONFIG_UNREADABLE; 模板迁移 pending → 409
    TEMPLATE_REVIEW_REQUIRED; 黑名单生效并作为排除原因返回。
    """
    try:
        tmpl = get_active_template()
    except ConfigUnreadableError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "CONFIG_UNREADABLE", "message": str(exc)},
        )
    if tmpl is None:
        raise HTTPException(status_code=400, detail="未找到可用因子模板")

    template = _validate_run_template({**tmpl, "schema_version": 3, "source": "db"})
    return _execute_db_screen(db, template)


@router.get("/cb-list/factors/industries")
def get_industries(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """行业目录(§3.3/§6.2): 静态申万映射 + 本地快照发现原始码, 不触网。

    条目: {industry_code(原始码), industry_name, industry_level,
    industry_mapped_code, industry_is_fallback, source(catalog|snapshot)}。
    快照中发现而静态目录未收录的原始码: 回退命中标注 fallback;
    完全未知保留选项(name 为 None, 前端显示"未映射 <code>"), 筛选永远
    匹配原始码。
    """
    from sqlalchemy import distinct

    from backend.services.industry import discovered_catalog_entry, static_catalog_entries

    entries = {e["industry_code"]: e for e in static_catalog_entries()}
    for (code,) in db.query(distinct(CbDailySnapshot.sw_cd)).all():
        code = str(code or "").strip()
        if not code or code in entries:
            continue
        entries[code] = discovered_catalog_entry(code)
    return sorted(entries.values(), key=lambda e: e["industry_code"])


@router.get("/cb-list/screen/intraday")
def screen_intraday(
    request: Request,
    ratings: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """盘中选债: 实时拉集思录列表+强赎 → 纯条件过滤(不打分不排序) → 剔除黑名单。

    输入约束由 IntradayFilterQuery 服务端强制(P2-R01): 价格/规模/年限非负、
    区间顺序、非有限数一律 422; 前端校验仅是即时反馈, 可被其他客户端绕过。

    字段对齐集思录筛选页: 转债价格区间/溢价率≤/剩余规模≤/剩余年限区间/
    到期收益率≥(简化口径: (赎回价-现价)/现价) + 评级多选。
    返回全部通过条件的债(顺序=集思录自然顺序, 默认双低升序)。
    不读快照、不落库: 价格/双低/溢价率/强赎计数全部是当次请求的实时值。
    盘后调用返回当日收盘数据(比日频任务快照更新)。
    耗时约 1~2s(两次实时 HTTP), 前端超时需放宽。

    黑名单联动: 查询时自动排除用户拉黑的转债, 返回 meta.blacklisted_count。
    """
    # 手动构建模型: Depends() 查询模式与 model_validator 抛错交互怪异,
    # 显式校验 + 统一 422 更可控
    try:
        query = IntradayFilterQuery.model_validate(request.query_params)
        filters = query.model_dump()
        # ratings 以逗号分隔传递: ?ratings=AA,AA+ (NONE=无评级占位符)
        filters["ratings"] = normalize_ratings(ratings.split(",") if ratings else [])
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0].get("msg", "参数非法"))

    try:
        result = screen_bonds_intraday(filters)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"实时数据拉取失败: {exc}")

    # 黑名单联动: 从结果中剔除被拉黑的转债
    blacklist_ids = get_blacklist_ids(db)
    if blacklist_ids:
        original_count = len(result.get("rows", []))
        result["rows"] = [
            r for r in result["rows"] if str(r.get("code")) not in blacklist_ids
        ]
        result["total_filtered"] = len(result["rows"])
        result["blacklisted_count"] = original_count - len(result["rows"])
    else:
        result["blacklisted_count"] = 0

    return result


# ---------------------------------------------------------------------------
# 黑名单管理
# ---------------------------------------------------------------------------

@router.get("/cb-list/blacklist")
def list_blacklist(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """返回全部黑名单列表(按拉黑时间倒序)。"""
    return get_blacklist(db)


@router.post("/cb-list/blacklist")
def add_blacklist(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """拉黑一只转债(幂等: 已存在则更新 reason)。

    body: {bond_id, bond_nm?, reason?}
    """
    bond_id = str(body.get("bond_id") or "").strip()
    if not bond_id:
        raise HTTPException(status_code=400, detail="bond_id 不能为空")
    bond_nm = body.get("bond_nm")
    reason = body.get("reason")
    return add_to_blacklist(db, bond_id, bond_nm=bond_nm, reason=reason)


@router.delete("/cb-list/blacklist/{bond_id}")
def remove_blacklist(bond_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """取消拉黑。"""
    removed = remove_from_blacklist(db, bond_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"黑名单中不存在 {bond_id}")
    return {"ok": True, "bond_id": bond_id}
