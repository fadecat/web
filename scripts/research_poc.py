# -*- coding: utf-8 -*-
"""次日 T 价位研究回放 Phase 0: AkShare/东财数据源 PoC。

直接调用真实 Provider(不做 akshare mock), 用于验收:
1. 可达性: 每标的每模式 3 次尝试(成功/失败/耗时/异常);
2. raw/hfq 日期配对 + OHLC 合法性;
3. 量额单位观测(股票手 vs ETF 份);
4. 与交易日历比对列出「开市但无日线」日期;
5. r_t = raw_close/hfq_close 阶跃标定(相对阶跃 > 1e-4 全部列出);
6. 同日双跑内容哈希比对(源端回写观察)。

报告写 <out>/poc_report.md + poc_report.json, stdout 打印摘要。
用法: python -m scripts.research_poc [--symbols 600900.SH,001286.SZ] [--years 2]
                                 [--out data/research/poc] [--runs 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# PoC 明确允许触真实数据源(conftest 的 socket 拦截只作用于 tests/)
from backend.services.market_data import (
    AdjustMode,
    AkShareEastmoneyProvider,
    ResearchSourceError,
)
from backend.utils import load_research_targets

REPO_ROOT = Path(__file__).resolve().parent.parent

_STEP_LIST_THRESHOLD = 1e-4  # PoC 标定: 相对阶跃超过此值全部列出


def _poc_provider(sleep_on: bool) -> AkShareEastmoneyProvider:
    return AkShareEastmoneyProvider(sleep=(time.sleep if sleep_on else (lambda _s: None)))


def _try_fetch(provider, symbol, security_type, mode, start, end, attempts=3):
    """可达性探测: 最多 attempts 次, 返回 (bars, attempts_report)。"""
    report = []
    bars = None
    for i in range(1, attempts + 1):
        began = time.perf_counter()
        try:
            bars = provider.get_daily_bars(symbol, start, end, mode, security_type=security_type)
            elapsed = round(time.perf_counter() - began, 2)
            report.append({"attempt": i, "ok": True, "elapsed_sec": elapsed, "rows": len(bars)})
            break
        except Exception as exc:  # noqa: BLE001 ProxyError 等网络异常逐次记录
            elapsed = round(time.perf_counter() - began, 2)
            report.append({
                "attempt": i, "ok": False, "elapsed_sec": elapsed,
                "error": f"{type(exc).__name__}: {exc}",
            })
            time.sleep(2)
    return bars, report


def _ratio_steps(paired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """r_t = raw_close/hfq_close 全序列相对阶跃, 列出 > 阈值的日期。"""
    steps = []
    for i in range(1, len(paired)):
        prev, cur = paired[i - 1], paired[i]
        if prev["hfq_close"] <= 0 or cur["hfq_close"] <= 0:
            continue
        r_prev = prev["raw_close"] / prev["hfq_close"]
        r_cur = cur["raw_close"] / cur["hfq_close"]
        if r_prev == 0:
            continue
        step = abs(r_cur - r_prev) / abs(r_prev)
        if step > _STEP_LIST_THRESHOLD:
            steps.append({
                "date": cur["trade_date"].isoformat(),
                "r_prev": r_prev, "r_cur": r_cur,
                "relative_step": step,
            })
    return steps


def _pair_by_date(raw_bars, hfq_bars) -> list[dict[str, Any]]:
    raw_map = {b.trade_date: b for b in raw_bars}
    hfq_map = {b.trade_date: b for b in hfq_bars}
    shared = sorted(set(raw_map) & set(hfq_map))
    return [
        {
            "trade_date": d,
            "raw_close": raw_map[d].close, "hfq_close": hfq_map[d].close,
        }
        for d in shared
    ]


def run_poc(symbols: list[tuple[str, str]], years: int, out_dir: Path, *, runs: int = 2, sleep_on: bool = True) -> dict:
    provider = _poc_provider(sleep_on)
    start = date.today() - timedelta(days=365 * years)
    end = date.today()

    # 日历(1 次尝试, 失败不阻塞)
    calendar_dates: list[date] = []
    calendar_report: dict[str, Any] = {"ok": False}
    try:
        sessions = provider.get_trade_calendar()
        calendar_dates = [s.trade_date for s in sessions if s.is_open]
        calendar_report = {"ok": True, "sessions": len(sessions)}
    except Exception as exc:  # noqa: BLE001
        calendar_report = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    results: list[dict[str, Any]] = []
    for symbol, security_type in symbols:
        entry: dict[str, Any] = {"symbol": symbol, "security_type": security_type}
        raw_bars, raw_attempts = _try_fetch(
            provider, symbol, security_type, AdjustMode.RAW, start, end)
        hfq_bars, hfq_attempts = _try_fetch(
            provider, symbol, security_type, AdjustMode.HFQ, start, end)
        entry["reachability"] = {"raw": raw_attempts, "hfq": hfq_attempts}

        if raw_bars is None or hfq_bars is None:
            entry["status"] = "unreachable"
            results.append(entry)
            continue

        raw_dates = {b.trade_date for b in raw_bars}
        hfq_dates = {b.trade_date for b in hfq_bars}
        entry["status"] = "fetched"
        entry["rows"] = {"raw": len(raw_bars), "hfq": len(hfq_bars)}
        entry["dates_match"] = raw_dates == hfq_dates
        entry["only_raw_dates"] = [d.isoformat() for d in sorted(raw_dates - hfq_dates)][:20]
        entry["only_hfq_dates"] = [d.isoformat() for d in sorted(hfq_dates - raw_dates)][:20]

        # 量额单位观测
        if raw_bars:
            sample = raw_bars[-1]
            entry["volume_observation"] = {
                "sample_date": sample.trade_date.isoformat(),
                "volume": sample.volume, "amount": sample.amount,
                "volume_unit": sample.volume_unit,
            }

        # 开市但无日线(日历可信时)
        if calendar_report["ok"]:
            bar_dates = raw_dates | hfq_dates
            missing = [d for d in calendar_dates if start <= d <= end and d not in bar_dates]
            entry["calendar_missing_bars"] = [d.isoformat() for d in missing[:50]]
            entry["calendar_missing_count"] = len(missing)

        # r_t 阶跃标定
        paired = _pair_by_date(raw_bars, hfq_bars)
        steps = _ratio_steps(paired)
        entry["ratio_steps_gt_1e-4"] = steps
        entry["ratio_step_count"] = len(steps)
        if steps:
            entry["max_ratio_step"] = max(s["relative_step"] for s in steps)
        else:
            entry["max_ratio_step"] = 0.0
        # 噪声上界(无阶跃日): 全序列相邻相对变化的最大值
        if len(paired) >= 2:
            noise = 0.0
            for i in range(1, len(paired)):
                prev, cur = paired[i - 1], paired[i]
                if prev["hfq_close"] > 0 and prev["raw_close"] > 0:
                    r_prev = prev["raw_close"] / prev["hfq_close"]
                    r_cur = cur["raw_close"] / cur["hfq_close"]
                    if r_prev:
                        noise = max(noise, abs(r_cur - r_prev) / abs(r_prev))
            entry["observed_noise_upper_bound"] = noise
        results.append(entry)

    # 同日双跑哈希比对(源端回写观察): 只对第一个可抓标的
    hash_report: dict[str, Any] = {"performed": False}
    if runs >= 2 and results:
        target = next((r for r in results if r["status"] == "fetched"), None)
        if target is not None:
            symbol = target["symbol"]
            security_type = target["security_type"]
            hashes = []
            for _ in range(runs):
                try:
                    bars = provider.get_daily_bars(
                        symbol, start, end, AdjustMode.RAW, security_type=security_type)
                    import hashlib
                    digest = hashlib.sha256()
                    for b in bars:
                        digest.update(f"{b.trade_date}|{b.open}|{b.high}|{b.low}|{b.close}".encode())
                    hashes.append(digest.hexdigest())
                except Exception as exc:  # noqa: BLE001
                    hashes.append(f"error: {exc}")
            hash_report = {
                "performed": True, "symbol": symbol, "runs": runs,
                "hashes": hashes, "stable": len(set(h for h in hashes if not str(h).startswith("error"))) <= 1,
            }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "params": {"symbols": [s for s, _ in symbols], "years": years,
                   "start": start.isoformat(), "end": end.isoformat(),
                   "step_list_threshold": _STEP_LIST_THRESHOLD},
        "calendar": calendar_report,
        "symbols_report": results,
        "same_day_hash": hash_report,
        "tolerance_suggestion": _tolerance_suggestion(results),
    }


def _tolerance_suggestion(results: list[dict[str, Any]]) -> dict[str, Any]:
    """根据标定数据给出 corporate_action_tolerance 建议区间。"""
    noises = [r.get("observed_noise_upper_bound") for r in results if r.get("observed_noise_upper_bound") is not None]
    event_steps = [r.get("max_ratio_step") for r in results if r.get("ratio_step_count")]
    noise_upper = max(noises) if noises else None
    event_min = min(event_steps) if event_steps else None
    suggestion = None
    if noise_upper is not None:
        # 容差须高于噪声、低于事件阶跃(若观测到)
        lower_bound = noise_upper * 3
        upper_bound = event_min if event_min is not None else None
        suggestion = {
            "noise_upper_bound": noise_upper,
            "min_event_step": event_min,
            "recommended_at_least": lower_bound,
            "recommended_at_most": upper_bound,
            "current_config": 0.002,
        }
    return suggestion or {"note": "样本不足, 保持 0.002 待标定"}


def _write_reports(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "poc_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 研究回放数据源 PoC 报告",
        "",
        f"- 生成时间: {report['generated_at']}",
        f"- 区间: {report['params']['start']} ~ {report['params']['end']}({report['params']['years']} 年)",
        f"- 日历: {'OK(' + str(report['calendar'].get('sessions')) + ' 交易日)' if report['calendar']['ok'] else '失败: ' + str(report['calendar'].get('error'))}",
        "",
        "## 逐标的",
        "",
    ]
    for entry in report["symbols_report"]:
        lines.append(f"### {entry['symbol']}({entry['security_type']})")
        lines.append(f"- 状态: {entry['status']}")
        if entry["status"] == "unreachable":
            lines.append(f"- 可达性: {json.dumps(entry['reachability'], ensure_ascii=False)}")
            lines.append("")
            continue
        raw_ok = all(a["ok"] for a in entry["reachability"]["raw"])
        hfq_ok = all(a["ok"] for a in entry["reachability"]["hfq"])
        lines.append(f"- 可达性: raw {'OK' if raw_ok else '有失败'} / hfq {'OK' if hfq_ok else '有失败'}")
        lines.append(f"- 行数: raw {entry['rows']['raw']} / hfq {entry['rows']['hfq']}, 日期匹配: {entry['dates_match']}")
        if entry.get("volume_observation"):
            obs = entry["volume_observation"]
            lines.append(f"- 量额样本({obs['sample_date']}): volume={obs['volume']} unit={obs['volume_unit']} amount={obs['amount']}")
        if "calendar_missing_count" in entry:
            lines.append(f"- 开市但无日线: {entry['calendar_missing_count']} 天" +
                         (f"(如 {entry['calendar_missing_bars'][:5]})" if entry["calendar_missing_bars"] else ""))
        lines.append(f"- r_t 阶跃(>1e-4): {entry['ratio_step_count']} 处, 最大 {entry['max_ratio_step']:.6f}")
        for step in entry["ratio_steps_gt_1e-4"][:10]:
            lines.append(f"  - {step['date']}: {step['relative_step']:.6f}(r {step['r_prev']:.6f} → {step['r_cur']:.6f})")
        if entry.get("observed_noise_upper_bound") is not None:
            lines.append(f"- 非事件日噪声上界: {entry['observed_noise_upper_bound']:.8f}")
        lines.append("")
    lines.append("## 同日双跑哈希")
    same = report["same_day_hash"]
    if same["performed"]:
        lines.append(f"- {same['symbol']}: {same['runs']} 次抓取, 稳定: {same['stable']}")
    else:
        lines.append("- 未执行(无可用标的)")
    lines.append("")
    lines.append("## 容差建议")
    lines.append("```json")
    lines.append(json.dumps(report["tolerance_suggestion"], ensure_ascii=False, indent=2))
    lines.append("```")
    (out_dir / "poc_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="研究回放数据源 PoC")
    parser.add_argument("--symbols", default=None,
                        help="逗号分隔规范代码(默认取 config/research.yaml 全部标的)")
    parser.add_argument("--years", type=int, default=2, help="回看年数(默认 2)")
    parser.add_argument("--out", default="data/research/poc", help="报告输出目录")
    parser.add_argument("--runs", type=int, default=2, help="同日双跑次数(默认 2)")
    parser.add_argument("--no-sleep", action="store_true", help="跳过标的间礼貌 sleep(仅本地调试)")
    args = parser.parse_args(argv)

    if args.symbols:
        symbols = []
        for raw in args.symbols.split(","):
            symbol = raw.strip().upper()
            if not symbol:
                continue
            # 无 yaml 元信息时按沪市 5 开头判 ETF(仅 PoC 探测用)
            code = symbol.split(".")[0]
            symbols.append((symbol, "ETF" if code.startswith("5") else "STOCK"))
    else:
        targets = load_research_targets(REPO_ROOT / "config" / "research.yaml")
        symbols = [(t["symbol"], t["type"].upper()) for t in targets]

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir

    print(f"[PoC] 标的: {[s for s, _ in symbols]}; 年数: {args.years}; 输出: {out_dir}")
    report = run_poc(symbols, args.years, out_dir, runs=args.runs, sleep_on=not args.no_sleep)
    _write_reports(report, out_dir)

    # stdout 摘要
    for entry in report["symbols_report"]:
        status = entry["status"]
        symbol = entry["symbol"]
        if status != "fetched":
            print(f"[FAIL] {symbol}: {status}")
            continue
        match = "匹配" if entry["dates_match"] else f"错位(仅raw {len(entry['only_raw_dates'])}, 仅hfq {len(entry['only_hfq_dates'])})"
        print(
            f"[OK] {symbol}: raw {entry['rows']['raw']} / hfq {entry['rows']['hfq']} 行, 日期{match}, "
            f"r_t 阶跃 {entry['ratio_step_count']} 处(最大 {entry['max_ratio_step']:.6f})"
        )
    print(f"[PoC] 报告已写入 {out_dir / 'poc_report.md'}")
    unreachable = [e for e in report["symbols_report"] if e["status"] != "fetched"]
    mismatch = [e for e in report["symbols_report"] if e["status"] == "fetched" and not e["dates_match"]]
    return 1 if (unreachable or mismatch) else 0


if __name__ == "__main__":
    sys.exit(main())
