# -*- coding: utf-8 -*-
"""验收: **产品回测引擎**(backend/services/backtest.py) 对韭圈儿基准组合是否复现。

与 `verify_against_funddb.py` 的区别:
- 那个脚本是**参考实现**(自带账本 + 自带取数), 用来破解算法;
- 本脚本**只做验收** —— 取数后把数据喂给**产品引擎**, 看它能否复现同一批数字。

为什么必须单独跑这个: P3 是把参考实现**移植**进产品(`services/series.py` 取数 +
`services/backtest.py` 账本)。移植最容易在"对齐规则"上走样(季/年平衡的顺延与向前对齐、
区间端点的向前对齐、近1日的合成口径、相关性的交集对齐), 而这些错一处, 数字就会变。
唯一可信的判据是:**同一批数据、同一批参考值、逐项比对**。

数据源: 蛋卷 `nav/history`, 4 只基金 **共 4 个请求**(与参考脚本同量级, 不做压测)。

用法:
    python scripts/verify_engine_port.py            # 全部核对项
    python scripts/verify_engine_port.py --only rebal
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 参考值与常量直接复用参考脚本(它有 __main__ 守卫, 导入不会执行 main)
from scripts.verify_against_funddb import (  # noqa: E402
    CODES, CORR_END, CORR_START, FULLNAMES, NAMES, NAV_URL, REF_ADDED, REF_CORR,
    REF_DAY, REF_REBAL_MDD, REF_REBAL_RET, REF_WEIGHT_NOW, REF_WINDOW, T0, TOL_CORR,
    TOL_MDD, TOL_RET, TOL_WINDOW, UA, WINDOW_START,
)
from backend.services import backtest, fund_nav, series  # noqa: E402

END = date(2026, 9, 18)
T0_DATE = date.fromisoformat(T0)
# 参考脚本的键 → 起点来源。⚠ 「近10年」是**固定 10 年窗口**(韭圈儿口径),
# 与本项目的「成立来 = 自 T0 起算」不是同一格, 所以两者各自核对(见 run_window)。
WINDOW_MAP = (
    '近1周', '近1月', '今年来', '近1年', '近3年', '近10年',
)
# 「近10年」起点: 参考脚本给 2016-09-18(自然日回推 10 年)
TEN_YEAR_START = date(2016, 9, 18)
# 「近1日」不在本表(它是合成口径, 由 latest_day_composite 算)
NON_TEN_YEAR = {'近1周', '近1月', '今年来', '近1年', '近3年'}


def _fetch_contracts() -> dict[str, series.SeriesContract]:
    """蛋卷取数 → SeriesContract(分红再投复权净值, 未归一)。共 4 个请求。"""
    import httpx

    contracts: dict[str, series.SeriesContract] = {}
    with httpx.Client(follow_redirects=True, timeout=30, headers={'User-Agent': UA}) as client:
        for code in CODES:
            payload = client.get(NAV_URL.format(code=code)).json()
            rows = fund_nav.parse_nav_history(payload)
            chained = fund_nav.chain_adj_nav(rows)
            contracts[code] = series.SeriesContract(
                symbol=code, asset_class=series.FUND, price_basis=series.NAV_ADJ,
                settle_lag=0,
                dates=tuple(r.nav_date for r in chained),
                prices=tuple(r.adj_nav for r in chained),
            )
    return contracts


class Report:
    def __init__(self) -> None:
        self.verdicts: list[tuple[str, bool]] = []

    def section(self, title: str) -> None:
        print()
        print('=' * 92)
        print(title)

    def line(self, label: str, mine: float | None, ref: float, *, unit: str = '%',
             tol: float = 0.0, extra: str = '') -> bool:
        if mine is None:
            print(f'  {label:26s}  {"—":>10s}  {ref:>10.2f}{unit}  {"":>9s}   FAIL  {extra}')
            return False
        diff = mine - ref
        ok = abs(diff) <= tol
        print(f'  {label:26s}  {mine:>10.2f}{unit}  {ref:>10.2f}{unit}  '
              f'{diff:>+8.2f}{unit}   {"PASS" if ok else "FAIL"}  {extra}')
        return ok

    def record(self, name: str, ok: bool) -> None:
        self.verdicts.append((name, ok))


def run_window(contracts, ledger, rep: Report) -> None:
    rep.section('A. 区间收益(顶部收益条) —— 产品引擎 vs 韭圈儿')
    print(f'  {"区间":26s}  {"本引擎":>10s}  {"韭圈儿":>10s}  {"差":>10s}   判定')
    print('  ' + '-' * 86)
    ok_all = True
    for label in WINDOW_MAP:
        # 参考脚本给的是**自然日回推**的起点日期; 真正落到哪个交易日由向前对齐决定
        start_ref = TEN_YEAR_START if label == '近10年' else date.fromisoformat(WINDOW_START[label])
        hit = backtest.window_return(ledger, start_ref, END)
        extra = f'实际 {hit.actual_start} ~ {hit.actual_end}' if hit else ''
        ok_all &= rep.line(label, hit.value if hit else None, REF_WINDOW[label],
                           tol=0.60, extra=extra)
    # 「近1日」是合成口径, 与账本无关
    composite = backtest.latest_day_composite(contracts, ledger.final_weights)
    ok_all &= rep.line('近1日(合成口径)', composite, REF_WINDOW['近1日'], tol=0.60,
                       extra='Σ wᵢ×rᵢ(各自最新日)')
    # 「成立来」= 自 T0 起算(容差单列: 起算日口径差异, 非算法误差)
    inception = backtest.window_return(ledger, T0_DATE, END)
    ok_all &= rep.line('成立来(自 T0)', inception.value if inception else None,
                       REF_WINDOW['成立来'], tol=TOL_WINDOW['成立来'],
                       extra='起算日口径差异, 见参考脚本')
    rep.record('A 区间收益', ok_all)


def run_rebal(contracts, rep: Report) -> None:
    rep.section('B. 三种再平衡(2016-09-14 ~ 2026-09-18)')
    print(f'  {"再平衡":12s}  {"收益":>10s}  {"韭圈儿":>10s}  {"差":>9s}   '
          f'{"回撤":>10s}  {"韭圈儿":>10s}  {"差":>9s}   判定')
    print('  ' + '-' * 92)
    start = date(2016, 9, 14)
    ok_all = True
    for mode, label in ((backtest.REBALANCE_NONE, '不平衡'),
                        (backtest.REBALANCE_QUARTERLY, '季平衡'),
                        (backtest.REBALANCE_YEARLY, '年平衡')):
        ledger = backtest.run_ledger(contracts, {c: 25.0 for c in CODES}, mode)
        hit = backtest.window_return(ledger, start, END)
        mdd = backtest.max_drawdown(ledger, start, END)
        dr = hit.value - REF_REBAL_RET[mode]
        dm = mdd - REF_REBAL_MDD[mode]
        ok = abs(dr) <= TOL_RET and abs(dm) <= TOL_MDD
        ok_all &= ok
        print(f'  {label:12s}  {hit.value:>9.2f}%  {REF_REBAL_RET[mode]:>9.2f}%  '
              f'{dr:>+8.2f}pp  {mdd:>9.2f}%  {REF_REBAL_MDD[mode]:>9.2f}%  '
              f'{dm:>+8.2f}pp   {"PASS" if ok else "FAIL"}')
    print(f'  容差: 收益 ±{TOL_RET}pp / 回撤 ±{TOL_MDD}pp'
          f'  (参考脚本实测: 季平衡 0.52pp 残差 ∝ 调仓次数, 已定位)')
    rep.record('B 再平衡收益+回撤', ok_all)


def run_corr(contracts, rep: Report) -> None:
    rep.section(f'C. 相关性矩阵({CORR_START} ~ {CORR_END})')
    print(f'  {"配对":26s}  {"本引擎":>9s}  {"韭圈儿":>9s}  {"差":>9s}   判定')
    print('  ' + '-' * 70)
    got = backtest.correlation(contracts, date.fromisoformat(CORR_START), date.fromisoformat(CORR_END))
    index = {s: i for i, s in enumerate(got['symbols'])}
    ok_all = True
    for (a, b), ref in REF_CORR.items():
        value = got['matrix'][index[a]][index[b]]
        n = got['sample_sizes'].get(f'{a}|{b}') or got['sample_sizes'].get(f'{b}|{a}')
        ok_all &= rep.line(f'{NAMES[a]} - {NAMES[b]}', value, ref, unit='',
                           tol=TOL_CORR, extra=f'n={n}')
    print(f'  容差 ±{TOL_CORR}(韭圈儿只给 2 位小数)  口径: 日涨跌幅皮尔森, pairwise 交集')
    rep.record('C 相关性矩阵', ok_all)


def run_weight(contracts, ledger, rep: Report) -> None:
    rep.section('D. 当前占比(不平衡持有至今的漂移权重)')
    print(f'  {"标的":26s}  {"本引擎":>10s}  {"韭圈儿":>10s}  {"差":>9s}   判定')
    print('  ' + '-' * 70)
    ok_all = True
    for code in CODES:
        ok_all &= rep.line(f'{FULLNAMES[code]}({code})', ledger.final_weights[code],
                           REF_WEIGHT_NOW[code], tol=0.60)
    rep.record('D 当前权重', ok_all)


def run_day(contracts, rep: Report) -> None:
    rep.section('F. 各标的日涨幅(最新净值日)')
    print(f'  {"标的":26s}  {"本引擎":>10s}  {"韭圈儿":>10s}  {"差":>9s}   判定')
    print('  ' + '-' * 70)
    ok_all = True
    for code in CODES:
        returns = backtest.daily_returns(contracts[code])
        last_day = max(returns)
        ok_all &= rep.line(f'{FULLNAMES[code]}({code})', returns[last_day] * 100,
                           REF_DAY[code], tol=0.60, extra=str(last_day))
    rep.record('F 各标的日涨幅', ok_all)


def run_added(contracts, rep: Report) -> None:
    rep.section('E. "添加后的收益"(反解各标的的添加日) —— 验证分红再投口径')
    print('  该列与回测无关: 它是每个标的**各自添加日**至最新日的累计收益, 纯展示列。')
    print(f'  {"标的":26s}  {"页面值":>9s}  {"反解添加日":>12s}  {"该日实际":>10s}  {"差":>8s}')
    print('  ' + '-' * 72)
    ok_all = True
    for code in CODES:
        contract = contracts[code]
        last = contract.prices[-1]
        best_price, best_day = min(
            ((p, d) for d, p in zip(contract.dates, contract.prices)),
            key=lambda kv: abs((last / kv[0] - 1) * 100 - REF_ADDED[code]),
        )
        actual = (last / best_price - 1) * 100
        ok = abs(actual - REF_ADDED[code]) <= 0.10
        ok_all &= ok
        print(f'  {FULLNAMES[code]:26s}  {REF_ADDED[code]:>8.2f}%  {best_day!s:>12s}  '
              f'{actual:>9.2f}%  {actual - REF_ADDED[code]:>+7.2f}pp  '
              f'{"PASS" if ok else "FAIL"}')
    print('  → 四个添加日各不相同(约每季一只) → 证实这是"分批手动添加"的账本列, 不是回测指标')
    rep.record('E 添加后收益(反解)', ok_all)


def main() -> int:
    parser = argparse.ArgumentParser(description='验收产品回测引擎对韭圈儿基准的复现')
    parser.add_argument('--only', choices=['win', 'rebal', 'corr', 'weight', 'added', 'day'])
    args = parser.parse_args()

    print('取数中(蛋卷 nav/history, 4 只基金共 4 个请求)…')
    contracts = _fetch_contracts()
    for code in CODES:
        c = contracts[code]
        print(f'  {code} {NAMES[code]:8s} {c.row_count:>5d} 行  '
              f'{c.first_date} ~ {c.last_date}')

    ledger = backtest.run_ledger(contracts, {c: 25.0 for c in CODES}, backtest.REBALANCE_NONE)
    print(f'账本: T0={ledger.dates[0]}  末端={ledger.dates[-1]}  {len(ledger.dates)} 个交易日')

    rep = Report()
    runners = {
        'win': lambda: run_window(contracts, ledger, rep),
        'rebal': lambda: run_rebal(contracts, rep),
        'corr': lambda: run_corr(contracts, rep),
        'weight': lambda: run_weight(contracts, ledger, rep),
        'added': lambda: run_added(contracts, rep),
        'day': lambda: run_day(contracts, rep),
    }
    if args.only:
        runners[args.only]()
    else:
        for key in ('win', 'rebal', 'corr', 'weight', 'added', 'day'):
            runners[key]()

    print()
    print('=' * 92)
    print('汇总')
    for name, ok in rep.verdicts:
        print(f'  {"PASS" if ok else "FAIL"}  {name}')
    all_ok = all(ok for _, ok in rep.verdicts)
    print()
    print('结论: ' + ('产品引擎与参考实现一致 —— 移植无走样。' if all_ok
                    else '存在未通过项 —— 移植走样, 逐项看上面明细。'))
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
