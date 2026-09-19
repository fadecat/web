# 组合实验室 · 数据维护设计（股票 / ETF / 场外基金）

- 日期：2026-09-19
- 定位：三类标的的数据**获取、定时、手动、幂等、修订、新鲜度**如何在本系统内维护
- 前提：算法与页面交互已定稿（见 `docs/portfolio-lab-index.md`），本文只谈"数据怎么进得来、怎么不出错"

---

## 一、结论先行

> **不需要新建调度框架。** 现有系统已有一套成熟的"标的级同步"骨架（注册表 + 任务注册 + 运行日志 + 新鲜度契约），
> 组合回测只需：**扩一个类型、接一个 Provider、加一张表、加一个 job**。
>
> 但有三处**结构性障碍**必须先解决，否则接不上（见第二节）。

| 项 | 现状 | 组合回测需要 |
| --- | --- | --- |
| 调度框架 | ✅ APScheduler + `tasks/registry.py` 单一真源 | 不改 |
| 手动触发 | ✅ `run_job_manually(job_id)` + `_trigger_sync(source, datasets)` | **加第三种：单标的即时同步** |
| 标的注册表 | ✅ `research_security`（只增不删、按 `enabled` 遍历） | 扩 `security_type=FUND` |
| 日线存储 | ✅ `research_daily_bar_raw` / `_adjusted`（配对 + content_hash + 修订追踪） | 股票/ETF 复用；**场外基金需新表** |
| Provider 路由 | ✅ `provider_factory(source)`（未知源报错、不静默换源） | 新增 `danjuan` 分支 |
| 交易日历 | ✅ `research_trade_calendar`（AkShare 新浪，版本化） | **复用**（场外基金净值日同为 A 股交易日） |
| 新鲜度契约 | ✅ `data_catalog.Policy(source, job_id, hour, minute, offset)` + `expected()` | 注册两个新条目 |
| 运行日志 | ✅ `run_logger.run_with_logging(...)`（手动/定时共用锁） | 复用 |

---

## 二、⚠ 三处必须先解决的结构性障碍

### 障碍 1：`_assert_symbol_type` 绑定了交易所后缀 —— 场外基金进不去

```python
# backend/services/market_data.py:286
def _assert_symbol_type(symbol, code, security_type):
    normalized_type = str(security_type).strip().upper()
    exchange = _symbol_exchange(symbol)          # ← 依赖 .SH / .SZ 后缀
    if exchange == "SSE" and normalized_type == "ETF" and not code.startswith("5"): ...
    ...
```

场外基金是 **6 位纯数字、无交易所概念**（蛋卷用 `100018`），`_symbol_exchange()` 拿不到值 → 校验链断裂。

**同时** `ResearchSecurity.exchange` 是 `NOT NULL`，注释写死 `SSE | SZSE`。

**解决**：引入**第三种标识形态** `100018.OF`（`.OF` = 场外基金，行业惯例后缀），并：
- `security_type` 枚举加 `FUND`
- `exchange` 加值 `OTC`（或允许空）
- `_assert_symbol_type` 对 `FUND` 走**独立分支**（只校验 6 位数字，不查交易所）
- `_tencent_code()` 这类后缀转换函数，遇到 `.OF` 应**明确报错**（场外基金不走腾讯），而不是静默拼成 `sh100018`

> **为什么不给场外基金也加 `.SH/.SZ`**：它是**申购赎回**、不走交易所撮合，硬套交易所后缀会在后续每个环节（日历、成交时点、成本）产生错误暗示。

### 障碍 2：`data_source` 与 `catalog()` 不一致（**已存在的显示层 bug**）

```
config/research.yaml:8          data_source: "tencent"
data_catalog.py:59              Policy("akshare", "research_daily_sync", 17, 30)
```

`Policy.source` 只用于**显示**（不参与调度），所以不影响抓取；但**数据管理页会把来源显示成 akshare，而实际抓的是腾讯**。

**建议**：`catalog()` 改为从 `load_research_settings()` 读 `data_source`，**不再硬编码**。这条与组合回测无关但顺手该修（否则组合回测标的也会继承同样的错误显示）。

### 障碍 3：`history_start` 是单一全局配置，且当前为 `2021-01-01`

```
config/research.yaml:4          history_start: "2021-01-01"
```

组合回测的基准组合 T0 = **2013-04-26**，用 2021 起的数据**根本跑不了**。

**两个方案**：

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A** 全局放宽 | 改 `history_start: "2013-01-01"`（或更早） | 简单。副作用：`/research` 的 5 个标的首次抓取变慢（腾讯分页，约 8 页/标的），之后增量无感 |
| **B** 标的级覆盖 | `research_security` 加 `history_start` 可空字段，为空则用全局 | 更灵活（不同标的可不同起点），但多一个字段与一层判断 |

**建议 A 起步、B 留作后续**：组合回测的标的天然需要长历史（"成立来"这一格就是卖点）。先全局放宽到 **2013-01-01**，等真出现"某些标的不需要长历史"的诉求再加 B。

---

## 三、设计：**两类数据形态**（这是全篇的关键区分）

现有 `research_daily_sync` 的模型是 **"raw/hfq 双序列配对发布"**。但三类标的不共享这个形态：

| 资产 | 序列数 | 字段 | 幂等键 | 修订机制 |
| --- | --- | --- | --- | --- |
| **股票 / ETF** | **双序列**（raw + hfq） | OHLCV + `adj_factor` | **配对 content_hash** | `research_data_revision` |
| **场外基金** | **单序列**（净值 + 日增长率） | `unit_nav` + `daily_return_pct` | **`(symbol, trade_date)` upsert** | 全量重拉比对 |

**为什么这个区分是根本性的**：

- 股票/ETF 需要 **raw 与 hfq 严格配对**（回放要算 `r_t = raw_close/hfq_close`），所以有"尾部发布滞后截齐"、"内部错位严格拒绝"这类逻辑；
- 场外基金**只有一条净值序列**，没有"配对"这个概念。把净值硬塞进 OHLC 四列后，"基金的开盘价是什么"这个问题会一直纠缠；而把配对校验套在单序列上，只会徒增失败模式。

→ **结论：两类形态各自一条链路，各自的表、各自的 job、各自的 Provider。**

---

## 四、设计：**两个 job**（不是三个，也不是一个）

```
research_daily_sync   既有，扩用     股票 / ETF     腾讯 fqkline      17:30   raw+hfq 配对
fund_nav_sync         新增           场外基金       蛋卷 djapi         23:10   单序列 upsert
```

### 为什么不合并成一个 job

| 维度 | 股票/ETF | 场外基金 |
| --- | --- | --- |
| 数据源 | 腾讯 `fqkline` | 蛋卷 `djapi` |
| 数据形态 | raw + hfq 配对 | 单序列 |
| 幂等方式 | content_hash 配对发布 | `(symbol, trade_date)` upsert |
| 发布时滞 | 盘中实时 → 收盘即全 | **T 日晚间**，QDII 再滞后 1~2 日 |
| 失败模式 | 尾部滞后需截齐、内部错位需拒绝 | 首日无 `percentage`、链式复权累积 |

合并会让**失败模式互相纠缠**（例如"配对失败"与"净值为空"混在同一个 `fail_count` 里），且无法独立重跑。

### 为什么不拆成三个（stock / etf / fund）

股票与 ETF 在腾讯源上是**同一套接口、同一套权益事件日历**（`_sina_parse_events` 对两者统一），拆开只是重复三次同样的逻辑。`security_type` 已经能在标的级区分。

### 时刻选择

- `research_daily_sync` 保持 **17:30**（既有，A 股收盘后腾讯数据已全）
- `fund_nav_sync` 新增 **23:10** —— 理由：普通基金净值约 20:00 前公布，**QDII 可能 22:00 之后**；放在 22:09 的 `index_eod_daily` 之后，避开同一时刻的拥挤
- ⚠ **两个 job 都进 `EVERYDAY_JOB_IDS`**（每自然日跑）：来源可能在周末/次日发布最近交易日数据，自然日跑 + 幂等无害（沿用既有先例）

---

## 五、设计：**三种触发方式**

沿用现有"定时与手动共用同一函数"的契约（`registry.py` 的 docstring 明确要求：*"保证「定时跑的」和「手动跑的」一定是同一个函数，不会各自漂移"*）。

| # | 方式 | 入口 | 范围 | 用途 |
| --- | --- | --- | --- | --- |
| 1 | **定时** | APScheduler（ECS 单进程） | 全部 `enabled=True` 标的 | 日常维护 |
| 2 | **手动整批** | `POST /api/data-status/jobs/{job_id}/run`（既有 `run_job_manually`） | 全部 `enabled=True` 标的 | 补跑、排障 |
| 3 | **手动单标的** ⭐新增 | `POST /api/portfolio/assets/{symbol}/sync` | **仅该标的** | 用户刚添加一只标的，不想等到 17:30/23:10 |

### 方式 3 的实现要点（新增）

```python
# 与整批任务共用同一把锁 → 避免"用户手动抓取"与"定时批量抓取"同时打同一个源
if not reserve_job(job_id_for_type(security_type)):     # STOCK/ETF → research_daily_sync
    return {"status": "busy", ...}                       # FUND       → fund_nav_sync

# 复用 job 内部的单标的逻辑（把它抽成可独立调用的函数）
sync_one_symbol(symbol)          # 抓取 → 落库 → 更新状态字段
```

**⚠ 日志策略（重要）**：单标的同步**不写 `TaskRunLog`** —— 否则用户每加一只标的就刷一条 run log，会把数据管理页的"运行记录"冲爆。

改为把结果写回 **`research_security` 的三个新字段**：

```
last_sync_at        datetime   # 最近一次同步时刻
last_sync_status    str        # success | failed | running
last_sync_error     str(255)   # 失败原因（截断）
last_sync_rows      int        # 本次写入行数
```

UI 上表现为：**添加标的的确认卡**里显示"同步中… / 已同步 5545 行（2003-12-02 ~ 2026-09-18） / 同步失败：<原因>"，前端轮询这个字段即可。**这也顺带解决了"添加后要等多久"的体验问题**——不必阻塞、不必转圈。

---

## 六、设计：**新鲜度契约**（数据管理页怎么显示）

现有机制：`data_catalog.catalog()` 声明 `Policy(source, job_id, hour, minute, trading_day_offset)`，`Policy.expected(now)` 算出**应到日期**与**下次到期时刻**，再与库内 `latest_date` 比对，得出 `fresh / stale / lagging / no_data`。

**要新增两个注册项**：

```python
catalog() 里
  # 既有组扩用（把"研究行情日线"改名为"个股/ETF 行情"）
  "个股/ETF 行情": {symbol: (name, Policy(<实际 source>, "research_daily_sync", 17, 30))}

  # 新增组
  "场外基金净值": {symbol: (name, Policy("danjuan", "fund_nav_sync", 23, 10, trading_day_offset=1))}
```

**`trading_day_offset=1` 是场外基金与股票/ETF 的关键差异**：

- 股票/ETF：收盘后（17:30）应到**当日**
- 场外基金：净值 T 日晚公布，QDII 再 +1~2 日 → 应到日期应为**上一交易日**（`offset=1`），否则每个交易日都会显示 `lagging`，红色告警天天亮

> 这正是 `Policy.trading_day_offset` 这个字段存在的意义（既有 `pe` / `dy` 就用了 `offset=1`，因为估值也是次日发布）。

---

## 七、设计：**幂等与修订追踪**

### 股票 / ETF —— 完全复用既有机制

| 机制 | 载体 |
| --- | --- |
| 配对发布 + 内容哈希 | `research_store.publish_paired_snapshot()` |
| 修订追踪 | `research_data_revision`（`inserted_rows` / `revised_rows`） |
| 快照 | `research_data_snapshot` |
| 尾部发布滞后截齐 | `_align_trailing_publication_lag()`（腾讯 ETF hfq 晚于 raw 出数 1 天，容忍 ≤5 bar） |

**无需改动**。

### 场外基金 —— 需要在新增逻辑里做到

| 风险 | 处理 |
| --- | --- |
| **重复写入** | 主键 `(symbol, trade_date)`，`INSERT ... ON CONFLICT DO UPDATE`；日频增量只拉最近 N 行（`size=5~20`），落到已有日期就是覆盖写 |
| **首日无 `percentage`** | 蛋卷成立首日的行只有 `date/nav/value`（无 `percentage`）→ 解析必须 `.get()` 兜底，**不可直接索引**（已实测：161116 的 2011-05-09 就是这种行） |
| **链式复权被破坏** | ⚠ **关键设计**：`daily_return_pct`（蛋卷 `percentage`）是**不可变的历史事实**，而 `adj_nav` 是**由它链式推出的派生值**。所以：<br>① **持久化 `unit_nav` + `daily_return_pct`**（事实）<br>② **`adj_nav` 由链式重算**，不依赖增量递推<br>③ 分红/份额折算只会改 `unit_nav`，**不改 `daily_return_pct`** → 只要以 `daily_return_pct` 为准，历史就是自洽的 |
| **历史被上游修订** | 低频（如每月）全量重拉一次，比对 `(symbol, trade_date, unit_nav, daily_return_pct)` 的哈希；有变化则记修订并重算该标的的 `adj_nav` |
| **成立来区间不完整** | 首次同步用 `size=6000` **一个请求拉全历史**（实测 100018：5545 行 / 411KB / 0.6s）；若 `total_items > 6000` 则按 `total_pages` 翻页（尚无实例，但代码要支持） |

---

## 八、设计：**限流纪律**（沿用既有克制原则）

> 选型依据 = **完成一次任务所需的请求数**，不是"能扛多大并发"。

| 源 | 首次回填 | 日频增量 | 间隔 |
| --- | --- | --- | --- |
| 腾讯 `fqkline`（股票/ETF） | ≈ 8 页/标的（640 条/页，2013 起 ≈ 3,200 交易日） | 1 页（`count=640` 未满即到底） | 既有实现已含间隔 |
| 蛋卷 `djapi`（场外基金） | **1 请求/标的**（`size=6000` 拉全） | **1 请求/标的**（`size=5~20`） | ≥ 0.5s，**串行** |

**组合规模下的实际量级**：一个组合通常 3~10 只标的 → 日频增量 **≤10 个请求/天**。这比现有 `commodity_daily`（75 个品种）还轻。

**硬约束（写进 Provider）**：
- 腾讯：`count` **必须 ≤640**，超过会**静默返回空数组**（`code=0` 不报错）→ 抓取器**必须校验返回行数**并游标分页
- 蛋卷：**无 Referer 依赖、无需登录**；触发限流的表现是 `result_code != 0`，须识别并退避
- **单标的串行 + 页间隔 + 指数退避 + 断点续传**（沿用既有 fetcher 纪律）

---

## 九、设计：**失败隔离与可观测**

沿用既有的"**单标的失败不中断其余**"（`run_research_daily_sync` 逐个 try/except，每标的新 Session）：

```
status = success  if fail==0 and success>0
         failed   if success==0
         partial  否则          ← 部分成功仍照常提交
返回 {status, success_count, fail_count, inserted_rows, revised_rows, errors[:10]}
```

**组合页面侧的数据就绪检查**（回测前）：

| 检查 | 不通过时的表现 |
| --- | --- |
| 每个标的都有数据 | 标的一行标红 + 「立即同步」按钮 |
| 最新日期不过期 | 提示"数据截止 09-17，建议先同步" |
| 各标的区间有重叠 | 明确提示"共同起点 = XXXX-XX-XX"（这正是起点规则的 UI 回显） |

**关键**：不阻塞、不静默降级——把问题**摆出来并给一个按钮**。

---

## 十、实施顺序（与 `flow.md` 的 P0~P3 对齐）

| 阶段 | 数据侧要做的事 | 状态 |
| --- | --- | --- |
| **P0** 股票/ETF 注册与按需抓取 | ① `security_type` 加 `FUND`（先搭好枚举）② `research_security` 加 4 个同步状态字段 ③ 抽出 `sync_one_symbol()` ④ 新增 `POST /api/portfolio/assets/{symbol}/sync` ⑤ `history_start` 放宽到 2013 ⑥ 顺带修 `catalog()` 的 source 硬编码 | ✅ **已完成**（2026-09-19，提交 `280a85b` / `bab81fe` / `9e16f92`） |
| **P1** 场外基金链路 | ⑦ `DanjuanProvider`（实现 `capabilities` / `get_daily_bars` / 复用交易日历）⑧ 迁移 **0006**：建 `fund_nav_daily`（⚠ 原写 0005，已被 P0-1 的 `0005_add_research_security_sync_state` 占用）⑨ 新增 `fund_nav_sync` job + 注册进 `registry` / `EVERYDAY_JOB_IDS` / `data_catalog` ⑩ 蛋卷的 `_assert_symbol_type` 独立分支 | ✅ **已完成**（2026-09-19，提交 `6fd2350`） |
| **P2** 序列层 | ⑪ `get_series()` 统一读两套表 ⑫ 口径标注（`HFQ` / `NAV_ADJ`）⑬ 交易日对齐（并集 + 前值填充） | ⬜ 未开始 |
| **P3** 回测与展示 | ⑭ 数据就绪检查接入组合页 ⑮ 列表页三格的缓存刷新挂到 job 末尾 | ⬜ 未开始 |

> **P0 是零风险起点**：不新增数据源、不建表、不做迁移，只是把标的入口从配置文件换成 API。做完这一步，"自由构建组合"的股票 + ETF 那一半就能用了。

---

## 十一、需要你拍板的 4 件事（**P0/P1 已按"建议"列全部落地**，此处留档）

| # | 事项 | 建议 | 落地情况 |
| --- | --- | --- | --- |
| 1 | **场外基金的标识形态**：`100018.OF` vs 纯 `100018` | **`100018.OF`**（显式、避免与指数/股票代码混淆；`ResearchSecurity.symbol` 已有唯一约束） | ✅ `portfolio_assets.py` 规范化为 `{code}.OF` |
| 2 | **`history_start` 放宽到哪一年** | **2013-01-01**（够覆盖基准组合的 2013-04-26；再早只是多抓几页，无收益） | ✅ `config/research.yaml` 已改为 `2013-01-01` |
| 3 | **`fund_nav_sync` 的调度时刻** | **23:10**（22:09 之后，给 QDII 留出公布时间） | ✅ `registry.py` 注册 `("fund_nav_sync", ..., 23, 10)` 并进 `EVERYDAY_JOB_IDS` |
| 4 | **`catalog()` 的 source 硬编码 bug 是否顺手修** | **修**（与组合回测无关，但会污染新标的的来源显示） | ⚠ **未修**：`data_catalog.py:47` 仍为 `load_research_settings().get("data_source", "akshare")`（从配置读、默认值 `akshare`）。场外基金走独立 Policy 不受影响，仅"研究行情日线"组的来源显示可能与实际（tencent）不符 |

---

## 附：本文与既有文档的关系

| 文档 | 关系 |
| --- | --- |
| `portfolio-lab-flow.md` §二/§三 | 本文是那张"新增表 + 序列层"的具体化；`fund_nav_daily` 的字段设计见本文第七节 |
| `portfolio-asset-universe-feasibility.md` | 可行性评估；本文是落地设计 |
| `data-source-research.md` / `data-source-ecs-probe-report.md` | 源选型与 ECS 实测依据 |
| `portfolio-lab-add-asset-ux.md` | 交互侧；本文的"单标的即时同步 + 轮询状态字段"是它的后端支撑 |
