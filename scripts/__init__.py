# -*- coding: utf-8 -*-
"""运维脚本包入口。

统一通过 `python -m scripts.<module>` 执行(见 Phase 5.2 计划 R7-02),
避免 direct-file `python scripts/<x>.py` 因 `scripts` 包不在 sys.path 而
报 ModuleNotFoundError。
"""
