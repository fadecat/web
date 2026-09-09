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

## 测试

```bash
# 后端(内存库, 不触真实数据源)
python -m pytest tests -q -p no:cacheprovider

# 前端(node 内置测试 + vitest 组件测试)
cd frontend
pnpm test          # = test:node + test:unit
pnpm build
```

测试约束: 任何测试访问真实 jisilu.cn / SMTP / 写入 data/web.db 即视为失败。

## 当前阶段

Phase 2 —— 接口合同与质量基线(筛选/评级/同步/设置的服务端合同 + 前后端测试)。
