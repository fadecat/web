# -*- coding: utf-8 -*-
"""数据查询服务层(阶段3)。

按 dataset_type 分文件, 提供 get_latest / get_history 契约(讨论稿 §4):
- prices.py      指数日线(IndexDailyQuote)
- valuations.py  估值快照(IndexValuationSnapshot)
- dividends.py   股息率(IndexDividendYield)
- bond_yields.py 国债收益率(CnBondYield)
- snapshots.py   转债快照(CbDailySnapshot)
- live.py        实时选债 gateway(从 cb_intraday 抽 fetch)

get_as_of(instruments, dataset_type, cutoff) 为预留契约, 待「所选截止日估值」
需求出现后再实现, 当前不写空实现(避免「配置了但代码不读取」式假契约)。

查询层只负责取数 + 序列化核心字段, 返回结构与旧路由完全一致(向后兼容);
路由只做 HTTP 包装。
"""
