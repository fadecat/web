# 第三阶段验收记录：合同收口（R3-01 ~ R3-04）

> 对应计划：`docs/2026-09-09-phase-3-review-and-execution-plan.md`
> 验收日期：2026-09-09（本地，未推送）
> 本轮范围说明：R3-01~R3-04 已实施；R3-05 在 R3-02 一并完成；T5（数据库迁移基线/Alembic）未在本轮实施，见文末结转说明。

## 1. 执行环境

| 工具 | 版本 / 路径 |
|---|---|
| Python | 3.13.14 — `C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe` |
| Node.js | 22.22.2 |
| pnpm | 10.33.2 |

## 2. 自动化验证结果（本轮实际执行）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q -p no:cacheprovider` | **171 passed**，0 failed（23.8s） |
| `node --test src/utils/*.test.mjs`（frontend） | **32 passed**，0 failed（含 dataManagementPolling 10 项） |
| `pnpm test:unit`（vitest：Bonds 7 + Settings 7 + DataStatus 4） | **18 passed**，0 failed |
| `pnpm build` | 通过，生产构建成功 |
| `git diff --check` | 通过（提交前核对） |

## 3. 缺陷关闭证据

### R3-01 / P1：测试隔离应用启动（T1）

**修复**（`tests/conftest.py` 重写 + `tests/test_test_environment.py` 新增 + 两个合同测试文件迁移）：

- conftest 顶部（任何 `backend.*` 导入之前）设置 `DATABASE_URL=sqlite:///:memory:`、`SCHEDULER_ENABLED=false` 环境变量。pydantic-settings 环境变量优先级高于 `.env`，因此**即使工作目录存在日常 `.env`（DATABASE_URL=sqlite:///./data/web.db），测试进程也强制使用内存库**。修复前该环境确实会让 `init_db()` 在 import 后触达日常库。
- 新增 `contract_client` fixture：替换 `app.router.lifespan_context` 为空实现（TestClient 上下文不再执行 `init_db()`/`start_scheduler()`），`get_db` 覆盖为测试会话工厂，teardown 恢复 lifespan、关闭全部会话、dispose 测试引擎。
- 新增 `startup_spies` 哨兵：真实 `init_db`/`start_scheduler` 在测试中被调用即抛 AssertionError（main 与 database/scheduler 模块双重打点）。
- autouse `_block_external_network`：socket 层拦截非 localhost 连接，意外外部调用直接失败。
- `test_cb_screen_contract.py` / `test_cb_factors_contract.py` 全部改用 `contract_client`，删除各自手写 fixture。

**测试证据**（`tests/test_test_environment.py`，5 项）：
- `test_settings_point_to_isolated_resources` — settings 指向内存库且调度关闭
- `test_contract_client_never_calls_production_startup` — 请求后哨兵计数为 0
- `test_request_session_is_closed` / `test_external_io_is_blocked` / `test_localhost_connections_still_allowed`

### R3-02 / P1：校验模型被丢弃（T2）

**修复**（`backend/api/routes/cb_screen.py` save_factors + `backend/api/schemas/cb_screen.py` StrategyTemplateModel）：

- 路由保存 `FactorsConfigModel.model_validate(body)` 的**模型输出**（`model_dump()`，含缺省 `ratings=[]`），不再把原始 body 传给 `write_config`——修复前缺省/null 评级会被 `_normalize_templates` 的旧配置迁移逻辑识别为"旧配置"补成七档。
- `ratings: null` → 422（原实现静默转 `[]`）；显式 `[]` = 不限保持。
- 新 POST 携带旧字段 `excluded_ratings` → 422（model_validator 拦截 extra 键）；旧文件读取迁移不变。
- 前端 Factors.vue 保存 payload 本就不含 excluded_ratings，无需改动。

**测试证据**（`tests/test_cb_factors_contract.py::TestR3Contracts`，6 项，先写反例红→修复后绿）：
- `test_missing_ratings_round_trip_is_unrestricted` — 响应/文件/再 GET 三处 ratings 均为 `[]`
- `test_null_ratings_rejected` / `test_new_post_rejects_legacy_exclusions`
- `test_legacy_read_migration_idempotent` — 旧文件迁移读两次结果一致
- 422 请求不写文件（`test_normalized_duplicate_ratings_rejected` 中断言文件不存在）

### R3-03 / P2：快捷筛选默认枚举评级（T3）

**修复**（`frontend/src/pages/Bonds.vue`）：

- `defaultFilters()` 的 `ratings` 从 `[...FALLBACK_RATINGS]`（14 项全选）改为 `[]`（不限）。首次进入与重置均为不限；el-select placeholder 本就是「不限」。
- localStorage 已保存的评级选择（v2 格式）恢复逻辑不变，用户偏好不被覆盖。
- 目录加载失败保持不限语义；兜底 14 项只作可选项。

**测试证据**（`frontend/tests/unit/Bonds.test.js`，7 项）：
- 首次进入/重置/目录失败/目录含未知 BB+ 四种场景查询参数 `ratings` 均为 `''`（不限）
- localStorage 已有 `['AA+','NONE']` 恢复后请求为 `'AA+,NONE'`，数值转换正确
- 非法文本不调用筛选接口
- 测试基础设施：el-input/el-button 功能桩（转发 v-model/click），el-table 列桩注入 row 插槽参数

### R3-04 / P2：卸载后迟到 POST 重启刷新（T4）

**修复**（`frontend/src/utils/dataManagementPolling.js` + `frontend/src/pages/DataStatus.vue`）：

- 控制器新增 `dispose()`：永久失效，之后任何 `start()` 被忽略；`stop()` 保持可重启语义。
- 页面新增 `isDisposed` 哨兵：`onBeforeUnmount` 置位并 `syncRefresh.dispose()`；`syncOne` 的 POST 续体、`saveIndex` 的 loadList 前后、`triggerRun` 的全部 await 之后均检查 `isDisposed`，卸载后不再启动刷新/轮询/更新界面。
- `syncRefresh.stop()` 改为 `dispose()`（卸载是永久语义，非暂时停止）。

**测试证据**：
- `dataManagementPolling.test.mjs` 新增 2 项（10/10 全过）：`dispose 后 start 被忽略, stop 后仍可重启`、`dispose 覆盖 inFlight 窗口`（load 在途时 dispose，完成后不续排、零定时器残留）
- `frontend/tests/unit/DataStatus.test.js`（4 项）：核心反例「卸载后迟到 POST 完成 → 无任何新 GET」、卸载后迟到 POST 失败静默、正常路径一次 POST + 立即 GET

### R3-05 / P2：重复评级被接受（T2 内一并完成）

**修复**：`StrategyTemplateModel._ratings_no_empty_no_dup` 遇归一后重复项从 `continue`（静默去重）改为 `raise ValueError`（422）。

**测试证据**：`test_exact_duplicate_ratings_rejected`（`["AAA","AAA"]`）、`test_normalized_duplicate_ratings_rejected`（`["AAA"," aaa "]`）均 422 且文件未变。

## 4. 对 `2026-09-09-phase-2-verification.md` 的更正

1. **第 3 节 P2-R02 行**「`"AAA"` 字符串不被拆成 `["A"]` 而是整体 422」当时属实，但**漏报了缺省/null 评级会落回旧七档迁移**的问题（R3-02），本轮已修复并补 round-trip 测试。
2. **第 3 节 P2-R02 行**「重复值 → 422」为不实陈述——当时实现是静默去重（R3-05），本轮已改为 422 并有测试。
3. **第 2 节**「所有测试均不访问生产数据库」不完全成立——修复前 contract 测试的 lifespan 会调用模块级 engine 的 `init_db()`（本轮未观察到实际写入，但隔离不成立，R3-01），本轮已用 lifespan 替换 + 哨兵 + 环境变量前置三重隔离。
4. **第 3 节 P2-R07 行**「快捷筛选默认空 ratings=不限」当时未实现（默认是 14 项全选），本轮已实现（R3-03）。

## 5. 结转项（不在本轮交付）

- **T5 数据库迁移基线（Alembic）**：未实施。按计划依赖 T1（已完成），可在下一轮独立实施；`init_db/create_all` 与自动迁移策略需单独决策。
- **Factors.vue 组件测试**：计划 T3 建议覆盖复制模板/空评级保存/预览 null/计数渲染，本轮未写（Bonds/DataStatus/Settings 三页已补）。
- **API 层序列化测试**：评级逗号编码目前由 Bonds 页测试经功能桩覆盖页面级行为；axios 参数层（paramsSerializer）未单独测。
- **无外部数据源的手工四路径走查**：本轮以自动化测试等价覆盖，未做独立手工执行记录。
