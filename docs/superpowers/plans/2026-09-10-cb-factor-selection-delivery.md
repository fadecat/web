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
- **暂存区操作说明**:T0 首次提交时发现预存的 3 个已暂存删除会随 `git commit` 进入提交(违反"不夹带"),已即时修复:软回退后 `git restore --staged` 将 3 个删除移出暂存区(磁盘上文件仍为删除状态,内容零改动),重新只提交交付记录;批次1 结束时再 `git add` 恢复其已暂存状态,保持移交时的基线形态。

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

## T2:V3 目录、强校验、条件引擎

### 实际签名与结构

- `backend/services/cb_factors.py`:`FACTOR_CATALOG` 类型化重构,每项含 `field/label/unit/tab`(旧键保留,前端兼容)+ `type/operators/filterable/scorable/allow_negative/description`;新增索引 `FACTOR_CATALOG_BY_FIELD: dict[str, dict]`。移除 `ytm_rt`(R1);`price` label 改「当前价格」;新增 `simple_maturity_yield_pct/redeem_price/redeem_remain_days/listed_days/industry_code/rating_cd/redeem_icons/stock_is_st/code`。
  - 负数允许标记(`allow_negative`):true = simple_maturity_yield_pct、premium_rt、increase_rt、sincrease_rt、pb(负净资产)、redeem_remain_days(数据域含负值);其余数值字段 false(价格/规模/价值/年限/成交额等,§4.3)。redeem_remain_days 的"负值数据"兼容由条件级 `negative=include` 表达,与阈值符号解耦。
  - `DEFAULT_CONFIG`(V2 三低)与读写函数未动(属 T3)。
- `backend/api/schemas/cb_screen.py` 新增 V3 模型(旧 IntradayFilterQuery/normalize_ratings/StrategyTemplateModel/FactorsConfigModel 原样保留):
  - `ConditionModel(id, field, op, value, enabled, missing="exclude", negative="compare")`:字段/运算符必须来自目录;strict 数值(拒绝 bool/字符串/NaN/Infinity);between 两端有限且 lo≤hi;目录 allow_negative=false 拒绝负阈值;枚举值必须字符串数组且启用时非空、去空白后拒绝空项与重复项;行业只收 6 位码或 NONE(中文名称 422)、强赎标记限 R/O/B/G、转债代码归一 6 位;`negative=include` 仅限 redeem_remain_days;simple_maturity_yield_pct/redeem_price 的 missing 必须 exclude。
  - `ScoringFactorModel(field, ascending=True, weight, enabled=True)`:weight 为 strict 有限正数;字段必须 scorable。
  - `SelectionTemplateModel`(含 `migration_issues=[]`,extra=forbid,target_count 1~50、hold_tolerance 0~20 strict int);`SelectionRunModel(schema_version=3, source=db|live)` 平铺继承模板字段;`SelectionConfigModel(version=3, revision 必填, active_id 必须存在, 模板 id 唯一, extra=forbid)`。
  - 同字段重复评分 422;V3 拒绝未知业务字段;`updated_at` 作为存储产物显式声明透传(GET→POST 原样回存不 422,其余未知顶层字段仍拒绝)。
- `backend/services/cb_conditions.py` 新增 `evaluate_conditions(cell, conditions) -> list[dict]`(纯函数,不读库不触网):AND 语义、跳过 enabled=false;数值缺失按 `missing`(默认 exclude,include 为旧模板兼容);空评级/空行业先归一 NONE 再匹配;`negative=include` 负值直接通过(旧安全天数行为);失败原因七键 `{rule_id, field, actual, op, expected, reason_code, message}`,reason_code ∈ missing/value_out_of_range/value_not_in/value_in_excluded/value_mismatch;字段取值映射 industry_code→cell.sw_cd、code→cell.bond_id(其余同名),集合取 cell.icons 键,ST 用现有「正股名含 ST」定义。

### 测试(先写后实现,反例先行确认)

`tests/test_cb_selection_conditions.py` 新建 84 用例,覆盖方案 T2 表格 9 项必测(含 `test_invalid_rule_prevents_fetch`:4 类非法 payload 均 ValidationError 且 `fetch_live_snapshot` mock 调用数为 0)及 §4.3 参数化非法输入。开发中修一处测试自身错误(`["AA"]` 单元素合法数组误列入非法用例)与一处引擎 bug(code 字段未映射到 cell.bond_id)。

### T2 指定验证(真实运行)

命令:`python -m pytest tests/test_cb_selection_conditions.py tests/test_cb_screen_contract.py -q -p no:cacheprovider`
结果:**97 passed**,退出码 0。

附加回归(T1+T2 全部相关文件):`python -m pytest tests/test_cb_factors_contract.py tests/test_cb_selection_conditions.py tests/test_cb_selection_metrics.py tests/test_industry_lookup.py tests/test_cb_screen_contract.py tests/test_queries.py -q -p no:cacheprovider` → **170 passed**,退出码 0(旧 HTTP 模型与原验证语义未回归)。

### 偏离与批次边界说明(T2)

1. **`test_invalid_rule_prevents_fetch` 在引擎层锁定而非 HTTP 层**:方案 T2 表格期望「422、抓取调用 0 次」,但 POST /cb-list/screen 路由接 V3 模型属 T4(本批次边界明确禁止改路由)。当前实现:SelectionRunModel 校验失败在任何抓取之前(测试 patch `backend.services.queries.live.fetch_live_snapshot` 证明调用数为 0),「先校验再抓取」的合同由 schema 层保证;T4 接线路由时必须保持"校验通过后才调用数据源",即可把该性质原样提升到 HTTP 层。**非偏离实现,是批次切面;T4 需补 HTTP 层断言**。
2. **`detail.code/message/path` 错误外形**:属路由错误映射(§4.3/T3/T4)。V3 模型的 ValidationError 自带 `loc`(如 `templates.0.conditions.2.value`)/`type`/`msg`,T3/T4 从 `exc.errors()` 直接映射即可,本批次未改路由。
3. **`allow_negative` 命名**:方案仅要求"负数允许标记"未定名,采用 `allow_negative`(bool)。后续批次读目录请用该键。
4. **redeem_remain_days 的 allow_negative=true**:方案 §4.3 负值禁止清单未列该字段;其数据域含负值(旧引擎 0≤remain≤阈值 判断的前提),负阈值可表达「已过触发日」,故阈值层面允许负数,数据负值兼容仍由条件级 negative 标志表达。listed_days 阈值保持非负(与旧 min_listing_days ≥ 0 一致)。
5. **枚举评级匹配大小写不敏感**(strip+upper 后比较,与旧引擎 `ratings_keep` 一致);行业/代码按原始码精确匹配。方案未细说,沿用旧语义。
6. **`updated_at` 透传例外**:GET 回读含存储产物 updated_at,V3 extra=forbid 下显式声明该字段避免原样回存 422;其余未知字段照拒。

## 批次1 交接要点(给批次2 agent)

- 公式与补充:`cb_metrics.finite_number/simple_maturity_yield_pct/enrich_cell(cell, redeem_cell)`;enrich 写入 `redeem_price` 与 `simple_maturity_yield_pct` 两键,T4 的 DB/live 管线在条件过滤前调用它。
- 引擎输入约定:`evaluate_conditions` 期望 cell 已 enrich;`redeem_remain_days`/`listed_days` 由 T4 的字段补充阶段写入 cell(引擎只读;listed_days 须按数据交易日/实时时区日期计算,不得 date.today)。industry_code 读 `cell.sw_cd`,code 读 `cell.bond_id`,redeem_icons 读 `cell.icons` 键集,ST 读 `cell.stock_nm`。
- 校验入口:T4 执行路由用 `SelectionRunModel.model_validate(body)`(平铺 schema_version=3+source+模板字段);T3 保存路由用 `SelectionConfigModel.model_validate(body)`。ValidationError 的 `exc.errors()[i]["loc"]` 即 path,可直接映射 `detail.code/message/path`。
- 目录事实源:`FACTOR_CATALOG_BY_FIELD[field]` 取 `type/operators/scorable/allow_negative/label`;T3 迁移 redemption safe days 时用 `negative="include"` + `missing="include"`;ytm_rt 已从目录移除,迁移特殊处理见方案 §5.2。
- V3 模型输出 `model_dump()` 可直接喂 `evaluate_conditions`(测试即此用法);旧 `FactorsConfigModel`/`StrategyTemplateModel` 未动,T3 磁盘升级 V3 后的 V2 客户端兼容(§5.3 第 7 条)仍走旧模型。


