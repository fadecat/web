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

## T1:公共指标与行业补充

### 实际函数签名(`backend/services/cb_metrics.py`,新建)

```python
def finite_number(value: Any) -> float | None
def simple_maturity_yield_pct(price: Any, redeem_price: Any) -> float | None
def enrich_cell(cell: dict[str, Any], redeem_cell: dict[str, Any] | None = None) -> dict[str, Any]
```

- 公式主体与方案 §3.1 逐字一致(None/bool 拒绝、字符串去空白去千分位、非有限数拒绝、p/r ≤ 0 拒绝)。
- `enrich_cell`:返回新 dict(不原地污染),写入 `redeem_price`(仅取 `redeem_cell.redeem_price`,经 `finite_number`;缺失记 None,即使原 cell 残留同名旧值也覆盖为 None)与 `simple_maturity_yield_pct`(由 `cell.price` 与上述赎回价计算)。`force_redeem_price` 不参与。
- `industry.py` 新增 `industry_info_of(sw_cd) -> dict | None`,返回 `{industry_code, industry_name, industry_level, industry_mapped_code, industry_is_fallback}`;`industry_name_of` 改为委托它取名,查表口径单一化,旧行为不变(原测试全过)。
- `cb_intraday.py` `_live_row` 改调 `simple_maturity_yield_pct`,`ytm_simple` 字段名与 3 位小数精度不变。

### 测试(先写反例再实现)

- `tests/test_cb_selection_metrics.py`(新建,30 用例)+ `tests/test_industry_lookup.py`(扩充 6 个 `industry_info_of` 用例:三级直查/二级形态/回退标注/未知/空码/与旧函数口径一致)。
- 反例先行确认:实现前运行 → `ImportError` 收集失败(退出码 2),符合 T1"先看它失败"要求。

### T1 指定验证(真实运行)

命令:`python -m pytest tests/test_cb_selection_metrics.py tests/test_industry_lookup.py tests/test_cb_screen_contract.py -q -p no:cacheprovider`

结果:**61 passed**,退出码 0。另做 `_live_row` 冒烟:100/110 → `ytm_simple=10.0`、125/110 → `-12.0`、赎回价缺失 → None,旧字段与精度不变。

### 偏离说明

- 公式边界微差(方案口径优先):旧 `_live_row` 内联公式不检查赎回价 ≤ 0(负赎回价会算出正值收益率);改用公共公式后该退化输入返回 None。真实数据无负赎回价,属方案统一口径的预期修正,非行为回归。

