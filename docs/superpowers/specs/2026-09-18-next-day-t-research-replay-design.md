# 次日 T 价位：研究回放优先设计（2026-09-18）

状态：V1 当前方案，待用户审阅；不包含券商下单、实盘建议或收益率声明。上一版[生产数据设计](2026-09-18-next-day-t-data-and-safety-design.md)保留作未来升级参考。

## 1. 目标、成果和边界

先回答“六档价位是否与历史次日的可触达区间匹配”，再决定是否开发每天收盘后的建议。标的从现有高股息股票和可转债正股名单选择；ETF 可手动添加。V1 输入为 AkShare/东方财富的股票、ETF **未复权 raw 与后复权 hfq 日线**；输出为逐日生成的价格计划及次日触达统计。研究回放只展示六档触达率、停用率、开盘失效率、双侧触达比例和样本覆盖；不计算精确 T 收益，不宣称日线高低价等于可成交订单。

V1 只对 A 股/场内 ETF 做研究，不连接券商、不自动提交条件单，不做全市场批量回填，不引入分钟数据。没有 Tushare 权限**不阻塞研究 V1**；未来的生产建议模式仍需独立审查盘后时效、证券交易规则和除权数据保证。

## 2. 分阶段交付

1. **Phase 0：数据源 PoC。** 对 3–5 个样本（至少包含长期分红股票、另一只 A 股、股票 ETF、其他类型 ETF）分别取 raw/hfq 220 根以上日线，校验接口可达性、日期/四价配对、单位、缺失、权益事件与源端回写。须在目标运行环境再跑一次。本机接口间歇性 `ProxyError`，所以目前不能把偶然成功的单次调用当成验收。
2. **Phase 1：行情研究层。** 实现 `AkShareEastmoneyProvider`、能力声明、raw/hfq 两套数据及抓取快照/修订记录。失败可重试，不能悄悄改用另一个来源；策略层无 AkShare 导入。
3. **Phase 2：单一指标/计划核心。** `generate_plan(symbol, as_of_date, parameters, portfolio=None)` 对线上与历史共用一份计算逻辑；先实现 EMA20、Wilder ATR14、120 日上下偏移分位、Z、六档价格与停用原因。研究模式的 `portfolio=None` 只输出价位，不伪造股数。
4. **Phase 3：历史回放。** 对每个 T，仅把 `trade_date <= T` 的数据交给计划核心；再用下一交易日 raw OHLC 做评价。记录每日计划、原因、原始命中证据及算法/参数版本。
5. **Phase 4：回放页面。** 按标的、日期、参数查看汇总与逐日明细；先于“次日 T 价位”实盘建议页面落地。
6. **以后：生产建议与分钟级执行回测。** 生产模式增加可信日历、盘后齐备与复权事件保证；分钟数据解决路径顺序、库存、费用与收益问题。两者分别评审，不随 V1 顺带上线。

Phase 0 未通过时仍可用固定样例验证纯算法，但不能展示某真实证券的历史统计为已验收结果。Phase 1/2/3 先行，页面不能替代数据质量验收。

## 3. Provider 能力而非厂商耦合

适配器返回规范化记录：`get_daily_bars(symbol, start, end, adjust_mode: RAW | HFQ)`；可选能力另有 `get_adj_factors`、`get_trade_calendar`、`get_security_info`。接口在创建时声明实际可用能力和来源，不能提供“合成 adj_factor”来满足检查。

| 能力 | AkShare 东方财富 V1 | 研究回放需求 | 未来生产建议需求 |
| --- | --- | --- | --- |
| `RAW_DAILY_BAR` | 股票/ETF 接口支持，待 PoC | 必需 | 必需 |
| `ADJUSTED_DAILY_BAR_HFQ` | 股票/ETF 接口支持，待 PoC | 必需 | 可用，但需额外质量保证 |
| `ADJ_FACTOR` | 不提供独立可核对序列 | 不要求 | 候选要求，另行设计 |
| `TRADE_CALENDAR` | 须另行接入并核验 | **必需**，否则只能报告“相邻有行情日”，不能断言下一交易日/停牌 | 必需 |
| `SECURITY_MASTER` | 独立元数据来源待 PoC | 仅展示名称时可手工维护；股数/交易规则不可猜 | 必需 |
| `POINT_IN_TIME_SNAPSHOT` | 本地开始抓取后可保存 | 历史回溯标注数据版本局限 | 严格复现时必需 |
| `EOD_FRESHNESS` | 不承诺 | 不要求 | 必需 |

项目当前 `backend/utils.py:is_trading_day` 靠人工节假日表，2027 年尚空，也没有覆盖整个历史回放区间；**不得**用于 Phase 3 的严肃交易日判断。Phase 0 优先验证独立交易日数据（如 AkShare 的 `tool_trade_date_hist_sina`），存成版本化日历；若未得到可信日历，先只运行“相邻有行情日触达研究”，并在结果中明示不能区分停牌与缺数。

AKShare 已安装版本 1.17.90；其两个函数签名均包含 `adjust` 参数，`''`/`hfq` 分别走东财 K 线接口的不同复权模式。官方文档说明股票与 ETF 日线的 OHLC/成交字段，且说明 qfq 历史随除权变化、hfq 保持既有历史价格尺度。这里把 hfq 作为**连续研究价格**，不是声称其供应商数据永不修订。[股票接口](https://akshare.akfamily.xyz/data/stock/stock.html)、[ETF 接口](https://akshare.akfamily.xyz/data/fund/fund_public.html)。

## 4. 数据事实与快照

| 表 | 关键字段与唯一键 | 约束 |
| --- | --- | --- |
| `research_security` | `symbol`、名称、证券类型、交易所、来源名单、研究启用状态 | 规范代码带交易所后缀，AkShare 适配器仅在边界转换成六位代码；不靠首位猜全体品种。 |
| `research_daily_bar_raw` | (`symbol`,`trade_date`,`source`)；OHLC、量额、源单位、`snapshot_id`、内容哈希 | 未复权交易价格；同日 OHLC 合法且与 hfq 日期配对。 |
| `research_daily_bar_adjusted` | (`symbol`,`trade_date`,`adjust_mode`,`source`)；OHLC、`snapshot_id`、内容哈希 | V1 仅 `HFQ`，不得命名为官方复权因子。 |
| `research_data_snapshot` | 抓取时间、来源/接口、版本、请求区间、返回行数、状态、数据哈希 | raw/hfq 配对抓取只在两边完整且日期匹配后发布为可用快照。 |
| `research_data_revision` | 业务键、旧/新载荷及哈希、首次/再次观察时间、快照 ID | 源端重写时保留旧版本；历史计划引用观察到的数据版本。 |
| `research_trade_calendar` | (`exchange`,`date`,`source`)；开市标记、版本 | 缺失时降低回放能力，不擅自认为周一至周五均开市。 |

可用数据对同一日期必须满足 raw/hfq 均存在、OHLC 有限且 `0 < low <= min(open,close) <= max(open,close) <= high`、`raw_close > 0`、`hfq_close > 0`。同一侧四价使用同一次配对快照；拒绝日期错位与部分成功。量额单位在适配器转换并显式标记。供应商修订时不覆盖旧观察历史；算法结果保存 `data_snapshot_id`、`algorithm_version`、参数、输入哈希和结果。**仅有哈希不足以复现**，必须能读回当时的载荷。

过去多年行情在今天首次下载，不是当年 T 日真实可获得的数据快照。V1 是“按 T 截断输入的回顾性信号回放”，并非严格 point-in-time 历史数据库；页面、报告与验收不得把两者等同。以后每天持续保存快照，才能逐渐形成真实可追溯的时点数据。

## 5. 一份计划核心与价格尺度

V1 的指标与上下偏移全部在**同一 hfq 连续价格空间**计算。后复权随权益事件在不同时段采用不同调整倍数，但把已构造的**整条 hfq 序列**乘同一个正数时，EMA、ATR、偏移倍数和 Z 才具有尺度不变性；不能把“有权益事件”误写成“每天同一复权倍数”。

在回放日 T，用至少 200 根 `date <= T` 的有效 hfq K 线预热。EMA20、Wilder ATR14 的初始化方法、120 样本线性插值分位法和缺口处理须有固定样例。对每个历史 t：

```text
TR_t   = max(H_t-L_t, abs(H_t-C_(t-1)), abs(L_t-C_(t-1)))
Down_t = max(0, C_(t-1)-L_t) / ATR_(t-1)
Up_t   = max(0, H_t-C_(t-1)) / ATR_(t-1)
Z_T    = (C_T-EMA20_T) / ATR_T
```

所有式中价格为 hfq；`ATR_(t-1)` 不读取 t 日高低价。T 日收盘后可计入最近 120 个偏移样本，再生成 T+1 计划。分位 P50/P70/P85 是历史**距离分布**，不是未经条件化的次日命中概率；Z 修正、趋势停用、行情状态和市场制度会使实际触达率偏离 50/30/15，偏离本身不自动证明算法错误。

沿用已审阅的候选价位：`Zc=clip(Z,-1.5,1.5)`；买距离 `max(0.2,Q_down_i+λZc)`，卖距离 `max(0.2,Q_up_i-λZc)`，默认 `λ=0.2`。hfq 候选价按 T 日**同日收盘**比值映射回未复权交易价：

```text
scale_T   = raw_close_T / hfq_close_T
raw_buy_i = hfq_buy_i * scale_T
raw_sell_i= hfq_sell_i * scale_T
```

再按证券 tick 取整；研究模式未核实 tick 时保留未取整的“模型价格”，不得显示为可下单报价。`scale_T` 必须有限且为正，raw/hfq 同日收盘两侧匹配；不把 `raw_close/hfq_close` 命名成供应商复权因子。若 `hfq_close_T` 很小或比值发生无法解释的跳变，停用或排除该评价日。

## 6. 停用原因与权益事件

逐日计划有 `status=ACTIVE|DISABLED` 和多个 `reason_code`，每个原因保存触发日期、测量值、阈值和证据。V1 不实现没有公式的 `TREND_BREAK`。既有保险丝保留：`INSUFFICIENT_HISTORY`（有效日线少于 200 根或 120 样本不足）、`EXTREME_Z`（`abs(Z)>1.5`）、`VOLATILITY_SPIKE`（`ATR14/C` 大于前 60 个有效交易日该比值中位数的 2 倍）。后两个阈值是待回放检验的候选，不是盈利保证。

新增/改写的数据原因：

| reason_code | 研究 V1 定义 |
| --- | --- |
| `RAW_HFQ_MISMATCH` | raw/hfq 日期不配对、任一根 OHLC 非法、同日收盘尺度比无效。 |
| `DATA_MISSING` | 可信日历已知某交易日应有数据，但 raw/hfq 缺一侧；无可信日历时只报告 `CALENDAR_UNVERIFIED`，不臆断停牌。 |
| `POSSIBLE_CORPORATE_ACTION` | `r_t=raw_close_t/hfq_close_t` 与前一交易日比值出现超过报价舍入容差的阶跃；跳变阈值须由 PoC 的原始样本固定，检出后 T 日不生成 T+1 有效计划。该启发式不等于官方权益事件证明，未检出也不保证没有事件。 |
| `CALENDAR_UNVERIFIED` | 无可核验的 T+1 交易日；仅允许“相邻有行情日”研究，不计作完整交易日回放。 |
| `SUSPENSION_UNVERIFIED` | 有可信交易日历但缺失证券日线，且无停牌证据；不写成已停牌。 |
| `NEXT_DAY_CORPORATE_ACTION` | 评价日 T+1 发现权益事件/比值跳变：该日从价位触达率的分子与分母均排除，并单列计数，因为 T 日交易价与次日 raw 价不可直接比较。 |

供应商只给 raw/hfq K 线时，不假装拿到了独立复权因子或正式权益事件数据。后续若接入 Tushare 因子/公告，可以替换证据来源，策略的 `reason_code` 语义仍保持可解释。

## 7. 回放评价口径

逐日生成计划时只能传入 `date <= T` 的 bars；拿到 T+1 raw OHLC **以后**才评价。日 K 评价顺序：先剔除 `DISABLED`、下一日权益事件和不完整评价日；以 `Open_(T+1)` 判定低于 `raw_close_T-1.5*ATR_T*scale_T` 时买侧失效、高于对称上限时卖侧失效；剩余买档用 `Low_(T+1) <= buy_i` 判触达，卖档用 `High_(T+1) >= sell_i` 判触达。开盘失效的一侧不进入相应档位命中率分母；触达只说明日内价格跨过模型价，不说明委托已成交。

按有效评价日分为 `BUY_ONLY`、`SELL_ONLY`、`BOTH_HIT`、`NO_HIT`，另单列 `DISABLED`、缺数据、下一日权益事件与开盘失效；双侧同时触达标 `PATH_AMBIGUOUS`，不假设先买后卖。即便单侧触达，跳空成交价、涨跌停、流动性、费用与 T+1 库存未模拟，也不计算收益。汇总每档明确显示**分子、分母、比例**和被排除原因，按股票/ETF、年份、趋势状态分层，避免总体比例掩盖样本差异。

参数比较只在固定训练区间选参数、后续不重叠验证区间评估；不能用全部年份挑出最好 λ/窗口，再对同一批年份宣称样本外有效。第一轮比较 λ∈{0,0.1,0.2,0.3} 与窗口∈{60,120,180}，但 120/200 根预热是默认基线；参数组合及样本覆盖均落入报告。UI 命名为“信号回放/价位触达”，不称完整交易收益回测。

## 8. 页面与验收

页面先做回放：标的、日期区间、数据版本、参数版本、raw/hfq 数据健康度、各档触达分子/分母、停用与排除分布、逐日价位及次日 OHLC。对 `PATH_AMBIGUOUS`、开盘失效和下一日权益事件有可点开的证据；不展示伪精确收益曲线。股票/ETF 选择复用现有名单并支持手动 ETF；研究不要求填写持仓与资金，等生产建议阶段再接数量计算。

完成标准：

- [ ] 3–5 只真实标的 raw/hfq 配对 PoC 在目标环境通过；本机代理问题被记录但不靠偶然单次成功宣称稳定。
- [ ] 证券代码/日期/四价/源单位规范化；同日期原始与 hfq 成对发布；源端更正可按快照版本还原。
- [ ] `generate_plan(T)` 在测试中无法读取 T+1；`ATR_(t-1)`、T 日入样本、映射与 tick 口径有固定样例。
- [ ] 日期缺失、可疑权益事件、极端 Z、波动尖峰、无日历分别给出可解释代码。
- [ ] 触达率各档分子/分母可由逐日明细复算，跳空侧/权益事件的排除规则一致。
- [ ] `BOTH_HIT` 不计算先后或收益；参数比较有不重叠训练/验证区间。

本设计进入实施计划前仍需用户确认：V1 的交付目标是**研究回放页面**，不是收盘后可直接挂单的建议；AkShare 东方财富是明示的研究数据源，真实连通性和权益事件检测容差由 Phase 0 实测定稿。

## 9. 数据源决策记录（2026-09-18，用户显式批准换源）

**事实（ECS 实测证据，见 `data/research/poc/`）**：东方财富 push2\* 行情 API 族对阿里云 ECS IP 存在激进反爬——首请求 200 后约 2 分钟，同 IP 对全部 push2\* 域（push2his 及 33.push2his 等镜像、http/https、有无 UA/Referer、requests 与 curl）均被 RST，封禁持续 30 分钟以上；同域其他族（datacenter-web / quote / data HTML）不受影响。`ak.stock_zh_a_hist` / `ak.fund_etf_hist_em` 锁定走 push2his，故在 ECS 上 research_daily_sync 不可用（Phase 0 Gate 回退路径）。本机 Windows 因系统代理长期 ProxyError，不能作为反证。

**换源结论（腾讯 fqkline + 新浪事件日历，均已在 ECS 按 PoC 级验收）**：

- **日线**：`web.ifzq.gtimg.cn/appstock/app/fqkline/get`（raw=`''`/hfq=`'hfq'`）。可达性、raw/hfq 日期全量配对、OHLC 合法性、同日双跑哈希稳定、12 连发限流（0.3s 间隔）全部通过。要点：单次上限 640 条按「窗口内最新 count 条」分页，游标逐页前移；行序为 `[date, open, close, high, low, volume]`（close 在第 2 列）；**股票与 ETF 成交量单位均为「手」**（×100 → 股/份，与新浪交叉验证比值恰为 100）。
- **权益事件（关键修正）**：腾讯 hfq 的 `r_t=raw_close/hfq_close` 在**非事件日**有 0.1%~0.4% 的比值抖动（低价高分红股 600900 全历史 1312/1375 天有 >1e-4 的步进，601288 最大 6.8%），噪声带与真实分红事件（0.4%~1%）重叠——**r_t 阶跃启发式在该源不可用**。
- **事件主检测改为新浪 hfq.js**：`finance.sina.com.cn/realstock/company/{sh600900}/hfq.js` 提供股票与 ETF 统一的除权除息日历（行 `{d, f, s?, u?}`，股票行无 `s/u`，跳过 `1900-01-01` 哨兵）；已与东财 datacenter-web 官方分红表及腾讯 r_t 大阶跃日期三向交叉验证一致。
- **r_t 阶跃降级为宽 sanity 兜底**：`corporate_action_tolerance` 由 0.002 调整为 0.05（仅拦真实异常量级跳变），`POSSIBLE_CORPORATE_ACTION` / `NEXT_DAY_CORPORATE_ACTION` 以事件日历命中为主、阶跃兜底为辅，evidence 中 `detected_by` 区分来源。原 0.002 容差标定问题随之消解。
- **交易日历**：不变，仍为新浪 `tool_trade_date_hist_sina`（腾讯适配器复用同一来源与规范化路径）。
- **落库**：新增 `research_corporate_event` 表（唯一键 symbol+event_date+source，迁移 0004）；`config/research.yaml` 增加 `data_source: "tencent"` 显式路由，akshare 路由保留。不静默换源原则不变：换源必须像本节一样留下显式决策记录。

风险与边界照旧：hfq 作为连续研究价格，不宣称供应商数据永不修订；事件日历是官方除权日的观察快照，因子字段仅信息性，不合成官方复权因子。

**部署期补充（2026-09-18 ECS 首跑实录）**：

- **分页尾窗坑**：末日部分页（<640 根）之后游标仍 ≥ history_start，会多发一次「无交易日尾窗」请求，腾讯对该请求回 `'day'` 空键体（hfq 请求也不例外）——曾致严格键检查全标的失败。修正：页行数不足即停翻页；全空响应视为空页而非换口径错误（替身键**有数据**时仍拒绝）。
- **ETF hfq 发布滞后**：收盘后腾讯 ETF（实测 510300）hfq 当日 bar 晚于 raw 发布，raw/hfq 末日差 1 天曾致整标的 REJECTED（丢全历史）。任务层新增**尾部发布滞后截齐**：仅当长侧多出的 bar 全部晚于短侧末日且 ≤5 根时，两侧截齐到共同末日发布（记 warning 日志）；内部空洞/大面积错位不截齐、保持严格拒绝。严格配对原则不变——发布的永远是完全配对的数据，只是末日以两侧较晚发布者为准。
