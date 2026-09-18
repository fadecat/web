# -*- coding: utf-8 -*-
"""做T策略模拟器 · 显式底仓库存模型 (v2, 2026-09-18)

与初版的关键差异:
  1. 复权: 用通达信 不复权/后复权 双序列自行构造分段常数因子 F, 后复权空间 = 总回报空间。
     研究模块返回的 scale 不是复权因子(每天变化), 不能用。
  2. 档位以「距 T 日收盘的百分比距离」表达, 与复权方式无关。
  3. 库存: shares = BASE_SH + (#未平买循环 - #未平卖循环) * U,
     约束是【每一侧各自的未平循环数 <= K】, 不是净头寸。
     (只约束净头寸会导致买卖两侧同时无限累积 —— 初版的 bug)
  4. 成本: max(名义 x 费率, 最低佣金) + 卖出印花税。
  5. 同日反手: 只有开盘价已越过挂单价时才认定当日完成(路径保守); path_opt=True 放开。

用法:
    from scripts.t_sim_core import simulate, TL, HFQ, BY_EVAL, levels_dist
    r = simulate(delta=0.013, K=3, U=1000, maxhold=10)

依赖数据文件(需自备):
    .tmp_d.json     /api/research/replays/{run_id}/days 的返回
    .tmp_ohlc.json  {"raw": {date: [O,H,L,C]}, "hfq": {...}} 通达信 period=4, tqFlag=0 / 2
"""


import json
import statistics as st

# ---------------- 数据 ----------------
_d = json.load(open('.tmp_d.json', encoding='utf-8'))
DAYS = sorted((_d if isinstance(_d, list) else _d['days']), key=lambda x: x['plan_date'])
_oh = json.load(open('.tmp_ohlc.json', encoding='utf-8'))
RAW0, HFQ0 = _oh['raw'], _oh['hfq']

EVALOH = {}
for x in DAYS:
    e = x.get('eval_date')
    if e and x.get('next_low') is not None and x.get('next_high') is not None:
        EVALOH[e] = [x['next_open'], x['next_high'], x['next_low'], x['next_close']]

TL = sorted(set(RAW0) | {x['plan_date'] for x in DAYS} | set(EVALOH))
RAW = {}
for d in TL:
    if d in RAW0 and all(v is not None for v in RAW0[d]):
        RAW[d] = [float(v) for v in RAW0[d]]
    elif d in EVALOH and all(v is not None for v in EVALOH[d]):
        RAW[d] = [float(v) for v in EVALOH[d]]
MISS = [d for d in TL if d not in RAW]
TL = [d for d in TL if d in RAW]

# 分段常数复权因子 F: TR = raw * F, 且 F 仅在真除权日跳变
# 真除权判据: 当日 raw 收益率与 hfq 收益率偏离 > 1.5%(分红造成的缺口)
fk = sorted(RAW0)
rr = {fk[i]: RAW0[fk[i]][3] / RAW0[fk[i - 1]][3] for i in range(1, len(fk))}
hh = {fk[i]: HFQ0[fk[i]][3] / HFQ0[fk[i - 1]][3] for i in range(1, len(fk))}
EXDIV = [d for d in fk[1:] if abs(hh[d] - rr[d]) > 0.015]
MULT = {}
m = 1.0
for d in fk:
    if d in EXDIV:
        m *= (1 + hh[d]) / (1 + rr[d])
    MULT[d] = m
MEND = MULT[fk[-1]]
F_OF = {d: MULT[d] / MEND for d in fk}          # 归一: F(末日)=1 → TR 价格为期末元
FKEYS = fk


def F_of(d):
    if d in F_OF:
        return F_OF[d]
    lo = [k for k in FKEYS if k <= d]
    return F_OF[lo[-1]] if lo else F_OF[FKEYS[0]]


HFQ = {d: [v * F_of(d) for v in RAW[d]] for d in TL}
IDX = {d: i for i, d in enumerate(TL)}
BY_EVAL = {x['eval_date']: x for x in DAYS if x.get('eval_date')}
BY_PLAN = {x['plan_date']: x for x in DAYS}
N = len(TL)
YRS = N / 243.0

# 振幅三分解(相对前收, 后复权空间 → 不受除权污染)
UP = {TL[i]: (HFQ[TL[i]][1] - HFQ[TL[i - 1]][3]) / HFQ[TL[i - 1]][3] for i in range(1, N)}
DOWN = {TL[i]: (HFQ[TL[i - 1]][3] - HFQ[TL[i]][2]) / HFQ[TL[i - 1]][3] for i in range(1, N)}
RANGE = {TL[i]: (HFQ[TL[i]][1] - HFQ[TL[i]][2]) / HFQ[TL[i - 1]][3] for i in range(1, N)}
RET = {TL[i]: HFQ[TL[i]][3] / HFQ[TL[i - 1]][3] - 1 for i in range(1, N)}

P_START, P_END = HFQ[TL[0]][3], HFQ[TL[-1]][3]


def levels_dist(r):
    """返回 (买档距离%, 卖档距离%) —— 相对 T 日收盘, 复权无关"""
    c = (r.get('evidence') or {}).get('close_raw_T')
    if not c or not r.get('buy_levels_raw') or not r.get('sell_levels_raw'):
        return None
    return ([(c - p) / c for p in r['buy_levels_raw']],
            [(p - c) / c for p in r['sell_levels_raw']])


# ---------------- 模拟器 ----------------
def simulate(delta=0.010, K=3, maxhold=10, U=1000, BASE_SH=10000,
             rate=0.00025, minfee=5.0, stamp=0.0005, buf=0.0,
             seg=None, path_opt=False, oracle=None, only=None,
             dists=None, Kb=None, Ks=None, dgrid=None, verbose=False, Kfun=None, ffun=None,
             stop=None):
    """显式底仓库存模型。所有 PnL 在后复权(总回报)空间。

    oracle: None | 'fill'(只挂必赚的单) | 'trend'(方向过滤) | 'delta'(完美反手目标)
    buf   : 成交缓冲, 买单需 L<=p*(1-buf), 卖单需 H>=p*(1+buf)
    """
    Kb = K if Kb is None else Kb
    Ks = K if Ks is None else Ks
    shares = BASE_SH
    cash = 0.0            # 后复权货币单位
    pend = []
    cyc = cyc_force = fills = 0
    gross = 0.0           # 已完成循环的价格差(含强平)
    gross_tgt = 0.0
    _tc = [0.0]
    _occ = [0.0, 0.0, 0.0, 0.0]   # sum_open_buy, sum_open_sell, bind_buy_days, bind_sell_days
    nstop = [0]

    def cost(side, px_hfq, d):
        px_real = px_hfq / F_of(d)
        notional = px_real * U
        c = max(notional * rate, minfee)
        if side < 0:
            c += notional * stamp
        v = c * F_of(d)
        _tc[0] += v
        return v

    def max_high(k0, k1):
        return max((HFQ[TL[j]][1] for j in range(max(k0, 0), min(k1 + 1, N))), default=float('-inf'))

    def min_low(k0, k1):
        return min((HFQ[TL[j]][2] for j in range(max(k0, 0), min(k1 + 1, N))), default=float('inf'))

    def will_complete(side, p, k):
        """oracle='fill': 该单今日能否成交并在 maxhold 内触及反手目标"""
        O0, H0, L0 = HFQ[TL[k]][0], HFQ[TL[k]][1], HFQ[TL[k]][2]
        if side > 0:
            if L0 > p * (1 - buf):
                return False, None
            entry = min(p, O0)
            tgt = entry * (1 + delta)
            if O0 <= entry and H0 >= tgt * (1 + buf):
                return True, (entry, tgt)
            j = min(k + maxhold, N - 1)
            return (max_high(k + 1, j) >= tgt * (1 + buf)), (entry, tgt)
        else:
            if H0 < p * (1 + buf):
                return False, None
            entry = max(p, O0)
            tgt = entry * (1 - delta)
            if O0 >= entry and L0 <= tgt * (1 - buf):
                return True, (entry, tgt)
            j = min(k + maxhold, N - 1)
            return (min_low(k + 1, j) <= tgt * (1 - buf)), (entry, tgt)

    for k, e in enumerate(TL):
        if seg and not (seg[0] <= e <= seg[1]):
            continue
        O, H, L, C = HFQ[e]

        # 1) 处理挂起的反手单
        np_ = []
        for p_ in pend:
            # 止损优先(保守: 假设日内先触及止损)
            exited = False
            if stop:
                if p_['side'] > 0:
                    sl = p_['entry'] * (1 - stop)
                    if L <= sl:
                        xp = min(sl, O)
                        shares -= U; cash += xp * U; cash -= cost(-1, xp, e)
                        gross += (xp - p_['entry']) * U
                        nstop[0] += 1
                        exited = True
                else:
                    sl = p_['entry'] * (1 + stop)
                    if H >= sl:
                        xp = max(sl, O)
                        shares += U; cash -= xp * U; cash -= cost(+1, xp, e)
                        gross += (p_['entry'] - xp) * U
                        nstop[0] += 1
                        exited = True
            if exited:
                cyc += 1
                fills += 1
                continue
            if p_['side'] > 0:                       # 曾买入, 等卖出
                hit = H >= p_['tgt'] * (1 + buf)
            else:
                hit = L <= p_['tgt'] * (1 - buf)
            if hit:
                if p_['side'] > 0:
                    shares -= U
                    cash += p_['tgt'] * U
                    cash -= cost(-1, p_['tgt'], e)
                    gross += (p_['tgt'] - p_['entry']) * U
                    gross_tgt += (p_['tgt'] - p_['entry']) * U
                else:
                    shares += U
                    cash -= p_['tgt'] * U
                    cash -= cost(+1, p_['tgt'], e)
                    gross += (p_['entry'] - p_['tgt']) * U
                    gross_tgt += (p_['entry'] - p_['tgt']) * U
                cyc += 1
                fills += 1
            elif maxhold and k - p_['i0'] >= maxhold:      # 强平并回底仓
                if p_['side'] > 0:
                    shares -= U
                    cash += C * U
                    cash -= cost(-1, C, e)
                    gross += (C - p_['entry']) * U
                else:
                    shares += U
                    cash -= C * U
                    cash -= cost(+1, C, e)
                    gross += (p_['entry'] - C) * U
                cyc += 1
                cyc_force += 1
                fills += 1
            else:
                np_.append(p_)
        pend = np_

        # 2) 执行当日新挂单
        r = BY_EVAL.get(e)
        if not r or r.get('status') != 'ACTIVE' or not r.get('buy_levels_raw'):
            continue
        if dists is not None:
            if r['plan_date'] not in dists:
                continue
            bd, sd = dists[r['plan_date']]
        else:
            _ld = levels_dist(r)
            if not _ld:
                continue
            bd, sd = _ld
        Pc = HFQ[TL[IDX[e] - 1]][3] if IDX[e] > 0 else C   # T 日(前一交易日)收盘

        # oracle='trend': 未来 maxhold 日方向
        if oracle == 'trend':
            j = min(k + maxhold, N - 1)
            fwd = HFQ[TL[j]][3] / C - 1
            skip_buy = fwd < -0.02
            skip_sell = fwd > 0.02
        else:
            skip_buy = skip_sell = False

        # 额度 = 每一侧各自最多 K 个未平循环(而不是净头寸!)。
        # 净头寸 = #未平买循环 - #未平卖循环, 若只约束净值, 两侧可同时无限累积。
        nb = sum(1 for x in pend if x['side'] > 0)      # 已买入待卖出
        ns = sum(1 for x in pend if x['side'] < 0)      # 已卖出待买回
        _occ[0] += nb
        _occ[1] += ns
        if Kfun is not None:
            _kk = Kfun(e)
            Kb, Ks = (_kk, _kk) if isinstance(_kk, (int, float)) else _kk
        if ffun is not None:
            _f = ffun(e)
            if _f[0]:
                skip_buy = True
            if _f[1]:
                skip_sell = True

        # --- 买单 ---
        if only != 'sell' and not skip_buy and not r.get('buy_open_invalid'):
            for t in range(3):
                if nb >= Kb:
                    _occ[2] += 1
                    break
                p = Pc * (1 - bd[t])
                ok = L <= p * (1 - buf)
                ent = tg = None
                if oracle == 'fill':
                    ok2, pr = will_complete(+1, p, k)
                    ok = ok and ok2
                if not ok:
                    continue
                entry = min(p, O)
                if oracle == 'delta' and dgrid:
                    j = min(k + maxhold, N - 1)
                    best = max_high(k + 1, j)
                    if O <= entry:
                        best = max(best, H)
                    r_ = best / entry - 1
                    cand = [d0 for d0 in dgrid if d0 <= r_]
                    if not cand:
                        continue
                    dd = max(cand)
                else:
                    dd = delta
                tgt = entry * (1 + dd)
                shares += U
                cash -= entry * U
                cash -= cost(+1, entry, e)
                fills += 1
                done = (O <= entry and H >= tgt * (1 + buf)) or (path_opt and H >= tgt * (1 + buf))
                if done:
                    shares -= U
                    cash += tgt * U
                    cash -= cost(-1, tgt, e)
                    gross += (tgt - entry) * U
                    gross_tgt += (tgt - entry) * U
                    cyc += 1
                    fills += 1
                else:
                    pend.append(dict(side=+1, entry=entry, tgt=tgt, i0=k))
                    nb += 1

        # --- 卖单 ---
        if only != 'buy' and not skip_sell and not r.get('sell_open_invalid'):
            for t in range(3):
                if ns >= Ks:
                    _occ[3] += 1
                    break
                p = Pc * (1 + sd[t])
                ok = H >= p * (1 + buf)
                if oracle == 'fill':
                    ok2, pr = will_complete(-1, p, k)
                    ok = ok and ok2
                if not ok:
                    continue
                entry = max(p, O)
                if oracle == 'delta' and dgrid:
                    j = min(k + maxhold, N - 1)
                    best = min_low(k + 1, j)
                    if O >= entry:
                        best = min(best, L)
                    r_ = 1 - best / entry
                    cand = [d0 for d0 in dgrid if d0 <= r_]
                    if not cand:
                        continue
                    dd = max(cand)
                else:
                    dd = delta
                tgt = entry * (1 - dd)
                shares -= U
                cash += entry * U
                cash -= cost(-1, entry, e)
                fills += 1
                done = (O >= entry and L <= tgt * (1 - buf)) or (path_opt and L <= tgt * (1 - buf))
                if done:
                    shares += U
                    cash -= tgt * U
                    cash -= cost(+1, tgt, e)
                    gross += (entry - tgt) * U
                    gross_tgt += (entry - tgt) * U
                    cyc += 1
                    fills += 1
                else:
                    pend.append(dict(side=-1, entry=entry, tgt=tgt, i0=k))
                    ns += 1

    W = shares * P_END + cash
    BH = BASE_SH * P_END
    alpha = W - BH
    base_mv0 = BASE_SH * P_START
    return dict(
        cyc=cyc, cyc_force=cyc_force, cyc_tgt=cyc - cyc_force, fills=fills, nstop=nstop[0],
        inv=shares - BASE_SH,
        gross=gross, gross_tgt=gross_tgt, tcost=_tc[0],
        occ=_occ, avg_open=(_occ[0] + _occ[1]) / max(N, 1),
        bind=(_occ[2] + _occ[3]) / max(N, 1),
        alpha=alpha, alpha_pct=alpha / base_mv0, alpha_ann=alpha / base_mv0 / YRS,
        per_share=alpha / F_of(TL[-1]) / BASE_SH,      # 元/股(期末口径)
        pend_left=len(pend),
    )


def row(nm, r):
    return (f'{nm:<26}{r["cyc"]:>7.0f}{r["cyc"]/YRS:>8.0f}{r["cyc_force"]/max(r["cyc"],1)*100:>8.0f}%'
            f'{r["alpha_pct"]*100:>10.2f}%{r["alpha_ann"]*100:>9.2f}%'
            f'{r["per_share"]:>10.3f}{r["inv"]:>+8d}{r["fills"]:>8.0f}')


HDR = (f'{"方案":<26}{"循环":>7}{"每年":>8}{"强平%":>8}'
       f'{"3年α":>10}{"年化":>9}{"元/股":>10}{"净头寸":>8}{"成交笔":>8}')
