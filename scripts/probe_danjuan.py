# -*- coding: utf-8 -*-
"""蛋卷基金(danjuanfunds.com)数据源 —— 接口结构验证器。

⚠ 使用纪律(与项目对集思录等数据源的既有做法一致):
  本脚本**只做接口结构验证**，默认仅发 1~3 个请求。
  严禁用于压测、连发探测、批量拉取。批量抓取必须走正式的 fetcher
  (单标的串行 + 页间隔 + 指数退避 + 断点续传 + 日频增量只拉最近)。

已验证结论(2026-09-19, 均为轻量验证):
  - 历史净值: GET /djapi/fund/nav/history/{code}?page=&size=
      - 无需 cookie、无 Referer 依赖
      - 响应含 total_items / total_pages, 可精确控制翻页
      - size 支持到 6000 → 一只老基金全历史**1~2 个请求**即可拉完
        (对比: 天天基金移动端 size=1000 需 7 页, Web 端 size=20 需 301 页)
      - 字段 percentage = **分红再投口径**日增长率(%)
        已用 000001 的历史除权日交叉验证: 单位净值口径 1.24x,
        链式 percentage 口径 7.23x, 差额即分红再投贡献
      - ⚠ 字段 value **恒等于 nav**, 不是累计净值, 勿当作复权序列使用
  - 基础信息: GET /djapi/fund/{code}
      - 含申赎费率(fund_rates)、申赎状态(declare_status/withdraw_status)、
        基金经理/托管行/风险等级 —— 这些正是天天基金移动端缺失的字段

复权净值构造(与天天基金口径一致):
    NAV_adj,t = NAV_0 × Π(1 + percentage_t / 100)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
NAV_URL = "https://danjuanfunds.com/djapi/fund/nav/history/{code}?page={page}&size={size}"
INFO_URL = "https://danjuanfunds.com/djapi/fund/{code}"
TIMEOUT = 30.0


def fetch(client: httpx.Client, url: str):
    """单次请求。返回 (ok, data_or_errmsg, bytes)。"""
    try:
        r = client.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:90], 0
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} {r.text[:70]!r}", len(r.content)
    try:
        payload = r.json()
    except Exception:
        return False, f"non-json {r.text[:70]!r}", len(r.content)
    code = payload.get("result_code")
    if code not in (None, 0, 200):
        return False, f"result_code={code} msg={payload.get('message')}", len(r.content)
    if not payload.get("data"):
        return False, f"empty data {r.text[:70]!r}", len(r.content)
    return True, payload["data"], len(r.content)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="蛋卷基金接口结构验证器(轻量, 禁止用于压测)")
    ap.add_argument("--codes", default="100018",
                    help="基金代码, 逗号分隔; 建议 <=3 只")
    ap.add_argument("--size", type=int, default=6000,
                    help="每页条数; 6000 可一页拉全历史, 请求数最少")
    ap.add_argument("--gap", type=float, default=3.0,
                    help="标的之间的间隔秒数(保持克制)")
    args = ap.parse_args()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if len(codes) > 5:
        print("拒绝执行: 本脚本仅用于结构验证, 请勿传入超过 5 只标的。", file=sys.stderr)
        return 2

    with httpx.Client(follow_redirects=True, verify=False) as c:
        for idx, code in enumerate(codes):
            t0 = time.time()
            ok, res, nbytes = fetch(c, NAV_URL.format(code=code, page=1, size=args.size))
            if not ok:
                print(f"{code}: FAIL {res}")
                continue
            items = res["items"]
            print(f"{code}: {len(items)} 行 / total={res['total_items']} / "
                  f"{res['total_pages']} 页 / {nbytes}B / {time.time()-t0:.1f}s")
            print(f"   区间 {items[-1]['date']} ~ {items[0]['date']}")
            print(f"   最新: {json.dumps(items[0], ensure_ascii=False)}")

            # 校验 percentage 是否为分红再投口径: 找 nav 跳空但 percentage 平滑的日子
            jumps = []
            for i in range(len(items) - 1):
                nav_d, nav_p = float(items[i]["nav"]), float(items[i + 1]["nav"])
                pct = float(items[i]["percentage"]) / 100.0
                if abs(nav_d / nav_p - 1.0 - pct) > 0.003:
                    jumps.append((items[i]["date"], items[i]["nav"],
                                  items[i]["percentage"], round((nav_d / nav_p - 1) * 100, 2)))
            if jumps:
                print(f"   除权日(percentage 与单位净值口径背离) {len(jumps)} 处, 例: {jumps[0]}")
            else:
                print("   未发现除权日(该基金期间无分红)")

            if idx < len(codes) - 1:
                time.sleep(args.gap)

        # 基础信息(只取第一只)
        print()
        ok, info, _ = fetch(c, INFO_URL.format(code=codes[0]))
        if ok:
            for k in ("fd_name", "found_date", "declare_status", "withdraw_status",
                      "risk_level", "keeper_name", "manager_name", "fund_rates"):
                print(f"  {k}: {json.dumps(info.get(k), ensure_ascii=False)[:160]}")
        else:
            print(f"  基础信息 FAIL {info}")

    print()
    print("注: 本次共用 %d 个请求。批量抓取请走正式 fetcher, 不要在探针里批量拉取。"
          % (len(codes) + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
