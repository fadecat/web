# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

market-daily 的 Web 化改造：独立的 FastAPI + Vue 3 应用，把原本 GitHub Actions 定时发送的静态 A 股邮件日报重构为可交互看板（市场估值 / 风格轮动 / 可转债筛选 / 数据管理）。架构设计文档见 `docs/web-refactor.md`，分阶段实施计划见 `docs/superpowers/plans/`。代码注释与文档均为中文，新代码保持中文 docstring 风格。

## 常用命令

Python 3.11+（开发环境 3.13）/ Node 20+ / pnpm 9+。所有命令从仓库根目录执行。

```powershell
# 一键启停本地环境(后端 8001 / 前端 5173, PID 与日志在 .runtime/)
.\scripts\app.ps1 start|status|restart|stop              # 全部
.\scripts\app.ps1 restart backend                        # 单独操作
.\scripts\app.ps1 build frontend                         # 前端构建

# ECS(阿里云, 经 SSH 别名 aliyun-ecs 调用远端 scripts/app.sh; 只做启停/构建, 不做 git pull/迁移)
.\scripts\app.ps1 status -Environment ecs
.\scripts\app.ps1 restart backend -Environment ecs

# 后端单独跑(开发)
uvicorn backend.main:app --port 8001                     # API 文档: /api/docs

# 前端 dev server(Vite 代理 /api 到后端)
cd frontend; pnpm dev
```

```bash
# 后端测试(内存库 + 隔离 lifespan, 不触真实数据源)
python -m pytest tests -q -p no:cacheprovider
python -m pytest tests/test_queries.py::test_xxx        # 单个测试

# 前端测试(node --test 跑 src/utils/*.test.mjs 纯函数测试 + vitest 组件测试)
cd frontend
pnpm test        # = test:node + test:unit
pnpm build
```

## 架构

分层单向依赖：**调度层(APScheduler，决定「何时」) → fetchers(纯函数，决定「如何抓」) → 存储(SQLite + SQLAlchemy 2.0) → Web 层(FastAPI 读库出 JSON) → 前端(Vue 3 + Element Plus + ECharts 画图)**。

- **抓取与调度彻底解耦**：`backend/services/fetchers/` 是从旧 market-daily 项目提取重写的干净函数（入参=标的，出参=结构化数据，无落盘/发信副作用）。同一套函数既被定时任务调，也被 Web 按需调。
- **任务单一事实源**：`backend/tasks/registry.py` 的 `DAILY_JOBS` 同时供 `scheduler.py`（定时触发）和 `data_status` 路由（手动触发）使用——定时跑的和手动跑的一定是同一个函数。`EVERYDAY_JOB_IDS` 之外的日频任务由任务内交易日判断兜底（只在交易日跑）。
- **单进程任务运行器**：`backend/services/run_logger.py` 用进程内 `_running` 集合做手动/定时共享互斥锁（`reserve_job`/`release_job`），执行状态持久化到 `task_run_log` 表；启动时 `recover_interrupted_runs()` 把遗留 running 标记为 interrupted。**只部署一个 scheduler 进程**，多进程需先引入外部租约。
- **数据时效分类**（决定处理方式）：日频快照（收盘后定时抓取落库，全天只读）/ 盘中实时（请求集思录拉全量 → 后端过滤，如 `cb_screen.py` 的 `source='live'`）/ 事件驱动。
- **API 全量返回**：接口不做服务端分页，筛选/排序交给前端（数据量小：估值一天几条、转债几百条）。后端只输出结构化 JSON，**不装 matplotlib**，图表全部由前端 ECharts 绘制。
- **集思录认证**：`backend/services/jisilu.py` 统一处理——AES-ECB 加密登录、cookie 落盘 `data/state/jisilu_session.json` 复用会话。**日登录次数受限**，新增抓取必须走既有会话复用，不要每次都登录。
- **数据模型**：快照用宽表（对齐源数据字段），历史序列单独建时序表，主键 `(date, code)`，历史全量保存。模型在 `backend/models/`。
- **前端**：`frontend/src/api/index.js` 是全部后端调用的封装层；可测试的纯逻辑放 `src/utils/*.mjs`（配 `*.test.mjs`），页面在 `src/pages/`，图表组件在 `src/components/`。路由 `src/router/index.js`。
- **生产形态**：`frontend/dist` 存在时由 FastAPI 静态托管（`main.py` 的 SPA 回退带路径穿越防护，勿削弱该 resolve 检查）。ECS 用 systemd 跑源码，不用 docker。

## 测试约束（违反即测试失败）

`tests/conftest.py` 在 import backend 之前强制隔离，任何测试：

- 禁止访问外部网络（socket 层拦截，仅放行 localhost）——触 jisilu.cn / SMTP 直接 AssertionError，需要外部数据必须显式 mock。
- 禁止写真实 `data/web.db`、禁止触发真实 `init_db`/`start_scheduler`（哨兵兜底）。路由测试用 `contract_client` fixture（隔离 lifespan + 线程安全内存库 + 请求级 Session）。
- 测试产物一律写 `.test-artifacts/`（gitignore），不进 `data/`；目录清理失败即测试失败（fail-closed）。

## 数据库迁移

启动仍用 `init_db()`/`create_all`（过渡状态），Alembic 已建初始版本。所有 alembic 命令**必须显式传 `-x database_url=`**（缺省即报错）：

```bash
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/new.db upgrade head
```

已有库接管走 `scripts/backup_db.py` → `scripts/adopt_db_copy.py` 单一编排入口（verify→compare→stamp→upgrade→冒烟），完整流程见 `docs/database-migration-runbook.md` §2.1。`scripts/` 下还有只读检查（`sqlite_readonly.py`）与恢复验证（`verify_db_restore.py`）等 CLI，均用 `python -m scripts.<name>` 调用。

## 注意

- `data/`（SQLite、日志、jisilu 会话）不纳入版本控制；配置化的标的清单在 `config/*.yaml`。
- 运行时状态（PID/进程指纹/日志）在 `.runtime/`，由 `scripts/app.ps1` 管理，不要手工清理。
- Windows 上 PowerShell 执行策略被拦时：`pwsh -ExecutionPolicy Bypass -File .\scripts\app.ps1 status`。返回码约定：0=成功，非 0=停止/健康检查失败/参数不支持。
