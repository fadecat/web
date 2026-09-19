# -*- coding: utf-8 -*-
"""把组合实验室跑到"页面上有数字"的一次性脚本: 注册标的 → 抓取 → 建组合 → 回测 → 刷缓存。

为什么需要它: P0~P3 打通后, 页面要看到东西还需要"库里有数据 + 有一个组合"。
新环境搭好或换库之后, 跑这一条就能直接开始看页面。

默认复刻韭圈儿的**基准组合 286241**(四只标的各 25%):
    161116 易方达黄金主题人民币A / 090010 大成中证红利指数A /
    100018 富国天利增长债券A / 513100 纳指ETF国泰

⚠ **会真实抓取**(场外基金走蛋卷、ETF 走腾讯), 但遵循数据源克制:
   串行、单标的失败不中断其余、重复执行幂等(注册去重 + 净值 upsert)。
   实测请求量级 ≈ 蛋卷 1 请求/只 + 腾讯 640 行/页 × 2 口径, 共约 11 个请求。

用法:
    python scripts/seed_baseline_portfolio.py                 # 抓取 + 建组合 + 回测
    python scripts/seed_baseline_portfolio.py --no-fetch      # 假设数据已同步, 只建组合/回测
    python scripts/seed_baseline_portfolio.py --symbols 600900.SH,100018.OF
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 允许从任意目录执行(与 scripts/migrate_db.py 同一套做法)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.models.database import SessionLocal  # noqa: E402
from backend.services import backtest_service, portfolio_assets, portfolio_store  # noqa: E402

logger = logging.getLogger("seed_baseline")

# (规范代码, 类型, 名称) —— 与韭圈儿 combination_details?combin_code=286241 一致
BASELINE = [
    ("161116.OF", "FUND", "易方达黄金主题人民币A"),
    ("090010.OF", "FUND", "大成中证红利指数A"),
    ("100018.OF", "FUND", "富国天利增长债券A"),
    ("513100.SH", "ETF", "纳指ETF国泰"),
]
DEFAULT_NAME = "基准组合（286241 复刻）"
DEFAULT_BENCHMARK = "000300"


def _register_and_sync(items, *, do_fetch: bool) -> list[int]:
    """注册 + 抓取, 返回标的 id 列表。抓取失败只记录, 不中断(组合仍可建, 缺数据的会被就绪检查拦下)。"""
    ids: list[int] = []
    with SessionLocal() as db:
        for symbol, security_type, name in items:
            try:
                # ⚠ `--symbols` 自定义时没有名称 → 用代码兜底, 不能让 UI 显示空白
                row = portfolio_assets.register(symbol, security_type, name or symbol, db=db)
            except Exception as exc:  # noqa: BLE001 单标的注册失败不中断其余
                logger.error("注册失败 %s: %s", symbol, exc)
                continue
            ids.append(row["id"])
            print(f"  注册 {symbol}  {name or symbol}  ({'已存在' if not row['created'] else '新建'})")

    if not do_fetch:
        print("  跳过抓取(--no-fetch)")
        return ids

    for security_id in ids:
        with SessionLocal() as db:
            from backend.models.research import ResearchSecurity

            security = db.get(ResearchSecurity, security_id)
            symbol = security.symbol if security else str(security_id)
        print(f"  抓取 {symbol} ...", end="", flush=True)
        outcome = portfolio_assets.sync_one(security_id)
        flag = "OK " if outcome["status"] == "success" else "失败"
        print(f" {flag} {outcome.get('rows', 0)} 行  {outcome.get('first_date')} ~ {outcome.get('last_date')}")
        if outcome["status"] != "success":
            print(f"      原因: {outcome.get('error')}")
    return ids


def _ensure_portfolio(name: str, weights: dict[str, float]) -> int:
    from sqlalchemy import select

    from backend.models.portfolio import Portfolio

    with SessionLocal() as db:
        existing = db.scalar(select(Portfolio).where(Portfolio.name == name, Portfolio.status == "active"))
        if existing is not None:
            portfolio_id = existing.id
            print(f"  复用已有组合 id={portfolio_id}")
        else:
            portfolio_id = portfolio_store.create_portfolio(db, name=name)["id"]
            print(f"  新建组合 id={portfolio_id}")

        for symbol in weights:
            try:
                portfolio_store.add_asset(db, portfolio_id, symbol)
            except portfolio_store.PortfolioError:
                pass  # 已在组合内(幂等)
        portfolio_store.set_weights(db, portfolio_id, weights)
    return portfolio_id


def main() -> int:
    parser = argparse.ArgumentParser(description="建一个可直接看页面的基准组合")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"组合名(默认: {DEFAULT_NAME})")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK, help="对照基准代码(默认 000300)")
    parser.add_argument("--symbols", default="", help="逗号分隔的自定义标的(需形如 600900.SH); 留空即用基准组合")
    parser.add_argument("--no-fetch", action="store_true", help="不抓取, 只用库里已有数据")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    items = [
        (code.strip().upper(), "FUND" if code.strip().upper().endswith(".OF") else "STOCK", "")
        for code in args.symbols.split(",") if code.strip()
    ] or BASELINE

    print("[1/4] 注册标的")
    ids = _register_and_sync(items, do_fetch=not args.no_fetch)
    if not ids:
        print("没有可用标的, 退出")
        return 1

    print("[2/4] 建组合并设权重")
    weights = {symbol: round(100.0 / len(items), 6) for symbol, _, _ in items}
    portfolio_id = _ensure_portfolio(args.name, weights)
    for symbol, weight in weights.items():
        print(f"  {symbol}  {weight:.2f}%")

    print("[3/4] 跑回测")
    with SessionLocal() as db:
        try:
            run = backtest_service.run_backtest(db, portfolio_id, benchmark_symbol=args.benchmark or None)
        except backtest_service.BacktestServiceError as exc:
            print(f"  回测未通过: {exc}")
            print("  提示: 先确认上面每个标的都抓取成功(失败的可单独重试)")
            return 1
        result = run["result"]
        print(f"  run_id={run['id']}  建仓日 T0={result['t0_date']}  "
              f"区间 {result['actual_start']} ~ {result['actual_end']}")
        print(f"  区间收益 {result['selected_return']:.2f}%   最大回撤 {result['selected_drawdown']:.2f}%")
        print(f"  收益条 " + "  ".join(
            f"{k}={v:.2f}%" for k, v in (result["window_returns"] or {}).items() if v is not None
        ))

        print("[4/4] 刷新列表页三格缓存")
        print(f"  {backtest_service.refresh_cached_metrics(db, portfolio_id)}")

    print()
    print(f"完成。打开前端后进入: /portfolios/{portfolio_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
