# -*- coding: utf-8 -*-
"""日频任务注册表: 任务ID → 函数 + 调度时间。

scheduler(定时触发)与 data_status 路由(手动触发)共用同一份定义,
保证「定时跑的」和「手动跑的」一定是同一个函数, 不会各自漂移。
"""
from __future__ import annotations

from backend.tasks.cb_index_tasks import run_cb_index_daily
from backend.tasks.cb_list_tasks import run_cb_list_daily
from backend.tasks.cb_redeem_tasks import run_cb_redeem_daily
from backend.tasks.commodity_tasks import run_commodity_daily
from backend.tasks.fund_tasks import run_fund_nav_sync
from backend.tasks.index_eod_tasks import run_index_eod_daily
from backend.tasks.portfolio_tasks import run_portfolio_cache_refresh
from backend.tasks.research_tasks import run_research_daily_sync
from backend.tasks.stock_dividend_tasks import run_stock_dividend_daily
from backend.tasks.stock_financial_tasks import run_stock_financial_monthly
from backend.tasks.style_rotation_tasks import run_style_rotation_daily
from backend.tasks.valuation_tasks import run_valuation_daily

# 每个自然日都跑的任务:
# - valuation/index_eod 是历史全量同步, 来源可能在周末发布最近交易日数据;
# - cb_redeem/cb_index 的集思录源当日值发布偏晚(15:03/15:04 只能抓到昨日),
#   次日/周末自然日补跑 + 幂等落库, 最近交易日的值最迟隔天追平。
# - research 是研究标的 raw/hfq 全量同步(幂等, 哈希比对), 自然日跑无害。
# - fund_nav 是场外基金净值单序列 upsert(幂等), 净值可能在周末/次日补发, 自然日跑无害。
# - portfolio_cache 重算三格缓存(纯本地读 + 覆盖写), 自然日跑无害且能让补发的净值当天反映到卡片。
# 其余任务是“当日市场快照”，仍只在周一至周五触发并由任务内交易日判断兜底。
EVERYDAY_JOB_IDS = frozenset({
    "cb_redeem_daily",
    "cb_index_daily",
    "valuation_daily",
    "index_eod_daily",
    "research_daily_sync",
    "fund_nav_sync",
    "portfolio_cache_refresh",
})

# (job_id, 函数, 展示名, hour, minute) —— 与 scheduler 注册一致
DAILY_JOBS: list[tuple[str, object, str, int, int]] = [
    ("cb_redeem_daily", run_cb_redeem_daily, "可转债强赎列表抓取", 15, 3),
    ("cb_index_daily", run_cb_index_daily, "可转债等权指数日频抓取", 15, 4),
    ("cb_list_daily", run_cb_list_daily, "可转债全量快照抓取", 15, 6),
    ("stock_dividend_daily", run_stock_dividend_daily, "高股息股票快照抓取", 15, 8),
    ("commodity_daily", run_commodity_daily, "商品价格与分位抓取", 15, 50),
    ("style_rotation_daily", run_style_rotation_daily, "指数日线（腾讯）抓取", 22, 3),
    ("valuation_daily", run_valuation_daily, "估值板块日频抓取", 22, 6),
    ("index_eod_daily", run_index_eod_daily, "指数收盘价（易方达）抓取", 22, 9),
    ("research_daily_sync", run_research_daily_sync, "研究行情日线（raw/hfq）抓取", 17, 30),
    # 场外基金: 普通基金净值约 20:00 前公布, QDII 可能 22:00 之后 → 23:10 给足时间;
    # 同时避开 22:03/22:06/22:09 的拥挤档。
    ("fund_nav_sync", run_fund_nav_sync, "场外基金净值（蛋卷）抓取", 23, 10),
    # 组合卡片三格缓存: **必须排在 fund_nav_sync 之后**(要用当天最新净值)。
    ("portfolio_cache_refresh", run_portfolio_cache_refresh, "组合卡片收益缓存刷新", 23, 30),
]

# 晚间补跑档(job_id → 时刻, 函数复用 DAILY_JOBS 同一份): 集思录源的当日值
# (强赎 15 日计数重算/等权指数当日行)盘后才发布, 15:0x 档抓到的还是昨日口径;
# 晚间再跑一次, store 层按 (bond_id, trade_date)/(trade_date) 同日覆盖写, 幂等。
EVENING_RERUN_JOBS: dict[str, tuple[int, int]] = {
    "cb_redeem_daily": (22, 0),
    "cb_index_daily": (22, 1),
}

# 低峰全市场财务快照：任务函数自行检查 02:00-05:00 窗口并按批次幂等。
MONTHLY_JOBS: list[tuple[str, object, str, int, int]] = [
    ("stock_financial_monthly", run_stock_financial_monthly, "全市场正股财务快照", 2, 10),
]

JOB_FUNCS: dict[str, object] = {job_id: func for job_id, func, _, _, _ in DAILY_JOBS}
JOB_FUNCS.update({job_id: func for job_id, func, _, _, _ in MONTHLY_JOBS})
