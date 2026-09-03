# -*- coding: utf-8 -*-
"""tasks 包: 调度层调用的业务任务函数。

职责: 串联 fetchers → store,一个函数完成一个板块的全量抓取落库。
不负责「何时」执行(由 scheduler 决定),只负责「做什么」。
"""
