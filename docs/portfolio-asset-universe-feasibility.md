# 组合标的接入可行性评估：股票 / ETF / 场外基金

- 日期：2026-09-19
- 问题：用户可在页面自由构建组合（含股票、ETF、场外基金），**可行性有多少？实现步骤是否清晰？**
- 性质：可行性评估，**不含代码改动**。所有结论基于代码核实与 ECS 实测。

---

## 〇、结论先行

| 维度 | 可行性 | 说明 |
| --- | --- | --- |
| **数据可得性** | **高（~95%）** | 三类资产均已验证可拿到全历史序列，且**不需要全市场代码表** |
| **工程可行性** | **中高（~75%）** | 现有基础设施可复用约八成；主要新工作在"基金 Provider"与"按需抓取 API" |
| **口径统一** | **中（~60%）** | 三类资产的"可回测序列"语义不同，有 3 个口径必须先定 |
| **整体** | **可行，无需推倒重来** | 但需从"白名单定时同步"扩展为"白名单 + 用户标的"双来源 |

**一句话**：技术路径是清晰的；真正的不确定性不在"能不能拿到数据"，而在**三类资产的口径如何统一到一个 `get_series` 契约里**——这部分必须先定规则，否则组合净值会混用口径。

---

## 一、三类资产的现状（代码核实）

| 资产类 | 数据源 | 数据可拿 | 现有支持 |
| --- | --- | --- | --- |
| **股票** | 腾讯 `fqkline` | ✅ 任意代码，含后复权 | Provider 就绪；`research.yaml` 里 3 只 |
| **ETF** | 腾讯 `fqkline` | ✅ 同上，与股票统一 | Provider 就绪；`research.yaml` 里 2 只 |
| **场外基金** | 蛋卷 `djapi` | ✅ 全历史 1 个请求 | **Provider / 表 / 抓取 / 任务：全无** |

### 本次新验证的两个关键前提

**① 腾讯源对任意代码可用，且自带名称** —— 这是"自由添加标的"能成立的关键。

```
GET web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh600519,day,,,5,hfq
→ data.sh600519.qt = ["1","贵州茅台","600519","1257.12",...]
                     ↑ 名称就在同一个响应里
```

**含义：不需要维护全市场代码表，也不需要额外的"代码→名称"解析请求。** 用户输入代码即可完成"抓取 + 命名"。

**② 蛋卷可解析基金代码；搜索接口存在，但硬依赖 token 且只覆盖基金**

```
GET danjuanfunds.com/djapi/fund/100018
→ fd_name=富国天利增长债券  fd_type=2  type_desc=债券型-普通债券  risk_level=2
```

搜索接口（用户提供，已实测）：

```
GET danjuanfunds.com/djapi/v2/search?key=<关键词>&source=index&xq_access_token=<TOKEN>
→ {"data":{"items":[{"id":2090,"scode":"161005","sname":"富国天惠成长混合（LOF）A",
                     "stype":"fund","yield":"7.6745","store_status":1,...}],
            "total_items":2,"total_pages":1},"result_code":0}
```

三点实测结论：

| 项 | 实测结果 |
| --- | --- |
| **是否需要 token** | **是，硬依赖**。不带则 `{"result_code":200007,"message":"api参数缺少"}` |
| **覆盖范围** | **仅场外基金**。`stype` 恒为 `fund`。搜"贵州茅台"返回 **0 条**；搜"红利低波"返回 20 条，全为 ETF 联接基金（如 `021550 博时中证红利低波动100ETF联接A`） |
| **分页** | 返回 `total_items` / `total_pages`，可控 |

**含义**：
- 场外基金**可以**按名称搜索 → 这是纯增益，解决了此前的 UX 缺口。
- 但**股票/ETF 搜不到**，仍需按代码输入（腾讯 `fqkline` 的响应自带名称，所以按代码添加后能正常显示）。
- **token 是雪球账号级凭据且会过期** → 不能进 git、必须有降级路径（见第六节决策 4）。

**⚠ 凭据处理**：`.env` 已在 `.gitignore` 且未被 git 跟踪；项目已有 `JISILU_USERNAME`/`JISILU_PASSWORD` 的同类先例，`backend/config.py` 通过 `load_dotenv` 主动加载。雪球 token 应照此办理——写入 `.env` 的 `XUEQIU_ACCESS_TOKEN=`，并在 `.env.example` 加同名空键。**不要把 token 写进代码、配置 yaml 或文档。**
>
> 另外提醒：该 token 已出现在对话记录中；若介意，建议在雪球侧重新登录使其失效后换新。

---

## 二、现有基础设施可复用清单（约八成）

| 组件 | 状态 | 复用方式 |
| --- | --- | --- |
| `research_security` 表 | ✅ **已存在** | 标的注册表，含 `symbol`/`name`/`security_type`/`exchange`/`source`/`enabled`/`selection_list` |
| `research_store.upsert_securities()` | ✅ **只增不删** | ⭐ 见下方"关键发现" |
| `research_daily_bar_adjusted` | ✅ 已存在 | 后复权 OHLCV + `content_hash` + `snapshot_id` + 修订追踪 |
| `research_data_snapshot` | ✅ 已存在 | 抓取快照：`raw_hash`/`hfq_hash`/`dates_match`/`status`/`reject_reason` |
| `research_trade_calendar` | ✅ 已存在 | 按 exchange 版本化，8,797 行（1990-12-19 起） |
| `MarketDataProvider` Protocol | ✅ 已存在 | `get_daily_bars` / `get_trade_calendar` / `get_corporate_events` |
| `provider_factory()` | ✅ 已存在 | 来源路由，**未知来源报错、不静默回退**——新增蛋卷只需加一个分支 |
| `TencentFqklineProvider` | ✅ 已就绪 | 任意代码可用（本次验证） |
| `run_research_daily_sync()` | ✅ 已就绪 | **按 `enabled=True` 遍历**，非按 yaml 遍历 |
| 前端 `ResearchReplay.vue` | ✅ 可参考 | 已有标的切换 UI 模式（但数据源是"已入库列表"） |

### ⭐ 关键发现：标的注册"只增不删"

```python
# research_store.upsert_securities (line 55-81)
existing = db.scalar(select(ResearchSecurity).where(ResearchSecurity.symbol == symbol))
if existing is None:
    db.add(ResearchSecurity(... enabled=True))     # 新增
else:
    existing.name = ...; existing.security_type = ...  # 仅更新同名标的
# 注意：没有任何 delete —— yaml 里没有的标的不会被清理
```

**这条决定了整个方案的形态**：

1. 用户通过 API 添加标的 → 写入 `research_security` → **永远不会被定时任务清掉**。
2. 而 `run_research_daily_sync()` 是**按表里 `enabled=True` 遍历**的（不是按 yaml 遍历），所以：

> **用户添加的标的，第二天会自动纳入 17:30 的定时同步——无需改动定时任务。**

这意味着"自由添加标的"的实现只需两块：**① 一个单标的即时抓取的服务函数；② 一个 API 把它暴露出去**。定时链路完全复用。

---

## 三、需要新增的 7 项

| # | 项目 | 工作量 | 说明 |
| --- | --- | --- | --- |
| A | `security_type` 支持 `FUND` | 小 | 模型注释 + `_TYPE_TO_SECURITY_TYPE` 映射 + `_assert_symbol_type`（基金代码**无交易所后缀**，现有断言会全部失败）+ 前端 |
| B | `FundNavProvider`（蛋卷） | 中 | 实现 `MarketDataProvider` Protocol；或用独立协议（见第四节决策 1） |
| C | 基金净值存储 | 小 | 新建 `fund_nav_daily` 或复用 `research_daily_bar_*`（见第四节决策 1） |
| D | **单标的按需抓取服务 + API** | 中 | `fetch_symbol_history(symbol)` + `POST /api/portfolio/assets`；复用 `run_research_daily_sync` 单标的逻辑 |
| E | 前端"添加标的" | 中 | 现在是**只读下拉**（`GET /api/research/securities`），需改为可输入：支持代码输入 + 即时解析名称；基金可叠加名称搜索（带 token 降级） |
| F | 统一序列层 `get_series()` | 中 | 口径标注 + `settle_lag` + 交易日对齐 + 成本档 |
| G | 组合定义 + 回测 | 大 | 即前一份设计稿的 Portfolio Lab，本评估不含 |

> 注意 A 的一个细节：股票/ETF 的 symbol 形如 `600900.SH`（带交易所后缀），而**场外基金是 6 位纯数字**（`100018`）。`_assert_symbol_type` 目前对无后缀代码会直接失败，且基金没有"沪/深"概念。需要引入第三种标识形态。

---

## 四、必须先定的 5 个决策（阻塞项）

### 决策 1：基金净值存哪张表？

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| **新建 `fund_nav_daily`**（推荐） | 语义干净（基金没有 OHLC）；字段贴合（`unit_nav`/`daily_return_pct`/`adj_nav`） | 需重新接 `snapshot`/哈希机制，或简化掉 |
| 复用 `research_daily_bar_adjusted` | 直接继承 `content_hash` + 修订追踪 | 语义污染：要把净值塞进 `open/high/low/close` 四列，未来必然困惑 |

**推荐新建表**，理由是"统一序列层"（F 项）本来就会遮蔽存储差异——上层只认 `get_series` 的返回契约，存储可以按资产类各用各的表。让基金数据假装成 K 线，是给未来埋坑。

### 决策 2：`history_start` 是否放宽？

当前全局配置 `history_start: "2021-01-01"`。若用户想回测"2015 年起"的组合，标的抓不到 2021 年前的数据。三个选项：① 全局放宽到 2013（覆盖大多数 ETF/基金）；② 按标的单独配置；③ 保持现状，UI 上显示"可用区间"。

**建议 ①**：抓取成本几乎不变（腾讯 640 条/页分页，多几年只是多几页），但换来 10 年以上的回测能力。**这一条同时影响 Benchmark 口径的验证**——检验红利资产的价格指数 vs 全收益差异，需要足够长的样本。

### 决策 3：场外基金的成交时点与成本档

- **成交时点**：T 日净值 **T 日晚间**才公布（QDII 为 T+1/T+2）→ 回测必须 **T+1 成交**，否则是前视。这条是硬约束，不是可选项。
- **成本档**：~~股票（卖出印花税 0.05%）/ ETF（免印花税免过户费）/ 场外（申赎费阶梯，<7 天 1.5% 惩罚性赎回费）三者差异巨大~~ → ✅ **已定：不计交易成本**（用户 2026-09-19 明确）。
  - 影响："再平衡贡献"由"三 Run 成本分解"简化为**两 Run 净值比**；换手率仍输出但只作参考（评估实盘可行性），不入收益计算。
  - 风险提示：实盘中 ETF 免印花税、场外短期赎回费 1.5% 等差异仍然存在，**回测结果不含这部分拖累**，实盘预期需自行打折。

### 决策 4：名称搜索怎么做？（含凭据策略）

现状：场外基金可搜（`/djapi/v2/search`，**硬依赖 token**）；股票/ETF 搜不到。

**建议分两层，且不让 token 进关键路径**：

| 层 | 做法 | 依赖 token |
| --- | --- | --- |
| **基础（必须可用）** | 按代码输入 → 服务端解析名称（基金走 `/djapi/fund/{code}`，股票/ETF 走腾讯响应自带的 `qt` 名称） | ❌ 不需要 |
| **增强（可选）** | **仅基金**支持按名称搜索，走 `/djapi/v2/search` | ✅ 需要 |

三条实现约束：

1. **token 缺失或失效时必须优雅降级**——搜索框提示"请输入代码"，而不是报错或卡住。**搜索是输入辅助，不是数据链路的一环。**
2. token 走 `.env`（已 gitignore）+ `.env.example` 空键，与集思录凭据同等待遇。
3. 股票/ETF 的名称搜索**不在本方案内**——如需，需另找源。但由于腾讯响应自带名称，"按代码添加"的体验已经可接受。

> 换言之：**搜索接口是锦上添花，不改变 P0/P1 的技术路线**。不应因为它存在就调整建表、抓取或序列层的任何设计。

### 决策 5：组合标的数量上限

影响是否需要批量抓取。按用户的克制原则：**不设硬上限，但抓取串行 + 标的间礼貌间隔**。一只基金 1 个请求、一只股票约 2 个请求（raw + hfq），10 只标的的组合首次构建约 20 个请求——**完全在合理范围**。不需要为"上百只标的"做设计。

---

## 五、实现步骤（4 阶段，每阶段可独立验证）

### P0：标的注册与按需抓取（股票 / ETF）· 复用最充分

1. 新增 `fetch_symbol_history(symbol, security_type, source)` 服务函数（抽取 `run_research_daily_sync` 的单标的逻辑）。
2. 新增 `POST /api/portfolio/assets`：**输入代码 → 腾讯抓取 → 解析名称（响应自带）→ upsert `research_security` → 落库 bars**。
3. 新增 `GET /api/portfolio/assets`（列表）/ `DELETE`（软删，置 `enabled=False`）。
4. 前端改造：把只读下拉改为"输入代码 → 解析预览（名称/类型）→ 确认添加"。
5. **验证点**：输入 `600519`（不在 yaml 里），能抓到全历史、显示"贵州茅台"、次日进入定时同步。

> 这一阶段的产出本身就有价值——它把"标的池"从 5 个白名单变成开放集合。

### P1：场外基金链路

1. 扩展 `security_type` 支持 `FUND`（模型 + 映射 + 断言 + 前端）。
2. 新增 `FundNavProvider`（蛋卷）：`/djapi/fund/{code}` 取元信息、`/djapi/fund/nav/history/{code}?size=6000` 取全历史。
3. 新建 `fund_nav_daily` 表 + alembic 迁移（0005）。
4. 在 `provider_factory` 加 `danjuan` 分支；`get_daily_bars` 内部把 `percentage` 链式复利成 `adj_nav`。
5. （可选）接名称搜索：`.env` 加 `XUEQIU_ACCESS_TOKEN`，封装 `/djapi/v2/search`，**token 缺失/失效时降级为"请输入代码"**。
6. **验证点**：输入 `100018`，抓到 5,545 行，`adj_nav` 与实测的 7.23x 累计一致；搜"富国天惠"能返回 `161005` 并可一键添加。

### P2：统一序列层

1. 定义 `SeriesContract`：`date` + `adj_price` + `metadata{price_basis, settle_lag, asset_class, currency}`。
2. 三个适配器：股票/ETF（HFQ 收盘）/ 指数（close，**须标注 PRICE**）/ 场外基金（`adj_nav`）。
3. 交易日对齐规则：**净值用并集 + 前值填充；再平衡触发用交集**（跨市场时；全境内资产则用 `research_trade_calendar`）。
4. **验证点**：同一组合里放入 红利ETF + 长债ETF + 场外基金，能拼成一张对齐面板，且每列都带口径标注。

### P3：组合定义 + 回测

即前一份 `portfolio-backtest-engine-design.md` 的内容，本评估不重复。

---

## 六、剩余风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| **Benchmark 口径未定** | 所有"相对红利超额"不可信 | 仍为硬阻塞；建议用决策 2 放宽样本后做实测验证 |
| 腾讯 `hfq` 非事件日抖动 0.1%~0.4% | 复权序列有噪声 | 已知问题，权益事件以新浪日历为主检测；Baostock 可作交叉校验 |
| 基金 `percentage` 仅 2 位小数 | 单日收益精度有限 | 组合层面影响 <1%；可用单位净值交叉校验 |
| 用户添加大量标的 | 首次抓取耗时（串行） | 按克制原则串行 + 进度反馈；不做并发 |
| 股票/ETF 无法名称搜索 | UX 略差（需知道代码） | 腾讯响应自带名称，按代码添加后显示正常；如确需，另找股票搜索源 |
| 雪球 token 过期 | 基金名称搜索失效 | **降级为"请输入代码"**，不影响抓取链路（搜索不是关键路径） |

---

## 七、下一步建议

**建议从 P0 开始**，理由：

1. **零新数据源、零新表、零迁移**——纯复用，风险最低。
2. 它独立地回答了用户的核心诉求（"自由构建组合"）中**股票 + ETF 这一半**。
3. 它验证了"按需抓取 + 注册表"这个模式本身；P1 的场外基金只是把这个模式套用到另一类资产。
4. 产出一个可用的标的池后，P3 的回测才有东西可跑。

**需要先拍板的**：第四节决策 1（基金净值存表）与决策 2（`history_start` 是否放宽）——这两条影响 P0/P1 的建表与抓取范围。决策 3（成交时点与成本档）影响 P2，可稍后定。
