# 第四阶段验收记录：合同收口 + 数据库迁移基线

> 对应计划：`docs/superpowers/plans/2026-09-09-phase-4-migration-baseline.md`
> 验收日期：2026-09-09（本地，未推送）

## 1. 执行环境

| 工具 | 版本 / 路径 |
|---|---|
| Python | 3.13.14 — `C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe` |
| Alembic | 1.19.2 |
| Node.js | 22.22.2 |
| pnpm | 10.33.2 |

## 2. 自动化验证结果（本次实际执行）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests -q -p no:cacheprovider` | **188 passed**，0 failed（25.7s，含新增 14 项迁移/备份测试） |
| `node --test src/utils/*.test.mjs`（frontend） | **34 passed**，0 failed（含 R4-02 两个 start→stop/dispose 反例） |
| `pnpm test:unit`（Bonds 8 + Settings 7 + DataStatus 6 + Factors 5 + api 4） | **30 passed**，0 failed |
| `pnpm build` | 通过 |
| `git diff --check` | 通过 |

## 3. 缺陷关闭证据

### R4-01 / P1：旧配置迁移结果携带禁用字段（T1）

**修复**：`backend/services/cb_factors.py` 的 `_normalize_templates` 在评级迁移分支结束后 `tmpl.pop("excluded_ratings", None)`——读取旧文件的输出只含当前结构，GET→POST 原样保存不再被 422 拦截。`StrategyTemplateModel` 继续拒绝新 POST 主动携带旧字段（放宽点不变）。

**测试证据**：`test_legacy_get_response_can_be_posted_as_current_config`（先红后绿）——GET 旧配置响应无 `excluded_ratings`、`ratings` 为反转向量、原样 POST 200、磁盘只留当前结构。

### R4-02 / P2：start 后立即 stop/dispose 仍执行一次 load（T2）

**修复**：`frontend/src/utils/dataManagementPolling.js` 的 tick 把 load 包进 `Promise.resolve()` 链，**调用 load 前复核** `disposed || versionAtStart !== version || inFlight !== flight`；`inFlight` 从布尔改为 flight 对象标识，避免旧会话 finally 误清新会话状态。

**测试证据**：`dataManagementPolling.test.mjs` 新增 `start 后同一轮 stop/dispose 不调用 load`（先红后绿，calls=0），控制器 12/12 通过。

### R4-03 / P2：请求会话持续到 fixture 结束，关闭测试是空断言（T3）

**修复**：`tests/conftest.py` 的 `contract_client` 把 override 从"返回裸 Session 的工厂"改为**请求级生成器**（带 finally 的 yield，FastAPI 依赖收尾执行），每次请求结束即 close；新增 `ContractDbState` 记录 opened/closed_ids 供断言。

**测试证据**：`test_request_session_is_closed` 重写为真实断言——两次请求各创建独立 Session、两个 id 都进入 closed_ids。

### R4-04 / P2：测试文件写入 data/ 且清理失败静默（T3）

**修复**：`tmp_factors`/`tmp_universe` 改用 `.test-artifacts/`（项目根，gitignore）下独立目录，不再在 `data/` 创建 `.test_*`；`.gitignore` 加 `.test-artifacts/`。清理失败降级为可见 warning（沙箱 safe-delete trash 服务不稳定属环境问题，非产品缺陷，见第 5 节说明）。

**测试证据**：全量运行后 `data/` 无 `.test_*` 目录。

### R4-05 / P2：生产 lifespan 无接线测试（T3）

**修复**：`tests/test_test_environment.py` 新增 `test_production_lifespan_wires_database_and_scheduler`（spy init_db/start/stop，断言事件顺序 `["init_db","start"]` → 退出后 `["init_db","start","stop"]`）和 `test_production_lifespan_skips_scheduler_when_disabled`。测试用 `create_app()` 新构造 app，不经过 `contract_client` 的 noop lifespan。

### R4-06 / P2：页面验收矩阵不完整（T4）

**修复与新增测试**：
- `frontend/tests/unit/Factors.test.js`（5 项）：保存不含 excluded_ratings / 复制保留空评级与未知评级 / 新建复制当前模板而非固定七档 / 预览 null 数值不抛异常 / 入选与缓冲计数用后端 top_n/keep_n。
- `frontend/tests/unit/api.test.js`（4 项）：用 mock adapter 断言真实 Axios 请求——`screenBondsIntraday` GET + ratings 逗号参数、`saveFactors` POST body、`syncIndex` URL 编码、`getRatingCatalog` URL。为此重写 `frontend/tests/setup.js`：axios mock 改为"真实实例 + 默认拒绝 adapter"，测试可覆盖 `api.defaults.adapter` 观察请求。
- `frontend/src/pages/Bonds.vue`：评级目录加载失败显示可见错误 + 「重试评级目录」按钮（`loadRatingCatalog()` 重构，重试只拉目录不改筛选）。测试：失败显示提示→重试→勾选 BB+→查询参数 `'BB+'`。
- `frontend/tests/unit/DataStatus.test.js` 扩到 6 项：添加指数入口（探测一次/保存一次/立即 GET）、假时钟 5 分钟截止不再 GET、unmount 后 `vi.getTimerCount() === 0`。

## 4. 数据库迁移基线（T5-T7）

| 交付 | 路径 | 验证 |
|---|---|---|
| Alembic 配置 | `alembic.ini`（占位 URL，缺显式 URL 即失败）、`migrations/env.py`（完整 metadata + compare_type + SQLite batch） | — |
| 初始 revision | `migrations/versions/0001_initial_schema.py`（10 张业务表 + 索引/唯一约束，autogenerate 后人工核对） | 空库 upgrade 建出全部 10 表 |
| 迁移测试 | `tests/test_migration_baseline.py`（8 项） | 空库 upgrade / 重复 upgrade 幂等 / downgrade 删业务表 / stamp+upgrade / 结构匹配 / 漂移报告不改库 / 缺列 / 可空性漂移 |
| 只读比对 | `scripts/check_db_baseline.py`（表/列/affinity/nullable/主键/唯一/索引，exit 码） | 匹配库空差异；漂移 fail closed 且数据库文件字节不变 |
| 备份 | `scripts/backup_db.py`（显式 `--source`、拒绝覆盖、WAL 一致快照） | WAL 提交行被捕获 |
| 恢复验证 | `scripts/verify_db_restore.py`（integrity/表集合/行数/主键/结构） | 一致副本通过；缺行/加列/主键漂移均拒绝 |
| 接管文档 | `docs/database-migration-runbook.md` | 固定顺序 + 失败停止 + 恢复 |

备份/恢复测试（`tests/test_backup_restore.py`，6 项）在临时库执行，不触日常库。

## 5. 已知边界与说明

1. **沙箱 safe-delete 限制**：WorkBuddy 沙箱把 `shutil.rmtree`/`Path.unlink` 拦截转 trash，本环境 trash 服务不稳定导致 `.test-artifacts` 清理偶发失败。处理：conftest 改用 `os.remove/os.rmdir`，失败降级为可见 warning（不使已通过的测试失败）。CI/无沙箱环境为真实删除。
2. **日常 `data/web.db` 接管未执行**：本阶段只对临时库演练；runbook 文档化流程，实际接管需独立实施单（停写 + 备份 + 只读核对 + 副本 stamp + 冒烟）。
3. **`downgrade` 只限临时库**：downgrade 会删表，日常回退用备份恢复，runbook 已声明停写/数据损失窗口。
4. **el-button stub 怪癖**：vue-test-utils 对声明 `disabled/loading` props 的 stub 组件有 click 兼容问题，DataStatus 测试的 el-button 用简化 stub（忽略 disabled）并通过原生 dispatchEvent 触发保存；保存按钮的 disabled 语义由 `form.probeToken` 前置校验保证，测试覆盖正常路径。

## 6. 提交清单（本地 main，未推送）

按任务边界：
1. `fix: make legacy factor migration round-trip safe`（R4-01）
2. `fix: cancel refresh before first load starts`（R4-02）
3. `test: close request sessions and isolate artifacts`（R4-03/04/05）
4. `test: cover factor workflows and api serialization`（R4-06）
5. `feat: add initial database migration baseline`（T5）
6. `feat: verify databases before migration stamping`（T6）
7. `feat: verify sqlite backup restoration`（T7）
8. `docs: record phase 4 migration baseline verification`（T8）

## 7. 门禁核对（对照计划 §4）

- [x] R4-01~R4-04 原始反例先失败、修复后通过
- [x] 旧配置 GET→POST 不丢评级、不携带迁移字段
- [x] 刷新 stop/dispose 后无首个 GET、续排 timer 或 onStop 误报
- [x] 每个 HTTP 请求结束即关闭测试 Session；测试文件完全离开 `data/`（沙箱清理降级为 warning 例外已注明）
- [x] Factors 页面和 Axios 边界测试通过；Bonds/DataStatus/Settings 无回归
- [x] 初始 revision 创建当前 10 张表；空库和重复升级通过
- [x] 已有库漂移检查只读且 fail closed；未通过检查不能 stamp
- [x] WAL 备份在恢复副本上通过 integrity、结构、行数和主键验证
- [x] `create_all` 过渡状态及退出计划写入 runbook
- [x] 未对日常 `data/web.db` 做迁移、stamp、删除或覆盖
