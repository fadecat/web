# Phase 6 设计输入：共享配置与持久化同步 operation

> 本文件只列**已验证事实**与**待设计问题**，不预先宣称选定新表或队列。
> 来源：第五阶段验收（`docs/2026-09-09-phase-5-verification.md`）与代码核查。

## 1. 已验证事实

### TaskRunLog（现有任务记录表）
- 定义：`backend/models/data_status.py:17` 附近 `TaskRunLog`。
- 字段：id / job_id / started_at / finished_at / duration_sec / status
  (success|partial|failed) / summary / error。
- 用途：`backend/services/run_logger.py` 记录定时任务单次运行；前端「抓取记录」页展示。
- 保留策略未知：未见定期清理/保留窗口逻辑（需核查 `run_logger` 是否删除旧行）。

### 同步操作（当前无持久化）
- 数据管理页「同步」与「添加指数」触发后台任务，前端用 5 分钟墙钟自动刷新
  （`frontend/src/utils/dataManagementPolling.js`），**无 run_id 关联**，
  刷新停止 ≠ 任务完成。任务真实结果以 TaskRunLog 为准。
- 手动运行复用 `/data-status/run/{job_id}`（`backend/api/routes/data_status.py`）。

### 配置文件的并发写入模型
- `factors.json`（`backend/services/cb_factors.py` FACTORS_PATH）：`write_config`
  直接 json.dump，无文件锁；路由 POST /cb-list/factors 单写。
- `index_universe.json`（`backend/services/index_universe.py`）：已实现原子写
  （临时文件 + rename）与锁（`data/index_universe.json` 运行时持久化）。
- 两者都是**进程内单写者**（FastAPI 单进程 uvicorn + scheduler 线程池内任务不写配置）。

### 权限主体
- 当前 API 全部无鉴权（`backend/main.py` 无认证中间件）；管理接口
  （data-management / data-status run / settings）同前。公网暴露 8000 端口。
- Phase 2 记录"管理接口鉴权暂缓(用户定)"。

### SQLite 单写者限制
- `data/web.db` 用 WAL + busy_timeout=5000（`backend/models/database.py`）。
- APScheduler 线程池与 FastAPI 请求并发写同一库，依赖 WAL 的读写并发。

### 迁移与回滚门禁（第五阶段已建立）
- Alembic 初始版本 0001（10 张业务表）；已有库接管必须：备份 → 只读核对 →
  副本 stamp → upgrade（runbook 可执行顺序，`tests/test_database_adoption.py` 证明）。
- `check_db_baseline` / `verify_db_restore` / `backup_db` 均为 fail-closed。
- 日常 `data/web.db` 尚未 stamp；应用启动仍用 `init_db()/create_all`。

## 2. 待设计问题（Phase 6 需回答）

1. **TaskRunLog 复用边界**：现有表字段能否承载"同步操作"语义？
   run_id/operation 标识应如何挂接（新列 vs 新表 vs 复用 job_id+时间）？
2. **operation/run 状态机**：初始抓取(首次添加)与常规同步(已有指数)是否同态？
   running/failed/succeeded/partial 的终止判定由谁给出（任务自身回写？）？
3. **前端与任务结果的关联**：5 分钟自动刷新是否替换为 run_id 轮询？
   刷新停止与"本次任务成功"的展示边界如何保证不误导？
4. **配置入库 vs JSON**：factors/index universe 是否搬入数据库？并发写入
   与备份恢复（WAL 一致性）如何保证？JSON 的原子写是否已足够？
5. **权限主体**：管理 API 鉴权方案（token/白名单）与敏感配置（集思录账号、
   SMTP 授权码）的加密存储边界。
6. **SQLite 写放大**：任务日志高频写入对 WAL 的影响，是否需要保留窗口/归档。

## 3. 明确不做（Phase 6 范围外）

- 不新增队列服务；不引入独立消息中间件。
- 不重做首页/不做全量 TypeScript 迁移。
- 日常库迁移按 runbook 独立实施，不随本阶段启动。
