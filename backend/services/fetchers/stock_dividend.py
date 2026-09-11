# -*- coding: utf-8 -*-
"""集思录股息率排行页数据抓取(股票高股息快照)。

数据源: https://www.jisilu.cn/data/stock/dividend_rate/ 页面及其
POST /data/stock/dividend_rate_list/ 接口(需登录态)。

覆盖算法(自适应, 不硬编码节点; 移植自 .test-artifacts/ggx-research/sweep.py 原型):
- 行业树(申万) x 总市值下限组合覆盖; 每个树节点单独 POST 查询;
- count_Info 首数字 = 该节点真实总数, len(rows) >= total 即完整收下;
- total 超过 cap(普通会员=100, is_member 时=300)且有子节点 → 下钻子节点;
- total 超过 cap 且是叶子 → total_value 区间递归二分。

分层约定: 本模块是纯函数层 —— 只抓取与解析, 不写库、不写 data/state;
行业树缓存与落库副作用归任务层/store 层(见 stock_dividend_tasks / stock_dividend_store)。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any

import httpx

from backend.services.jisilu import get_cookie

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DIVIDEND_PAGE_URL = "https://www.jisilu.cn/data/stock/dividend_rate/"
DIVIDEND_LIST_URL = "https://www.jisilu.cn/data/stock/dividend_rate_list/"

# 普通会员单请求行数硬顶(实测); is_member=1 时接口允许 300, 算法自适应
CAP_MEMBER = 300
CAP_GUEST = 100

# 请求间隔(秒)与单请求失败退避(秒) —— 对源站礼貌
REQUEST_PAUSE = 1.5
RETRY_BACKOFF = (10.0, 20.0, 40.0)
RETRY_TIMES = 3  # 失败后退避重试次数(总尝试 = 1 + RETRY_TIMES)

# 叶子二分的开上界候选切点(总市值, 亿); 与下界几何中点二选一
MARKET_CAP_GRID = [30, 50, 80, 120, 200, 400, 800, 1500]

DIVIDEND_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.jisilu.cn/data/stock/dividend_rate/",
    "Origin": "https://www.jisilu.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# 查询表单骨架: 与页面浏览器请求一致; industry / total_value_a/b 由算法填充。
# 注: page/rp/market[] 服务端硬顶或忽略, 无分页旁路(研究已证, 勿再探测)。
BASE_FORM: dict[str, Any] = {
    "market[]": ["sh", "sz"],
    "industry": "",
    "province": "",
    "pe": "",
    "pb": "",
    "dividend_rate": "",
    "roe": "",
    "pe_temperature": "",
    "pb_temperature": "",
    "aft_dividend": "",
    "roe_average": "",
    "revenue_average": "",
    "profit_average": "",
    "eps_growth_ttm": "",
    "cashflow_average": "",
    "int_debt_rate": "",
    "total_value_a": "",
    "total_value_b": "",
    "float_value_a": "",
    "float_value_b": "",
    "rp": 25,
}


# ---------------------------------------------------------------------------
# 行业树解析
# ---------------------------------------------------------------------------

def parse_industry_options(html: str) -> list[dict[str, Any]]:
    """从股息率排行页 HTML 解析行业树下拉, 返回节点列表。

    集思录无行业树 JSON 接口, 树只在页面服务端渲染的
    <select id="select_industry"> 里; 每个非空 <option> 携带:
    value(树值) / data-idx(申万码) / data-level(层级) /
    data-cnts(全量计数) / data-nm(名称路径)。

    返回节点结构与树缓存文件一致:
        [{val, sw, nm, level, cnts}, ...](按页面出现顺序)。
    树结构编码在 val 前缀: 一级 2 位 / 二级 4 位 / 三级 6 位, 子 = 父前缀。
    """
    select_m = re.search(
        r'<select\b[^>]*\bid="select_industry"[^>]*>(.*?)</select>', html, re.S
    )
    if not select_m:
        return []
    body = select_m.group(1)

    def _attr(attrs: str, name: str) -> str:
        m = re.search(rf'{name}="([^"]*)"', attrs)
        return m.group(1).strip() if m else ""

    nodes: list[dict[str, Any]] = []
    for opt_m in re.finditer(r"<option\b([^>]*)>([^<]*)</option>", body):
        attrs = opt_m.group(1)
        value = _attr(attrs, "value")
        if not value:
            continue  # 「全部」占位项
        try:
            level = int(_attr(attrs, "data-level") or 0)
            cnts = int(_attr(attrs, "data-cnts") or 0)
        except ValueError:
            level, cnts = 0, 0
        nodes.append({
            "val": value,
            "sw": _attr(attrs, "data-idx"),
            "nm": _attr(attrs, "data-nm") or opt_m.group(2).strip(),
            "level": level,
            "cnts": cnts,
        })
    return nodes


def fetch_industry_tree(cookie: str | None = None) -> list[dict[str, Any]]:
    """抓取行业树(登录态 GET 页面 HTML 并解析)。

    参数:
        cookie: 集思录会话 cookie(调用方经 jisilu.get_cookie() 取得,
                整轮共用); 缺省时内部自取。

    返回: 节点列表(见 parse_industry_options); 页面无行业树时抛错。
    本函数只抓与解析, 不落盘(月度缓存归任务层)。
    """
    headers = dict(DIVIDEND_HEADERS)
    headers["Cookie"] = cookie if cookie else get_cookie()
    resp = httpx.get(DIVIDEND_PAGE_URL, headers=headers, timeout=15, follow_redirects=True)
    resp.raise_for_status()
    nodes = parse_industry_options(resp.text)
    if not nodes:
        raise ValueError("股息率排行页未解析到行业树(option), 页面结构可能已变更")
    return nodes


def build_tree_index(tree: list[dict[str, Any]]) -> tuple[list[str], dict[str, list[str]]]:
    """由节点列表构建 (根节点值列表, 父→子值映射)。

    层级编码在 value 前缀: 二级的父 = val[:2], 三级的父 = val[:4]。
    """
    by_val = {n["val"]: n for n in tree}
    children: dict[str, list[str]] = {n["val"]: [] for n in tree}
    roots: list[str] = []
    for node in sorted(tree, key=lambda x: x["val"]):
        if node.get("level") == 1:
            roots.append(node["val"])
            continue
        parent = node["val"][:2] if node.get("level") == 2 else node["val"][:4]
        if parent in children:
            children[parent].append(node["val"])
    return roots, children


# ---------------------------------------------------------------------------
# 快照抓取
# ---------------------------------------------------------------------------

def _parse_count_info(count_info: object) -> int:
    """count_Info 首数字 = 真实总数(如 '967 条'); 解析失败返回 0。"""
    m = re.match(r"(\d+)", str(count_info or ""))
    return int(m.group(1)) if m else 0


def fetch_dividend_snapshot(
    cookie: str | None = None,
    tree: list[dict[str, Any]] | None = None,
    *,
    min_total_value: float = 200,
) -> dict[str, Any]:
    """执行行业树 x 市值下限的自适应覆盖抓取, 返回当日快照。

    参数:
        cookie: 集思录会话 cookie(整轮共用, 不重复登录); 缺省内部自取。
        tree: 行业树节点列表(调用方缓存传入); 缺省时现场抓页面解析。
        min_total_value: 总市值下限(亿), 默认 200; 传 0/None 表示全市场。

    返回:
        {
          rows: [cell, ...](集思录原始 48 字段, 按 stock_id 去重),
          meta: {
            trade_date: 占多数的 last_dt(定稿交易日, 空数据时 None),
            node_count: 查询过的树节点数,
            request_count: 实际发出的 HTTP 请求数(含重试),
            failed_queries: 重试后仍失败的节点查询数(其行业分支会缺数),
            cap: 本轮生效的行数上限(100/300, 随 is_member 自适应),
            warnings: [str, ...](对账不一致/节点失败等),
            tree_drift: bool(缓存树陈旧信号),
          },
        }
    """
    if tree is None:
        tree = fetch_industry_tree(cookie)
    headers = dict(DIVIDEND_HEADERS)
    headers["Cookie"] = cookie if cookie else get_cookie()

    by_val = {n["val"]: n for n in tree}
    roots, children = build_tree_index(tree)

    cells: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    queried_nodes: set[str] = set()
    accepted_totals: list[int] = []
    stats = {"requests": 0, "failed": 0, "cap": CAP_GUEST, "drift": False}
    min_value = float(min_total_value) if min_total_value else None

    def _query(form: dict[str, Any], label: str) -> tuple[int, list[dict[str, Any]]]:
        """POST 一次列表接口; 失败退避重试 RETRY_TIMES 次, 仍失败返回 (0, [])。"""
        last_exc: Exception | None = None
        for attempt in range(1 + RETRY_TIMES):
            stats["requests"] += 1
            try:
                resp = httpx.post(
                    DIVIDEND_LIST_URL,
                    headers=headers,
                    params={"___jsl": f"LST___t={int(time.time() * 1000)}"},
                    data=form,
                    timeout=30,
                )
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise ValueError("接口返回非对象 JSON")
                total = _parse_count_info(payload.get("count_Info"))
                rows = payload.get("rows") or []
                if not isinstance(rows, list):
                    raise ValueError("接口返回 rows 非列表")
                # cap 随会员态自适应(普通会员=100, 升级会员=300 自动生效)
                if payload.get("is_member"):
                    stats["cap"] = CAP_MEMBER
                time.sleep(REQUEST_PAUSE)
                return total, rows
            except Exception as exc:  # noqa: BLE001 单请求失败不拖垮整轮
                last_exc = exc
                if attempt < RETRY_TIMES:
                    time.sleep(RETRY_BACKOFF[attempt])
        stats["failed"] += 1
        warnings.append(f"节点查询失败(已重试 {RETRY_TIMES} 次): {label}: {last_exc}")
        return 0, []

    def _accept(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            cell = row.get("cell") if isinstance(row, dict) else None
            if not isinstance(cell, dict):
                continue
            sid = str(cell.get("stock_id") or "").strip()
            if sid and sid not in cells:
                cells[sid] = cell

    def _form(industry: str, a: float | None, b: float | None) -> dict[str, Any]:
        form = dict(BASE_FORM)
        form["industry"] = industry
        form["total_value_a"] = str(int(a)) if a else ""
        form["total_value_b"] = str(int(b)) if b else ""
        return form

    def _bisect(industry: str, a: float | None, b: float | None) -> bool:
        """递归市值二分: 返回是否完整收齐。"""
        total, rows = _query(_form(industry, a, b), f"bisect[{a},{b}]")
        if not total and not rows:
            return False
        if len(rows) >= total:
            _accept(rows)
            accepted_totals.append(total)
            return True
        # 选切点: 有上界取几何中点, 开上界取网格里首个可用值
        if b:
            t = round(((a or 5) * b) ** 0.5)
            # a=None(全市场模式开下界)时无退化可比, 跳过守卫
            if a and t <= a:
                # 市值区间窄到切不出新端点, 防御性终止(实测未出现)
                warnings.append(f"市值二分区间不可再分: [{a}, {b}] total={total}")
                _accept(rows)
                return False
        else:
            t = next((g for g in MARKET_CAP_GRID if not a or g > a), (a or 0) * 2)
        return _bisect(industry, a, t) and _bisect(industry, t, b)

    def _fetch_node(val: str) -> bool:
        node = by_val.get(val) or {}
        queried_nodes.add(val)
        total, rows = _query(
            _form(val, min_value, None), f"{node.get('nm', '?')}({val})"
        )
        cnts = int(node.get("cnts") or 0)
        # 树陈旧信号①: 带市值过滤的 API total 不应超过树的全量计数;
        # 无市值过滤时 count_Info 与树计数应严格一致(原型即此用法)。
        # cnts 缺失(=0)时不比对 —— 树的 counts 仅是规划优化项, 可退化纯自适应。
        if cnts > 0 and ((total > cnts) or (not min_value and total != cnts)):
            stats["drift"] = True
        if len(rows) >= total:
            _accept(rows)
            accepted_totals.append(total)
            return True
        kids = children.get(val) or []
        if kids:
            ok = True
            for kid in kids:
                ok = _fetch_node(kid) and ok
            return ok
        # 叶子超 cap → 市值二分兜底
        return _bisect(val, min_value, None)

    for root in roots:
        _fetch_node(root)

    # 树陈旧信号②: 抓回行的 sw_cd 不以任何已查节点 value 为前缀(树缺新节点的信号)
    for cell in cells.values():
        sw_cd = str(cell.get("sw_cd") or "")
        if sw_cd and not any(sw_cd.startswith(v) for v in queried_nodes):
            stats["drift"] = True
            warnings.append(f"行 sw_cd={sw_cd} 不匹配已查行业节点(缓存树可能缺新节点)")
            break

    # 对账: Σ最终收下节点的 total vs 去重 stock_id 数
    sum_totals = sum(accepted_totals)
    if sum_totals != len(cells):
        warnings.append(f"覆盖对账不一致: Σ节点 total={sum_totals}, 去重股票数={len(cells)}")

    last_dt_counts = Counter(
        str(c.get("last_dt") or "") for c in cells.values() if c.get("last_dt")
    )
    trade_date = last_dt_counts.most_common(1)[0][0] if last_dt_counts else None

    return {
        "rows": list(cells.values()),
        "meta": {
            "trade_date": trade_date,
            "node_count": len(queried_nodes),
            "request_count": stats["requests"],
            "failed_queries": stats["failed"],
            "cap": stats["cap"],
            "warnings": warnings,
            "tree_drift": bool(stats["drift"]),
        },
    }
