# -*- coding: utf-8 -*-
"""日频任务注册表: 任务ID → 函数 + 调度时间。

scheduler(定时触发)与 data_status 路由(手动触发)共用同一份定义,
保证「定时跑的」和「手动跑的」一定是同一个函数, 不会各自漂移。
"""
from __future__ import annotations

from backend.tasks.cb_index_tasks import run_cb_index_daily
from backend.tasks.cb_list_tasks import run_cb_list_daily
from backend.tasks.cb_redeem_tasks import run_cb_redeem_daily
from backend.tasks.index_eod_tasks import run_index_eod_daily
from backend.tasks.style_rotation_tasks import run_style_rotation_daily
from backend.tasks.valuation_tasks import run_valuation_daily

# (job_id, 函数, 展示名, hour, minute) —— 与 scheduler 注册一致
DAILY_JOBS: list[tuple[str, object, str, int, int]] = [
    ("cb_redeem_daily", run_cb_redeem_daily, "可转债强赎列表抓取", 15, 3),
    ("cb_index_daily", run_cb_index_daily, "可转债等权指数日频抓取", 15, 4),
    ("cb_list_daily", run_cb_list_daily, "可转债全量快照抓取", 15, 6),
    ("style_rotation_daily", run_style_rotation_daily, "风格轮动日频抓取", 22, 3),
    ("valuation_daily", run_valuation_daily, "估值板块日频抓取", 22, 6),
    ("index_eod_daily", run_index_eod_daily, "指数日线收盘价(eod)抓取", 22, 9),
]

JOB_FUNCS: dict[str, object] = {job_id: func for job_id, func, _, _, _ in DAILY_JOBS}
