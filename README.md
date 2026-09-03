# web

market-daily 项目的 Web 化改造 —— 独立的 FastAPI + React 应用。

将原本通过 GitHub Actions 定时发送静态邮件的 A 股日报系统，重构为支持动态筛选与交互式图表的 Web 应用。

## 设计文档

- [docs/web-refactor.md](docs/web-refactor.md) —— 架构 / 技术选型 / 数据库 / API 设计 / 分阶段计划

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 |
| 数据库 | SQLite(可平滑迁移 MySQL) |
| 数据获取 | httpx(纯函数,与调度解耦) |
| 调度 | APScheduler |
| 日志 | loguru |
| 前端 | React + Vite + ECharts(后续接入) |

## 项目结构

```
web/
├── backend/
│   ├── main.py                 # 应用工厂入口
│   ├── config.py               # 配置加载(pydantic-settings)
│   ├── models/                 # ORM 模型(宽表 + 时序表)
│   ├── services/fetchers/      # 数据抓取纯函数(解耦调度)
│   ├── api/routes/             # 路由(全量返回)
│   └── scheduler.py            # 定时任务编排
├── config/                     # 板块标的配置(yaml)
├── docs/                       # 设计文档
├── tests/                      # 测试
└── data/                       # SQLite / 日志(不纳入版本控制)
```

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env

uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

API 文档: `http://localhost:8000/api/docs`

## 当前阶段

Phase 1 —— 市场估值板块(抓取 → 落库 → API)。
