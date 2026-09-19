# -*- coding: utf-8 -*-
"""数据源可达性探针(只读, 不写入任何业务数据)。

用途: 在目标运行环境(尤其是阿里云 ECS)上验证候选数据源是否可达、是否被限流。
本仓库已记录: 东财 push2* 行情族对 ECS IP 首请求后即封禁(约 2 分钟起, 持续 30 分钟+)。
天天基金(api.fund / fundmobapi)不属于 push2 族, 需实测确认。

用法:
    python scripts/probe_data_source.py                 # 探测全部候选(每目标 1 个请求)
    python scripts/probe_data_source.py --only fund     # 只探测场外基金候选

判定:
    OK            首请求拿到真实数据( payload 非空 )
    RATE_LIMITED  返回 ErrCode!=0 / payload 为空(限流; 窗口性, 触发后 >=30 分钟不可用)
    BLOCKED       首请求即失败(不可达/被封/non-json)

⚠ 使用纪律(与项目对集思录等数据源的既有做法一致):
   本脚本**只做可达性判断**, 每个目标仅 1 个请求。
   禁止用于压测、连发探测、批量拉取 —— 限流阈值无需测出, 按保守口径设计即可。
   批量抓取一律走正式 fetcher: 单标的串行 + 页间隔 + 指数退避 + 断点续传 + 日频增量只拉最近。
   选型依据 = 完成一次任务所需的请求数(越少越克制), 而非"能承受多大并发"。

注意: 东财系接口在限流时仍返回 HTTP 200, 响应体形如
      {"Datas":null,"ErrCode":61136403,"ErrMsg":"网络繁忙，请稍后重试！"}
      因此**绝不能**只用子串是否出现来判断成功, 必须解析 payload 是否非空。
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
TIMEOUT = 15.0


@dataclass
class Target:
    key: str
    label: str
    url: str
    kind: str                     # 判定器类型, 见 probe_once
    headers: dict[str, str] = field(default_factory=dict)
    note: str = ""

    # 判定器约定: 一律解析 JSON payload 是否非空, 而非子串匹配
    #   fund_web      -> Data.LSJZList 非空 list 且 ErrCode==0
    #   fund_mobile   -> Datas 非空 list
    #   tencent_text  -> 响应为 JSON(带 code) 或纯文本, 含 "day" 且长度>200
    #   push2his      -> data.klines 非空 list


def build_targets() -> list[Target]:
    return [
        Target(
            key="tencent_fqkline",
            label="腾讯 fqkline 日线(对照组, ECS 已验收)",
            url="https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                "?param=sh600900,day,,,10,hfq",
            kind="tencent_text",
            note="股票/ETF 日线主力源, 预期 OK",
        ),
        Target(
            key="em_fund_web",
            label="天天基金 Web 历史净值 api.fund.eastmoney.com/f10/lsjz",
            url="http://api.fund.eastmoney.com/f10/lsjz?fundCode=000001&pageIndex=1&pageSize=5",
            kind="fund_web",
            headers={"Referer": "http://fundf10.eastmoney.com/jjjz_000001.html", "User-Agent": UA},
            note="pageSize 硬上限 20; Referer 为硬依赖(缺失返回 ErrCode -999); 字段最全(含 SGZT/SHZT/FHFCZ)",
        ),
        Target(
            key="em_fund_mobile",
            label="天天基金 移动端历史净值 fundmobapi/FundMNHisNetList (pageSize=20)",
            url="https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
                "?FCODE=000001&pageIndex=1&pageSize=20&plat=Android&appType=ttjj"
                "&product=EFund&Version=1&deviceid=1",
            kind="fund_mobile",
            headers={"User-Agent": UA},
            note="无 Referer 依赖; 缺申赎状态与分红字段; 用于连发探测限流",
        ),
        Target(
            key="em_fund_mobile_big",
            label="天天基金 移动端大分页 pageSize=1000(生产拟采用)",
            url="https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
                "?FCODE=000001&pageIndex=1&pageSize=1000&plat=Android&appType=ttjj"
                "&product=EFund&Version=1&deviceid=1",
            kind="fund_mobile",
            headers={"User-Agent": UA},
            note="全历史 6010 条仅需 7 页(对比 Web 接口 301 页); 复用同一判定器",
        ),
        Target(
            key="em_push2his",
            label="东财 push2his 行情(对照组, ECS 历史被封)",
            url="https://push2his.eastmoney.com/api/qt/stock/kline/get"
                "?secid=1.600900&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=1&beg=20240101&end=20240110",
            kind="push2his",
            headers={"User-Agent": UA},
            note="ECS 上预期 BLOCKED; 若 OK 说明封禁已解除, akshare 行情接口可复用",
        ),
    ]


def _throttle_hint(payload: dict) -> str:
    """东财系限流时仍返回 HTTP 200, 需显式识别。"""
    code = payload.get("ErrCode") or payload.get("ErrorCode") or payload.get("code")
    msg = payload.get("ErrMsg") or payload.get("ErrorMessage") or payload.get("message")
    if code not in (None, 0, "0"):
        return f"ErrCode={code} {msg or ''}".strip()
    return ""


def probe_once(client: httpx.Client, t: Target) -> tuple[str, str, int]:
    """返回 (status, detail, size); status ∈ OK / RATE_LIMITED / BLOCKED。"""
    try:
        resp = client.get(t.url, headers=t.headers, timeout=TIMEOUT)
    except Exception as exc:  # 超时/连接重置(RST 在 httpx 表现为异常)
        return "BLOCKED", f"{type(exc).__name__}: {exc}"[:120], 0

    body = resp.text
    size = len(body)
    detail = f"HTTP {resp.status_code}, {size}B"

    if resp.status_code != 200:
        return "BLOCKED", detail + f" | {body[:100]!r}", size

    # --- 结构化判定: payload 是否为空 ---
    payload: dict = {}
    rows: list = []
    try:
        obj = resp.json()
        if isinstance(obj, dict):
            payload = obj
    except Exception:
        payload = {}

    if t.kind == "fund_web":
        data = payload.get("Data") or {}
        rows = data.get("LSJZList") or []
    elif t.kind == "fund_mobile":
        rows = payload.get("Datas") or []
    elif t.kind == "push2his":
        rows = (payload.get("data") or {}).get("klines") or []
    elif t.kind == "tencent_text":
        if "day" in body and size > 200:
            return "OK", detail + f" | 文本含 day", size
        return "BLOCKED", detail + f" | 未取到 kline 文本 | {body[:100]!r}", size

    if rows:
        first = rows[0]
        sample = str(first)[:60]
        return "OK", detail + f" | rows={len(rows)} 首行={sample}", size

    hint = _throttle_hint(payload)
    if hint:
        # 业务层报错(如 61136403 网络繁忙) = 限流/风控, 与网络封禁区分开
        return "RATE_LIMITED", detail + f" | {hint}", size
    return "BLOCKED", detail + f" | payload 为空 | {body[:100]!r}", size


def main() -> int:
    ap = argparse.ArgumentParser(
        description="数据源可达性探针(克制版: 每个目标仅 1 个请求, 不测限流阈值)")
    ap.add_argument("--only", default="", help="只探测含该关键字的 target key")
    args = ap.parse_args()

    targets = [t for t in build_targets() if args.only in t.key]
    if not targets:
        print(f"no target matched --only={args.only!r}")
        return 2

    print(f"{'KEY':20s} {'VERDICT':14s}  DETAIL")
    print("-" * 110)
    verdicts: dict[str, str] = {}
    with httpx.Client(follow_redirects=True, verify=False) as client:
        for t in targets:
            status, detail, _ = probe_once(client, t)
            verdicts[t.key] = status
            print(f"{t.key:20s} {status:14s}  {detail}")

    print()
    print("=== 判定摘要 ===")
    for t in targets:
        print(f"  {verdicts[t.key]:14s} {t.label}")
        if t.note:
            print(f"                注: {t.note}")
    print()
    print("判定口径: OK = 首请求拿到非空 payload; RATE_LIMITED = ErrCode!=0(限流);")
    print("          BLOCKED = 不可达/被封/non-json。")
    print()
    print("⚠ 本脚本只做可达性判断(每目标 1 个请求), 不探测限流阈值、不做连发或批量拉取。")
    print("  阈值无需测出: 按本仓库既有结论「东财系触发后 >=30 分钟不可用」保守设计即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
