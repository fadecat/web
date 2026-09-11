"""本地选债验收服务器：隔离模板/数据库、固定行情，无外部抓取。

运行 .venv/Scripts/python -m scripts.selection_acceptance；访问 127.0.0.1:8002。
仅用于浏览器验收，禁止用它代替生产入口。
"""
from pathlib import Path
import os
import json
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts" / "selection-browser"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + (ARTIFACTS / "acceptance.db").as_posix()
os.environ["SCHEDULER_ENABLED"] = "false"

from backend.services import cb_factors
cb_factors.FACTORS_PATH = ARTIFACTS / "factors.json"
from backend.main import app
from backend.models.database import init_db, SessionLocal
from backend.models.valuation import CbDailySnapshot, CbRedeemDaily
from backend.services.queries import live

CELLS = [
    {"bond_id": "110001", "bond_nm": "验收A", "stock_nm": "测试股份", "price": 100, "dblow": 110, "premium_rt": 10, "sw_cd": "760201", "rating_cd": "AA"},
    {"bond_id": "110002", "bond_nm": "验收B", "stock_nm": "测试股份", "price": 125, "dblow": 130, "premium_rt": 5, "sw_cd": "610101", "rating_cd": "NONE"},
    {"bond_id": "110003", "bond_nm": "验收C", "stock_nm": "测试股份", "price": 110, "dblow": 120, "premium_rt": 10, "sw_cd": "999999", "rating_cd": "BB+"},
]
REDEEMS = [
    {"bond_id": "110001", "redeem_price": 110, "redeem_flag": "X", "redeem_icon": "R", "delist_dt": "2026-10-09", "redeem_remain_days": -1},
    {"bond_id": "110002", "redeem_price": 110, "redeem_flag": "Y", "redeem_icon": "R", "redeem_dt": "2026-09-30", "redeem_remain_days": 0},
    {"bond_id": "110003", "redeem_price": 110, "redeem_flag": "X", "redeem_icon": "", "redeem_remain_days": 3, "redeem_real_days": 12, "redeem_count_days": 15, "redeem_total_days": 30},
]


def main():
    init_db()
    with SessionLocal() as db:
        if not db.query(CbDailySnapshot).count():
            for c in CELLS:
                db.add(CbDailySnapshot(trade_date=date(2026, 9, 11), raw_json=json.dumps(c), **{k: v for k, v in c.items() if hasattr(CbDailySnapshot, k)}))
            for c in REDEEMS:
                db.add(CbRedeemDaily(trade_date=date(2026, 9, 11), raw_json=json.dumps(c), **c))
            db.commit()
    if not cb_factors.FACTORS_PATH.exists():
        cb_factors.save_config_v3({"version": 3, "active_id": "acceptance", "templates": [{"id": "acceptance", "name": "隔离验收模板", "conditions": [], "strategy_factors": [], "target_count": 10, "hold_tolerance": 0, "migration_issues": []}]}, "missing")
    live.fetch_live_snapshot = lambda: live.LiveSnapshot(CELLS, REDEEMS, "ok")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)


if __name__ == "__main__":
    main()
