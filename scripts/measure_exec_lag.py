# -*- coding: utf-8 -*-
"""场外基金成交时点敏感性测量：当日净值成交 vs T+1 成交。

用途
----
回答"成交时点口径（歧义审计 D2）对回测影响有多大"。同一实现、只改**执行日**，
输出三档再平衡 × 两种口径的收益/回撤，与韭圈儿公布值对照。

结论（2026-09-19 实测）
--------------------
    再平衡    当日净值(复刻)          T+1 成交              差(收益)    差(回撤)
    不平衡    211.24%(+0.02pp)     211.81%(+0.59pp)      +0.57pp    -0.04pp
    季平衡    170.93%(+0.03pp)     168.15%(-2.75pp)      -2.78pp    -0.01pp
    年平衡    169.02%(+0.10pp)     170.91%(+1.99pp)      +1.89pp    +0.01pp

    → 收益受影响 0.6~2.8pp（足以让验收 FAIL）；**回撤几乎不受影响（≤0.05pp）**。

数据成本：只读，4 个请求（复用 verify_against_funddb 的取数函数）。

注：本工具用"全局交集"交易日作价格轴，与主回归脚本的"并集 + 前值填充"略有差异
（故不平衡基准为 211.24% 而非 211.15%）—— 此处关注的是**同一实现下两种口径之差**。
"""
from __future__ import annotations
import sys
import importlib

sys.path.insert(0, '.')
m = importlib.import_module('scripts.verify_against_funddb')

raw = m.load_fund_nav()          # {code: [(date, nav, pct), ...] 升序}
CODES, W, T0, END = m.CODES, m.WEIGHT, m.T0, m.END
WIN_START = '2016-09-14'
REF = {'none': (211.22, -16.15), 'quarterly': (170.90, -15.05), 'yearly': (168.92, -14.95)}


def cumulative(code):
    """全历史累计（首行归一 1.0），返回 {date: level}"""
    out, price = {}, 1.0
    for i, (d, _n, pct) in enumerate(raw[code]):
        if i == 0:
            out[d] = 1.0
            continue
        price *= (1 + pct)
        out[d] = price
    return out


CUM = {c: cumulative(c) for c in CODES}


def level_at(c, date):
    """>= date 的最后一个可用 level"""
    hit = None
    for d, v in CUM[c].items():
        if d <= date:
            hit = v
        else:
            break
    return hit


def union_dates(lag: int):
    """返回 (执行日, 计价日序列)。lag=0 → 执行日=T0；lag=1 → 执行日=T0 之后第一个共同交易日。"""
    inter = sorted(set.intersection(*[set(CUM[c]) for c in CODES]))
    i = inter.index(T0)
    exec_date = inter[i + lag]
    return exec_date, inter[i + lag:]


def run(lag: int, freq: str):
    exec_date, dates = union_dates(lag)
    # 各标的以"执行日"归一为 1.0
    scale = {c: level_at(c, exec_date) for c in CODES}
    adj = {c: {d: CUM[c][d] / scale[c] for d in dates} for c in CODES}

    shares = {c: W[c] for c in CODES}
    rebal = m.rebalance_dates(dates, freq) if freq != 'none' else set()
    if lag == 1 and rebal:                    # 调仓日的下一交易日才成交
        idx = {d: i for i, d in enumerate(dates)}
        rebal = {dates[idx[d] + 1] for d in rebal if d in idx and idx[d] + 1 < len(dates)}

    latest, series = {}, []
    for d in dates:
        for c in CODES:
            latest[c] = adj[c][d]
        nav = sum(shares[c] * latest[c] for c in CODES)
        series.append((d, nav))
        if d in rebal:
            shares = {c: nav * W[c] / latest[c] for c in CODES}

    def nav_le(t):
        hit = None
        for d, v in series:
            if d <= t:
                hit = (d, v)
            else:
                break
        return hit

    s, e = nav_le(WIN_START), nav_le(END)
    ret = (e[1] / s[1] - 1) * 100
    win = [(d, v) for d, v in series if WIN_START <= d <= END]
    peak, mdd = win[0][1], 0.0
    for _, v in win:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return ret, mdd * 100, exec_date


print('场外基金成交时点敏感性（区间 2016-09-14 ~ 2026-09-18，与韭圈儿对照）')
print()
hdr = (f"{'再平衡':10s}{'口径':16s}{'建仓/执行日':>14s}{'收益':>11s}{'差(韭圈儿)':>12s}"
       f"{'回撤':>11s}{'差(韭圈儿)':>12s}")
print(hdr)
print('-' * len(hdr))
for freq, label in [('none', '不平衡'), ('quarterly', '季平衡'), ('yearly', '年平衡')]:
    for lag, tag in [(0, '当日净值(复刻)'), (1, 'T+1 成交')]:
        ret, mdd, ed = run(lag, freq)
        br, bm = REF[freq]
        print(f'{label:10s}{tag:16s}{ed:>14s}{ret:>10.2f}%{ret - br:>+11.2f}pp'
              f'{mdd:>10.2f}%{mdd - bm:>+11.2f}pp')
    print()

print('参考：韭圈儿公布值')
for freq, label in [('none', '不平衡'), ('quarterly', '季平衡'), ('yearly', '年平衡')]:
    br, bm = REF[freq]
    print(f'  {label:10s}收益 {br:8.2f}%   回撤 {bm:8.2f}%')
