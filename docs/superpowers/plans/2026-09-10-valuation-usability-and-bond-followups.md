# 估值体验调整与转债后续需求实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优先完成估值入口文案、十年期国债走势叠加、成长100纳入估值名单；另四项转债需求经用户确认后进入实施。

**Architecture:** 沿用 Vue 页面、通用估值图表、FastAPI 服务和统一指数名单；为已有股债时间序列补一个字段，复用已有数据探测与同步通道。不新增数据源、不做数据库结构迁移。

**Tech Stack:** Vue 3、ECharts 5、Vitest、FastAPI、SQLAlchemy、pytest、YAML 与运行时 JSON 名单。

**基线：** 2026-09-10，`main` 当前提交 `8e99fb06f9409a46af66d93de03b07027945c8b8`。本文件是任务规划与交接实施说明，本轮未修改业务代码，未开展线上数据验证。路径除特别标注外均相对 `D:\gitub_codes\web`。

## 1. 范围与推进顺序

本次目标是内部团队日常使用的稳定性与操作效率，作为独立产品改进迭代管理，不代表此前所有架构阶段已经结束。

| 顺序 | 任务 | 优先级 | 交付物 | 预估工作量 |
|---|---|---|---|---|
| A1 | 一级入口显示“股债差｜股债比” | P1 | 页面文案与入口回归 | 0.5 小时 |
| A2 | 叠加同期十年期国债收益率 | P1 | 服务字段、双轴图、测试 | 0.5–1 人日 |
| A3 | 纳入成长100（980080） | P1 | 默认名单、存量环境接入与数据验收 | 0.5 人日，外部数据等待另计 |
| B1 | 转债中位数走势图 | 待确认 | 指标口径及展示位置确认后实施 | 暂不排期 |
| B2 | 筛选与因子合并、自定义收益率统一 | 待确认 | 字段与模板迁移方案确认后实施 | 暂不排期 |
| B3 | 条件模板重命名 | 待确认 | 名称校验、持久化与回归 | 暂不排期 |
| B4 | 细分行业列 | 待确认 | 行业层级、旧项目复用方式确认后实施 | 暂不排期 |

执行顺序为 A1 → A2 → A3 → 集成验收。A3 的数据源探测可在 A2 开发期间提前执行；若探测失败，A1/A2 仍可独立交付。资源安排以一名开发者为主；如使用低消耗 subagent，仅委派独立测试审查，避免多人同时改估值页面。

## 2. 现状核对与目标差距

**裁决：部分达标。** 已有股债两类指标和基础数据通道，但没有满足本轮入口、叠加曲线、名单覆盖目标。

1. 一级入口仅显示“股债差”，内部实际还包含“股债比”，入口命名未表达功能范围。定位：`frontend/src/pages/ValuationDetail.vue:28`、`:100`。
2. 股债历史序列只返回 `date/spread/ratio`，国债收益率仅有顶层最新值，不能直接画同期历史曲线。定位：`backend/services/equity_bond.py:64`、`:96`；当前图表接收单一值序列，见 `frontend/src/components/ValuationChart.vue` 的 `buildOption`。
3. 默认估值配置缺少980080；且已生成的运行时名单优先于默认配置，单改 YAML 不能保证存量环境生效。定位：`config/valuation.yaml`；`backend/services/index_universe.py:154`；测试基线 `test_save_and_load_roundtrip`。

另一个验收约束：名单页面直接遍历估值快照，而非指数配置，见 `frontend/src/pages/ValuationList.vue` 的 `loadData`。因此“配置已添加”不等于“用户已能看到成长100”，必须验证数据同步与列表展示。

## 3. A1：股债入口名称调整

**设计决定：** 一级 Tab 从“股债差”改为“股债差｜股债比”，取消旧的单独命名；保留内部“股债差／股债比”切换。这里的方括号表示标签外观，不作为文案字符。不增加第五个一级 Tab，不同时展示三条不同口径主曲线。

修改：`frontend/src/pages/ValuationDetail.vue`，`METRIC_OPTIONS` 中 `eb` 项的 `label`。

- [ ] 将标签改成 `股债差｜股债比`，保留内部 key `eb`。
- [ ] 保留 `/valuation/:code?tab=eb` 直达行为、默认股债差视图与时间窗口选择。
- [ ] 检查 375px 和桌面宽度，入口不遮挡、不截断，切换区域能操作。
- [ ] 手工验收入口文案；该文案调整不单独新增只断言字符串的测试，相关路由和切换行为纳入 A2 测试。

验收：只有一个股债一级入口；两种指标均能切换；既有链接和 PE/PB/股息率入口可用。

## 4. A2：叠加十年期国债收益率

### 4.1 数据契约

修改 `backend/services/equity_bond.py` 的 `compute_equity_bond`，在已筛选有效数据的循环中把同日原始 `y` 放入每个序列点：

```python
series.append({
    "date": d,
    "spread": round(ep - y, 4),
    "ratio": round(ep / y, 4),
    "cn_10y_bond_yield": y,
})
```

示例：PE=10、国债收益率=2.5 时，返回 `spread=7.5`、`ratio=4.0`、`cn_10y_bond_yield=2.5`。收益率单位为百分数值，不是0.025。

继续通过现有 `/valuation/equity-bond?index_code=...` 返回；不新拉一次国债接口。保留顶层字段、`include_series=False` 的列表响应行为，以及有效样本最少20条的门槛。

日期严格沿用 PE 与国债收益率的有效交集，按升序返回；不从股债差／股债比反推收益率，不前填、不插值。这意味着曲线展示的是当前股债图的可比日期，不承诺覆盖 PE 缺失日的全部国债交易日。若以后需要完整国债单图，另立需求。

### 4.2 前端契约与交互

修改文件：

- `frontend/src/pages/ValuationDetail.vue`：`chartData`、股债图调用、单位说明。
- `frontend/src/components/ValuationChart.vue`：可选对照序列、双轴、图例、tooltip、响应式布局与 watch。

为通用图表增加可选 props：`comparisonValues=[]`、`comparisonLabel=''`、`primaryUnit=''`、`comparisonUnit='%'`。旧调用不用传参，保持当前单图行为。

股债分支使用同一个 `windowed` 数组生成日期、主指标、国债值，保证3/5/10年切换的起止日期一致。旧后端缺少新增字段时，该点按 `null` 处理；全部缺失时隐藏对照轴与图例项，显示“同期国债走势数据暂缺”，主图继续可用。

| 项目 | 股债差模式 | 股债比模式 |
|---|---|---|
| 左轴／主线 | 股债差，百分点 | 股债比，倍 |
| 右轴／对照线 | 十年期国债收益率，% | 十年期国债收益率，% |
| 分位参考线 | 仅股债差样本 | 仅股债比样本 |

- [ ] 注册 `LegendComponent`，图例完整显示主指标和“十年期国债收益率”。
- [ ] 国债线使用另一种颜色及虚线，不带面积填充；缺失点不连线。
- [ ] tooltip 同时显示日期、两条序列名称、数值和单位，缺失值为 `—`，不输出 `undefined/NaN`。
- [ ] 分位和均值继续只取主指标；对照线不进入分位计算、不拥有主指标参考线。
- [ ] 右轴为移动端留出标签空间；图例不覆盖缩放条或图形。
- [ ] watch 纳入全部新 props；从股债返回 PE/PB/股息率时，第二条曲线和右轴被移除。
- [ ] 公式说明统一为 `盈利收益率=100/PE（%）`、`股债差=盈利收益率−国债收益率（百分点）`、`股债比=盈利收益率/国债收益率（倍）`。

### 4.3 测试与完成条件

新增 `tests/test_equity_bond.py`，至少覆盖以下真实计算行为（实现前先写失败用例）：

- `test_series_includes_same_day_bond_yield`：构造至少20个有效日期，检验已知 PE/y 对应的三个数值。
- `test_series_uses_valid_intersection_in_date_order`：输入乱序、缺日期、空值及非正 PE/y，返回有效日期交集；主指标与国债点一一对应。
- `test_bond_overlay_preserves_summary_and_sample_threshold`：少于20个有效样本仍返回 `None`；20个样本的摘要与旧计算口径一致；不请求 series 时不增加历史载荷。

新增 `frontend/tests/unit/ValuationDetail.test.js`：使用模拟接口和路由，验证 `?tab=eb`、spread/ratio 切换、不同窗口同日对齐、国债字段缺失时主指标仍可用。

新增 `frontend/tests/unit/ValuationChart.test.js`：按现有 Vitest 约定模拟 ECharts，断言右轴单位、tooltip双值、null缺口、主指标分位不受国债值影响、移除可选序列后恢复单轴。浏览器补验移动端布局和真实 hover，模拟组件测试不能替代视觉检查。

- [ ] 执行 `python -m pytest tests/test_equity_bond.py`。
- [ ] 在 `frontend` 执行 `pnpm test:unit tests/unit/ValuationDetail.test.js tests/unit/ValuationChart.test.js`。
- [ ] 记录真实浏览器上的指标切换、窗口切换、tooltip 和375px截图。

## 5. A3：成长100（980080）接入

### 5.1 默认环境

修改 `config/valuation.yaml`，沿用现有格式加入：

```yaml
- name: "成长100"
  code: "980080"
  type: "valuation"
  index_detail_url: "https://www.etf.com.cn/api/etf-api-service/index/detail?indexCode=980080"
```

该 URL 是根据已有来源格式构造的候选地址，本轮未验证远端可用性。执行时必须通过既有探测流程验证指数身份及估值能力，不能把 URL 拼接成功当作数据源支持证据。此次只纳入估值 dataset，不自动添加行情任务。

### 5.2 存量环境

复用 `backend/api/routes/data_management.py:99` 的 probe、`:207` 的添加入口和 `:271` 的同步入口。以下路径相对项目 API base，使用现有 API client 的前缀：

1. 读取运行时名单，检查980080是否已存在，记录名称、enabled、dataset、storage_code；记录原始状态以供回滚。
2. `POST /data-management/probe`，正文 `{"code":"980080","source":"efunds"}`。
3. 确认返回指数身份为目标指数、valuation能力可用，再用返回的 `probe_token` 调用 `POST /data-management/indexes`，正文含 `code/name/source/datasets:["valuation"]/probe_token`。
4. 新增路径已经触发初始同步。检查任务日志及同步结果；仅在失败恢复或未完成时使用 `POST /data-management/indexes/980080/sync`，避免重复触发。
5. 校验快照、详情、列表中的980080；历史长度不足时使用现有缺数据展示，不补造分位。

注意：添加接口会写入指定 dataset 的绑定，不能将其视为对所有既有记录都无副作用的 upsert。若980080已正确配置，跳过添加；若已存在不同来源／存储键或明确停用，保留原状态并列为接入冲突，不覆盖用户配置。不要通过删除整个 `index_universe.json` 强制重建名单。

### 5.3 验证

修改 `tests/test_index_universe.py`，覆盖默认名单出现980080、估值任务可枚举它、无重复记录；保留运行时名单优先的既有行为。数据管理相关测试使用模拟来源，不依赖远端可用性。

- [ ] 测试首次环境与已有运行时 JSON 环境两条路径；未涉及指数的停用状态与绑定保持不变。
- [ ] 执行 `python -m pytest tests/test_index_universe.py` 及改动涉及的数据管理测试。
- [ ] 保存探测成功结果、同步任务标识、最新真实数据日期与980080详情页截图。
- [ ] 列表可见成长100，点击进入正确详情，PE/PB等值来自真实数据；不以空记录或0值伪装完成。

若来源不支持980080，本任务记录为“配置方案完成，数据接入未达标”，列出探测错误和缺失能力；不临时替换另一个指数或扩展为新爬虫项目。

## 6. 待用户确认的四项转债需求

以下均不进入本轮业务修改。建议选择用于减少后续讨论成本，尚未视为用户已确认。

### B1：中位数走势

证据：`backend/services/fetchers/cb_index.py` 已将 `mid_price` 映射为 `median_price`；`backend/services/cb_index_store.py` 已入库；`backend/api/routes/cb_index.py:36`、`:42` 已返回价格中位数和溢价率中位数。代码存在不等于历史库字段填充完整，本轮未查询线上历史完整率。

- [ ] 确认指标：建议“转债价格中位数（元）”；另一选择是溢价率中位数（%），不可统称中位数而不注明对象。
- [ ] 确认位置：建议在转债页面增加独立走势图，默认近一年，保留完整历史选择。
- [ ] 确认缺失策略：展示已有样本与无数据提示；如需补历史，仅重用现有转债指数抓取流程。

确认后修改 `frontend/src/pages/Bonds.vue`、`frontend/src/api/index.js`，必要时新增专用图表组件。不要新增同义数据库字段或重复抓取接口。验收选择日期排序、单位、空数据及缺失区间，不把“每个交易日全市场价格中位数”混同于“一段时间指数的中位值”。

### B2：筛选与因子合并、收益率统一

证据：`backend/services/cb_intraday.py:12`、`:98` 使用 `ytm_simple=(到期赎回价−现价)/现价×100`；`backend/services/cb_screen.py:79`、`:335` 与 `backend/services/cb_factors.py:35` 仍涉及 `ytm_rt`。现有自定义值是非年化的到期价格收益率，不能默认等同于集思录原生指标。

- [ ] 确认合并：建议以选债因子为唯一条件编辑器，筛选结果保留在同一工作流程，旧路由重定向。
- [ ] 确认自定义口径：是否沿用上述已有 `ytm_simple` 公式。建议 UI 标注“到期收益率（自定义，非年化）”，帮助说明完整公式。
- [ ] 确认旧模板迁移：建议保留原模板备份；含 `ytm_rt` 的条件展示旧值与新口径差异，经明确确认后转为 `ytm_simple`，不静默替换字段含义。

确认后的实现边界：从 `cb_intraday.py` 提取纯计算函数，两个筛选入口、因子目录、展示、排序及评分共用；赎回价缺失或价格无效返回 null，不退回集思录数值。原始 `ytm_rt` 可保留为来源字段，但不得作为已选自定义因子的隐式替代。先核对 DB 与实时路径均有赎回价输入，再执行统一，不能只改列标题。

主要文件：`backend/services/cb_intraday.py`、`cb_screen.py`、`cb_factors.py`、对应路由；`frontend/src/pages/Factors.vue`、`Bonds.vue`、路由配置。验收用同一输入及同一模板比较两个入口入选结果、排序；另覆盖缺赎回价、0价格、负收益率、模板升级与旧链接。

### B3：模板重命名

证据：`frontend/src/pages/Factors.vue` 当前有新建、复制、保存和模板标签显示逻辑，尚无明确重命名入口；关联 `frontend/src/api/index.js` 的 `saveFactors` 使用现有保存接口。

- [ ] 确认独立修复：建议不等待 B2 合并，作为小任务先做。
- [ ] 确认规则：建议去掉首尾空白，名称长度1–40个字符，同一模板集合中禁止重名。
- [ ] 确认保存交互：建议重命名后标记未保存，沿用页面统一保存；取消重命名不产生修改。

确认后给模板增加重命名入口，更新 name，保持 id、当前选中项、条件和排序不变。后台同步校验名字，避免仅前端校验被绕过。验收空名称、重名、超长、取消、保存失败提示、刷新后持久化；不得用“删除再新建”实现重命名。

### B4：细分行业列，复用 market-daily

已找到实际目录 `D:\gitub_codes\market-daily`。可复用材料：`src/convertible/industry.py`、`config/sw_industry_2021.json`；`src/convertible/three_low/render.py:209` 当前调用 `l1_name_of`，显示一级行业。行业模块还包含详情页三级名称解析，但不要因此对列表每行新发网络请求。

- [ ] 确认“细分行业”层级：建议申万三级；若需要一级行业，应直接命名“行业”。
- [ ] 确认未知代码：建议显示 `—` 并保留原始 `sw_cd` 用于排查，不将一级回退结果冒充三级分类。
- [ ] 确认位置：建议债券名称附近新增列，允许移动端横向滚动；本项只加展示，不自动增加行业筛选条件。

复用方式：先检查现有项目是否已经有行业公共模块；没有则移植旧项目现有静态映射与必要查询函数，记录来源版本和授权归属检查结果。按 `by_code` 的 level 读取三级名称，不重新编写一套行业抓取器；部署不得依赖开发机绝对路径导入相邻仓库。已有 `sw_cd` 进入结果转换层后派生 `industry_name`，实时与 DB 结果使用同一个查询函数。

验收包含：已知三级代码、只有一级／二级的代码、未知代码和空值；两个结果来源显示一致，列表加载不增加逐债网络请求。

## 7. 集成验收与交付检查单

本轮文档完成不代表业务功能完成。下面由实际实施者执行并填入证据；未执行的检查保持未勾选。

- [ ] A1–A3 分别提交，提交说明写明变更、测试及尚未完成的数据验证。
- [ ] 在仓库根目录执行 `python -m pytest`；在 `frontend` 执行 `pnpm test`、`pnpm build`。记录命令、结果与提交号，遇到失败区分本次回归和环境阻塞。
- [ ] 浏览器检查估值列表 → 980080详情 → 股债入口 → 两种指标 → 三种时间范围 → 返回其他估值图的完整流程。
- [ ] 桌面与375px视口检查单位、图例、tooltip、空数据及错误提示。
- [ ] 新后端配旧前端正常；新前端配旧响应时只缺失国债对照线，不导致整个页面失败。
- [ ] 运行时名单接入前后差异只包含本任务预期变化；记录数据备份位置与回滚步骤。
- [ ] 交付变更清单、测试结果、浏览器截图和同步证据；结论按“达标／部分达标／不达标”填写。任一 A 项缺少实测证据，不宣称本轮全部可验收。
- [ ] B1–B4 保持待确认，收到对应选择后补充实施任务和工期，再改代码。

回滚：A1/A2 可独立回退代码，新 series 字段无需数据库回滚。A3 仅撤销本轮新建的名单条目或恢复其原状态；不要覆盖接入后其他人新增的名单内容，不删除历史行情／估值数据。默认 YAML 与运行时名单需分别处理，不能认为回退代码会自动撤销运行时接入。
