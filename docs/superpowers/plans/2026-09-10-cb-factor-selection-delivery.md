# 转债选债 V3 交付记录(批次1:数据与协议 T0~T2)

> 实施者:批次1 agent(数据与协议)。合同文档:`2026-09-10-cb-factor-selection-design-and-implementation.md`(简称"方案")。
> 本记录按任务推进逐节更新,只覆盖 T0/T1/T2;T3 及之后批次由后续 agent 接手并续写。

## T0:基线与执行环境

### 基线

- 基线 HEAD:`36c8c17`(feat(bonds): 可转债列表新增细分行业列(申万2021映射)),与方案证据基线一致。
- `git status --short`(开工时):

```text
M  frontend/src/pages/Bonds.vue            (已暂存删除)
D  frontend/src/utils/filterValidation.js  (已暂存删除)
D  frontend/src/utils/filterValidation.test.mjs (已暂存删除)
 M frontend/src/layouts/AppLayout.vue
 M frontend/src/pages/CbMarket.vue
 M frontend/src/router/index.js
?? docs/superpowers/plans/2026-09-10-cb-factor-selection-design-and-implementation.md
```

- `git diff --stat`(未暂存):AppLayout.vue 1 行、CbMarket.vue 1 行、router/index.js 8 行变更(3 files, 1 insertion, 9 deletions)。
- `git diff --cached --stat`(已暂存删除):Bonds.vue 652 行、filterValidation.js 50 行、filterValidation.test.mjs 74 行(3 files, 776 deletions)。
- 上述预存改动属 T7 提交范围,本批次提交按文件名显式 `git add`,不夹带。

### 解释器实测(修订②:以实测为准,不照抄方案快照)

- PATH 中 `python` = conda Python **3.12.4**(`D:\conda\python.exe`),满足 3.11+。
- 依赖实测:fastapi 0.116.1 / sqlalchemy 2.0.42 / pydantic 2.11.7 / pytest 9.0.2 / alembic 已装。
- **直接使用该解释器,未创建 .venv**(方案 T0 修订②:已有合格解释器时不得重复建虚拟环境)。
- pnpm 10.33.2 / node v24.11.0(本批次用不到前端,仅记录)。

### T0 指定测试(真实运行)

命令:`python -m pytest tests/test_cb_factors_contract.py tests/test_cb_screen_contract.py tests/test_industry_lookup.py tests/test_queries.py -q -p no:cacheprovider`

结果:**45 passed, 17 warnings**,退出码 0。

(前端 `pnpm test:unit tests/unit/Factors.test.js` 属 T5~T7 范围,批次1 不执行;且预存改动中 Bonds 相关文件已删除,孤儿测试 Bonds.test.js 尚未随删,按修订① 属 T7 处理项,批次1 不动。)

### 环境限制

- ECS 进程模型:**未核实**(批次1 无法读取 ECS;方案 5.3 要求在 T3 落地并发锁前由该批次核对,此处如实记录)。
- 本机有已运行服务,全量 `tests` 存在与本批次无关的既有失败(主审已确认基线):`test_app_operations_scripts.py` 2 个、`test_database_adoption.py` 6 个、`test_migration_baseline.py::test_wrong_nullable_reported`、`test_cli_entrypoints.py` 4 个 PermissionError。批次1 不触碰、不修复。
