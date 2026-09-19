# 组合实验室 · 交接索引（2026-09-19）

> **本文只是一份地址清单，不重述研究内容。** 要看结论去 `portfolio-lab-index.md`（唯一入口）。
> 仓库根：`C:\Users\han\桌面\code\web`

---

## 一、上午~下午产出的专题文档（全部在 `docs/`）

| 文档 | 末次修改 | 作用 | 何时读 |
|---|---|---|---|
| `docs/portfolio-lab-index.md` | 15:42 | **唯一入口**：口径速查 16 条 + **待办清单（§四为权威）** + 变更记录 + 文档自检 | 先读这份 |
| `docs/funddb-portfolio-algorithm.md` | 15:08 | **算法原理**：破解过程、口径推导、6 步定义、5 个陷阱 | 怀疑口径时 |
| `docs/portfolio-lab-verification.md` | 14:41 | **验收报告**：三方核对 24 点实测、残差分析、验收断言 | 改引擎后回归 |
| `docs/portfolio-lab-page-spec.md` | 15:08 | **页面规格**：§一~八 = 组合详情页 L2；**§九 = 组合列表页 L1** | 做前端时 |
| `docs/portfolio-lab-flow.md` | 14:59 | **端到端流程**：五段流程、数据结构、统一序列层、**端点全表（P0~P3 分期）** | 建表/定接口（⚠ §五 已作废） |
| `docs/portfolio-lab-multi-portfolio.md` | 15:10 | **多组合建模归属**：为何「再平衡/基准/区间」归 `backtest_run` | 定表结构前 |
| `docs/portfolio-lab-add-asset-ux.md` | 15:09 | **加标的交互**：两态入口、5 步流程、多候选、起点变化提示、8 种异常 | 动 P0 时 |
| `docs/portfolio-lab-ambiguity-audit.md` | 15:09 | **歧义审计**：明确 28 / 仍有歧义 7 / 待核实 1；红线项已全关闭 | 开工前第一份 |
| `docs/portfolio-lab-arch-review.md` | 15:42 | **架构与产品 Review**：E1 ETF 口径已裁决为「产品差异」+ 可砍复杂度 + 5 处代码层建议 | 开工前必读 |
| `docs/portfolio-lab-data-maintenance.md` | 19:08 | **数据维护设计**：三处结构性障碍、两类数据形态、两个 job、三种触发、幂等/修订/新鲜度 | **动 P1 时**（⚠ 本文 §十⑦ 与 §三 有冲突，见下） |
| `docs/portfolio-asset-universe-feasibility.md` | 13:36 | **标的接入可行性**：三类资产数据可得性、`research_security` 现状、按需抓取设计 | 加标的时 |
| `docs/portfolio-backtest-engine-design.md` | 13:37 | 早期引擎设计。**文首有 ⚠ 更新块、正文有 ❌ 标记**，仅部分仍有效 | 查历史决策 |

**数据源调研（也是今天上午做的）**

| 文档 | 末次修改 | 作用 |
|---|---|---|
| `docs/data-source-research.md` | 12:07 | 股票/ETF/场外基金三类数据源选型调研 |
| `docs/data-source-ecs-probe-report.md` | 12:07 | **ECS 实测报告**：腾讯 `fqkline`、蛋卷 `djapi` 的接口契约与硬事实 |

---

## 二、可执行工具（`scripts/`）

| 脚本 | 末次修改 | 作用 |
|---|---|---|
| `scripts/verify_against_funddb.py` | 14:13 | **回归工具**：`--only win\|rebal\|corr\|weight\|added\|day`，当前 **6 类全 PASS** |
| `scripts/measure_exec_lag.py` | 14:36 | 成交时点敏感性（当日净值 vs T+1）—— D2 裁决的量化依据 |
| `scripts/probe_danjuan.py` | 12:05 | 蛋卷接口结构验证（克制版，>5 标的拒绝执行） |
| `scripts/probe_data_source.py` | 12:44 | 数据源探针（结构验证） |

---

## 三、最详细的原始记录

| 路径 | 说明 |
|---|---|
| `.workbuddy/memory/2026-09-19.md` | **今天全部过程的逐条记录（117 KB）**：算法破解、六类验收、用户每轮裁决、P0/P1 实施与踩坑。**想知道"为什么这么定"就看这里** |
| `.workbuddy/memory/MEMORY.md` | 项目长期记忆（已精简重写）：铁律、数据环境、数据源硬事实、组合实验室定稿、既有基础设施、ECS |
| `.workbuddy/memory/2026-09-18.md` | 前一天（研究回放模块）的记录 |

---

## 四、代码产出（5 个 commit，均未 push）

```
e162da5  fix+docs: 场外基金新鲜度补 trading_day_offset=1、P0/P1 验收与文档对齐（第二会话接手后）
6fd2350  P1 场外基金链路（fund_nav_daily + fund_nav.py + fund_tasks.py + 注册闭环）
9e16f92  P0-3 前端标的库页 + 添加标的组件
bab81fe  P0-2 标的注册与单标的同步 API（4 端点）
280a85b  P0-1 后端基础层（FUND/OTC + 4 同步状态列 + alembic 0005）
```

关键新增文件：

- `backend/services/fund_nav.py` — 蛋卷净值解析 + 分红再投链式复权 + 幂等 upsert
- `backend/tasks/fund_tasks.py` — `run_fund_nav_sync`（23:10，进 `EVERYDAY_JOB_IDS`）
- `backend/services/portfolio_assets.py` — 代码归一化 / probe / 注册 / `sync_one`
- `backend/api/routes/portfolio.py` — `/api/portfolio/assets/*`
- `migrations/versions/0005_*.py`、`0006_add_fund_nav_daily.py`
- `frontend/src/components/portfolio/AddAssetDialog.vue`、`pages/PortfolioAssets.vue`

---

## 五、接手前必须知道的 5 条

1. **文档有一处自相矛盾未裁决**：`data-maintenance.md` §十 ⑦ 要求 `DanjuanProvider` 实现 bar 形状的 `get_daily_bars`；§三 又论证基金是**单序列**、不该塞 OHLC。实现按 §三 走了独立模块，`provider_factory("danjuan")` **未注册**。
2. **本地 `data/web.db` 是老快照**（无 research 表、`alembic_version` 为空）→ 要真跑需先建新库 `alembic upgrade head` 再同步。
3. **测试必须用 conda python**：`C:\Users\han\miniconda3\python.exe`（3.12.3，已补装 alembic）。用项目 `.venv` 会出现一批 teardown 报错（宿主安全删除组件拦截）。
4. **迁移类测试必须分文件跑**，混跑会出现 21 个 `windows-sandbox-recycle-bin-unavailable` teardown 报错（环境问题，非代码）。
5. **前端 `AddAssetDialog.vue` 还留着已失效的 501 分支**（后端 FUND 已打通，不再返回 501），无害但该清。

---

## 六、遗留待办（**接手者从这里开始**）

| # | 事项 | 出处 | 状态 |
|---|---|---|---|
| 1 | `AddAssetDialog.vue` 清理已失效的 501 分支（后端 FUND 已打通，不再返回 501） | §五-5 | ⬜ |
| 2 | `provider_factory("danjuan")` **未注册** —— `data-maintenance.md` §十⑦ 要求 `DanjuanProvider` 实现 bar 形状 `get_daily_bars`，§三 又论证基金是单序列。**冲突未裁决**，实现按 §三 走独立模块 | §五-1 | ⬜ **需拍板** |
| 3 | `catalog()` 的 source 硬编码（`data_catalog.py:47` 默认值 `akshare`，实际源是 `tencent`） | data-maintenance §十一-4 | ⬜ 建议顺手修 |
| 4 | **P2 统一序列层 + 组合定义**（下一个阶段） | flow §七 | ⬜ 未开始 |

---

## 七、第二会话接手记录（19:05 起）

- 确认前任 agent **已完成并提交 P1**（`6fd2350`），工作区干净 → 任务由"补完 P1"转为"验收 + 收尾"
- **修一处真实缺陷**：场外基金 `Policy` 补 `trading_day_offset=1`（原缺失会让"应到日期"恒为当日 → 数据管理页**每个交易日误报 `lagging` 红色告警**；与既有 `pe`/`dy` 的 `offset=1` 先例一致）
- 新增 `scripts/verify_p1_fund_nav.py`（P1 端到端验收，单标的 1 请求）。**实测 100018：5545 行 / 2003-12-02 ~ 2026-09-18 / 成立首日 `percentage` 为 None 已兜底 / 链式复权 `unit_nav` 1.3684 → `adj_nav` 4.856**，5 项校验全 PASS
- 测试：`data_status`+`fund_tasks` **35 passed**、`portfolio_assets`+`fund_tasks` **48 passed**、迁移类 **21 passed**（teardown 报错是 safe-delete shim 在沙箱下无法用回收站的**环境噪音**，测试本身全过，非代码问题）
- 文档对齐：index 文首加"两套 P0/P1 编号陷阱"提示并标注 P0/P1 完成；data-maintenance 迁移编号 0005→0006（0005 已被 P0-1 占用）并补 4 项待拍板的落地情况；flow 实施阶段表加状态列
- 提交 `e162da5`，工作区干净
