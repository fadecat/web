# 集思录转债强赎数据链路审查与整改方案

审查日期：2026-09-11  
审查范围：集思录 `cb_list_new`、`redeem_list` → 项目抓取与快照 → 选债因子二次过滤与页面状态展示  
审查结论：**不达标**。原始字段基本完整，但项目把源数据的多个业务维度压缩成 `R/O/B/G` 图标和一段展示文本，导致“已公告强赎”“临近到期”“满足强赎计数”之间出现语义混用，模板过滤可误排转债。

## 1. 结论与风险排序

### P0：`R` 被直接解释为“已公告强赎”，会把临近到期债误判为已公告强赎

- 代码在 `backend/services/cb_screen.py:532-533` 只判断 `redeem_icon == "R"` 就返回“已公告强赎”。
- 2026-09-03 本地原始快照存在两类 `R`：`R + redeem_flag=Y` 2 条，`R + redeem_flag=X` 2 条。
- 反例 `110075 南航转债`：`redeem_icon=R`、`redeem_flag=X`、`redeem_dt=null`、`delist_dt=2026-10-09`，源数据文字为“2026-10-09 最后交易”。它表达临近到期/最后交易日，不是已公告强赎。
- 结果：列表文案错误；选择“排除已公告强赎”时，也会误排临近到期债。

### P0：模板过滤读取 `cb_list_new.icons`，没有使用 `redeem_list` 的公告标志和日期

- `backend/services/cb_conditions.py:175-189` 从 `cell.icons` 取 `R/O/B/G` 集合后直接做 `not_any`。
- `backend/services/cb_template_migration.py:183-207` 又把旧模板默认 `R/O/B` 原样迁移为该条件，错误语义会被固化到新模板。
- `cb_list_new.icons` 可用作源站展示标记，却不足以回答“是否已经公告强赎”；至少需要联合 `redeem_flag`、`redeem_dt`、`delist_dt`。
- 结果：当前“强赎状态”不是稳定业务字段，而是对源站图标的直接过滤。

### P1：`redeem_remain_days` 的名称和说明与源数据不符

- `backend/services/cb_factors.py:121-123` 将其命名为“距强赎触发天数”，并写成“已过触发日时可为负”。
- 源站页面实际显示“至少还需 N 天”，它与 `redeem_real_days / redeem_count_days / redeem_total_days` 共同描述 M/N 条款计数。例如金田转债快照为 `remain=8, real=7, count=15, total=30`。
- 本地 315 条快照中有 298 条 `redeem_remain_days=-1`。这些值代表当前不适用或未进入有效倒计数，不能解释为“已经过去 1 天”。
- `backend/services/cb_template_migration.py:337-344` 的旧规则兼容行为会让负值通过，这个执行效果可保留，但字段必须改名为“至少还需触发天数”，并将 `-1` 归一为 `null/not_applicable` 后再过滤。

### P1：普通转债被展示为 `0/15 | 30`

- `backend/services/cb_screen.py:527-541` 在无状态图标时，只要三个计数字段存在就输出计数字符串。
- 集思录全量 `redeem_list` 会给大量普通债返回 `real_days=0, count_days=15, total_days=30, remain_days=-1`；当前逻辑因此把未进入计数状态的债展示成 `0/15 | 30`。
- 正确条件应是：`remain_days >= 0` 或存在明确的计数/状态语义时才显示计数；普通监控状态不展示该字符串。

### P1：DB 路径丢弃了判定强赎状态所需的字段

- 存储层已经保存 `redeem_flag`、`redeem_dt`、`recount_dt`、`delist_dt` 和公告文字，见 `backend/services/cb_redeem_store.py:57-69`、`backend/services/cb_redeem_store.py:78-91`。
- DB 读取映射只返回图标、计数和到期赎回价，见 `backend/api/routes/cb_screen.py:219-227`。
- 结果：实时路径可能拿到完整原始 cell，DB 路径却无法区分 `R+Y` 和 `R+X`；两条路径无法实现相同的业务判定。

### P1：强赎接口失败会被压成空数组，调用方无法区分失败与合法空集

- `backend/services/fetchers/cb_redeem.py:50-54` 已正确区分“响应结构异常”和“结构正常但 rows 为空”。
- 但 `backend/services/queries/live.py:30-35` 捕获所有异常后统一返回 `[]`，再次丢失了该区别。
- 结果：上层只能依靠空列表推测数据是否可用，无法返回准确的降级原因，也不能监控登录失效或接口结构变化。

### P1：当前本地两类快照不同日，依赖赎回数据的 DB 筛选无法执行

- 本地库实测：`cb_daily_snapshot` 最新为 2026-09-08，共 303 条；`cb_redeem_daily` 最新为 2026-09-03，共 315 条。
- DB 路径采用严格同日口径，见 `backend/api/routes/cb_screen.py:200-217` 及该文件 298 行后的执行逻辑。
- 严格同日是正确的数据一致性约束；应修复强赎任务调度、失败告警或补数流程。不能直接把 9 月 3 日的赎回价和计数拼到 9 月 8 日行情上。

## 2. 源字段语义表

下面的含义由 2026-09-03 保存的 `raw_json`、结构化列，以及 2026-09-11 集思录强赎页面的展示交叉验证得出。

| 源字段 | 实际含义 | 示例 | 项目应采用的字段 |
|---|---|---|---|
| `redeem_price` | 到期赎回价；用于项目定义的简单到期收益率 | 万讯转债 `118.00` | `maturity_redeem_price`，兼容期可保留 `redeem_price` 别名 |
| `force_redeem_price` | 强赎触发价，即正股价格阈值 | 万讯转债 `8.359` | `force_redeem_trigger_price` |
| `real_force_redeem_price` | 公告后的实际强赎价 | 万讯转债 `101.332` | `announced_redeem_price` |
| `redeem_real_days` | 当前窗口已经满足条件的天数 | 金田转债 `7` | `trigger_days_met` |
| `redeem_count_days` | 条款要求满足的天数 M | 金田转债 `15` | `trigger_days_required` |
| `redeem_total_days` | 统计窗口天数 N | 金田转债 `30` | `trigger_window_days` |
| `redeem_remain_days` | 至少还需满足的交易日数；`-1` 多数表示当前不适用 | 金田转债 `8` | `trigger_days_remaining`，将负数归一为 `null` |
| `redeem_flag` | 公告维度标志，实测 `Y` 可确认已公告，`N` 对应公告不赎回，`X` 为其他/未公告状态 | 万讯 `Y`；南航 `X` | 参与归一化，不直接展示给用户 |
| `redeem_icon` | 源站展示图标，不能单独充当业务状态 | `R` 同时出现在已公告强赎与临近到期 | 只保留为 `source_redeem_icon` 供排障 |
| `redeem_dt` | 公告强赎相关日期 | 万讯 `2026-09-11` | `redeem_date` |
| `delist_dt` | 最后交易/退市相关日期 | 南航 `2026-10-09` | `last_trade_date` 或经源定义确认后的 `delist_date` |
| `force_redeem` | 源站组合说明文字 | “最后交易日…赎回价…” | 原文留档与详情提示，不用于字符串反向解析 |

价格字段不能互换。项目定义的“简单到期收益率”继续使用 `redeem_price`；`force_redeem_price` 是正股触发阈值；`real_force_redeem_price` 才是公告后的实际强赎价。现有公式对前两者的区分已经在 `backend/api/routes/cb_screen.py:203-204` 和 `backend/services/cb_factors.py:116-120` 明确。

## 3. 建议的归一化状态模型

新增一个唯一归一化函数，例如 `normalize_redeem_state(redeem_cell)`。展示、过滤、API DTO 和 DB/实时路径必须共用它。不要在前端解释源图标。

建议输出：

```json
{
  "status_code": "ANNOUNCED_REDEEM",
  "status_label": "已公告强赎",
  "trigger_days_remaining": null,
  "trigger_days_met": 21,
  "trigger_days_required": 15,
  "trigger_window_days": 30,
  "redeem_date": "2026-09-11",
  "last_trade_date": "2026-09-11",
  "maturity_redeem_price": 118.0,
  "announced_redeem_price": 101.332,
  "source_redeem_icon": "R",
  "source_redeem_flag": "Y"
}
```

第一版判定优先级：

| 优先级 | 判定依据 | 归一状态 | 是否属于“已公告强赎”筛选 |
|---:|---|---|---|
| 1 | `redeem_flag == Y`，并保留公告/赎回日期 | `ANNOUNCED_REDEEM` | 是 |
| 2 | `redeem_icon == R`、`redeem_flag != Y`、存在 `delist_dt` 且无 `redeem_dt` | `NEAR_MATURITY` | 否；应由独立“临近到期”条件控制 |
| 3 | `redeem_icon == B` 或 `trigger_days_remaining == 0` 且计数已达到要求 | `TRIGGER_MET` | 由“已满足强赎条件”控制 |
| 4 | `redeem_icon == O` | `ANNOUNCED_INTENT` | 暂按独立状态；上线前补采样确认 O 的页面文案和 flag 组合 |
| 5 | `redeem_icon == G` 或 `redeem_flag == N` | `NO_REDEEM_ANNOUNCED` | 否 |
| 6 | `trigger_days_remaining > 0` | `TRIGGER_COUNTING` | 由“至少还需触发天数”数值条件控制 |
| 7 | 其他 | `MONITORING` | 否 |

`O` 在本地 2026-09-03 样本中没有出现，因此不能仅凭旧代码中文标签断言其完整语义。实现时应把未知组合记录为数据质量事件，并保留源字段，避免静默归入错误状态。

## 4. 二次过滤应如何改

模板不再暴露 `R/O/B/G`。向用户提供稳定的业务条件：

1. **强赎业务状态**：可多选排除 `已公告强赎 / 公告拟强赎 / 已满足强赎条件 / 公告不赎回 / 计数中 / 普通监控`。
2. **至少还需触发天数**：数值条件，只对 `TRIGGER_COUNTING` 生效；源值 `<0` 归为缺失/不适用，按模板的 missing 策略处理。
3. **临近到期**：独立布尔或“距最后交易日天数”条件，不能并入“已公告强赎”。
4. **到期赎回价**：继续使用 `redeem_price`。
5. **简单到期收益率**：继续使用 `(到期赎回价 - 当前价) / 当前价 × 100`，维持用户已确认的项目口径。

旧模板迁移不能静默把 `R` 翻译为单一业务状态。建议：

- 含 `R` 的旧模板迁移为 `migration_issue=pending`，提示其过去同时覆盖“已公告强赎”和“临近到期”；由用户选择新业务状态后才可执行。
- `B/G/O` 也迁移为新枚举，不再读取 `cell.icons`。
- 旧 `redeem_safe_days=N` 可迁为“至少还需触发天数 > N”，但字段说明必须标明负值/不适用值的迁移兼容策略。

## 5. 实施顺序

### T1：建立契约测试和样本夹具

- 保存至少 6 类脱敏原始 cell：`R+Y` 已公告、`R+X+delist_dt` 临近到期、`B` 已满足、`G+N` 不赎回、正数 remain 计数中、`remain=-1` 普通监控。
- 为三个价格字段分别放不同数值，测试禁止串用。
- 契约测试直接断言归一状态和输出字段，不断言源图标等于中文状态。

### T2：实现单一归一化层

- 新建纯函数模块，输入原始 `redeem_cell`，输出上述规范对象。
- DB `_load_redeem_map` 补齐 `redeem_flag/redeem_dt/recount_dt/delist_dt/force_redeem/real_force_redeem_price`；后者需要新增结构化列或从 `raw_json` 明确读取。
- 实时和 DB 都先归一化，再进入 enrich、过滤和 DTO。

### T3：切换筛选与模板迁移

- 因子目录删除面向用户的 `redeem_icons`，加入 `redeem_status_code`、`trigger_days_remaining`、`near_maturity` 或相应日期差字段。
- 条件引擎从规范字段读取，不再读取 `cell.icons`。
- 旧模板中含 `R` 的迁移进入待确认状态，避免静默改变投资筛选结果。

### T4：修正 API 与页面展示

- API 返回结构化状态，同时在兼容期保留旧 `redeem` 文本。
- 页面用 `status_label` 与计数结构拼装：计数中显示“至少还需 3 天（12/15｜30）”；普通监控留空；临近到期显示最后交易日；已公告强赎显示公告/最后交易/实际强赎价。
- 筛选结果解释应显示命中的业务条件和值，不显示 `R/O/B/G`。

### T5：补齐新鲜度和失败可观测性

- `fetch_live_snapshot` 返回显式状态，例如 `redeem_fetch_ok`、`redeem_fetch_error`、`redeem_cells`，合法空集保持 `ok=true`。
- 对日频任务增加“转债行情日 D 与强赎日 R 不同日”告警和补数入口。
- 保留严格同日合并；接口应清楚返回 `D/R` 和不可用原因。

## 6. 验收核对清单

- [ ] `R+Y+redeem_dt` 显示并过滤为“已公告强赎”。
- [ ] `R+X+delist_dt` 显示为“临近到期”，选择“排除已公告强赎”时不会被排除。
- [ ] `B` 显示为“已满足强赎条件”，可独立筛选。
- [ ] `remain=3, real=12, count=15, total=30` 显示“至少还需 3 天（12/15｜30）”。
- [ ] `remain=-1, real=0` 的普通债不显示 `0/15｜30`，数值筛选按 missing 策略处理。
- [ ] 到期赎回价、强赎触发价、实际强赎价使用三组独立字段和测试值。
- [ ] DB 与实时路径对相同 cell 生成完全相同的归一状态。
- [ ] 强赎接口结构错误时 API 返回可识别的降级原因；结构正常的空 rows 不报接口失败。
- [ ] 行情快照与强赎快照不同日时不混用，并产生可见告警。
- [ ] 旧模板含 `R` 时进入待确认，未经确认不能用含糊语义执行。

## 7. 本轮验证记录

- 对本地 `data/web.db` 做只读统计：最新强赎快照 315 条；组合为 `空+X=274`、`G+N=36`、`R+X=2`、`R+Y=2`、`B+X=1`。
- 对关键样本核对 `raw_json` 与结构化列：万讯转债同时存在 `redeem_price=118.00`、`force_redeem_price=8.359`、`real_force_redeem_price=101.332`，证明三类价格不可混用。
- 通过已登录的集思录强赎页面只读核对当前展示，页面同时出现“已公告强赎”“临近到期”“至少还需 N 天”等互斥语义。
- 当前本地后端端口未运行，无法在本轮末尾重复调用盘中 API；代码层和数据库层反例已能稳定复现上述根因。
- 当前机器没有可用 Python 3 解释器，未执行 pytest；本轮没有修改业务代码。
