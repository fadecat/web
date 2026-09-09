# 当前工作区审查与下一批推进计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 每个工作包用复现测试、补丁、回归结果独立验收。

**Goal:** 先把当前止血补丁收敛到可验收状态，再推进持久化任务状态与共享配置，服务内部团队日常使用。

**Architecture:** 本批保留页面和 API 结构，修复调用链、数据合同和轮询语义；下一批依照既有总体计划建立迁移和任务运行边界。

**Tech Stack:** 现有 Vue/FastAPI/SQLAlchemy/SQLite；本批使用 Node 内置测试及项目测试环境，组件测试工具按总体计划 T00 接入。

## 1. 当前 Git 快照

审查日期：2026-09-09。分支 `main`，HEAD `23ad995`。本地显示跟踪 `origin/main`，本次未 fetch，因此不判断远端服务器是否有新提交。

- 8 个已跟踪业务文件有未暂存修改：4 个后端服务/抓取文件，4 个 Vue 页面。
- diff 合计 134 行新增、40 行删除，暂存区为空。
- 3 个未跟踪文件：两份既有整改文档，以及 `frontend/src/utils/filterValidation.js`。
- 本文件是本次审查新增交付物，不计入上述初始快照。
- 未见测试文件、依赖配置、迁移文件的工作区改动；总体计划 T00 尚无相应工作区交付证据。
- Git 提示 `.pytest_cache` 无读取权限，不影响本次已跟踪源代码 diff 检查，但不能声称已检查该缓存目录内容。

## 2. 具体缺陷与目标裁决

只依据工作区 diff 及执行结果评判，不采纳作者注释中的完成宣称。前三项属于当前新增实现中的缺陷。

| 编号 | 等级 | 具体问题与反例 | 证据 |
|---|---|---|---|
| R1 | P1 | 查询按钮无法执行：`runScreen` 中新增 `let filters` 遮蔽外层 ref；赋值右侧读取局部 undefined 的 `.value`，在发请求前抛 TypeError。 | `frontend/src/pages/Bonds.vue:106`、`:108`。本次抽取实际函数体，在 Node 中执行，得到 `Cannot read properties of undefined (reading 'value')`。 |
| R2 | P1 | 新轮询用 `ok/warn/stale` 判终态，真实数据状态为 fresh/stale/lagging/no_data 等。fresh 不停止，stale 立即停止；新鲜度本身不能证明本次任务完成。 | `frontend/src/pages/DataStatus.vue:172`—`:178`；`backend/services/data_management.py:73`；`backend/services/data_status.py:70`；`frontend/src/utils/dataManagementView.js:8`。本次执行判定表达式，fresh=false、stale=true。 |
| R3 | P2 | 数字校验遗漏有限性：400 个 9 是纯数字，Number 转成 Infinity；validateFilters 只判断 NaN，仍返回 ok=true，把非有限上限送给接口。 | `frontend/src/utils/filterValidation.js:16`、`:29`。本次执行输出 `price_max: Infinity`、`ok: true`。 |
| R4 | P2 | 实际入选计数改了一半：selected_count 新增，但旁边容差人数仍是 keep_n-top_n。目标10、容差5、仅3只通过时仍会显示5只在容差范围。属于当前计数整改的残留缺口，不是新引入的算法回归。 | `backend/services/cb_screen.py:362`—`:364`；`frontend/src/pages/Factors.vue:440`—`:445`。 |

**裁决：部分达标，但不具备合入/发布条件。** 当前补丁覆盖了评级传参、非法输入、预览空值、实际入选计数、设置错误页与取消动作；R1 阻断核心查询，R2 未建立真实完成判定，R3/R4 未满足数据合同要求，且没有新增自动化回归证据。

## 3. 与上一版计划的差异

| 原任务 | 当前状态 | 下一步 |
|---|---|---|
| T01 / F01 评级传参 | 页面层已改为逗号字符串，但调用链被 R1 阻断。 | 验证按钮→normalize→Axios→后端 query 整条链，不能只看对象转换。 |
| T01 / F03 预览 null | 已添加非空判断；尚无组件运行测试。 | 增加延迟响应和错误响应测试后关闭。 |
| T01 / F04 真实计数 | 实际入选新增，容差仍是配置值。 | 补实际缓冲人数，所有响应分支统一字段。 |
| T01 / F05 校验 | 常规整数、小数、空串与非法文本在 Node 复核符合预期；R1/R3 仍在。 | 修调用及有限数字校验，补后端相同约束。 |
| T01 / F09 设置 | 已增加加载错误分支和取消函数。 | 覆盖 GET失败、取消、保存成功后重读失败；验证反馈不自相矛盾。 |
| T03 / F02 轮询 | 定时器内 POST 已改 GET；任务状态仍未建立。 | 本批先用有上限的状态刷新，不宣称完成；后续按 run_id 收敛。 |
| 评级范围 | 额外取消了抓取层七档限制，并支持 NONE，超出旧整改稿的七档合同。 | 单独登记产品范围变更，保留工作区代码，独立测试与提交；同步文档。 |

评级范围影响文件：`backend/services/fetchers/cb_list.py:25`、`backend/services/cb_factors.py:151`、`frontend/src/pages/Bonds.vue:34`、`frontend/src/pages/Factors.vue:7`。删除请求参数不能单凭注释证明上游返回“所有评级”；本次未请求外部行情。新建/重置默认集合、已有 v2 浏览器条件和旧模板语义需要一起验收，不能顺手删除旧偏好。

## 4. 第一批：收敛当前补丁（预计 3～5 个工作日）

估算基于 1 名前端、1 名后端、QA 兼职；环境缺失、身份集成和数据库迁移不计入这一批。逐包提交，不先启动大型页面重写。

### A0：固定基线与回归入口（0.5～1 日）

文件：新增 `frontend/src/utils/filterValidation.test.mjs`、组件测试目录 `frontend/tests/unit/`；按总体计划修改 `frontend/package.json`、开发依赖和 README。

- [ ] 保留现有 8 文件修改与未跟踪工具文件；记录 HEAD/diff，不执行 reset、checkout 覆盖或清理。
- [ ] 开发开始时创建 `codex/remediation-stabilization` 隔离分支，工作区修改原样带入；不先提交存在 R1 的补丁。
- [ ] Node 测试覆盖有限数、空值、0、负回报、区间倒置；Vue 组件测试覆盖真实点击处理，避免全部只测试纯函数。
- [ ] 建 Python 3 项目环境、安装测试依赖，测试使用内存库/临时配置、假行情和假邮件；核对无真实副作用后执行现有后端测试。
- [ ] 把新工具文件纳入对应提交；不能只暂存 Bonds.vue 而遗漏其 import 的未跟踪文件。

可直接加入的 R3 回归测试（在修复前应失败）：

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseFilterNumber, validateFilters } from './filterValidation.js';

test('filter numbers reject overflow while preserving zero', () => {
  assert.equal(parseFilterNumber('0'), 0);
  assert.equal(parseFilterNumber(''), null);
  assert.equal(Number.isNaN(parseFilterNumber('9'.repeat(400))), true);
  assert.equal(validateFilters(
    {price_max:'9'.repeat(400)}, {price_max:'最高价'}
  ).ok, false);
});
```

### A1：恢复快速查询调用链（0.5 日，依赖 A0）

修改：`frontend/src/pages/Bonds.vue:105`、`frontend/src/utils/filterValidation.js:12`；新增测试 `frontend/tests/unit/Bonds.test.js`。

- [ ] 组件挂载时 mock 黑名单接口；模拟点击“查询”，断言调用 screenBondsIntraday 一次。旧代码必须产生失败，明确捕获未处理异常。
- [ ] `runScreen` 的局部变量改名为 `normalizedFilters`；输入仍读外层 `filters.value`，请求传 `normalizedFilters`，不更改表单 ref 名称。
- [ ] parseFilterNumber 转换后执行 `Number.isFinite`，非有限值返回 NaN；validateFilters 对空串保留省略语义。
- [ ] 限制价格、规模和年限不小于0；回报与溢价率允许负值；后端做同样字段与范围校验，错误返回422。前端错误不发请求。
- [ ] 断言默认120/30能发请求，评级 AAA+AA+ 转换后后端能正确还原；有效查询失败后 loading 归零，可再次查询。

关键修改形态：

```js
// runScreen 内部，保留原来的错误处理与 loading finally
let normalizedFilters;
normalizedFilters = normalizeFilters(filters.value);
// 请求参数使用 normalizedFilters

// parseFilterNumber 中，通过既有格式校验后
const value = Number(s);
return Number.isFinite(value) ? value : NaN;
```

退出门槛：R1/R3 回归测试、旧14项工具测试、Axios参数测试和构建均通过。提交主题：`fix: restore bond screening and validate finite inputs`。

### A2：修正同步刷新语义（0.5～1 日，依赖 A0）

修改：`frontend/src/pages/DataStatus.vue:162`、`:95`；新增 `frontend/tests/unit/DataStatusPolling.test.js`。本批不伪造 run_id 完成能力。

- [ ] 保留“只触发一次POST”改动，删除用数据集 freshness 判定任务终态的逻辑。
- [ ] 将行为明确命名为“自动刷新数据状态”：只 GET；定时用单次 setTimeout 串行调用，前次未完成不启动下一次。
- [ ] 每3秒刷新，按墙钟5分钟截止而非同状态100次；截止文案为“已停止自动刷新，任务结果请查看抓取记录”，不能断言仍在运行或已经成功。
- [ ] loadList 显式返回本次是否成功更新，避免内部 catch 吞错后外层继续用旧 dm 判定；请求失败停止自动刷新并显示可重试错误。
- [ ] 卸载与切换目标递增版本并取消 timer，旧回调返回后不得重新创建计时器；fresh/stale/disabled 都只是展示数据，不影响任务完成声明。
- [ ] 假时钟测试：请求耗时超过3秒无重叠、GET失败、目标不存在、页面卸载、状态交替、5分钟截止；POST次数始终为1。

退出门槛：上述测试通过、不会重复触发采集、UI不冒充知道任务终态。提交主题：`fix: bound data refresh without guessing task completion`。完整 run_id 机制放第二批 B2。

### A3：完成计数与设置页回归（0.5～1 日，依赖 A0）

修改：`backend/services/cb_screen.py:355`、`backend/api/routes/cb_screen.py:117`、`:134`、`frontend/src/pages/Factors.vue:440`、`frontend/src/pages/Settings.vue:46`；新增 `tests/test_screen_counts.py`、`frontend/tests/unit/Factors.test.js`、`frontend/tests/unit/Settings.test.js`。

- [ ] 增加 `buffer_count = sum(1 for r in result_rows if r['holdable'] and not r['selected'])`；保留 top_n/keep_n 为兼容配置值，页面不再把差值当人数。
- [ ] 空快照、实时、数据库、默认策略四类响应都返回 selected_count/buffer_count；空结果为0，不靠客户端目标数兜底成“实际”。
- [ ] 目标10/容差5，用0、3、12、20条通过样本，分别断言入选0/3/10/10，缓冲0/0/2/5。
- [ ] 预览慢响应期间结果为null，组件不得报错；失败可重试，草稿仍保留。
- [ ] 设置GET失败只出现错误与重试；取消还原已保存字段；保存成功但重读失败要表达“已保存，重新读取失败”，不能当作保存失败重复提交。
- [ ] 测试邮件仍使用服务端已保存配置；本批至少在界面明确该规则，后续脏状态禁用依照总体计划 T09。

退出门槛：计数用例、组件加载/取消/失败用例通过。计数与设置分别提交，便于独立回退。

### A4：隔离评级范围变更并补合同（0.5～1 日，依赖 A1/A3）

文件：当前4个后端修改文件、两个转债页面；测试新增 `tests/test_rating_contract.py`、`tests/test_cb_fetch_request.py`，更新两份总体整改文档的评级约定。

- [ ] 范围决策登记：是否保留扩大评级范围；若保留，明确新用户默认值、旧用户偏好保留、重置含义、NONE含义。未经产品确认不擅自回退现有工作区，也不把新范围当原计划已批准。
- [ ] 把评级代码与显示名称集中定义；验证是否需要 BBB+/BBB- 等细分，不能把手列14项称为已验证完整全集。
- [ ] NONE只表示缺失评级，空数组表示不限；大小写、空白、重复、异常类型分别测试，非法结构返回字段错误，不按字符串逐字符解释。
- [ ] mock抓取请求验证已去除rating限制参数；mock上游包含BBB/缺失评级，验证传递、快速筛选、策略筛选、配置保存重读一致。
- [ ] 上游真实支持情况单独登记验证结果；不为合同测试发生产行情请求。评级放开也影响日频快照，统计变化必须在发行说明中列出。
- [ ] 将该行为变更拆为独立提交/PR；旧整改稿中“七档范围”的合同与QA一起更新，保留历史基线说明。

### A5：合入前验收（0.5 日，依赖 A1～A4）

- [ ] 所有新增回归测试先有旧实现失败记录，再有修复后的通过记录。
- [ ] 前端执行 Node工具测试、组件测试、构建；后端执行相关测试及全套回归，所有外部访问使用替身。
- [ ] 本地接口联调至少完成“输入→查询→错误恢复”“策略预览→真实计数”“设置读取失败→重试”“添加指数→状态刷新”四条路径。
- [ ] 用 `git diff --check` 检查补丁；逐文件暂存并检查 staged diff，无数据、凭据、日志与构建产物混入。
- [ ] 分开文档、查询修复、计数修复、设置、轮询、评级范围变更；不要求这些文件在同一次提交中完成。
- [ ] 按问题编号复审；R1/R2未关闭不得进入团队发布。R3/R4纳入本批关闭，不靠“构建通过”豁免。

## 5. 第二批：第一批验收后再推进

| 顺序 | 工作 | 依赖与具体产出 | 预计开发人日 |
|---|---|---|---|
| B1 | 迁移与共享配置（原T04） | 验证旧库schema→建立Alembic基线→事务导入JSON→版本号冲突409→并发与恢复测试；禁止直接改线上表。 | 4～6 |
| B2 | 本次运行可追踪（原T03） | B1迁移基础完成；POST事务创建operation/run，返回id；GET仅查询；状态与数据freshness分开；替换A2临时自动刷新。 | 4～6 |
| B3 | 身份和权限（原T02） | 确定可信身份来源；实现服务端角色、共享修改审计、CORS合同；可与B1开发，但多人正式使用前必须验收。 | 3～4 |
| B4 | 两条转债流程（原T06/T07） | B1/B3及查询元信息合同通过后，做草稿/应用参数、模板版本、筛选解释和组件拆分；不在第一批混入重写。 | 沿用原计划 |

需要在第二批开始前冻结的清单：

- [ ] 评级扩大范围及默认行为已登记并更新合同。
- [ ] 选择团队可信身份来源，指定共享配置管理角色。
- [ ] 明确数据备份路径、旧库副本、迁移窗口和恢复负责人。
- [ ] 本次任务与数据集状态采用独立字段，所有调用方签字确认接口样例。

## 6. 本次已执行检查与限制

- `git diff --check`：通过。
- 当前前端 `pnpm build`：通过，2255 modules；这没有识别R1。
- 当前两个既有Node测试文件：14/14通过；它们不覆盖本次新增调用链。
- 实际函数体最小复现：R1 TypeError已复现，未发网络请求。
- 实际校验工具执行：R3 Infinity且ok=true已复现；常规120/0/-1.5与非法文本也已核对。
- 轮询判定表达式复现：fresh=false、stale=true；完整页面计时器与后台运行尚未联调。
- 后端pytest本次未执行；不得沿用不存在的通过结果。Python项目环境按A0补齐。
- 本次只新增此推进计划，没有修改、暂存或提交既有业务补丁。

原总体路线仍参考 [整改意见](2026-09-09-remediation-design.md) 和 [总体实施计划](2026-09-09-remediation-plan.md)。本文件作为当前工作区的增量审查与第一批执行顺序，不把已尝试的代码改动等同于验收完成。
