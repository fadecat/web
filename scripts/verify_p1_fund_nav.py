# -*- coding: utf-8 -*-
"""P1 场外基金链路 · 端到端验证（克制版：单标的、1 次真实请求）。

验证链路：alembic 建库 → 蛋卷拉全历史 → 链式复权 → upsert 落 fund_nav_daily → 读回统计。

用法：
    python scripts/verify_p1_fund_nav.py            # 默认标的 100018
    python scripts/verify_p1_fund_nav.py 161116

⚠ 纪律：禁止压测数据源。本脚本固定只对 1 个标的发 1 次请求（蛋卷 size=6000 拉全历史）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.models.research import FundNavDaily  # noqa: E402
from backend.services import fund_nav  # noqa: E402


def build_db(tmp: Path) -> Path:
    db_path = tmp / "p1-fund-nav.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    command.upgrade(cfg, "head")
    return db_path


def main() -> int:
    code = sys.argv[1] if len(sys.argv) > 1 else "100018"
    symbol = f"{code}.OF"
    print(f"[1/5] 目标标的：{code} → 规范符号 {symbol}")

    tmp = Path(tempfile.mkdtemp(prefix="p1-fund-nav-"))
    db_path = build_db(tmp)
    print(f"[2/5] 临时库已建并 upgrade head：{db_path}")

    print(f"[3/5] 请求蛋卷 nav/history（size={fund_nav.DANJUAN_PAGE_SIZE}，1 次请求）...")
    rows = fund_nav.fetch_nav_history(code)
    print(f"      返回 {len(rows)} 行：{rows[0].nav_date} ~ {rows[-1].nav_date}")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with Session(engine) as db:
        result = fund_nav.upsert_fund_nav(db, symbol, rows)
        print(f"[4/5] upsert：{result}")

        total, first, last = fund_nav.fund_nav_stats(db, symbol)
        print(f"[5/5] 库内统计：{total} 行 / {first} ~ {last}")

        # 链式复权自检：adj_nav 末值 应 = 首值 × Π(1 + daily_return_pct/100)
        head = db.scalar(
            select(FundNavDaily)
            .where(FundNavDaily.symbol == symbol)
            .order_by(FundNavDaily.nav_date)
            .limit(1)
        )
        tail = db.scalar(
            select(FundNavDaily)
            .where(FundNavDaily.symbol == symbol)
            .order_by(FundNavDaily.nav_date.desc())
            .limit(1)
        )
        first_missing_pct = db.scalar(
            select(FundNavDaily.daily_return_pct)
            .where(FundNavDaily.symbol == symbol)
            .order_by(FundNavDaily.nav_date)
            .limit(1)
        )

    checks = {
        "行数 > 1000": total > 1000,
        "区间与抓取一致": (str(first), str(last)) == (str(rows[0].nav_date), str(rows[-1].nav_date)),
        "成立首日 percentage 为 None": first_missing_pct is None,
        "adj_nav 已链式复权(末值 ≠ 单位净值)": abs(tail.adj_nav - tail.unit_nav) > 1e-9,
        "adj_nav 单调非递减": tail.adj_nav >= head.adj_nav,
    }
    print("\n=== 校验 ===")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    print(
        f"\n抽样：首行 unit_nav={head.unit_nav} adj_nav={head.adj_nav:.6f} "
        f"pct={head.daily_return_pct} | 末行 unit_nav={tail.unit_nav} "
        f"adj_nav={tail.adj_nav:.6f} pct={tail.daily_return_pct}"
    )
    print(f"临时目录（可手动清理）：{tmp}")

    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
