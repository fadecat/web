# 组合实验室：端到端结构与流程

- 日期：2026-09-19
- 需求：**用户可在页面自由构建投资组合（股票 / ETF / 场外基金），设定权重与再平衡规则，运行回测并与基准对照。**
- 范围：只描述**主干结构与流程**。~~交易成本分档~~（**已定：不计**，2026-09-19 用户明确）、凭据管理等附属议题不在本文。
- 关联：可行性评估见 `portfolio-asset-universe-feasibility.md`；回测引擎细节见 `portfolio-backtest-engine-design.md`。
- ⚠ **入口索引**：组合实验室相关文档共 6 份，先读 `docs/portfolio-lab-index.md`。

---

## 〇、一句话概括

> 这个需求不是"再加一个页面"，而是**新增一条从"标的"到"组合净值"的数据通路**。
> 现有系统的通路是「数据 → 页面」；本需求要加的是「数据 → 序列 → 组合 → 回测 → 归因」。

---

## 一、五段流程

| 段 | 用户做什么 | 系统做什么 | 数据落点 |
| --- | --- | --- | --- |
| **① 添加资产** | 输入代码（或搜基金名称），确认 | 解析名称/类型 → 注册标的 → 抓取全历史 → 落库 → 回状态 | `research_security` + 日线/净值表 |
| **② 构建组合** | 设权重、选再平衡规则、选基准 | 校验权重、保存组合定义 | `portfolio` + `portfolio_asset` |
| **③ 运行回测** | 点"运行" | 统一取序列 → 对齐 → 算目标权重 → 执行 → 记账本 → 出 NAV | `backtest_run` + NAV |
| **④ 查看结果** | 看净值/回撤/指标/对照 | 计算绩效、归因、对照 | —（读取即可） |
| **⑤ 持续维护** | 无（自动） | 每日 17:30 自动同步所有已注册标的 | 复用现有定时任务 |

**关键：① 与 ⑤ 是同一条链路的两端。** 添加时手动抓一次，之后自动跟着定时任务走。

---

## 二、数据结构

### 复用（不新建）

| 表 | 作用 | 现状 |
| --- | --- | --- |
| `research_security` | **标的注册表**（`symbol`/`name`/`security_type`/`source`/`enabled`） | 已存在，需扩展 `security_type` 支持 `FUND` |
| `research_daily_bar_adjusted` | 股票 / ETF 后复权 OHLCV + 哈希 + 修订追踪 | 已存在 |
| `research_data_snapshot` | 抓取快照与校验 | 已存在 |
| `research_trade_calendar` | 交易日历（按 exchange 版本化） | 已存在 |
| `research_replay_run` | **Run 范式参照物**（参数即身份 + `input_hash` + 算法版本） | 已存在，作为组合 Run 的设计模板 |

### 新增

| 表 | 作用 | 备注 |
| --- | --- | --- |
| `fund_nav_daily` | 场外基金净值（`unit_nav` / `daily_return_pct` / `adj_nav`） | 语义与 OHLCV 不同，**不塞进 bar 表** |
| `portfolio` | **组合身份**（`name` / `note` / `status` / 时间戳） | ⚠ **不存**再平衡/基准/区间（见下注） |
| `portfolio_asset` | **组合成员**（`target_weight` 初始比例 + **`added_at`** + `sort_order`） | 唯一键 `(portfolio_id, symbol)`；`added_at` 供「添加后的收益」用 |
| `backtest_run` | 一次回测 = 一个 Run（组合快照 + **`rebalance` / `benchmark` / `start` / `end`** + `input_hash`） | 沿用 `research_replay_run` 的字段设计语言 |
| `backtest_metrics` | Run 的绩效指标（NAV 建议存文件） | |

> ⚠ **2026-09-19 归属修正**：「再平衡 / 基准 / 区间」**不属于 `portfolio`，属于 `backtest_run`**。
> **组合只描述"持有什么、各多少"；"怎么算"属于 Run**——否则"看三种再平衡"要建 9 个组合。
> 场景对比见 `docs/portfolio-lab-multi-portfolio.md` 第一节。折中：`portfolio` 可存
> `default_rebalance` / `default_benchmark`，**仅作新建 Run 时的预填**。
>
> ⚠ **范围（用户 2026-09-19 三轮澄清后定稿）**：**支持多组合**（用户可创建多个）。
> 用户路径：`组合回测` → **组合列表** → 新建/进入组合 → 添加标的 → **调平衡状态** → 看结果。
> - **L1 组合列表**管"组合"（新建 / 进入 / 复制 / 改名 / 删除）
> - **L2 组合详情**管"标的与回测"（复刻韭圈儿；**平衡方式只是页面上一个切换**，不是新建组合）
>
> ⚠ **"平衡方式"落库归 `backtest_run`，不归 `portfolio`**（组合只描述"持有什么、各多少"）。
> ⚠ **单组合不丢历史**：`backtest_run` 存的是**跑那一次时的组合快照**，旧配置改掉后以前的 Run 仍可复现。
> 详见 `portfolio-lab-multi-portfolio.md` 〇之二。

**结构关系**：

```
research_security ──┬── research_daily_bar_adjusted   (股票/ETF)
                    └── fund_nav_daily                (场外基金)
                            │
                            ↓  统一序列层 get_series()
                    portfolio_asset ──→ portfolio
                            │
                            ↓
                      backtest_run ──→ backtest_metrics
```

**要点**：`research_security` 是**唯一的标的入口**。三类资产共用一张注册表，差异只在"落到哪张价格表"以及"用哪个 Provider 抓"。

---

## 三、统一序列层（核心抽象）

这是整个结构里唯一的新抽象，其余都是拼装：

```python
get_series(asset_id, start, end) -> SeriesContract
```

`SeriesContract` 至少包含：

| 字段 | 说明 |
| --- | --- |
| `date` / `adj_price` | 对齐后的日期与可比价格 |
| `price_basis` | `HFQ`（股票/ETF）/ `NAV_ADJ`（基金）/ `PRICE`（指数） |
| `settle_lag` | 成交滞后：股票/ETF = 0；**场外基金 = 1（T+1）** |
| `asset_class` | `stock` / `etf` / `fund` |

**为什么必须有 `price_basis`**：三类资产的"可回测序列"来源不同，混用会静默出错。把口径显式挂在返回值上，UI 可以逐列标注，归因也能追溯。

---

## 四、接口结构（新增）

> **阶段标注**：`P0` = 标的注册与按需抓取（股票/ETF）｜`P1` = 场外基金链路｜`P2` = 序列层 + 组合定义｜`P3` = 回测 + 展示。
> 组合相关端点**本期全做**（支持多组合）；仅"跨组合对照"标注 `二期`。裁决依据见 `portfolio-lab-multi-portfolio.md` 〇之二。

**标的**

| 方法 | 路径 | 作用 | 阶段 |
| --- | --- | --- | --- |
| `GET` | `/api/portfolio/assets/probe?code=` | **只解析不落库**：返回名称/类型/可用区间/数据状态。⚠ **须支持返回多候选**（六位数字三义冲突，见 `portfolio-lab-add-asset-ux.md`） | P0 |
| `POST` | `/api/portfolio/assets` | 注册 + 触发抓取 | P0 |
| `GET` | `/api/portfolio/assets` | 已注册标的列表（含行数/区间） | P0 |
| `POST` | `/api/portfolio/assets/{id}/refresh` | 单标的补抓 | P0 |
| `GET` | `/api/portfolio/assets/search?q=` | 名称搜索（**仅场外基金**，走雪球搜索，需 token；失效须降级为"请输入代码"） | P1 |
| `DELETE` | `/api/portfolio/assets/{id}` | 软删（置 `enabled=False`）—— ⚠ **仅在无组合引用时允许**，否则会影响定时同步 | 二期 |

**组合（L1）**

| 方法 | 路径 | 作用 | 阶段 |
| --- | --- | --- | --- |
| `GET` | `/api/portfolio/portfolios` | 组合列表（**含每行"最近一次回测"的收益/回撤**，列表页要用） | P2 |
| `POST` | `/api/portfolio/portfolios` | 新建组合（可带 `from_id` 实现"复制现有"） | P2 |
| `GET` | `/api/portfolio/portfolios/{id}` | 单组合详情（成员 + 权重 + `added_at` + **当前占比** + **添加后收益**） | P2 |
| `PATCH` | `/api/portfolio/portfolios/{id}` | 改名 / 备注 / 改成员与权重 | P2 |
| `DELETE` | `/api/portfolio/portfolios/{id}` | 软删（归档可恢复，不级联删标的与 Run） | P2 |

**回测（L2；参数即身份，含再平衡/基准/区间）**

| 方法 | 路径 | 作用 | 阶段 |
| --- | --- | --- | --- |
| `POST` | `/api/portfolio/backtests` | 运行回测（异步）。参数幂等复用，返回 `run_id` | P3 |
| `GET` | `/api/portfolio/backtests` | Run 列表（按 `portfolio_id` 过滤） | P3 |
| `GET` | `/api/portfolio/backtests/{run_id}` | 单 Run 详情（净值/回撤/指标/相关性） | P3 |
| `GET` | `/api/portfolio/backtests/compare?ids=` | **多 Run 对照**（同持仓 × 不同再平衡/区间/基准） | P3 |
| `GET` | `/api/portfolio/backtests/cross-compare?ids=` | **跨组合对照**（不同持仓）⚠ 须"对齐交集区间" | 二期 |

**两处设计参照**：

1. `probe` 与 `assets` 分开 —— "解析"轻量（1 个请求）、"注册+抓取"重量（可能几秒）。UI 上表现为"输入代码 → 立即显示名称供确认 → 点确认才开始抓"。
2. **再平衡 / 基准 / 区间 只出现在 `backtests` 的请求体里，不出现在 `portfolios` 的读写字段里** —— 这是归属修正的接口侧体现（见第二节表设计注）。

---

## 五、前端页面结构

> ❌ **2026-09-19 本节作废**：以下是早期"左中右三栏编辑器"设想；用户已确定**复刻韭圈儿组合详情页**，实际页面为**上下分段式**（收益条 → 组合收益 Tabs → 净值曲线 → 指标卡 → 相关性矩阵 → 组合详情表），**以 `docs/portfolio-lab-page-spec.md` 为准**。另：本节列的再平衡选项"月 / 偏离阈值"韭圈儿并不提供，实际只有 **不平衡 / 季平衡 / 年平衡**。
>
> 本节**仍有效**的仅一条：下方「导航归属」。

```
/portfolio-lab
├── 左：资产编辑器
│   ├── 搜索/输入框（代码 或 基金名称）
│   ├── 解析预览卡（名称·类型·可用区间·数据状态）
│   └── 组合成员表（资产 | 权重 | 已用数据区间）
├── 中：参数区
│   ├── 再平衡规则（不再平衡 / 月 / 季 / 年 / 偏离阈值）
│   ├── 基准（默认红利指数）
│   └── 回测区间
└── 右：结果区
    ├── 净值曲线 + 回撤曲线
    ├── 指标表（CAGR / MDD / Sharpe / Calmar / 换手 / 相对基准超额）
    └── Run 列表（可勾选两个做对照）
```

**导航归属**：新增一级模块「组合」→ 页面「组合实验室」+ 「回测中心」。与既有的「市场分析」「资产研究」并列。

---

## 六、四个关键设计判断

### 1. 不新建 `asset_master`，扩展 `research_security`

`research_security` 已具备 `symbol`/`name`/`security_type`/`source`/`enabled` 五个核心字段，缺的只是"支持第三类资产"。新建一张表会立刻产生"两张表谁是真源"的问题。**扩展它，代价是多一个枚举值。**

### 2. 组合实验 = `/research` 已有范式的推广，不另起炉灶

现有 `/research`（次日 T 价位回放）已经建立了这套设计语言：

| 范式要素 | `/research` 已有 | 组合回测对应 |
| --- | --- | --- |
| 参数即身份的唯一键 | `(symbol, λ, window, algo_ver, start, end)` | `(组合快照, 规则, 基准, start, end)` |
| `input_hash` | ✅ | ✅ |
| 算法版本 | `ALGORITHM_VERSION` | 需加 |
| 训练/验证切分 | ✅ | 可沿用 |
| 对比接口 | `GET /replay-comparison` | `GET /backtests/compare` |
| 结构性防前视 | `generate_plan()` 首行过滤 `date <= T` | 目标权重按 T 日算、T+1 执行 |

**换句话说，组合回测在数据结构上不是新物种，是"多标的版本"。** 这能省掉大量设计工作，也让两个模块的 Run 可以互相参照。

### 3. 场外基金必须走独立价格表

`research_daily_bar_adjusted` 的语义是 OHLCV。把净值塞进 `open/high/low/close` 四列，短期能跑，长期一定会误导人（"基金的开盘价是什么？"）。**统一序列层本来就负责遮蔽存储差异，所以上层完全不受影响。**

### 4. 定时同步零改动

已验证：`run_research_daily_sync()` 按 `research_security.enabled=True` 遍历，且 `upsert_securities()` 不删任何标的。所以：

> **用户添加的标的，次日自动进入 17:30 定时同步，不需要改定时任务一行代码。**

这是整个方案里最省事的一环，值得明确记下来。

---

## 七、实施阶段

| 阶段 | 交付 | 依赖新数据源 | 依赖新表 | 状态 |
| --- | --- | --- | --- | --- |
| **P0** 标的注册 + 按需抓取（股票/ETF） | 能在页面添加任意股票/ETF 并看到数据状态 | ❌ | ❌ | ✅ **已完成**（`280a85b` / `bab81fe` / `9e16f92`） |
| **P1** 场外基金链路 | 同上，覆盖基金 | ✅ 蛋卷 | `fund_nav_daily` | ✅ **已完成**（`6fd2350`，端到端实测 100018：5545 行 / 2003-12-02 起） |
| **P2** 统一序列层 + 组合定义 | 三类资产能组成一个组合并存下来 | ❌ | `portfolio` 等 | ⬜ 未开始（**下一个阶段**） |
| **P3** 回测引擎 + 结果展示 | 点"运行"能出净值/指标；能对照两个 Run | ❌ | `backtest_run` 等 | ⬜ 未开始 |

**P0 是唯一的零风险起点**：不动表、不动迁移、不加数据源，纯复用现有抓取与注册逻辑，只是把入口从"配置文件"换成"API"。

> ⚠ **编号陷阱**：提交信息里的 **P0-1 / P0-2 / P0-3** 指的是**本表 P0 阶段内部的三次提交**，与本表的 P1/P2/P3 不是同一套编号。详见 `portfolio-lab-index.md` 文首提示。

---

## 八、当前待定（不阻塞流程设计）

| 项 | 状态 |
| --- | --- |
| **交易成本** | ✅ **已定：不计**（用户 2026-09-19 明确） |
| **回测算法** | ✅ **已定**：份额法账本 + 三种再平衡，见 `docs/funddb-portfolio-algorithm.md`（回归工具全部 PASS） |
| **序列层取数起点** | ✅ **已定（2026-09-19，本节新增）**：起点由 **建仓日 T0 = 各标的"数据可得区间"的共同起点** 决定，可能早至 2003（本例 2013-04-26）。**不引入"成立日/上市日"外部元数据**（用户裁决：回测起止日 = 数据起止日）。⚠ **组合实验室不得依赖 `research_daily_bar_adjusted`**——该表 `history_start: "2021-01-01"` 且仅 5 个标的，会让 2013 基准日的复刻直接不可行。正确做法：股票/ETF 走腾讯 `fqkline`（支持任意起始日）、场外基金走蛋卷（全历史 1 个请求），按需落**独立价格表/缓存** |
| **验收基线** | ✅ **已定（本节新增）**：`docs/portfolio-lab-verification.md` 第五节的 24 项断言（6 类），页面数值须逐项对齐 |
| **Benchmark 价格指数 vs 全收益** | ⬜ 待定（P3 前，影响"相对超额"可信度；**本模块回测本身不依赖它**，仅"相对基准超额"受影响） |
| **雪球 token 与名称搜索** | ⬜ 待定（P1 后；搜索是输入辅助，不进关键路径，token 失效须降级为"请输入代码"） |

> **算法已定稿**（详见 `docs/funddb-portfolio-algorithm.md` 与验收报告 `docs/portfolio-lab-verification.md`）：复刻目标为韭圈儿组合页，核心为**份额法账本**——
>
> - **建仓日 T0** = 各标的"数据可得区间"的共同起点 = max(各标的首个可用数据日)（本例 2013-04-26）
> - **不引入"成立日/上市日"**：个股无此接口、场内 ETF 也拿不到 → 用数据区间才普适
> - 份额固定、**区间查询不重置权重**；起点规则 = 共同（`T_start = max(用户选择, T0)`）
> - 再平衡支持 **不平衡 / 季平衡 / 年平衡**（韭圈儿无月度、无阈值选项）
> - **不计交易成本**
