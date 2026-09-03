# market-daily Web 版 —— 初始技术方案（v0.1）

> 本文档是**全新独立 Web 项目**的设计方案：把 market-daily 的「静态邮件日报」能力，重构为一个**独立仓库、全新架构**的「可交互 Web 看板」。
> 状态：**讨论中 / 初始版本**，后续随决策迭代。
> 记录日期：2026-09-03
> 定位说明：本文档属于**新项目**（独立仓库），旧的 `market-daily` 保持不动，仅作为「抓取/策略逻辑的参考实现」。

---

## 1. 项目背景与改造动机

- **现状**：market-daily 是 A 股每日简报，5 个板块各生成一封 SMTP 邮件日报，运行在 GitHub Actions 上（**该仓库保持不动**）。
- **痛点**：邮件是**静态快照**，读者只能看「当天这一封」，无法按日期回溯、无法按条件筛选、无法交互式探索。
- **改造目标**：新建一个**独立仓库的 Web 项目**，把「每天一封静态邮件」变成「可查询、可筛选、可回溯的 Web 看板」。
- **核心原则**：**新起炉灶**。不 import 旧工程代码，只从旧项目「提取」抓取/策略逻辑并**重写为干净函数**；旧项目的邮件日报形态、JSON+git 持久化、渲染发信等一律不继承。
- **改造重心**：**前端 Web 化**（前端技术栈、展示风格后续再定）。

---

## 2. 现有项目事实基线（勘探结论）

### 2.1 规模

| 维度 | 数值 |
|------|------|
| Python 文件 | 85 个 |
| 代码量 | 约 17,838 行 |
| 测试文件 | 47 个 |
| 数据持久化 | JSON 文件 + git commit（无数据库） |

### 2.2 模块划分（按代码量）

| 模块 | 代码量 | 内容 |
|------|--------|------|
| valuation | 7540 行 | 市场估值（**首个改造对象**） |
| convertible | 3958 行 | 转债行情 |
| rotation | 1107 行 | 资产轮动 |
| dividend_observation | 768 行 | 红利观察研究 |
| commodity | 574 行 | 商品极值 |
| coal | 351 行 | 煤炭日报（CCTDA 图片转发，**暂不做**） |
| common | 1249 行 | 公共基础（env/jisilu/email/storage/alerts/fonts/whitelist） |

### 2.3 现有架构特征（对重构有利）

1. **已有清晰分层**：数据获取层（fetch）、渲染层（render）、存储层（storage）已分离。
2. **已有预览能力**：各板块 `run_preview()` 已能把图 base64 内嵌、输出纯 HTML，是 Web 化的天然入口。
3. **凭据管理已规范**：`env.py` 统一读 `.env.local` / 环境变量，Web 化后直接读服务器环境变量。
4. **集思录会话复用**：cookie 落盘 `data/state/jisilu_session.json`，避免日登录额度耗尽。

### 2.4 存储层现状（storage.py 三类存储）

| 类型 | 路径 | 用途 |
|------|------|------|
| state | `data/state/*.json` | 运行状态（持仓/净值/去重） |
| archive | `data/archive/<dataset>/*.json` | 历史归档（按 key 合并去重） |
| snapshot | 任意路径 | 带 content_hash 的快照 |

---

## 3. 技术选型（已敲定）

### 3.1 技术栈

| 维度 | 选型 | 依据 |
|------|------|------|
| Web 框架 | **FastAPI** + Uvicorn | 复用 v2_cb_rotation 已验证方案 |
| ORM / DB | **SQLAlchemy 2.0 + SQLite** | 轻量，1-2 人够用，复用 v2 方案 |
| 数据校验 | **Pydantic v2** | FastAPI 原生 |
| 调度 | **APScheduler** | 替代 GitHub Actions cron |
| 日志 | **loguru** | 复用 v2 方案 |
| 前端 | React + Vite + ECharts（技术栈待定，暂不介入） | 后端只出 JSON，图表由前端 ECharts 绘制 |
| 数据获取 | 复用 market-daily 现有 fetch 模块（需解耦） | 核心决策 |

### 3.2 关键决策：数据获取与调度解耦

> 把「怎么抓数据」（可复用的抓取方法）与「什么时候抓数据」（调度层决定）彻底分离。
> 同一套抓取方法，既能被每日定时任务调，也能被盘中手动/Web 按需调。

---

## 4. 数据时效性分类（核心设计）

按**数据更新节律**分类，而非按板块一刀切：

| 类型 | 数据 | 更新节律 | 处理策略 |
|------|------|---------|---------|
| **日频快照** | 指数估值(PE/PB/股息率)、股债收益差、果仁行业、风格轮动、商品极值、转债等权指数、巨潮财报 | 收盘后更新 | **收盘后定时抓取落库，全天只读** |
| **盘中实时** | 转债价格 + 正股价格（集思录拉取时携带） | 盘中随行情 | **盘中请求集思录拉全量 → 二次过滤** |
| **事件驱动** | 董秘互动、转债日历、财报 | 不定期 | 按需拉取 |

### 4.1 盘中实时筛选模式（已实践，复用 v2）

v2_cb_rotation 已验证的模式，直接复用：

```
盘中请求集思录 → fetch_all_bonds() 拉全量（含实时价）
             → 后端 filter_cb() 按排除规则过滤
             → three_low_strategy() 打分排序
             → 返回前端
```

（接口耗时约 5-15 秒，注释已明确）

---

## 5. 架构设计（目标态）

```
┌─────────────────────────────────────────────┐
│           调度层（APScheduler）              │
│   收盘定时  │  盘中触发  │  Web 按需         │
└──────────────┬──────────────────────────────┘
               │ 调用
┌──────────────▼──────────────────────────────┐
│        数据获取层（复用现有 fetch）           │
│   纯函数化：入参=标的，出参=结构化数据        │
└──────────────┬──────────────────────────────┘
               │ 写入
┌──────────────▼──────────────────────────────┐
│           存储层（SQLite + SQLAlchemy）      │
│   落库 + 历史留存 + 可查询                   │
└──────────────┬──────────────────────────────┘
               │ 读取
┌──────────────▼──────────────────────────────┐
│            Web 层（FastAPI）                 │
│   读库 + 筛选 + 返回 JSON 给前端             │
└─────────────────────────────────────────────┘
```

### 5.1 应用形态

- **单应用多板块**：一个 FastAPI 应用，5 个板块作为 5 个 router（不是按板块拆服务）。
- 符合「先从市场估值做起，逐步加板块」的节奏。

---

## 6. 分阶段实施计划

| 阶段 | 内容 | 状态 |
|------|------|------|
| **Phase 1** | 市场估值板块：收盘后定时抓取 → 落库 → Web 展示 | 待启动 |
| Phase 2 | 转债板块：盘中实时筛选模式（复用 v2） | 待定 |
| Phase 3 | 其余板块（轮动/商品）逐板块接入 | 待定 |
| Phase 4 | 前端体验升级（图表交互、指标切换等） | 待定 |

> **暂不做**：煤炭日报（coal）—— 本质是 CCTDA 图片转发业务，无筛选/交互需求，不纳入 Web 化范围。

---

## 7. 风险与注意事项

1. **抓取/渲染耦合**：旧项目的 fetch 函数部分耦合了「落盘/生成图/发信」副作用。新项目需「提取重写」——把抓取逻辑改写成只返回数据的干净函数，剥离所有副作用。旧项目测试覆盖高（47 文件），可作重写时的行为参照。
2. **集思录登录额度**：日登录次数受限，迁移到 ECS 后定时任务 + Web 实时请求共用账号，需延续会话复用逻辑。
3. **图表策略（待定）**：现有是 matplotlib 服务端出图内嵌；Web 化时可「继续服务端出图」或「前端数据驱动画图」，需在 Phase 1 前敲定。
4. **盘中价格频次（待定）**：转债盘中快照的刷新频率（秒级/分钟级/日级）尚未最终敲定，不影响 Phase 1 推进。

---

## 8. 工具函数清单（可复用抓取/策略能力）

> 按板块梳理，Web 化时直接 import 复用，无需重写。

### 8.1 公共基础层（common/）

| 函数 | 能力 |
|------|------|
| `jisilu.get_cookie` / `make_session` | 集思录账密登录 + cookie 会话复用（落盘 state） |
| `jisilu.fetch_realtime_lists` | 拉股票ETF/黄金ETF/QDII 实时列表（含价格） |
| `email.*` | HTML 渲染辅助 + SMTP 发送（Web 化后渲染层可复用） |
| `storage.*` | 三类存储：state / archive / snapshot |

### 8.2 市场估值（valuation，Phase 1）

| 函数 | 能力 |
|------|------|
| `fetch.fetch_index_detail` | 指数 PE/PB/股息率（易方达接口） |
| `fetch.fetch_index_dividend_yield` | 指数股息率（独立 JSON） |
| `fetch.fetch_index_eod_price_data` | 指数日线 EOD |
| `fetch.fetch_index_valuation_percentile` | 指数估值分位 |
| `fetch.fetch_cn_10y_bond_yield` | 10Y 国债收益率（股债收益差） |
| `dividend.fetch.fetch_data` | 高股息数据 |
| `guorn.fetch_industry_valuation` | 果仁行业估值 |
| `run._fetch_valuation_items` | 估值核心聚合（需解耦落盘副作用） |

### 8.3 转债行情（convertible，Phase 2）

| 函数 | 能力 |
|------|------|
| `three_low.strategy.fetch_cb_list` / `fetch_redeem_list` | 转债列表 + 强赎列表（含盘中价） |
| `three_low.strategy.three_low_strategy` | 三低打分排序 |
| `screening.strategy.fetch_cb_data` / `filter_cb` | 低价债筛选 |
| `screening.archive.fetch_cb_detail_page` | 转债详情页（下修条款） |
| `irm.query.query_irm` | 董秘互动 |

### 8.4 资产轮动（rotation，Phase 3）

| 函数 | 能力 |
|------|------|
| `etf_data.fetch_etf_daily` / `fetch_close_series` | ETF 日线 / 收盘序列 |
| `strategy.fetch_etf_list_realtime` | ETF 实时列表 |
| `strategy.run_strategy` / `compute_drawdown_stats` | 动量轮动 + 回撤统计 |

### 8.5 商品极值（commodity，Phase 3）

| 函数 | 能力 |
|------|------|
| `core.fetch_history` | 75 品种期货历史 |
| `core.compute_window_percentiles` | 多周期分位数 |
| `core.evaluate_symbol` / `run_scan` | 极值评估 + 扫描 |

---

## 9. Web 目标项目结构（独立仓库，全新架构）

> 新起炉灶：不 import 旧代码，只从旧项目「提取」抓取/策略逻辑重写。目录全新设计。

```
market-daily-web/              # 独立新仓库
├── backend/                   # FastAPI 应用（唯一 Python 包）
│   ├── main.py                # create_app 工厂
│   ├── api/routes/            # valuation / convertible / rotation / commodity 各一 router
│   ├── services/              # 业务服务：抓取 + 落库
│   │   ├── fetchers/          # 从旧项目「提取重写」的抓取函数（干净、无副作用）
│   │   ├── strategies/        # 从旧项目「提取」的策略算法（纯函数）
│   │   └── ...
│   ├── models/                # SQLAlchemy ORM + Pydantic schema
│   ├── scheduler.py           # APScheduler 定时任务
│   └── config.py              # 环境变量加载
│
├── frontend/                  # React + Vite（技术栈待定）
│   ├── pages/                 # 按板块分页
│   ├── components/            # 表格·筛选器·图表
│   └── api/                   # axios 封装
│
├── config/                    # 板块配置（从旧项目复制 valuation.yaml 等标的清单）
├── data/                      # SQLite 数据库（app.db，运行期生成）
├── tests/                     # 新项目测试
├── requirements.txt
└── .env.example               # 凭据模板
```

**关键原则**：
- **不 import 旧工程**：`fetchers/`、`strategies/` 里的代码是从旧项目「提取 + 重写」的，去掉落盘/发信副作用，成为干净的函数
- 旧 `market-daily` 仓库保持不动，仅作参考实现
- 前端与后端通过 `/api` 解耦，前端 build 产物由 FastAPI 静态托管

---

## 10. 数据库 Schema 设计（Phase 1：市场估值板块）

> 已敲定的设计取向：**快照用宽表**（对齐易方达源数据）、**历史序列单独建时序表**、**全量保存**。

### 10.1 设计取向

| 决策 | 结论 | 理由 |
|------|------|------|
| 快照表结构 | 宽表 | 对齐易方达源数据的平铺字段，落库即字段映射 |
| 历史序列 | 单独时序表 | 结构天然是「日期 + 值」，不参与宽窄之争 |
| 历史保留 | 全量保存 | 时序数据是长期资产，支持分位/回测 |

### 10.2 表清单

| 表名 | 类型 | 用途 |
|------|------|------|
| `index_meta` | 静态维度表 | 指数元数据（代码/名称/类型/来源 URL） |
| `index_valuation_snapshot` | 每日快照（宽表） | 每指数每天的 PE/PB/PS/股息率/股债收益差 |
| `index_pe_history` | 时序表 | PE-TTM 历史（画分位图） |
| `index_pb_history` | 时序表 | PB-LF 历史 |
| `index_dividend_yield_history` | 时序表 | 股息率历史 |
| `index_eod_price` | 时序表 | 指数日线收盘价 |
| `bond_10y_history` | 时序表 | 10Y 国债收益率（板块级共享） |
| `fx_history` | 时序表 | 汇率（USDCNH）历史 |
| `style_rotation_history` | 时序表 | 风格轮动指数日线 |

### 10.3 核心表结构（细化）

**`index_valuation_snapshot`（每日快照，宽表）**

| 字段 | 类型 | 说明 |
|------|------|------|
| date | TEXT (YYYY-MM-DD) | 估值日期（主键之一） |
| index_code | TEXT | 指数代码（主键之一） |
| pe_ttm | REAL | PE-TTM 当前值 |
| pe_ttm_pct_3m / 6m / 1y / 2y / 3y / 5y / 10y / ty / bgn | REAL | PE 各档分位 |
| pb_lf | REAL | PB-LF 当前值 |
| pb_lf_pct_* | REAL | PB 各档分位 |
| ps_ttm | REAL | PS-TTM 当前值 |
| ps_ttm_pct_* | REAL | PS 各档分位 |
| dividend_yield | REAL | 股息率当前值 |
| dividend_yield_pct_1y / 3y / 5y / 10y | REAL | 股息率分位 |
| dividend_yield_avg_5y | REAL | 股息率 5 年均值 |
| equity_bond_spread | REAL | 股债收益差当前值 |
| equity_bond_spread_pct_1y / 3y / 5y / 10y | REAL | 股债收益差分位 |
| cn_10y_bond_yield | REAL | 10Y 国债收益率（板块级） |
| created_at | TEXT | 落库时间 |

> 主键：`(date, index_code)`，唯一约束去重。

**时序表通用结构**（以 `index_pe_history` 为例）

| 字段 | 类型 | 说明 |
|------|------|------|
| date | TEXT | 日期 |
| index_code | TEXT | 指数代码 |
| pe | REAL | PE-TTM 值 |

> 主键 `(date, index_code)`；其余时序表同构（`pb`、`yield`、`close`、`yield_pct` 等值列）。

### 10.4 与旧项目 archive 的关系

- 旧项目用 `data/archive/<dataset>/<code>.json` 存历史，新项目改为 SQLite 表，**一一对应**：
  - `index_valuation_percentile` → `index_valuation_snapshot` + `index_pe_history`/`index_pb_history`
  - `index_dividend_ratio` → `index_dividend_yield_history`
  - `index_eod` → `index_eod_price`
  - `bond_10y` → `bond_10y_history`
  - `fx` → `fx_history`
- 新项目首次启动可**回填**旧项目已有归档数据，保证历史连续（需写一个一次性导入脚本）。

---

## 11. API 接口设计（Phase 1：市场估值板块）

### 11.1 返回策略：全量返回

- **接口不做服务端分页/裁剪**，一次返回完整数据，筛选/排序交给前端。
- 数据量小（估值一天 8 指数、转债几百条），全量返回最简单且性能无压力。
- 边界：全量返回 = 「某日期的全部指数快照」或「某指数的完整历史序列」，而非无差别倒库。

### 11.2 图表策略：前端 ECharts

- **后端只输出结构化 JSON，不做任何绘图**。
- 旧项目的 matplotlib 出图逻辑（+中文字体+base64 内嵌）**不迁移**，后端 requirements 不装 matplotlib/Pillow。
- 图表由前端 ECharts 拿 JSON 绘制（前端暂时不介入，后续对接）。

### 11.3 部署方式：源码启动（暂不考虑 docker）

- 用 systemd 管理 uvicorn 进程，直接跑源码。
- 后续若有多实例/环境隔离需求，再评估 docker。

### 11.4 接口清单（初步）

| 方法 | 路径 | 返回 | 说明 |
|------|------|------|------|
| GET | `/api/valuation/snapshot?date=YYYY-MM-DD` | 该日期全部指数快照 | 全量，无分页 |
| GET | `/api/valuation/snapshot/latest` | 最新交易日快照 | 默认首页 |
| GET | `/api/valuation/history?index_code=xxx&metric=pe/pb/...` | 该指数完整历史序列 | 全量，供 ECharts |
| GET | `/api/valuation/indexes` | 指数元数据列表 | 供筛选下拉 |
| GET | `/api/valuation/dates` | 可用日期列表 | 供日期选择器 |

> 筛选（按指数/日期/股息率阈值等）由前端对全量数据二次过滤。

---

## 12. 待决策项（后续讨论）

- [ ] 前端技术栈 & 展示风格（后续专门聊，等后端数据接口做好后再对接）
- [ ] 转债盘中价格刷新频率
- [ ] API 接口细节打磨（参数、错误码、响应结构）
