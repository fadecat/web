# -*- coding: utf-8 -*-
"""fetchers 包: 数据抓取纯函数集合。

原则(见 docs/web-refactor.md):
- 纯函数: 入参明确(URL / 代码),出参为可序列化的 dict,不碰 DB / 不碰全局状态。
- 与调度解耦: 何时抓取由 scheduler 或 API 触发决定,本层只负责「抓」。
- 从旧 market-daily 中提取并重写,不 import 旧工程包。
"""
