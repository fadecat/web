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

---

# 批次2 交付记录(T3 模板迁移与文件保存 / T4 统一执行与数据质量)

> 实施者:批次2 agent(迁移与执行)。基线:批次1 结束提交 `51f9fa9`(即 `36c8c17` + T0~T2 三个提交)。本批次只做 T3/T4,提交:`6a9f9a0`(T3)、`6f4f9f3`(T4)。

## T3:模板迁移、重命名与文件保存

### 实际签名与结构

- **`backend/services/cb_template_migration.py`(新建)**:
  - `migrate_config_to_v3(config: dict) -> dict`:纯函数,深拷贝入参,零磁盘 IO;version=3 输入原样深拷贝返回(幂等不动点);V1/V2 逐模板 `_migrate_template`;
  - `TEMPLATE_NAME_MAX_CHARS = 40`;条件生成顺序固定保证确定性:exclusion_rules 派生条件(文件顺序) → `rating_cd in`(missing="exclude",旧白名单连缺失评级一起排除) → `redeem_icons not_any`(缺省 R/O/B;显式 [] = 不生成;非法标记 archived 留痕) → `redeem_remain_days gt`(missing="include"+negative="include",仅 safe_days≥0 时生成) → `listed_days gte`(仅 >0) → `code not_in`(归一 6 位去重) → `stock_is_st eq false` 收尾;条件 id "c1".."cN";
  - §5.2 ytm_rt:条件与评分都产生 `replaced_metric` 问题(id 前缀 "legacy-yield-",replacement_field=simple_maturity_yield_pct),enabled→status=pending(阻塞执行),停用→archived;
  - `unmappable_rule`(未知字段/运算符/非数字阈值):启用 pending、停用 archived;`invalid_value_dropped`(负阈值/非法枚举/非正权重重置为 1/超界参数夹取/无法归一代码)一律 archived;`duplicate_scoring_dropped`(重复/不可评分评分字段保留首个);未知模板键 `unknown_field_dropped`(fields 列表);
  - 名称 trim → 超 40 截断(`name_truncated`)→ 重名按文件顺序追加"(迁移N)"(`duplicate_name_renamed`,casefold 判重);缺失/重复 id 生成稳定迁移 ID `migrated-<文件序号>`(冲突追加 `-k`);active_id 悬空重置为首个模板(`active_id_reset`);
  - 问题收集器 `_IssueSink`:id 前缀按类独立计数,保证 issue id 稳定且跨类不冲突。
- **`backend/services/cb_factors.py`(§5.3)**:
  - `CONFIG_LOCK = threading.RLock`(单进程并发边界);`MISSING_REVISION = "missing"`;
  - `load_current_config() -> (revision, config|None)`:revision=磁盘原始字节 SHA256;无文件返回 (missing, None);V1/V2 归一,V3 原样;坏文件/未知版本抛 `ConfigUnreadableError`(**R9 修订:read_config 不再静默回默认**);
  - `save_config_v3(config, expected_revision)`:锁内重读比对(不一致抛 `ConfigConflictError(current_revision)`)→ 磁盘非 V3 时 `_backup_before_first_v3`(独占 `O_CREAT|O_EXCL` 创建 `factors.pre-v3.<旧文件hash12>.json`,已存在则验证逐字节相同)→ 组装 `{version:3, active_id, templates, updated_at}`(revision 不入正文)→ `_atomic_write_bytes`(同目录唯一临时文件+fsync+`os.replace`,失败保留原文件并清理临时文件)→ 返回 `{"data", "revision"(新字节 SHA256)}`;
  - `write_config` 改原子替换;`get_active_template` V3 感知(懒加载迁移,只内存)。
- **`backend/api/schemas/cb_screen.py`**:`SelectionTemplateModel`/`_ConditionsTemplateBase` 名称 trim 后 ≤40(`TEMPLATE_NAME_MAX_CHARS`);`SelectionConfigModel._names_unique_casefold`(归一重名 422,列出重复名);旧 `StrategyTemplateModel`/`FactorsConfigModel` 与评级语义反例原样保留。
- **`backend/api/routes/cb_screen.py`**:
  - GET /cb-list/factors:load → 无文件用归一默认 → `migrate_config_to_v3` → `{**v3, "revision"}`;坏文件 503 `{"code":"CONFIG_UNREADABLE"}`;**GET 不写磁盘不产生备份**;
  - POST 版本分发:`body.version == 3` → `_save_factors_v3`(SelectionConfigModel 强校验 → 422 detail `{code:"INVALID_CONFIG", message, path, errors:[{code,message,path}]}`,path 由 `exc.errors()[i]["loc"]` join;`ConfigConflictError` → 409 `CONFIG_CONFLICT` + current_revision;响应 `{ok:true, data:{..., revision}}`);否则 `_save_factors_legacy`(磁盘已 V3 → 409 `CLIENT_UPGRADE_REQUIRED`;旧 FactorsConfigModel 校验,422 detail 保持旧字符串外形;评级语义反例不回归)。

### 测试

`tests/test_cb_selection_templates.py`(新建,33 用例)覆盖方案 T3 必测:迁移两次完全一致+幂等不动点+入参零污染+零磁盘 IO;ytm 条件/评分 pending(停用 archived);双标签同 revision 一胜一 409(且冲突请求不改磁盘);`os.replace` 失败(monkeypatch)原文件字节不变+临时文件清理;坏 JSON/未知版本 503 不回默认;首次备份逐字节一致且二次保存不重复备份;重命名只改 name;带空格同名重命名零实质变动;空白名/41 字符/归一重名 422 且文件不变;revision 缺失/未知顶层字段/重复 id/active_id 悬空 422;旧客户端兼容(空盘/V2 盘可存,V3 盘 409,ratings:"AAA" 反例保持)。
`tests/test_cb_factors_contract.py` 两处断言按方案授权修订到 V3 字段:GET→POST 往返改断 `rating_cd in` 条件逐字段保持(excluded_ratings=["AA"] 反例语义不变);不限评级改断"无 rating_cd 条件"。**评级语义反例(test_string_rating_not_split_to_chars 等 10 项)全部原样保留且通过。**

### T3 指定验证(真实运行)

命令:`python -m pytest tests/test_cb_selection_templates.py tests/test_cb_factors_contract.py -q -p no:cacheprovider`
结果:**56 passed**,退出码 0。
附加回归(+screen 合同+条件引擎+metrics):**153 passed**,退出码 0。

### T3 提交事故与修复(如实记录)

首次 T3 提交时 `git commit` 把预存的 3 个已暂存删除(Bonds.vue、filterValidation.{js,test.mjs})一并带入(9 files changed)——与 T0 批次记录的坑完全相同。已即时修复:`git reset --soft HEAD~1` 回退后改用 **pathspec 提交**(`git commit -m ... -- <显式文件列表>`,新文件先显式 `git add`),最终提交 `6a9f9a0` 恰好 6 个本批次文件;3 个预存删除保持已暂存状态、AppLayout/CbMarket/router 保持未暂存,移交形态与批次1 交接一致。**后续批次提交务必用 pathspec 或先清理暂存区。**

## T4:接通统一执行和数据质量合同

### 实际签名与结构

- **`backend/services/cb_screen.py`(重写为 V3 统一管线,§6.1)**:
  - `screen_bonds(rows, template, redeem_map=None, *, as_of_date=None, blacklist_ids=None, trade_date=None, redeem_trade_date=None, redeem_loaded=True, redeem_unusable=False, warnings=None)`;`screen_bonds_live(records, template, redeem_cells=None, *, as_of_date=None, blacklist_ids=None, warnings=None)`;业务输入相同则结果相同(DB/live 共用 `_run_pipeline`);
  - 管线:`_enrich_rows`(dict 输入归一副本,ORM 行走 `_row_to_cell`;`enrich_cell` 写 redeem_price/简单收益率,再写 redeem_remain_days(仅同日强赎快照,覆盖 raw_json 残留)与 listed_days(以 as_of_date 为基准,**不读系统当天**)) → `_apply_filters`(黑名单+`evaluate_conditions`,每行只进一个列表、原因可合并) → `_score_and_order`(排名线性打分,同分按代码升序;总分降序→双低升序 None 最后→代码升序;无启用评分因子=filter_only,双低升序) → 入选标记 → `_to_dto`;
  - `RedeemDataUnavailableError`:模板依赖 `{simple_maturity_yield_pct, redeem_price}`(启用条件或评分)且全市场收益输入全部缺失时抛出(§3.2,路由映射 503);`redeem_unusable=True` 时服务内强制丢弃 redeem_map(严格同日);
  - DTO 新增:`industry_code`(原始 sw_cd,筛选匹配口径)/`industry_name/level/mapped_code/is_fallback`(映射结果)/`simple_maturity_yield_pct`;保留旧键(price/dblow/premium_rt/curr_iss_amt/convert_value/year_left/pb/rating/redeem_price/redeem_gap/redeem/change_rt/industry_name/total_score);excluded_rows 带 `exclude_reasons`(七键结构数组);`format_redeem_status` 的 None 计数与空串同口径(旧入口共用,展示修正);
  - 响应新增 `selection_mode("scored"|"filter_only")` 与 `meta`(§6.2 九键);filter_only 时 selected/holdable 全 False、selected_count/buffer_count/top_n/keep_n=0、total_score=null;`blacklisted_count` 与其他原因重叠、不从 total 二次扣减;`total_all = total_filtered + total_excluded`。
- **`backend/services/industry.py`**:新增 `static_catalog_entries()`(by_code 全目录,source="catalog")与 `discovered_catalog_entry(sw_cd)`(快照发现码:回退命中标注 fallback+名称,完全未知 name=None 保留选项,source="snapshot");不触网。
- **`backend/api/routes/cb_screen.py`**:
  - `_load_redeem_map(db, as_of_date)` 重写:取 `max(trade_date) ≤ D` 的最近强赎快照 R,**纳入 redeem_price 列**;返回 ({bond_id: cell}, R);
  - `_validate_run_template`:`SelectionRunModel.model_validate` → 422 `{code:"INVALID_CONFIG",message,path,errors}`(**校验先于任何数据源调用**);migration_issues 含 status=pending → 409 `{"code":"TEMPLATE_REVIEW_REQUIRED", issues}`;
  - POST /cb-list/screen:db 路径 `_execute_db_screen`(D=最新快照日;无快照 200+`meta.data_status="no_snapshot"`,区别于 0 只符合;R 严格同日;R≠D 或缺失时 redeem_loaded=False+警告含 D/R 日期);live 路径 `fetch_live_snapshot` 失败 502;空强赎列表=赎回数据不可用(§3.2 统一提示,不虚构原因);live `trade_date=None` 不冒充交易日,`fetched_at`=东八区固定偏移 `+08:00` ISO 时间;两路径均挂黑名单;
  - GET /cb-list/screen/active:`get_active_template`(V3 迁移内存态)→ 同一校验/pending 拒绝/同一引擎;配置损坏 503 CONFIG_UNREADABLE;响应带 template_id/template_name;
  - GET /cb-list/factors/industries:静态目录+`distinct(sw_cd)` 快照发现合并,按 industry_code 升序;
  - 旧入口未动:GET intraday 与 blacklist* 端点行为不变(`test_cb_screen_contract.py` 13 项全过)。
- **`backend/api/schemas/cb_screen.py`**:`SelectionRunModel` 增加 `migration_issues: list[dict] = []`(pending 随模板平铺携带,供路由拒绝)。
- **`frontend/src/api/index.js`**:仅追加 `getIndustryCatalog()`(6 行,唯一前端改动,无 UI 改动)。

### 测试

`tests/test_cb_selection_api.py`(新建,11 用例,真实 ORM 数据+隔离内存库):方案 T4 全部 9 个必测名称(`test_db_live_same_input_same_selection`/`test_db_redeem_price_loaded_from_orm`/`test_pending_legacy_yield_cannot_run`/`test_active_applies_blacklist`/`test_no_snapshot_distinct_from_no_matches`/`test_redeem_unavailable_fails_dependent_template`/`test_redeem_date_mismatch_is_visible`/`test_selection_counts_partition_all_rows`/`test_filter_only_has_no_false_selected_badges`)+HTTP 层非法请求 422 且抓取 0 次(批次1 遗留项按交接要求提升到 HTTP 层)+行业目录合并(760201 catalog/610101 fallback→610100 水泥/999999 未映射)。固定验收数据 A/B/C 与方案一致:简单收益率 gte 0 只保留 A(10%),B 因 -12 排除、C 因赎回价缺失排除;C 在不配该条件时出现在符合结果;A 拉黑后排除且原因 rule_id=global_blacklist 可见。

### T4 指定验证(真实运行)

命令:`python -m pytest tests/test_cb_selection_api.py tests/test_cb_selection_conditions.py tests/test_queries.py tests/test_cb_screen_contract.py -q -p no:cacheprovider`
结果:**117 passed**,退出码 0。
全量回归:`python -m pytest tests -q -p no:cacheprovider` → **474 passed, 3 failed, 5 errors**,失败/错误全部为 T0 记录的既有基线(test_app_operations_scripts 2 failed+1 error、test_migration_baseline::test_wrong_nullable_reported、test_cli_entrypoints 4 PermissionError),本批次零新增失败。

### ECS 进程模型核实(§5.3 前置条件,只读)

`ssh aliyun-ecs "systemctl cat webapp"` 实测:`ExecStart=/opt/webapp/venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000`,**无 --workers 参数 = 单 worker 进程**,`CONFIG_LOCK` 进程内互斥的部署前提成立。未改动任何线上配置。

### 偏离与实现说明(T4)

1. **503 判定口径**:方案"全市场收益率输入均无效"实现为"全量输入行 simple_maturity_yield_pct 全为 None 且模板启用收益率/赎回价条件或评分";`redeem_remain_days` 条件不计入依赖(迁移语义 missing=include,缺数据时优雅放行,不构成假空结果风险)。部分债缺赎回价按 §3.2 单债缺失+计数警告处理,不 503。
2. **strict 同日在服务层强制**:`screen_bonds(redeem_unusable=True)` 内部丢弃 redeem_map,而非依赖路由传空——路由层错误不会导致跨日数据混入。
3. **live 路径黑名单**:GET/POST 的 live 执行同样查库挂黑名单(§6.3"所有新模板执行入口"),命中进 excluded_rows 并计入 blacklisted_count。
4. **industry_code 的 DTO 字段语义**:行 DTO 的 `industry_code` 恒为原始 sw_cd(即使映射未收录),映射产物放 `industry_name/level/mapped_code/is_fallback`——支撑前端把结果行原始码合并进选项(§6.2)。
5. **东八区用固定偏移** `timezone(timedelta(hours=8))`:中国无夏令时,且不依赖 Windows/容器的 tzdata 可用性。
6. **旧 V2 硬编码引擎函数移除**(check_exclusion_rules/filter_cb/three_low_strategy/_screen_cell_rows 等):方案 §6.1 明确新路径不再执行旧硬编码条件,且全仓无其他调用方;`format_redeem_status`/`_row_to_cell` 保留(cb_intraday 共用)。

## 批次2 交接要点(给批次3 前端 agent)

### 执行响应形状(POST /cb-list/screen 与 GET /cb-list/screen/active,200 时)

```json
{
  "total_all": 3, "total_filtered": 1, "total_excluded": 2,
  "top_n": 10, "keep_n": 10, "selected_count": 1, "buffer_count": 0,
  "selection_mode": "scored",
  "template_id": "yield-only", "template_name": "简单收益率筛选",
  "rows": [{
    "rank": 1, "selected": true, "holdable": true,
    "code": "110001", "name": "A债",
    "industry_code": "760201", "industry_name": "环保设备Ⅲ",
    "industry_level": 3, "industry_mapped_code": "760201", "industry_is_fallback": false,
    "price": 100.0, "change_rt": null, "dblow": 105.0, "premium_rt": 5.0,
    "curr_iss_amt": 5.0, "convert_value": null, "year_left": 2.0, "pb": null,
    "rating": "AA", "redeem_price": 110.0, "simple_maturity_yield_pct": 10.0,
    "redeem_gap": 10.0, "redeem": "", "total_score": 1.0
  }],
  "excluded_rows": [{"...同上字段, rank/selected/holdable 为 null/false, total_score 为 null...",
    "exclude_reasons": [{"rule_id": "c1", "field": "simple_maturity_yield_pct", "actual": -12.0,
      "op": "gte", "expected": 0, "reason_code": "value_out_of_range", "message": "..."}]}],
  "source": "db",
  "meta": {
    "data_status": "ready", "trade_date": "2026-09-10", "redeem_trade_date": "2026-09-10",
    "fetched_at": null, "quote_time": null, "redeem_loaded": true,
    "missing_yield_count": 1, "blacklisted_count": 0, "warnings": []
  }
}
```

- `selection_mode="filter_only"` 时:rows 按 dblow 升序(None 最后)→代码升序;每行 `selected/holdable=false`、`total_score=null`;顶层 `top_n/keep_n/selected_count/buffer_count=0`——前端不要渲染入选徽标。
- `data_status="no_snapshot"`:rows/excluded_rows 为空、trade_date=null,与"0 只符合"(ready)区分展示;live 时 `trade_date=null` 只显示 `fetched_at`("抓取于")。
- 被排除原因:七键结构,`rule_id="global_blacklist"`/`reason_code="blacklisted"` 表示黑名单;`reason_code="missing"` 且 field=simple_maturity_yield_pct 时 message 已说明"无到期赎回价"。

### 错误码合同(执行/保存共用 detail.code)

| HTTP | code | 场景 |
|---|---|---|
| 422 | `INVALID_CONFIG` | V3 校验失败;detail={code,message,path,errors[]},path 形如 `templates.0.conditions.2.value` 或 `conditions.0.value`(执行为平铺 loc) |
| 409 | `CONFIG_CONFLICT` | 保存 revision 不匹配;detail 附 `current_revision`,前端应重新 GET |
| 409 | `CLIENT_UPGRADE_REQUIRED` | 磁盘已 V3 而旧客户端(无 revision)保存 |
| 409 | `TEMPLATE_REVIEW_REQUIRED` | 执行/active 模板含 pending 迁移项;detail 附 `issues[]`(id/kind/origin/original/replacement_field/message) |
| 503 | `REDEEM_DATA_UNAVAILABLE` | 赎回数据不可用 × 模板依赖收益率/赎回价;前端提示改用实时行情 |
| 503 | `CONFIG_UNREADABLE` | factors.json 损坏/版本不支持,绝不回默认 |
| 502 | (字符串 detail) | live 上游抓取失败(沿用旧形态) |

- 保存请求:POST /cb-list/factors,`version=3 + revision(GET 返回值) + active_id + templates[]`;模板含 `migration_issues` 字段(可原样回存,不 422)。GET /cb-list/factors 无文件时 revision=`"missing"`,可直接作为首次保存的 revision。

### 行业目录(GET /cb-list/factors/industries,不触网)

条目数组按 industry_code 升序:`{industry_code(原始码,筛选匹配口径), industry_name(未知为 null→显示"未映射 <code>"), industry_level, industry_mapped_code, industry_is_fallback, source("catalog"|"snapshot")}`;选项文字须附原始代码与回退层级(如 "水泥(610101,按申万二级映射)"),前端封装 `getIndustryCatalog()` 已就绪。

### 其他

- 迁移 pending 的模板:执行两入口 409,但保存/回存合法;前端需在模板编辑器提供"确认迁移"交互(改掉 pending 项后保存)。
- `test_invalid_rule_prevents_fetch` 已在 HTTP 层补齐(tests/test_cb_selection_api.py::TestInvalidInputPreventsFetch),批次1 遗留项关闭。
- 预存工作区改动(AppLayout.vue/CbMarket.vue/router/index.js 未暂存;Bonds.vue/filterValidation.* 已暂存删除)保持原样未动,属 T7 提交范围。



