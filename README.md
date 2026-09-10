# web

market-daily 项目的 Web 化改造 —— 独立的 FastAPI + Vue 3 应用。

将原本通过 GitHub Actions 定时发送静态邮件的 A 股日报系统，重构为支持动态筛选与交互式图表的多模块 Web 应用（市场估值 / 风格轮动 / 可转债筛选 / 数据管理）。

## 设计文档

- [docs/web-refactor.md](docs/web-refactor.md) —— 架构 / 技术选型 / 数据库 / API 设计 / 分阶段计划

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + Uvicorn + Pydantic 2 |
| ORM | SQLAlchemy 2.0 |
| 数据库 | SQLite(可平滑迁移 MySQL) |
| 数据获取 | httpx(纯函数,与调度解耦) |
| 调度 | APScheduler |
| 日志 | loguru |
| 前端 | Vue 3 + Vite + Element Plus + ECharts |

## 项目结构

```
web/
├── backend/
│   ├── main.py                 # 应用工厂入口
│   ├── config.py               # 配置加载(pydantic-settings)
│   ├── models/                 # ORM 模型(宽表 + 时序表)
│   ├── services/               # 业务服务(筛选/抓取/数据管理)
│   │   └── fetchers/           # 数据抓取纯函数(解耦调度)
│   ├── api/routes/             # 路由(全量返回)
│   └── scheduler.py            # 定时任务编排
├── frontend/                   # Vue 3 SPA(vite)
├── config/                     # 板块标的配置(yaml)
├── docs/                       # 设计文档
├── tests/                      # 后端测试(pytest)
└── data/                       # SQLite / 日志(不纳入版本控制)
```

## 快速开始

支持版本: Python 3.11+(开发环境 3.13) / Node 20+(开发环境 22) / pnpm 9+。

```bash
# 后端
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env

uvicorn backend.main:app --host 0.0.0.0 --port 8000   # 本地开发常用 --port 8001

# 前端
cd frontend
pnpm install
pnpm dev                          # vite dev server, 代理 /api 到后端
```

API 文档: `http://localhost:8000/api/docs`

## 应用启停

日常操作统一从仓库根目录执行 `scripts/app.ps1`。本地默认后端端口为
`8001`，前端端口为 `5173`，与 Vite 代理配置一致。

```powershell
# 整个本地开发环境
.\scripts\app.ps1 start
.\scripts\app.ps1 status
.\scripts\app.ps1 restart
.\scripts\app.ps1 stop

# 单独操作
.\scripts\app.ps1 restart backend
.\scripts\app.ps1 stop backend
.\scripts\app.ps1 restart frontend
.\scripts\app.ps1 build frontend
```

本地进程的PID、进程指纹和日志保存在 `.runtime/`。脚本优先使用
`APP_PYTHON` 指定的Python，其次查找 `.venv`、PATH和本机WorkBuddy运行时：

```powershell
$env:APP_PYTHON = 'D:\tools\Python313\python.exe'
.\scripts\app.ps1 restart backend
```

Windows也用同一入口管理ECS。它通过SSH别名 `aliyun-ecs` 调用ECS仓库中的
`scripts/app.sh`；该命令只做启停和构建，不执行 `git pull`、数据库迁移或数据同步。

```powershell
.\scripts\app.ps1 status -Environment ecs
.\scripts\app.ps1 restart backend -Environment ecs
.\scripts\app.ps1 build frontend -Environment ecs
.\scripts\app.ps1 restart frontend -Environment ecs
.\scripts\app.ps1 restart -Environment ecs
.\scripts\app.ps1 stop -Environment ecs
.\scripts\app.ps1 start -Environment ecs
```

ECS前端是由FastAPI托管的静态文件，没有独立前端进程。因此ECS上的
`restart frontend` 会先安装锁定依赖、在临时目录构建并替换 `frontend/dist`，
然后重启 `webapp` 并检查 `/api/health`；`start frontend` 和 `stop frontend`
会被拒绝。完整的 `restart` 与 `restart frontend` 使用同一安全流程。

默认值可覆盖：

```powershell
.\scripts\app.ps1 status -Environment ecs `
  -EcsHost aliyun-ecs -EcsRoot /opt/webapp -ServiceName webapp

# 只查看计划，不启动、停止或连接ECS
.\scripts\app.ps1 restart -DryRun
.\scripts\app.ps1 restart -Environment ecs -DryRun
```

若 PowerShell 执行策略阻止脚本，可用一次性进程范围调用：

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\app.ps1 status
```

返回码约定：操作成功为 `0`；服务停止、健康检查失败、参数不支持或PID归属
校验失败均为非零。ECS上的直接等价命令为
`bash scripts/app.sh <action> [all|backend|frontend]`。

## 测试

```bash
# 后端(内存库 + 隔离 lifespan, 不触真实数据源)
python -m pytest tests -q -p no:cacheprovider

# 前端(node 内置测试 + vitest 组件测试)
cd frontend
pnpm test          # = test:node + test:unit
pnpm build
```

测试约束: 任何测试访问真实 jisilu.cn / SMTP / 写入 data/web.db 即视为失败。
测试产物在 `.test-artifacts/`(gitignore), 不进入 `data/`。

## 数据库迁移

应用启动仍用 `init_db()/create_all`(过渡状态, 不自动迁移; `data/web.db` 尚未 stamp)。
Alembic 已建初始版本, 所有迁移命令**必须显式传 `-x database_url=`**(缺省即报错):

```bash
# 新空库
python -m alembic -x database_url=sqlite:///D:/absolute/path/to/new.db upgrade head

# 已有库接管(备份后用单一编排入口完成 verify→compare→stamp→upgrade→冒烟, 见 docs/database-migration-runbook.md §2.1):
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.backup_db --source D:/path/to/web.db --destination-dir D:/path/to/backups
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.adopt_db_copy --source D:/path/to/web.db --backup-copy D:/path/to/backups/web.<ts>.db --revision 0001

# 列出备份
& 'C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe' -m scripts.backup_db --list D:/path/to/backups
```

## 当前阶段

Phase 5 —— 迁移安全收口(R5-01~09 修复: 只读检查/内容级恢复验证/可执行接管链/严格测试清理)。
