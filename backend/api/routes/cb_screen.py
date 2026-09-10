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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.schemas.cb_screen import (
    FactorsConfigModel,
    IntradayFilterQuery,
    SelectionConfigModel,
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
from backend.services.cb_screen import screen_bonds, screen_bonds_live

router = APIRouter()


@router.get("/cb-list/factors/catalog")
def get_factor_catalog() -> list[dict[str, str]]:
    """返回可用因子字段目录。"""
    return FACTOR_CATALOG


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


def _load_rows(db: Session) -> list[CbDailySnapshot]:
    """加载最新交易日的全量转债快照。"""
    latest = db.query(func.max(CbDailySnapshot.trade_date)).scalar()
    if not latest:
        return []
    return db.query(CbDailySnapshot).filter(
        CbDailySnapshot.trade_date == latest
    ).all()


def _load_redeem_map(db: Session) -> dict[str, dict[str, Any]]:
    """加载最新交易日的强赎列表快照, 返回 {bond_id: cell dict}。

    若 redeem 快照不存在, 返回空 dict(不影响筛选, 仅 redeem_safe_days 不生效)。
    """
    from backend.models.valuation import CbRedeemDaily

    latest = db.query(func.max(CbRedeemDaily.trade_date)).scalar()
    if not latest:
        return {}
    rows = db.query(CbRedeemDaily).filter(
        CbRedeemDaily.trade_date == latest
    ).all()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        result[r.bond_id] = {
            "redeem_icon": r.redeem_icon,
            "redeem_remain_days": r.redeem_remain_days,
            "redeem_real_days": r.redeem_real_days,
            "redeem_count_days": r.redeem_count_days,
            "redeem_total_days": r.redeem_total_days,
        }
    return result


@router.post("/cb-list/screen")
def screen(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    """按传入模板配置筛选打分。

    模板结构对齐 v2_cb_rotation(见 cb_factors.py DEFAULT_CONFIG)。
    body 可含 "source": "db"(默认, 读最新交易日快照) 或 "live"(实时拉集思录, 不落库)。
    """
    source = str(body.get("source") or "db").strip().lower()

    if source == "live":
        # 实时: 拉集思录 → 同一套打分引擎(不落库)
        try:
            from backend.services.queries.live import fetch_live_snapshot

            records, redeem_cells = fetch_live_snapshot()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"实时数据拉取失败: {exc}")
        result = screen_bonds_live(records, body, redeem_cells=redeem_cells)
        result["source"] = "live"
        return result

    rows = _load_rows(db)
    if not rows:
        return {"total_all": 0, "total_filtered": 0, "total_excluded": 0, "top_n": 0, "keep_n": 0, "rows": [], "excluded_rows": [], "source": "db"}

    redeem_map = _load_redeem_map(db)
    result = screen_bonds(rows, body, redeem_map=redeem_map)
    result["source"] = "db"
    return result


@router.get("/cb-list/screen/active")
def screen_active(db: Session = Depends(get_db)) -> dict[str, Any]:
    """按当前 active 模板筛选打分。"""
    tmpl = get_active_template()
    if tmpl is None:
        raise HTTPException(status_code=400, detail="未找到可用因子模板")

    rows = _load_rows(db)
    if not rows:
        return {"total_all": 0, "total_filtered": 0, "top_n": 0, "keep_n": 0, "rows": []}

    redeem_map = _load_redeem_map(db)
    result = screen_bonds(rows, tmpl, redeem_map=redeem_map)
    result["template_id"] = tmpl.get("id")
    result["template_name"] = tmpl.get("name")
    return result


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
