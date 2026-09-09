# 第二阶段：接口合同与质量基线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口上一轮筛选、评级、同步和设置修复的跨层合同，并建立可重复执行的前后端测试基线，使下一阶段数据库迁移和任务持久化有可靠回归保护。

**Architecture:** 保留现有 Vue 3 + FastAPI 模块化单体。本阶段把输入约束下沉到 FastAPI 请求模型，把评级语义集中到后端目录接口，把页面计时和表单逻辑抽成可测试单元；不在本阶段改数据库结构或引入任务队列。

**Tech Stack:** Vue 3、Element Plus、Axios、Node test/Vitest、FastAPI、Pydantic 2、SQLAlchemy、pytest。

---

## 1. 审核范围与提交基线

审核范围为 `23ad995..02c2cce`，共 6 个本地提交：

| 提交 | 内容 |
|---|---|
| `b3133d9` | 整改设计、总体计划、复审推进计划 |
| `e0bda90` | 评级传参、数值校验、查询变量遮蔽修复 |
| `70564fb` | 同步后只读自动刷新 |
| `8a5c8ee` | 实际入选/缓冲计数和预览空值保护 |
| `caca5fc` | 设置页取消与加载错误 |
| `02c2cce` | 抓取层取消评级白名单 |

审查时分支为 `main`，比本地记录的 `origin/main` 超前 6 个提交；没有执行 `git fetch`，因此不据此判断远端服务器的最新状态。审查开始时已跟踪工作区和暂存区均为空。

本轮差异共涉及 13 个文件，新增 1014 行、删除 50 行，其中 733 行为文档。`git diff --check 23ad995..HEAD` 报告整改设计文档第 3～5 行 Markdown 硬换行尾随空格；属于格式问题，不影响运行。

## 2. Review 意见

按照项目 Review 原则，先列具体缺陷，再记录验证通过项。以下意见仅依据 diff、当前代码和可执行检查，不以提交说明作为正确性证据。

### 2.1 具体缺陷

| ID | 优先级 | 缺陷与反例 | 证据 | 本阶段处理 |
|---|---|---|---|---|
| P2-R01 | P1 | 后端没有复现前端数值约束。直接请求 `price_min=-1`、`year_left_min=-2` 或 `price_min=130&price_max=120` 会进入实时抓取并筛选；前端校验可以被其他客户端或旧页面绕过。 | `backend/api/routes/cb_screen.py:143`、`:145`—`:175`；新增校验只在 `frontend/src/utils/filterValidation.js:12`—`:49`。 | Task 2 建立 Pydantic 请求模型和路由测试。 |
| P2-R02 | P1 | 策略配置仍接受任意 dict，`ratings` 没有类型校验。若 API 收到 `ratings: "AAA"`，规范化代码会按字符迭代并保存为 `["A"]`；收到对象则会把键当评级保存。 | `backend/api/routes/cb_screen.py:52`—`:56`；`backend/services/cb_factors.py:153`—`:165`。 | Task 3 拒绝错误结构，迁移兼容只处理明确旧格式。 |
| P2-R03 | P1 | 手动点击指数“同步”只 POST 一次并立即 GET 一次，没有调用新增自动刷新；慢任务期间用户仍看不到后续变化。自动刷新只在“添加指数成功”路径启动。 | `frontend/src/pages/DataStatus.vue:147`—`:157`；添加路径调用位于 `:358`—`:368`。 | Task 4 让两条触发路径复用同一刷新控制器。 |
| P2-R04 | P2 | “全部评级”仍由两个页面各自硬编码 14 项，后端却允许任意非空评级。上游出现未列出的评级时，用户无法在 UI 精确选择；两个页面以后还可能漂移。 | `frontend/src/pages/Bonds.vue:34`；`frontend/src/pages/Factors.vue:7`；`backend/services/cb_factors.py:151`—`:165`。 | Task 3 建唯一评级目录和明确的“不限”语义。 |
| P2-R05 | P2 | 新增 8 项测试只覆盖纯校验函数，没有覆盖点击查询、Axios 参数、FastAPI 解析、预览渲染、同步计时器或设置页恢复。原先的变量遮蔽和跨层评级问题都可能在这些测试通过时复发。 | `frontend/src/utils/filterValidation.test.mjs:4`—`:6`、`:71`—`:74`；`frontend/package.json:6`—`:10` 没有 test 脚本。 | Task 1、2、4、5 建立组件与路由级回归。 |
| P2-R06 | P2 | 保存设置成功后调用 `load()`；`load()` 自己吞掉读取异常，随后仍提示“配置已保存”。界面同时进入加载错误态，但没有说明“保存成功、重新读取失败”，用户可能重复提交。 | `frontend/src/pages/Settings.vue:24`—`:39`、`:50`—`:57`。 | Task 5 分离保存结果与刷新结果。 |
| P2-R07 | P2 | 新建策略模板仍以旧七档列表作为无基准模板时的默认评级，和页面“完整评级谱系”“默认全选”的说明不一致。后端默认策略也仍是七档。 | `frontend/src/pages/Factors.vue:54`—`:68`；`backend/services/cb_factors.py:43`—`:44`、`:74`—`:77`。 | Task 3 明确快速筛选与策略模板的不同默认规则。 |

### 2.2 已执行验证

- `node --test src/utils/requestGuard.test.mjs src/utils/dataManagementView.test.mjs src/utils/filterValidation.test.mjs`：22 项通过。
- `pnpm build`：通过，Vite 转换 2255 个模块。
- `git diff --check 23ad995..HEAD`：仅报告设计文档 3 行尾随空格。
- 后端 pytest 未执行：系统 `python` 为 2.7.16；工作区没有 `.venv`；可用的独立 Python 3.12.14 环境没有 FastAPI。未执行不计为失败，也不能作为通过证据。

### 2.3 达标裁决

**部分达标。** `e0bda90` 修复了查询变量遮蔽、评级 query 编码和有限数校验；`70564fb` 消除了定时重复 POST；`8a5c8ee` 修复了实际计数；`caca5fc` 补上了设置加载失败入口。P2-R01/P2-R02 仍使服务端合同可被绕过，P2-R03 使手动同步体验未闭环，P2-R05 表明这些行为没有端到端回归保护，因此不能判定上一阶段整体达标。

## 3. 下一阶段任务目标

本阶段建议周期为 5～8 个开发工作日，按 1 名前端、1 名后端、QA 兼职估算。最终必须得到以下五项可验收结果：

1. 所有筛选与策略输入由服务端模型验证，前端校验只负责即时反馈。
2. 评级的目录、值、显示名和默认语义只有一个服务端事实来源；新增评级不会要求同时修改两个页面。
3. 添加指数和手动同步均满足“一次 POST，后续只 GET”，刷新失败、超时和卸载都有确定状态。
4. 设置保存、重新读取和取消是三个可区分的结果，不产生误导性成功提示。
5. 开发者能用固定命令运行前端工具/组件测试、后端测试和构建；测试不会访问真实行情、邮件或生产数据库。

本阶段不做 Alembic 数据迁移、共享配置进库、身份平台接入、持久化 operation/run、首页重做或全量 TypeScript 迁移。这些工作依赖本阶段建立的合同和测试，在下一轮推进。

## 4. 文件结构

建议新增和修改如下：

| 路径 | 责任 |
|---|---|
| `backend/api/schemas/cb_screen.py` | 快速筛选、策略配置的 Pydantic 输入模型与字段级错误。 |
| `backend/services/rating_catalog.py` | 评级规范值、显示名、排序和 NONE 映射；不包含页面状态。 |
| `backend/api/routes/cb_screen.py` | 只做 HTTP 转换、权限前置接口和应用服务调用。 |
| `backend/services/cb_factors.py` | 兼容旧配置并保存已校验结构；不再接收任意形状。 |
| `frontend/src/api/index.js` | 评级目录、筛选接口调用；参数序列化集中在 API 层。 |
| `frontend/src/utils/filterValidation.js` | 前端即时数值校验。 |
| `frontend/src/utils/dataManagementPolling.js` | 可注入时钟的单一刷新控制器。 |
| `frontend/src/pages/Bonds.vue` | 使用目录、表单和查询状态。 |
| `frontend/src/pages/Factors.vue` | 使用目录、模板草稿和实际计数。 |
| `frontend/src/pages/DataStatus.vue` | 调用刷新控制器，不自己管理多套定时状态。 |
| `frontend/src/pages/Settings.vue` | 区分保存与刷新结果。 |
| `tests/test_cb_screen_contract.py` | FastAPI 路由输入、错误和评级合同。 |
| `tests/test_cb_factors_contract.py` | 旧配置兼容、错误结构和默认策略。 |
| `frontend/tests/unit/*.test.js` | 页面事件、计时和错误状态。 |

## 5. 实施任务

### Task 1：建立统一测试入口

**Files:**

- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.js`
- Create: `frontend/tests/setup.js`
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `README.md`

- [ ] **Step 1: 固定运行时版本**

在 README 记录 Python 3.12、Node、pnpm 的支持版本和 Windows 启动命令。修正 README 中 React/Phase 1/8000 与当前 Vue/多模块/8001 的偏差。

- [ ] **Step 2: 增加明确脚本**

`frontend/package.json` 至少提供：

```json
{
  "scripts": {
    "test:node": "node --test src/utils/*.test.mjs",
    "test:unit": "vitest run",
    "test": "pnpm test:node && pnpm test:unit",
    "build": "vite build"
  }
}
```

- [ ] **Step 3: 隔离副作用**

pytest 默认使用内存 SQLite 或工作区临时数据库；mock `httpx`、SMTP、调度器和后台线程。任何测试出现真实 `jisilu.cn`、SMTP 地址或 `data/web.db` 写入即失败。

- [ ] **Step 4: 执行基线**

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
```

Expected: 三条命令退出码均为 0，测试报告包含用例数量；不能用构建成功代替测试。

- [ ] **Step 5: 提交**

```powershell
git add README.md requirements.txt requirements-dev.txt frontend/package.json frontend/vitest.config.js frontend/tests/setup.js
git commit -m "test: establish reproducible frontend and backend checks"
```

### Task 2：将快速筛选约束下沉到后端

**Files:**

- Create: `backend/api/schemas/__init__.py`
- Create: `backend/api/schemas/cb_screen.py`
- Modify: `backend/api/routes/cb_screen.py:143`
- Create: `tests/test_cb_screen_contract.py`
- Create: `frontend/tests/unit/Bonds.test.js`

- [ ] **Step 1: 先写失败的 HTTP 合同测试**

使用 FastAPI TestClient，mock `screen_bonds_intraday`，覆盖：负价格、负年限、倒置区间、NaN/Infinity、合法负溢价、合法负收益率、评级逗号传参。非法输入断言 422，并断言 mock 抓取函数调用次数为 0。

- [ ] **Step 2: 定义模型**

模型至少表达以下规则：

```python
class IntradayFilterQuery(BaseModel):
    price_min: Annotated[float | None, Field(ge=0)] = None
    price_max: Annotated[float | None, Field(ge=0)] = None
    curr_iss_amt_max: Annotated[float | None, Field(ge=0)] = None
    year_left_min: Annotated[float | None, Field(ge=0)] = None
    year_left_max: Annotated[float | None, Field(ge=0)] = None
    premium_rt_max: float | None = None
    ytm_min: float | None = None
```

模型验证器拒绝所有非有限数，并验证 `price_min <= price_max`、`year_left_min <= year_left_max`。具体错误挂在对应字段或区间错误码，不返回 Python 堆栈。

- [ ] **Step 3: 路由只传已验证数据**

保留当前 GET URL 兼容调用方；用 FastAPI query dependency 构建模型。评级字符串解析后交给评级目录规范化，异常值返回 422。

- [ ] **Step 4: 增加页面事件测试**

挂载 Bonds 页面，mock 黑名单和筛选 API；点击查询断言只调用一次、参数包含 `ratings: "AAA,AA+"`。非法文本断言 API 未调用，loading 最终归零。

- [ ] **Step 5: 验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cb_screen_contract.py -q -p no:cacheprovider
Set-Location frontend
pnpm test:unit -- Bonds
```

Expected: HTTP 合同和页面事件全部通过。

```powershell
git add backend/api/schemas backend/api/routes/cb_screen.py tests/test_cb_screen_contract.py frontend/tests/unit/Bonds.test.js
git commit -m "fix: enforce bond screening contract on the server"
```

### Task 3：统一评级目录和策略输入

**Files:**

- Create: `backend/services/rating_catalog.py`
- Modify: `backend/api/schemas/cb_screen.py`
- Modify: `backend/api/routes/cb_screen.py:40`
- Modify: `backend/services/cb_factors.py:40`
- Modify: `frontend/src/api/index.js`
- Modify: `frontend/src/pages/Bonds.vue:34`
- Modify: `frontend/src/pages/Factors.vue:7`
- Create: `tests/test_cb_factors_contract.py`
- Create: `frontend/tests/unit/RatingCatalog.test.js`

- [ ] **Step 1: 冻结语义**

快速筛选默认 `ratings=[]`，表示不限，避免新增评级被旧“全选列表”排除。现有策略模板继续保留保存的七档规则，避免行为突变；新模板复制当前模板。NONE 只映射缺失评级，不作为真实信用等级。

- [ ] **Step 2: 建唯一目录**

后端目录定义 `{value,label,order,is_missing}`。提供 `GET /api/cb-list/factors/ratings`；响应包含当前已知规范等级，并合并数据库快照发现的未知非空值，未知值排在末尾。两个页面删除本地 RATING_OPTIONS。

- [ ] **Step 3: 拒绝错误配置结构**

策略保存改用 Pydantic 模型。`ratings` 只接受 `list[str]`；字符串、对象、包含空字符串、重复值或非字符串元素返回 422。兼容 `excluded_ratings` 只在读取旧文件的迁移函数中执行，不用于新 POST。

- [ ] **Step 4: 覆盖兼容与未知值**

测试必须包含：`"AAA"` 字符串不会被保存成 `["A"]`；NONE 匹配空评级；未知上游值可出现在目录并可保存；旧七档策略读取结果不变；快速筛选空列表不限评级。

- [ ] **Step 5: 验证并提交**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_cb_factors_contract.py tests/test_cb_screen_contract.py -q -p no:cacheprovider
Set-Location frontend
pnpm test:unit -- RatingCatalog
```

```powershell
git add backend/services/rating_catalog.py backend/api/schemas/cb_screen.py backend/api/routes/cb_screen.py backend/services/cb_factors.py frontend/src/api/index.js frontend/src/pages/Bonds.vue frontend/src/pages/Factors.vue tests/test_cb_factors_contract.py frontend/tests/unit/RatingCatalog.test.js
git commit -m "fix: centralize rating semantics and validate strategies"
```

### Task 4：统一数据状态自动刷新

**Files:**

- Create: `frontend/src/utils/dataManagementPolling.js`
- Create: `frontend/src/utils/dataManagementPolling.test.mjs`
- Modify: `frontend/src/pages/DataStatus.vue:147`
- Create: `frontend/tests/unit/DataStatus.test.js`

- [ ] **Step 1: 写计时失败测试**

假时钟覆盖：添加指数、手动同步、GET 慢于间隔、GET 失败、5 分钟截止、切换目标、组件卸载。每种路径断言触发 POST 恰好一次，GET 不重叠。

- [ ] **Step 2: 抽刷新控制器**

控制器接口固定为：

```js
const refresh = createAutoRefresh({
  load,
  intervalMs: 3000,
  deadlineMs: 300000,
  now: () => Date.now(),
  schedule: (fn, ms) => setTimeout(fn, ms),
  cancel: (id) => clearTimeout(id),
});
```

公开 `start(context)`、`stop(reason)`、`isRunning()`；同一时刻最多一个 load。数据新鲜度不参与任务终态判断。

- [ ] **Step 3: 两条入口复用**

`syncOne(code)` POST 成功后启动控制器；添加指数成功后也调用同一入口。POST 失败不启动刷新。刷新错误提示“自动刷新失败”；到期提示“已停止自动刷新，请查看抓取记录”。

- [ ] **Step 4: 保持限制陈述准确**

本阶段仍没有 run_id，所以 UI 不显示“本次任务成功”。抓取记录页是最终状态入口。下一阶段持久化 operation/run 后替换固定 5 分钟刷新。

- [ ] **Step 5: 验证并提交**

```powershell
Set-Location frontend
node --test src/utils/dataManagementPolling.test.mjs
pnpm test:unit -- DataStatus
```

```powershell
git add frontend/src/utils/dataManagementPolling.js frontend/src/utils/dataManagementPolling.test.mjs frontend/src/pages/DataStatus.vue frontend/tests/unit/DataStatus.test.js
git commit -m "fix: share bounded refresh across sync entry points"
```

### Task 5：区分设置保存与重新读取结果

**Files:**

- Modify: `frontend/src/pages/Settings.vue:24`
- Create: `frontend/tests/unit/Settings.test.js`

- [ ] **Step 1: 写状态组合测试**

覆盖 GET 初始失败、保存失败、保存成功且重读成功、保存成功但重读失败、取消编辑、敏感值留空。保存和 GET 分别使用独立 mock。

- [ ] **Step 2: 让 load 返回结果**

`load()` 返回 `{ok:true}` 或 `{ok:false,error}`。初始加载失败进入阻断错误页；保存后的重读失败保留“保存已完成”事实，同时显示“页面未能重新读取，请重试加载”，不提示再次保存。

- [ ] **Step 3: 取消只恢复已保存快照**

成功 GET 后保存一份不可变 draft 基线。取消恢复该快照；未成功读取配置时不提供编辑或取消操作。

- [ ] **Step 4: 验证并提交**

```powershell
Set-Location frontend
pnpm test:unit -- Settings
```

```powershell
git add frontend/src/pages/Settings.vue frontend/tests/unit/Settings.test.js
git commit -m "fix: report settings save and reload outcomes separately"
```

### Task 6：阶段验收和文档同步

**Files:**

- Modify: `docs/2026-09-09-remediation-design.md`
- Modify: `docs/2026-09-09-remediation-plan.md`
- Modify: `docs/2026-09-09-next-iteration-plan.md`
- Modify: `README.md`
- Create: `docs/2026-09-09-phase-2-verification.md`

- [ ] **Step 1: 清理文档合同**

统一“快速筛选空评级=不限”“旧策略保留七档”“目录可发现未知评级”。修复 `git diff --check` 报告的 3 行尾随空格。

- [ ] **Step 2: 运行完整验证**

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
Set-Location frontend
pnpm test
pnpm build
Set-Location ..
git diff --check HEAD~5..HEAD
```

Expected: 所有命令退出码 0；验证文档记录实际测试数量、构建结果、Python/Node/pnpm 版本和未覆盖范围。

- [ ] **Step 3: 手工核心路径**

使用假数据或隔离测试环境完成：快速筛选合法/非法输入、低评级与无评级、策略保存错误结构、手动同步自动刷新、添加指数自动刷新、设置保存后重读失败。不得向真实邮箱发送测试邮件。

- [ ] **Step 4: Review 检查表**

- [ ] P2-R01～P2-R07 每项都有测试名和关闭证据。
- [ ] 没有用前端校验替代服务端校验。
- [ ] 没有把数据 fresh/stale 当成任务执行状态。
- [ ] 没有两个页面各自维护评级常量。
- [ ] 没有测试访问真实数据源、真实邮件或工作数据库。
- [ ] 没有数据文件、`.env`、日志、缓存和 `frontend/dist` 进入提交。

- [ ] **Step 5: 提交阶段证据**

```powershell
git add README.md docs/2026-09-09-remediation-design.md docs/2026-09-09-remediation-plan.md docs/2026-09-09-next-iteration-plan.md docs/2026-09-09-phase-2-verification.md
git commit -m "docs: record phase 2 contract verification"
```

## 6. 阶段完成定义

只有以下条件全部满足，第二阶段才可判定“达标”：

- [ ] P2-R01、P2-R02、P2-R03 三个 P1 缺陷关闭并有失败→通过的回归证据。
- [ ] P2-R04～P2-R07 有对应测试和实际关闭提交。
- [ ] 后端全套 pytest 在项目 Python 3 环境通过；未安装依赖不再是验收状态。
- [ ] 前端工具测试、组件测试和生产构建通过。
- [ ] 筛选、策略、同步、设置四条路径在隔离环境完成手工验证。
- [ ] 工作区除下一阶段明确文档外为空，提交按任务边界可独立回退。

如果 P1 未清零，下一轮继续本阶段，不进入数据库迁移。完成后下一阶段按顺序推进：Alembic 与共享配置事务化（原 T04）→ 持久化 SyncOperation/TaskRun（原 T03）→ 身份与权限（原 T02，可与前两项并行开发但在团队发布前完成）。

## 7. 风险与回退

- 评级默认语义改变会改变结果数量。快速筛选改为空数组“不限”时，发布说明必须展示前后条件；旧策略不得自动扩档。
- Pydantic 严格验证可能暴露旧客户端的非法请求。保留原 URL，但不保留错误数据的静默接受；前端同步更新后再发布。
- 刷新控制器只改善可见状态，不提供任务持久化。服务重启后本次运行仍可能无法关联，这属于下一阶段目标，UI 必须如实描述。
- 每个任务独立提交。出现回归时回退对应提交，不回退已验证的查询修复或覆盖用户数据。
