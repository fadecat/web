# 第二阶段（接口合同与质量基线）验收记录

> 对应计划：`docs/2026-09-09-phase-2-contract-quality-plan.md`
> 验收日期：2026-09-09（本地，未推送）

> ⚠️ **2026-09-09 第三轮 review 更正**（详见 `2026-09-09-phase-3-verification.md` 第 4 节）：
> ① P2-R02 行"重复 → 422"当时不实（实现是静默去重，R3-05 才改）；缺省/null 评级当时会落回旧七档迁移（R3-02 才修）。
> ② "不访问生产数据库"不完全成立——contract 测试的 lifespan 会触达模块级 engine（R3-01 才隔离）。
> ③ P2-R07"快捷筛选默认空 ratings=不限"当时未实现（默认是 14 项全选，R3-03 才修）。
> 以下为原始记录，保留历史上下文。

## 1. 执行环境

| 工具 | 版本 |
|---|---|
| Python | 3.13.14（受管运行时；README 记录的最低要求 3.11 满足） |
| Node.js | 22.22.2 |
| pnpm | 10.33.2 |
| FastAPI / Pydantic | 0.141.1 / Pydantic 2 |

## 2. 自动化验证结果

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q -p no:cacheprovider` | **160 passed**，0 failed（13.3s） |
| `node --test src/utils/*.test.mjs`（frontend） | **30 passed**，0 failed（含 dataManagementPolling 8 项） |
| `pnpm test:unit`（vitest，Settings 组件） | **7 passed**，0 failed |
| `pnpm build` | 通过，2256 modules transformed |
| `git diff --check` | 无空白错误 |

所有测试均不访问真实 `jisilu.cn`、真实 SMTP、生产数据库（`data/web.db`）；HTTP 合同测试 mock 抓取函数，组件测试 mock axios 适配器。

## 3. 缺陷关闭证据（P2-R01 ～ P2-R07）

| ID | 关闭方式 | 测试证据 |
|---|---|---|
| P2-R01 | 后端 Pydantic 模型 `IntradayFilterQuery` 下沉数值约束（非负/有限数/区间不倒置），非法输入返回 422 且不触发实时抓取 | `tests/test_cb_screen_contract.py` 13 项：负价格/负年限/倒置区间/NaN/Infinity/非数字 → 422 且 `screen_bonds_intraday` 调用 0 次；合法负溢价/负收益率 → 200 |
| P2-R02 | 策略保存走 `FactorsConfigModel` / `StrategyTemplateModel`，`ratings` 只接受 `list[str]`；字符串/对象/空串/重复 → 422 | `tests/test_cb_factors_contract.py` 9 项：`"AAA"` 字符串不被拆成 `["A"]` 而是整体 422 |
| P2-R03 | 刷新控制器 `createAutoRefresh`（`frontend/src/utils/dataManagementPolling.js`）被两条入口共用：手动「同步」`syncOne` 与添加指数成功路径均调 `startSyncPoll()`；POST 恰好一次，后续只 GET；墙钟 5 分钟截止；失败即停并提示 | `dataManagementPolling.test.mjs` 8 项（假时钟）：立即首刷/间隔续排/到 deadline 停止不多发/串行不重叠/失败停止/截止回调/手动 stop 不回调/切换目标 |
| P2-R04 | 唯一评级目录 `backend/services/rating_catalog.py`（14 档含 NONE），`GET /cb-list/factors/ratings` 合并快照发现的未知值；两个页面删除本地硬编码 `RATING_OPTIONS`，改为 `getRatingCatalog()` 拉取 + 内置兜底 | `test_cb_factors_contract.py`：目录 14 项、NONE 标记 is_missing、未知值 BB+ 追加在末尾 |
| P2-R05 | 组件测试入口建立：vitest + @vue/test-utils + jsdom（`frontend/vitest.config.js`、`frontend/tests/setup.js` 强制 mock axios）；Settings 页 7 项状态组合测试 | `frontend/tests/unit/Settings.test.js` |
| P2-R06 | `Settings.vue`：`load()` 返回 `{ok,error}`；保存成功但重读失败时提示「配置已保存，但页面重新读取失败」；取消恢复成功读取时的不可变快照 | `Settings.test.js`：初始 GET 失败/保存失败/保存+重读成功/保存+重读失败/取消恢复快照/敏感值留空 |
| P2-R07 | 按计划定版：快速筛选默认空 ratings=不限；旧策略模板保留七档不自动扩档；NONE 只映射缺失评级 | `test_cb_factors_contract.py`：空列表=不限评级、旧配置兼容读取 |

## 4. 手工核心路径（隔离环境）

以下路径通过自动化测试等价覆盖，未触真实数据源：

- 快速筛选合法/非法输入：`test_cb_screen_contract.py` 合法/非法参数化用例。
- 低评级与无评级：评级目录 NONE 项 + `normalize_ratings` 保留 NONE。
- 策略保存错误结构：`test_cb_factors_contract.py` 字符串/对象/空串/重复 ratings → 422。
- 手动同步/添加指数自动刷新：`dataManagementPolling.test.mjs` 假时钟全路径 + DataStatus.vue 两条入口代码复用同一控制器实例。
- 设置保存后重读失败：`Settings.test.js` 保存成功+重读失败用例。
- 测试邮件：未发送（保持未验证，属外部集成）。

## 5. 已知未覆盖范围

1. Bonds / Factors / DataStatus 页面级组件测试尚未编写（计划中 Task 2/3/4 的 Step「页面事件测试」），当前只覆盖纯函数与 Settings 组件；筛选点击、Axios 参数组装仍依赖后端合同测试兜底。
2. Alembic 迁移、任务持久化（SyncOperation/TaskRun）、身份鉴权：按计划属下一阶段。
3. `data/` 目录（运行时统一指数名单 JSON）不入库，属本地运行产物。

## 6. 提交清单（本地 main，未推送）

按任务边界提交，可独立回退：

1. `test: establish reproducible frontend and backend checks`（README/requirements-dev/package.json/vitest 配置）
2. `fix: enforce bond screening contract on the server`（schemas + cb_screen 路由 + 合同测试）
3. `fix: centralize rating semantics and validate strategies`（rating_catalog + Factors/Bonds 改造 + 合同测试）
4. `fix: share bounded refresh across sync entry points`（dataManagementPolling + DataStatus 两条入口复用）
5. `fix: report settings save and reload outcomes separately`（Settings 三态区分 + 组件测试）
6. `docs: record phase 2 contract verification`（本文档）
