# 市场数据平台整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本稿按人工开发交接组织工作包；如用编码代理执行，由对应任务负责人逐包实施，不自动整库重写。

**Goal:** 将现有平台整改为内部团队可每日使用、结果可解释、采集可追踪、共享配置可恢复的工具。

**Architecture:** 保留 Vue/FastAPI 模块化单体；分开查询、配置写入与采集任务。SQLite 继续使用，加入版本迁移与并发更新校验，采集由单独 worker 执行。

**Tech Stack:** 现有 Vue 3 / Element Plus / ECharts / FastAPI / SQLAlchemy / SQLite / APScheduler；新增 Alembic、前端组件与 E2E 测试工具，版本经 T00 验证后锁定。

**输入基线：** Git `23ad995`；[整改意见与目标设计](2026-09-09-remediation-design.md) 的 F01～F20 为问题编号，本文 QA-01～QA-16 为验收编号。新路径、接口和测试名称均为计划新增，不表示仓库已存在。

---

## 1. 执行规则与工作包总览

先取当前分支与数据备份，在隔离分支逐包开发。每个 PR 围绕一个行为变化：先补能揭示问题的测试，确认旧实现失败，再修复并验证；不要把界面重排、数据库迁移和算法调整混成一个 PR。已有未提交修改由负责人确认归属，不能覆盖。

| 工作包 | 负责人 | 依赖 | 开发人日 | 对应问题 / 验收 | 可独立验收的产出 |
|---|---|---|---|---|---|
| T00 环境与基线 | 后端+前端 | 无 | 2～3 | F19 / QA-15 | 新人可按文档启动、测试；建立隔离样本与迁移前备份。 |
| T01 核心错误修复 | 前端+后端 | T00 | 2～3 | F01/F03/F04/F05/F09 / QA-01/03/04/05/06 | 当前页面业务错误修复，小范围可先发布。 |
| T02 身份与权限 | 后端 | T00；身份来源冻结 | 3～4 | F06/F15 / QA-10 | 服务端角色边界、跨源预检、基础审计。 |
| T03 同步操作状态 | 后端+前端 | T04 迁移基础；T02 | 4～6 | F02/F10/F16 / QA-02/13 | 创建一次、只读轮询、重启可追踪。 |
| T04 共享配置持久化 | 后端 | T00 | 4～6 | F07/F08/F17 / QA-11/12/14 | 配置进库、版本冲突、幂等导入。 |
| T05 数据与查询契约 | 后端 | T01；T04 字段模型 | 3～5 | F11/F12/F13 / QA-07/08 | 时间、质量、指标口径、错误协议统一。 |
| T06 快速筛选流程 | 前端+后端 | T01/T02/T05 | 4～6 | F01/F05/F11/F18/F20 / QA-01/05/07/09 | 条件与结果绑定、详情抽屉、统一排除名单。 |
| T07 策略模板流程 | 前端+后端 | T01/T02/T04/T05 | 4～6 | F03/F04/F07/F11/F20 / QA-03/04/07/11 | 草稿/预览/保存/默认策略明确，评分可解释。 |
| T08 市场分析流程 | 前端+后端 | T05 | 4～6 | F12/F13/F14/F20 / QA-08 | 独立指标状态、日期、参数应用、按需历史。 |
| T09 布局与数据运维页 | 前端 | T02/T03/T06 的通用组件 | 3～4 | F09/F10/F18/F20 / QA-02/06/09 | 导航统一、任务详情、设置状态、响应式。 |
| T10 运维与恢复 | 后端 | T02/T03/T04 | 3～4 | F16/F17 / QA-13/14/15 | worker 部署、备份恢复、告警和运行手册。 |
| T11 工作台与试运行 | 全团队 | T06～T10 | 3～5 | F18/F20 / QA-09/16 | 轻量工作台、核心 E2E、负载与试运行记录。 |

合计 39～58 开发人日，另计 8～12 QA 人日。可以分别推进前后端工作，但接口/迁移先冻结；不把估算当承诺。日历规划约 6～9 周，T00 后根据人员与环境重估。T04 迁移基础先于 T03，表格编号不是串行执行顺序。

里程碑：M0=T00/T01；M1=T02/T04/T03/T05；M2=T06/T07；M3=T08/T09；M4=T10/T11。M1 未完成前仅允许单进程受控环境；团队正式推广在 M4 验收后。

## 2. 接口合同：先定这一页，再并行开发

以下为新版本约定。旧接口保留为适配层，前端逐模块迁移；所有业务调用完成迁移并经过一个试运行周期后再删旧接口。

### 2.1 快速筛选和策略预览

`POST /api/v1/bonds/screen`：JSON 请求消除数组 query 协议歧义。快速筛选与策略预览共用数据元信息，规则引擎保持独立。

```json
{
  "mode": "quick",
  "source": "live",
  "filters": {"ratings": ["AAA", "AA+"], "price_min": null, "price_max": 120},
  "sort": {"field": "ytm_simple", "direction": "desc"}
}
```

响应最小结构如下，示例日期和值仅用于说明协议：

```json
{
  "result_id": "r-example",
  "definition_version": "screen-v1",
  "applied_parameters": {"mode":"quick","source":"live","filters":{"ratings":["AAA","AA+"],"price_min":null,"price_max":120},"sort":{"field":"ytm_simple","direction":"desc"}},
  "meta": {"source":"live","observed_at":null,"fetched_at":"2026-09-09T15:10:00+08:00","quality":"unknown","warnings":["SOURCE_DATE_UNAVAILABLE"]},
  "counts": {"total":0,"passed":0,"excluded":0,"target":null,"selected":0,"buffer":0},
  "rows": []
}
```

source 只接受 live/db，非法值 422，不能静默当 db。数值必须有限，价格/规模/年限不小于 0；回报和溢价率允许负值；上下限成对验证。评级只接受当前 7 档，空数组代表“不限当前支持范围”。快速模式 target=null；策略模式 target=配置目标，selected 为实际选中数，buffer 为未入选但落在 target+容差内的实际行数。

source=db 且没有快照时返回成功响应但 quality=unavailable、warnings=[NO_SNAPSHOT]；UI 显示“暂无快照”，不能显示“筛选无匹配”。上游主数据失败返回 502，不返回空成功。强赎数据失败可返回 partial，但明确哪些规则无法评估：启用了强赎约束的策略行按“无法验证”排除，不静默放行；快速查看可展示已有行情但不伪造强赎值。这是行为变更，需更新 `tests/test_queries.py` 的降级场景和策略回归数据。

### 2.2 同步操作和任务状态

`POST /api/v1/sync-operations`，管理员；提交 `Idempotency-Key`，body 为 `{index_code, datasets}`。同一 key+同一 body 返回同一 operation_id；同一 key 不同 body 返回 409。key 绑定操作者，至少保留 24 小时，同一天内重试不新增操作。

响应 202：`{operation_id, status:"queued", run_ids:[], status_url}`。先在事务中写操作再响应，不能先开线程后补日志。

`GET /api/v1/sync-operations/{id}`：返回状态、时间、请求范围、run 列表与子项。任何 GET 都不能产生采集副作用。`POST /api/v1/sync-operations/{id}/retry` 只针对终态失败或中断子项创建新操作，记录 retry_of；重复点击仍受幂等 key 保护。

状态归并按固定优先级：有 running→running；否则有 queued→queued；全部 skipped→skipped；有成功且有 failed/interrupted→partial；无成功且有 failed→failed；无成功且有 interrupted→interrupted；其余 success/skipped 组合→success。空子项未分派为 queued，分派失败必须落 failed，不得永久 queued。partial 是终态。没有“failed 列表非空所以 busy”的兜底。

前端每 3 秒 GET，页面隐藏时降为 15 秒；120 秒后提示“仍在执行”，允许继续查看，不能重新 POST。离开页面取消计时与在途请求；回页通过 operation_id 恢复。若一个数据集已有运行，事务关联既有运行或排队，不同意图不能并发写同一 dataset binding。

### 2.3 版本写入与错误

`PUT /api/v1/strategies/{id}` 请求 `{expected_version, name, definition}`；成功 `{id, version, updated_at}`，版本失配 409 `VERSION_CONFLICT`。独立 `POST /api/v1/strategies/{id}/activate` 指定已保存 version，事务修改团队默认引用。默认模板不能直接删除，先选择替代；删除保留软删除状态及版本记录。

订阅唯一键为指数 code，绑定唯一键为 subscription_id+dataset。新增已有绑定返回 409 `BINDING_EXISTS`，独立换源操作展示旧/新来源并留审计；历史行增加来源标识或保持旧 storage key，禁止直接覆盖同代码历史。

错误协议：`{error:{code,message,fields,request_id}}`。401 未登录、403 无权限、409 冲突、422 字段错误、502 上游失败、503 内部临时不可用。fields 为字段名到错误文案数组；堆栈、密钥和原始上游响应不得回传。旧接口 detail 由临时客户端适配器处理。

## 3. 工作包实施清单

### T00：建立可复现基线

文件：修改 `README.md`、`requirements.txt`、`frontend/package.json`、`frontend/vite.config.js`；新增 `requirements-dev.txt`、`docs/development.md`、`docs/operations.md`、`tests/fixtures/`、`frontend/tests/fixtures/`。锁文件采用仓库已有 pnpm；Python 锁定方式由本任务选定并提交可复现产物。

- [ ] 记录 Python 3、Node、pnpm 版本；修正 README 的 Vue 技术栈、模块列表及 Windows 命令，统一后端 8001 与代理；给出生产与开发的不同入口。
- [ ] 建专用测试数据库与配置目录；禁止测试默认读取真实凭据、发送邮件或请求外部行情。审核现有测试 monkeypatch 覆盖后再跑全套。
- [ ] 固定样本：至少 6 只转债，覆盖 AAA/AA+、负溢价、空价格、0 值、强赎缺失；指数样本覆盖不同数据日期、断点、空历史、两个代码。
- [ ] 建 frontend test、test:unit、test:e2e 脚本与配置，新增 Vitest/Vue Test Utils/Playwright 开发依赖并锁版本；先保证旧 14 项 Node 测试纳入 test。
- [ ] 对生产样本副本做基线记录：表行数、主键/日期范围、配置哈希、算法样本输出。迁移前备份数据库和 JSON，实际恢复至隔离目录核对。
- [ ] 运行 QA-15；单独提交环境 PR。环境不齐时记录准确阻塞，不把“未运行”写成通过。

### T01：修复当前最影响使用的错误

文件：修改 `frontend/src/api/index.js`、`frontend/src/pages/Bonds.vue`、`frontend/src/pages/Factors.vue`、`frontend/src/pages/Settings.vue`、`backend/services/cb_screen.py`；新增 `frontend/src/utils/filterValidation.js`、`frontend/tests/unit/legacyContracts.test.js`、`frontend/tests/unit/Factors.test.js`、`frontend/tests/unit/Settings.test.js`、`tests/test_screen_counts.py`。

- [ ] 先加 QA-01/03/04/05/06 回归，用旧代码验证失败。数组合同测试使用真实 Axios 序列化；不能仅验证 normalizeFilters 返回了数组。
- [ ] 旧 GET 接口先显式 `ratings: ratings.join(',')`；空数组发空字符串。新 POST 接口上线后再迁移，禁止同时偷偷改后端 query 协议。
- [ ] 结果卡里的所有结果访问放入 `v-if="previewResult"` 边界，loading 与结果组件分开；保存空态不触发 null 解引用。
- [ ] 后端保留兼容字段时令 top_n/keep_n 表示真实计数，另添 target_count/hold_limit；同步调整前端标题。固定样本断言 selected_count 等于实际 selected 行数。
- [ ] 严格解析数字，错误就地显示；补充区间验证。设置页定义取消恢复函数，加载失败不展示可编辑空配置。
- [ ] 执行 QA-01/03/04/05/06、现有前端测试、构建与相关后端测试；分别提交“合同/数字”“筛选渲染与计数”“设置交互”小 PR。

针对 F01 的合同回归可用以下具体测试思路，旧实现必须因缺少 ratings key 而失败（T00 配置 Vitest 后使用）：

```js
import { describe, it, expect, vi } from 'vitest';
import api, { screenBondsIntraday } from '../../src/api/index.js';

describe('legacy ratings contract', () => {
  it('uses the backend ratings key and preserves AA+', async () => {
    const spy = vi.spyOn(api, 'get').mockResolvedValue({ data: {} });
    try {
      await screenBondsIntraday({ ratings: ['AAA', 'AA+'] });
      const [url, config] = spy.mock.calls[0];
      const uri = new URL(api.getUri({ ...config, url }), 'http://test.local');
      expect(uri.searchParams.get('ratings')).toBe('AAA,AA+');
      expect(uri.searchParams.has('ratings[]')).toBe(false);
    } finally {
      spy.mockRestore();
    }
  });
});
```

### T02：建立内部团队权限

文件：新增 `backend/api/dependencies/access.py`、`backend/services/access.py`、`backend/models/audit.py`、`tests/test_access.py`；修改 `backend/main.py` 与所有写接口、`frontend/src/router/index.js`。审计表迁移随 T04 数据库版本基础合并，合并前可用内存库测试。

- [ ] 冻结身份来源。若选网关身份，服务只允许网关访问，网关覆盖外部同名头；若选 OIDC，后端校验签名、issuer、audience、过期时间，浏览器只用会话。
- [ ] 按设计稿角色表保护路由，写接口、实时抓取和管理员错误详情均不能只依赖前端隐藏。加入 401/403 与资源存在性一致响应。
- [ ] 审计共享修改和采集触发；敏感字段仅记录 changed=true。Cookie 会话方案需 CSRF 保护，SameSite 策略与同源部署一起验证。
- [ ] 明确 CORS 可信来源，允许 GET/POST/PUT/PATCH/DELETE/OPTIONS，执行 QA-10；未认证用户不得通过预检成功绕过业务权限。
- [ ] 用测试身份完成全角色矩阵，再部署到试运行环境；不要求用户在代码或文档中提供凭据明文。

### T04：数据库迁移与共享配置（先于 T03）

文件：新增 `alembic.ini`、`migrations/env.py`、`migrations/versions/0001_existing_schema.py`、`migrations/versions/0002_shared_configuration.py`、`backend/models/strategy.py`、`backend/models/subscription.py`、`backend/repositories/strategies.py`、`backend/repositories/subscriptions.py`、`scripts/import_legacy_config.py`、`tests/test_config_migrations.py`、`tests/test_config_concurrency.py`；修改 `backend/models/database.py`、`backend/services/cb_factors.py`、`backend/services/index_universe.py` 与配置路由。

- [ ] 从当前模型建立初始 schema 版本；已有库必须验证表/列后才可 stamp，不能不检查直接 stamp head。
- [ ] 加入 strategy、strategy_version、team_default_strategy、index_subscription、dataset_binding、audit_event。主键和唯一约束承担完整性，不依赖“先查后写”避免重复。
- [ ] JSON 只作迁移输入；dry-run 输出记录数/重复键/非法值，不写。真实导入在一个事务内，保存源文件哈希与 migration_batch_id，同一批重复执行不重复导入。
- [ ] 版本更新用 `UPDATE ... WHERE id=:id AND version=:expected`，rowcount=0 返回 409；同一事务写新不可变版本与审计。索引添加与启停改成局部事务，不再全文件覆盖。
- [ ] 损坏 JSON 中止导入并报告路径和解析位置，不能生成默认策略假装成功；原文件只读保存归档。旧服务通过仓储适配维持旧 API。
- [ ] QA-11/12/14：两个会话同时编辑、并发添加、导入重复执行、导入中途失败都必须可核对；迁移验收后再切换写入口。

### T03：同步任务状态与执行解耦

文件：新增 `backend/models/sync_operation.py`、`migrations/versions/0003_sync_operations.py`、`backend/application/sync_operations.py`、`backend/api/routes/sync_operations.py`、`backend/worker.py`、`frontend/src/features/data-management/api.js`、`frontend/src/features/data-management/useSyncOperation.js`、`tests/test_sync_operations.py`、`frontend/tests/unit/useSyncOperation.test.js`；修改 `backend/main.py`、`backend/scheduler.py`、`backend/services/run_logger.py`、`backend/api/routes/data_management.py`、`frontend/src/pages/DataStatus.vue`。

- [ ] 先写 mock 时钟/worker 的 QA-02：一次创建后只允许 GET，任何额外 POST 让测试失败；覆盖所有状态和刷新恢复。
- [ ] 请求内提交 operation、幂等 key 和任务目标；worker 领取 queued 项。用数据库条件更新获得租约（owner、expires_at、attempt），领取失败不可执行。
- [ ] worker 心跳默认 10 秒，租约默认 60 秒；写入校验 attempt，过期旧 worker 不能提交结果。外部抓取可重试，落库按数据自然键 upsert；提供至少一次执行与幂等写入，不承诺跨崩溃的绝对 exactly-once。
- [ ] 调度只创建操作，不在 Web lifespan 启动采集；手动/定时走同一服务。任务支持 code+dataset 范围，避免单指数操作扫全部名单。
- [ ] 前端使用 operation_id GET 轮询，取消裸 POST 定时器；终态停止，运行中刷新、跳页再回来都恢复准确状态。任务详情以 run/items 直接展示，删掉双源状态判断。
- [ ] QA-02/13 通过后切换旧触发接口到新服务适配；进程关闭先停止领取再完成/标记当前任务。

### T05：统一数据状态与查询响应

文件：新增 `backend/schemas/screening.py`、`backend/schemas/data_meta.py`、`backend/schemas/errors.py`、`backend/application/screening.py`、`backend/services/data_quality.py`、`frontend/src/api/client.js`、`tests/test_screening_contracts.py`、`tests/test_data_quality_contract.py`；修改 `backend/api/routes/cb_screen.py`、`backend/api/routes/valuation.py`、`backend/services/queries/live.py`、`backend/services/cb_intraday.py`。

- [ ] 实现本文接口模型，额外未知字段拒绝或明确兼容名单，不能让拼错字段静默丢失；后端对所有前端校验再次校验。
- [ ] 将实时数据与快照适配成统一 Snapshot 输入，保留原始来源和观察时间，不把 fetched_at 写成行情时间。
- [ ] quality 与 warnings 分数据集返回；缺时间为 unknown；强赎失败不等同空列表。按 2.1 的规则约束执行降级，记录不可评估原因。
- [ ] 制定字段字典：label、unit、precision、definition_version、missing_reason；计数由最终结果生成；响应附实际 applied_parameters。
- [ ] 市场读取增加 start/end 与标的参数，指标独立返回日期，避免每次全历史；缓存键包含代码、窗口、定义版本、最新数据版本。
- [ ] 完成 QA-07/08 的合同测试和请求错误测试；固定时钟保证断言可重复。

### T06：快速筛选与团队排除名单

文件：新增 `frontend/src/features/bonds/BondFilterForm.vue`、`BondResults.vue`、`BondDetailDrawer.vue`、`BlacklistDialog.vue`、`useBondScreen.js`（以上均在该目录）、`frontend/src/components/common/DataState.vue`、`DataFreshness.vue`、`FilterSummary.vue`（以上均在 common）、`frontend/tests/unit/BondScreen.test.js`、`frontend/tests/e2e/bond-screen.spec.js`；修改 `frontend/src/pages/Bonds.vue`、黑名单接口及 `backend/application/screening.py`。

- [ ] 将页面拆成表单、结果、详情、名单四块，用 composable 管理 draft/applied/request/result；所有字段校验及状态规则按设计稿 5.1。
- [ ] URL 保存已应用非敏感条件和排序，localStorage 按版本保存个人草稿；两者优先级为 URL > 上次应用 > 默认，避免打开分享链接被本地草稿覆盖。
- [ ] 先校验、再冻结参数、再查询；使用 AbortController+请求序号，旧请求不能改结果、错误或 loading。改条件后持续显示未应用标记。
- [ ] 接入统一团队排除名单，显示影响范围、原因和操作者；新增/恢复后更新列表和计数，策略筛选使用同一名单输入。
- [ ] 单债详情复用结果字段，历史查询单独 loading/失败；回到列表保留滚动/排序，空结果给“放宽条件”。
- [ ] QA-01/05/07/09 通过；旧页面 URL 不变，按功能开关切换新组件，出现回归可切回旧界面但继续走修复后的接口。

### T07：策略草稿、版本与解释

文件：新增 `frontend/src/features/strategies/StrategyList.vue`、`StrategyEditor.vue`、`StrategyPreview.vue`、`useStrategyDraft.js`、`api.js`，新增 `frontend/tests/unit/StrategyDraft.test.js`、`frontend/tests/e2e/strategy.spec.js`、`tests/test_strategy_versions.py`、`tests/test_score_explanation.py`；修改 `frontend/src/pages/Factors.vue`、`backend/services/cb_screen.py`、策略路由。

- [ ] 新建模板与复制模板分离：新建空业务规则+明确默认基础条件，复制保留来源版本；编辑草稿用深拷贝并按 template_id 分开缓存。
- [ ] 预览返回规则版本/草稿哈希、准确计数；仅保存后能设默认；保存冲突显示服务端新版本，允许复制为新模板或重新加载，不能自动覆盖。
- [ ] 离开、删除、切默认都有明确状态；取消对话框不报“删除失败”；删除默认模板先选替代方案，整个默认变更事务化。
- [ ] 评分结果增加 explanation 数组，列出字段原值、方向、名次分、权重、贡献；贡献加总与 total_score 一致。保持现有排序/并列规则不变，以样本快照保护。
- [ ] 将“持仓数量/容差保留”改为“目标候选数量/排名缓冲区”，去掉暗示自动卖出的文案。没有持仓输入就不展示“应买/应卖”。
- [ ] QA-03/04/07/11 与得分解释测试通过；验证草稿预览不改变数据库默认策略。

### T08：市场分析页面

文件：修改 `frontend/src/pages/ValuationList.vue`、`ValuationDetail.vue`、`Rotation.vue`、`frontend/src/components/ValuationChart.vue`、`RotationChart.vue`、`PeChart.vue`、`frontend/src/utils/chartLink.js`；新增 `frontend/src/features/market/useValuation.js`、`useRotation.js`、`frontend/tests/unit/MarketViews.test.js`、`frontend/tests/e2e/market.spec.js`；后端修改 valuation/style_rotation 路由及对应查询服务。

- [ ] 为 PE/PB、股息、股债指标建立独立状态，请求部分失败保留可用区块；指标卡显示自己的数据日期与来源。
- [ ] watch route.params.code，初始化与同组件跳转共用加载函数，卸载失效在途请求；列表返回恢复原排序与滚动。
- [ ] 轮动表单 draft/applied 分离，点击应用才请求；请求签名缓存同代码同窗口结果，主图与补充图独立失败，不重复抓全历史。
- [ ] 图表空值断开、日期交集/缺点明确，Tooltip 显示单位和对应日期；保留原有三图悬停联动与双轴含义。
- [ ] QA-08 与 `tests/test_style_rotation_analysis.py`、`tests/test_style_rotation_valuation.py` 通过；不在 UI 重构中修改算法窗口定义。

### T09：布局、数据运维与设置

文件：修改 `frontend/src/layouts/AppLayout.vue`、`frontend/src/router/index.js`、`frontend/src/style.css`、`frontend/src/pages/DataStatus.vue`、`Settings.vue`；新增 `frontend/src/styles/tokens.css`、`frontend/src/components/common/PageHeader.vue`、`TaskStatusBadge.vue`、`frontend/src/features/data-management/IndexWizard.vue`、`RunDetail.vue`、`frontend/tests/e2e/navigation.spec.js`、`settings.spec.js`。

- [ ] 导航菜单由路由 meta 生成，按角色过滤，页面不重复输出全局标题；补 404、详情面包屑、折叠菜单可访问名称。
- [ ] 页面统一错误/空态/卡片/表单组件，删除重复自定义按钮样式；桌面紧凑布局与移动抽屉遵循设计稿第 6 节。
- [ ] 新增指数三步向导，来源能力、探测失败、过期、已存在绑定都有确定状态；保存后跳 operation_id 详情，暂停不删除历史。
- [ ] 设置页静态标签、成功读取后编辑、取消还原、脏状态禁测试邮件；请求错误转用户文案与 request_id。
- [ ] QA-02/06/09 通过；键盘完整走一次筛选、弹窗、菜单，验证 Esc 和焦点返回。

### T10：部署、备份、恢复和运行手册

文件：修改 `scripts/backup_db.py`、`docs/operations.md`、`backend/api/routes/health.py`、`backend/services/notifications.py`；新增 `scripts/restore_verify.py`、`tests/test_worker_recovery.py`、`tests/test_backup_restore.py`、`docs/release-checklist.md`。

- [ ] 发布拓扑固定为 Web 服务与单 worker，只有 worker 运行调度；readiness 检查 schema 版本与数据库可用，不能只返回进程活着。
- [ ] 备份产物加入应用版本、schema 版本、配置清单、校验摘要；按当前需求建议每日备份并在每次变更前备份，保留 14 天，负责人确认磁盘容量。
- [ ] 恢复演练在隔离目录，验证表结构、配置、读取接口和固定筛选结果；不连接邮件或真实行情。建议目标 RPO≤24小时、RTO≤2小时，必须演练计时才可宣称达标。
- [ ] 观测指标包括 API 延迟/错误率、最老 queued 时长、过期租约、各数据集最新日期、写锁等待；任务 partial/failed/interrupted 告警去重并附 operation_id。
- [ ] QA-13/14/15 通过，手册给出停止写入、备份、迁移、启动、验证、回退命令；不依赖维护者记忆。

### T11：工作台与试运行验收

文件：新增 `frontend/src/pages/Workspace.vue`、`frontend/tests/e2e/workspace.spec.js`、`tests/test_workspace_summary.py`、`docs/pilot-report.md`；修改路由和只读聚合接口。

- [ ] 工作台只聚合数据健康摘要、最近保存的团队模板、本人浏览器最近使用入口；管理员有失败任务入口。复用既有数据，不新增投资信号或评分算法。
- [ ] 默认入口是否切工作台由试运行结果决定，旧 URL 不失效；最近使用数据有明确个人/团队标签。
- [ ] 在固定环境运行 QA-16 负载与首屏检查，记录版本、样本、并发、p50/p95、失败数，不能只给平均值。
- [ ] 3～5 名试运行用户连续使用 5 个工作日，覆盖查询、模板、恢复和同步；记录完成时间、误操作、结果疑问和阻断项。
- [ ] 清零 P1，再完成下面发布清单；本次整改结束以验收记录为依据，不以页面数量为依据。

## 4. 验收用例（开发与 QA 共用）

所有数据为测试夹具；真实行情不是可重复测试的断言依据。本文测试名称为未来实现要求；执行前须在 T00 建好工具环境。

| 编号 / 测试名 | 操作与输入 | 必须满足的断言 |
|---|---|---|
| QA-01 `legacy ratings contract` / `test_screen_ratings` | AAA、AA+、AA 各一条，选择 AAA；再选择 AA+；再不限。 | query/JSON 协议匹配；分别只返回对应评级；AA+ 的 + 不变空格；不限仅表示当前支持范围。 |
| QA-02 `useSyncOperation.test.js` / `test_sync_idempotency` | 提交一次；模拟 queued→running→success，另测 partial/failed/interrupted/skipped；刷新页面；重复幂等 key。 | 最多一次 POST 创建，后续仅 GET；终态停止；刷新仍定位同操作；failed 不显示 busy；不同 body 同 key 为 409。 |
| QA-03 `Factors.test.js` | 使用延迟 500ms 的预览，结果初始 null；再返回成功、失败、空结果。 | loading 不渲染 null 属性；无 Vue render error；失败保留草稿；空态可再执行。 |
| QA-04 `test_screen_counts` | target=10、tolerance=5，仅 3 条通过；另测 12 条和 0 条。 | 分别 selected=3/10/0、buffer=0/2/0；显示目标与实际；counts 与行标记一致。 |
| QA-05 `test_filter_validation` | abc、120abc、120,5、Infinity、空串、0、负溢价、最低价>最高价。 | 非法不请求且说明字段；空串不限；0 不丢失；允许负溢价；区间倒置 422；原结果带旧条件标记。 |
| QA-06 `Settings.test.js` / `settings.spec.js` | 初始 GET 500；配置后重新编辑再取消；未保存时测试邮件；保存失败。 | GET 失败字段名称仍可读且禁保存；取消恢复；草稿不发送邮件；失败不清草稿；敏感值不回显。 |
| QA-07 `BondScreen.test.js` / `StrategyDraft.test.js` | A 请求慢、B 请求快；编辑条件但未应用；B 失败；切模板再返回。 | 结果/标题/条件同源；A 不覆盖 B；旧结果明显标识；草稿不丢；预览不持久化。 |
| QA-08 `MarketViews.test.js` / `market.spec.js` | 股息请求失败、PE 成功；两个指数同组件路由切换；混合日期、null 与 0；缩短区间。 | PE 保留，股息局部错误；新代码新数据；独立日期；null 断点且 0 保留；只请求所需窗口；三图日期联动准确。 |
| QA-09 `navigation.spec.js` | 1440×900、1280×720、768×1024、390×844；键盘完成查询/抽屉/关闭；未知路由。 | 无页面级横向溢出，表格自身滚动除外；桌面常用筛选两行内；标题一致；焦点可见并返回；404 可返回。 |
| QA-10 `test_access` | 无身份/viewer/analyst/admin 调用所有写操作；跨源 PUT/PATCH/DELETE 预检；伪造角色头。 | 401/403/允许匹配角色表；跨源允许方法但不绕过身份；伪造头无效；审计无敏感明文。 |
| QA-11 `test_config_concurrency` | 两会话都读取 v3，A 保存 v4 后 B 用 v3 保存；写入中断；修改默认模板。 | B 收 409，不覆盖 A；事务失败无半写；默认引用与版本一致；无静默回默认配置。 |
| QA-12 `test_subscription_concurrency` | 同时添加两个不同代码；同代码重复；为已有估值绑定改源。 | 两个不同代码均存在；重复键确定返回；已有绑定不隐式换源，历史不被覆盖。 |
| QA-13 `test_worker_recovery` | 两 worker 同时领取同数据集；领取后进程退出；旧 worker 租约过期后返回。 | 同租约一个有效领取者；中断可追踪；旧 attempt 不能写；重试无重复业务行；Web 启动不触发调度。 |
| QA-14 `test_config_migrations` / `test_backup_restore` | 空库、旧 schema 副本、损坏 JSON、重复导入；备份恢复到隔离路径。 | 两种库升级到目标；损坏输入中止；重复导入幂等；恢复后配置和固定样本结果一致；日志记录时长。 |
| QA-15 干净环境启动演练 | 新目录按 README 安装；配置测试库；启动 8001 API 与前端。 | 不依赖 Python 2.7/个人绝对路径；代理连通；健康检查通过；全套测试可执行且没有真实外部副作用。 |
| QA-16 `workspace.spec.js` + 负载记录 | 20 并发、1000 转债行、50 指数各 10 年日频；固定机器，外部源用延迟/失败夹具。 | 本地只读 API p95≤500ms；不含外部等待的查询处理 p95≤1s；首屏可交互≤2.5s（固定测试网络）；0 重复任务、0 未捕获前端异常、0 未解释计数差异。外部请求单独报告，不掺入本地指标。 |

QA-16 性能值是建议目标；夹具规模超过当前通常数据量以提供余量。必须报告测试机配置与网络条件，否则不可作跨机器结论。

## 5. 验证命令与现有证据

以下命令在 T00 配置完成后执行；Python 明确使用项目 `.venv`，不是当前 PATH 中的 Python 2.7。现有项目尚无 test:unit/test:e2e 脚本，必须先建，不能现在声称执行通过。

```powershell
# 仓库根目录；依赖已按 T00 安装
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

```powershell
# frontend 目录
node --test src/utils/requestGuard.test.mjs src/utils/dataManagementView.test.mjs
pnpm run test:unit
pnpm run test:e2e
pnpm build
```

每个 PR 附相关用例通过数、旧实现失败证据、接口/迁移变化及回退方式；新接口至少有一次经 HTTP 路由的集成测试，不能全部绕过 FastAPI 直接调用 Python 函数。

本次已执行事实：上述两个 Node 工具测试文件 14/14 通过，`pnpm build` 成功。后端 pytest 未执行（Python 3 环境缺 FastAPI），E2E 工具尚未配置。浏览器仅检查前端初始/失败状态，正常数据全链路需实施团队补齐。

## 6. 数据切换、发布和回退

1. **准备**：归档当前代码版本、schema、数据库备份、两个 JSON 文件、运行方式；在隔离副本 dry-run 配置导入与 schema 迁移。备份不打包进 Git。
2. **维护窗口**：停止新任务领取，等待运行结束或显式标记中断；关闭配置写入口；再次备份。先扩展表结构，再导入共享配置。
3. **验证**：核对指数 code/绑定来源/策略数量与默认引用，执行固定筛选样本，确认数据日期与历史未变化；本次迁移不重新抓取全部行情。
4. **切流**：后台读写切到数据库仓储，旧 JSON 不再写；旧 API 适配新模型。前端模块通过开关分批切换，观察一个使用周期再移除兼容。
5. **回退条件**：任一 P1、配置丢失、重复采集、计数不一致、恢复校验失败即停止推广。先关闭受影响新入口，暂停 worker，保留错误操作与日志。
6. **回退方式**：扩展式 schema 优先回旧应用兼容版本，保留新增表；若已产生新配置写入，先导出版本与审计，不允许直接还原旧备份覆盖新数据。需恢复旧库时进入维护窗口，由负责人确认导出与对账后切库；事后重放已核对变更。

发布死检查清单：

- [ ] F01～F10 对应核心问题已修复并有运行测试，所有 P1 已关闭。
- [ ] QA-01～QA-16 有实际结果，未执行项明确标注且不能作为验收通过。
- [ ] 一次点击创建一次同步意图；GET 无采集副作用，跨重启可追踪。
- [ ] 模板、排除名单、来源变更有身份/版本/审计；并发冲突不静默覆盖。
- [ ] 筛选计数、参数、日期、来源、降级原因均与当前结果一致。
- [ ] 数据迁移和恢复演练通过，回退不会覆盖未导出的新配置。
- [ ] 完成 5 个工作日试运行，阻断项清零，指定后续故障负责人。

## 7. 追踪矩阵

| 问题 | 实施包 | 问题 | 实施包 |
|---|---|---|---|
| F01 评级协议 | T01/T06 | F11 旧结果与草稿 | T05/T06/T07 |
| F02 POST 轮询 | T03 | F12 日期混合 | T05/T08 |
| F03 null 渲染 | T01/T07 | F13 部分失败 | T05/T08 |
| F04 目标当实际 | T01/T07 | F14 详情复用 | T08 |
| F05 非法数值 | T01/T05/T06 | F15 CORS 方法 | T02 |
| F06 权限缺失 | T02 | F16 多进程调度 | T03/T10 |
| F07 策略覆盖 | T04/T07 | F17 schema 升级 | T04/T10 |
| F08 名单覆盖/换源 | T04 | F18 布局导航 | T06/T09/T11 |
| F09 设置状态 | T01/T09 | F19 文档环境 | T00/T10 |
| F10 任务状态失真 | T03/T09 | F20 模块与流程测试 | T06/T07/T08/T09/T11 |
