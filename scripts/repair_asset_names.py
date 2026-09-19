# -*- coding: utf-8 -*-
"""修正 `research_security` 里「名字就是代码」的脏数据(基金/股票都一样)。

**背景(实测踩坑)**: `seed_baseline_portfolio.py` 对 `--symbols` 自定义标的曾用
`name or symbol` 兜底 → 标的库与组合详情页上基金名显示成 `161116.OF` / `513100.OF`。

**做法**: 扫全表挑出「名字为空 / 名字等于代码」的行, 按类型取真名后覆盖写:
- 场外基金(FUND) → 蛋卷详情 `/djapi/fund/{code}`(真名/类型/经理都在这)
- 场内 ETF 以 `.OF` 形态注册(如 513100.OF, 蛋卷详情回「该基金暂不销售」)→ 回落腾讯行情
- 股票 / ETF → 腾讯行情 `fqkline`

**节流**: 串行、每标的 1~2 个请求、失败跳过不中断; 只改 name 一列, 不碰 source/selection_list。
幂等: 再跑一次会显示"无需修正"。默认 `--dry-run` 只预览, 真正写入要加 `--apply`。

用法(仓库根或任意目录均可):
    python scripts/repair_asset_names.py            # 预览
    python scripts/repair_asset_names.py --apply    # 写入
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.models.database import SessionLocal  # noqa: E402
from backend.models.research import ResearchSecurity  # noqa: E402
from backend.services import portfolio_assets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="修正标的的脏名字(代码当名字)")
    parser.add_argument("--apply", action="store_true", help="真正写入(默认只预览)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    with SessionLocal() as db:
        rows = list(db.scalars(select(ResearchSecurity).order_by(ResearchSecurity.id)).all())
        targets = [
            row for row in rows
            if portfolio_assets.is_placeholder_name(row.name, row.symbol[:6])
        ]
        print(f"标的共 {len(rows)} 个, 其中名字待修正 {len(targets)} 个")
        if not targets:
            print("无需修正 ✓")
            return 0

        fixed = failed = 0
        for row in targets:
            resolved = portfolio_assets.resolve_asset_name(row.symbol, row.security_type)
            if not resolved:
                failed += 1
                print(f"  … {row.symbol:12s} {row.name!r:16s} → 取不到真名(保留原值)")
                continue
            fixed += 1
            print(f"  ✓ {row.symbol:12s} {row.name!r:16s} → {resolved!r}")
            if args.apply:
                row.name = resolved

        if args.apply:
            db.commit()
            print(f"\n已写入 {fixed} 个, 失败 {failed} 个")
        else:
            print(f"\n[预览] 可修正 {fixed} 个, 取不到 {failed} 个 —— 加 --apply 写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
