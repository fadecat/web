# 转债因子筛选：产品设计与实施计划

> 交付对象：通过外部工具派发的实施 agent。按本文 T0–T8 顺序实施；本任务不要求派生 subagent。如使用 superpowers，采用 executing-plans 逐任务执行。用户已指定本轮作者负责设计和最终收尾，实施由其他模型完成。

> **修订记录（2026-09-10，主审复核）**：设计定稿后经主审与用户确认，做四处修订——① 补漏项：`frontend/tests/unit/Bonds.test.js` 为孤儿测试（import 已删除的 Bonds.vue），必须跟删，否则 vitest 收集失败（§1、T7）；② T0 解释器路径为设计时会话的环境快照，实施者必须以实测为准，不照抄（T0）；③ 用户裁定：保留「转债市场」菜单与页面，「菜单只保留转债选债」仅指筛选入口唯一（T7）；④ §9 派发提示补预存改动的提交边界。以下正文未逐处重标修订记号，以本块为准。
>
> **修订⑤（2026-09-11，用户裁决）**：数据链路评审 `docs/reviews/2026-09-11-jisilu-redeem-data-flow-review.md`（主审已核实代码与数据层证据）证实本方案 §4.2 的 `redeem_icons`（R/O/B/G）条件语义不成立——源站 `R` 图标混合「已公告强赎」与「临近到期」两种业务状态。**强赎相关设计以该评审文档为准**：因子目录改为业务状态枚举 / 触发天数 / 临近到期条件，条件引擎不再读取 `cell.icons`，旧模板 R/O/B 条件迁移改挂 pending 待确认；实施顺序按评审 §5 T1–T5（其 T4 的页面展示部分归前端批次）。本文 §4.2 中 `redeem_icons` 行作废，其余章节继续有效。

**Goal：** 以可重命名的筛选模板组织因子条件，在唯一选债页面完成配置、执行、查看结果；保留现有结果列，新增细分行业、到期赎回价、简单到期收益率。

**Architecture：** 复用 FastAPI 现有 `/api/cb-list` 接口、快照模型、实时抓取入口和评分算法。统一字段补充、模板协议和条件执行，Vue 页面拆分为模板工具栏、因子编辑器、结果表及状态管理。模板继续使用 JSON 文件持久化，本轮不引入通用规则平台。

**Tech Stack：** Python 3.11+、FastAPI、Pydantic、SQLAlchemy；Vue 3、Element Plus、Axios；pytest、Vitest、Vue Test Utils、pnpm。

**状态：** 设计与实施交接稿；不是已完成功能报告。日期：2026-09-10。证据基线：HEAD `36c8c17` 加当前工作区。执行前必须再次记录 HEAD 和 diff；行号可能随实现变化。

---

## 0. 实施者先读这一页

### 0.1 用户明确要求

1. 只保留一个选债入口，移除独立“转债筛选”页面。
2. 筛选通过因子配置实现。不同筛选模板对应不同条件，没有第二份盘中筛选表单。
3. 模板支持重命名，便于管理。
4. 使用旧筛选后端的简单到期收益率，替代集思录 YTM；不改变为真实年化 YTM。
5. 保留当前结果表全部业务列，新增细分行业、到期赎回价、简单到期收益率。
6. 兼顾内部团队日常使用、操作效率、Windows 开发及 ECS 运行。

### 0.2 本文定版决定

- 页面标题和菜单：**转债选债**。路由继续为 `/factors`；`/cb-list` 重定向到它。
- 管理对象名称：**筛选模板**。内部保留稳定 `id`，重命名不创建新模板。
- 指标名称：**简单到期收益率**；字段唯一为 `simple_maturity_yield_pct`。
- 公式：`(redeem_price - price) / price * 100`；未年化，不含票息和税。
- 页面仅配置模板的 `conditions` 和 `strategy_factors`。本轮不增加 OR、嵌套表达式、脚本因子、回测、自动交易。
- 行业既可展示，也可在模板中包含/排除；赎回价及简单到期收益率支持条件筛选、表格排序和因子打分。
- 启用条件之间为 AND；枚举in命中任意所选值即通过，not_in命中任意所选值即排除，not_any与所选状态存在交集即排除。
- 数据源属于本次执行选项，不是模板条件。默认数据库快照，实时行情手动执行。
- 本轮不增加结果历史库。后文“上次结果”只指当前页面内存中的结果。
- 当前评分能力保留。纯筛选时不强制配置评分因子，展示全部符合条件的债。

### 0.3 对前面讨论的澄清

- 不再使用“到期收益空间”等替代名称，统一采用用户要求的简单到期收益率。
- `industry_code` 保存行业代码，不能像早期示意 JSON 那样把“银行”等中文名称放入代码数组。
- “新建”和“复制”分开：新建使用明确列出的安全初始条件，复制继承当前模板草稿。现有前端测试中“新建复制当前模板”需有依据地改为新合同。
- 移除旧 UI 不代表可以删除所有 `/cb-list` 后端接口；行情、黑名单、因子 API 仍在使用该前缀。
- 本轮保留旧 intraday HTTP 入口供兼容，但新页面不得调用它。它的旧响应字段 `ytm_simple` 可作为新公共公式的兼容映射存在，不建立第二套公式。

## 1. 现状审核及证据

**裁决：部分达标。** 当前工作区已删除旧页面入口，但指标、模板、结果和持久化仍未满足本需求。以下是代码证据，不采用提交作者的自述作为结论。

| 编号 | 具体缺陷或偏离 | 证据 | 本轮处理 |
|---|---|---|---|
| R1 | 因子目录仍提供集思录 `ytm_rt` | `backend/services/cb_factors.py:35` | T1/T2 替换因子定义 |
| R2 | DB 赎回数据映射没有 `redeem_price`，结果无法计算新指标 | `backend/api/routes/cb_screen.py:118` | T1/T4 补齐且测 ORM 路径 |
| R3 | 因子过滤遇到字段缺失直接放行；简单收益率下限会被绕过 | `backend/services/cb_screen.py:110` | T2 明确缺失策略 |
| R4 | 现有因子规则只支持数值 lt/gt，不能表达行业条件 | `frontend/src/pages/Factors.vue:353` | T2/T6 类型化目录与条件编辑 |
| R5 | 选中模板仅修改 editingId，旧 previewResult 仍可能显示在新模板下面 | `frontend/src/pages/Factors.vue:261`、`:446` | T5 结果绑定模板和执行签名 |
| R6 | 强赎复选框直接 v-model，没有更新 dirty 的事件 | `frontend/src/pages/Factors.vue:277` | T5 统一 patch 和规范化比较 |
| R7 | 因子结果表缺少全部三列 | `frontend/src/pages/Factors.vue:472` | T7 保留原列并补充 |
| R8 | 模板名仅要求非空，未限制空白、长度和重名 | `backend/api/schemas/cb_screen.py:89` | T3 完整名称校验 |
| R9 | 配置直接覆盖写文件；坏文件读入时静默回默认，可能掩盖原模板丢失 | `backend/services/cb_factors.py:173`、`:184` | T3 备份、原子写入、错误显式返回 |
| R10 | 当前删除旧路由却没有重定向 | `frontend/src/router/index.js:20`；当前 router diff | T7 加旧路径跳转 |

本轮已通过浏览器打开 `http://127.0.0.1:5173/factors`，读取页面结构：全局过滤、排除因子、打分因子初始均折叠；持仓参数、数据源、复制、删除、执行及保存集中在底部。本轮没有保存模板、运行实时抓取或进行像素级多尺寸验收。布局尺寸是下面提出的目标，不能写成已通过视觉验证。

当前工作区已有：`AppLayout.vue`、`router/index.js`、`CbMarket.vue` 修改；`Bonds.vue`、`filterValidation.js`、`filterValidation.test.mjs` 删除。其中市场图表改动与本任务无关，不覆盖、不顺手提交。**另有孤儿测试 `frontend/tests/unit/Bonds.test.js`（import 已删除的 Bonds.vue），必须随本任务删除，否则 vitest 收集失败**（见修订记录①、T7）。行业映射已在 `36c8c17` 入库，复用 `backend/services/industry.py` 和 `backend/data/sw_industry_2021.json`。

## 2. 用户操作与页面布局

### 2.1 每天使用的主流程

```text
打开转债选债
  → 载入默认模板及模板目录
  → 默认模板有效时自动执行一次数据库快照筛选
  → 查看符合条件的全部转债及入选标记
  → 切换模板：载入对应草稿和该模板自己的上次结果
  → 修改条件：结果标记“条件已修改，待重新筛选”
  → 点击“执行筛选”：使用当前草稿；无需先保存
  → 点击“保存模板”：持久化当前模板；不自动重新抓取行情
```

只在首次进入页面自动运行一次 DB。切换模板、修改条件、切换数据源都不自动发起行情请求。初次模板需迁移确认时，不自动运行。

### 2.2 桌面端布局

目标浏览器视口：1440×900、1280×800。既有应用侧栏约220px，避免再加一条常驻模板侧栏。

```text
┌ 应用侧栏 ┬ 转债选债 ───────────────────────────────────────────┐
│         │ 模板 [稳健筛选 ▼] 默认  [新建] [重命名] [更多 ▾]      │
│         │ 草稿未保存  [数据库快照 / 实时行情] [保存模板] [执行筛选]│
│         ├─────────────────┬───────────────────────────────────┤
│         │ 因子配置   [收起]│ 稳健筛选 · 快照 2026-09-09          │
│         │ 筛选条件 (5)    │ 350 全量 / 42 符合 / 10 入选        │
│         │ 价格 80～120    │ [全部符合 42] [入选 10] [排除 308]   │
│         │ 简单收益率 ≥ 0  │ [代码/名称查找]                     │
│         │ 行业 不属于 …   │ 结果表（独立滚动、表头固定）          │
│         │ ＋ 添加条件     │                                   │
│         │ 排序与入选 ▾    │                                   │
│         │ 模板说明 ▾      │                                   │
└─────────┴─────────────────┴───────────────────────────────────┘
```

尺寸约束：内容区域 `min-width:0`；工作区 `grid-template-columns:320px minmax(0,1fr)`，间距16px。工具栏允许两行，执行按钮始终可见。条件面板收起后结果占满可用宽度。结果表自身横向滚动，不把浏览器页面撑宽；不再使用 `max-height=2050`。

筛选条件默认展开；排序与入选默认折叠但展示“启用3个评分因子，目标10只”等摘要。不要把所有配置折叠后只留空白页。

在视口宽度低于1200px时，条件编辑改为抽屉，避免应用侧栏+320px条件区挤压结果。低于768px抽屉占满宽度，底部保留“完成配置 / 执行筛选”，表格横向滚动保留所有业务列。至少验收375×812和768×1024。

### 2.3 模板管理

模板用可搜索下拉框管理，列表项显示名字、启用条件数和“默认”文字标记。多个模板不横向无限堆 Tab。

| 操作 | 交互和保存行为 |
|---|---|
| 新建 | 输入名称，生成新ID，创建本地草稿；提示“尚未保存”；默认条件见第4节 |
| 复制 | 深拷贝当前草稿，生成新ID，命名“原名 副本”，重复则“原名 副本 2”；不共享条件引用 |
| 重命名 | 弹框预填当前名；确认只更新当前草稿 name；回车确认、Esc取消；保存模板后持久化 |
| 删除 | 更多菜单中，确认框显示模板名；至少保留一个模板；删除已保存模板需提交服务器成功后再从本地移除 |
| 设为默认 | 更多菜单；保存 active_id 成功后显示“默认”，失败保持原默认状态 |
| 保存模板 | 仅保存当前草稿；底层接口虽提交整份配置，但其他模板取已保存版本，避免误保存其他草稿 |
| 放弃修改 | 恢复该模板已保存版本；新建未保存模板直接移除；不影响其他草稿 |

名称：trim 后1～40 Unicode字符，后端用 `casefold()` 检查集合内重复；前端做即时检查，后端为准。取消重命名不产生 dirty。复制40字长名称时先截断原名，为“ 副本 N”预留空间。ID用 `crypto.randomUUID()`，不可用名称或仅毫秒时间戳作为ID。

删除默认模板时，确认框注明将以保存列表中的第一项作为新默认；同一次请求提交删除和 active_id 变更。新建/复制的未保存模板不能先设默认，先提示保存。删除其他模板时不能提交当前草稿的隐式修改。

### 2.4 条件编辑

每行：启用开关、字段选择、操作符、类型匹配的输入、单位、删除。选择字段后清空不兼容值，不能把旧字段值继续提交。

- 数值：`≥`、`≤`、区间（两端都包含）。需兼容旧安全天数严格 `>`，支持 `gt`。
- 单值枚举：属于、不属于；使用可搜索多选，不允许启用时选空集合。
- 多状态集合：不包含任一所选状态，例如强赎 R/O/B。
- 布尔：是/否，例如“正股ST = 否”。
- 个券代码：不属于，输入6位代码或带交易所后缀，统一归一为6位 code。
- 不配置评级条件表示不限；`NONE` 表示无评级。未知非空评级可保留和查询，不裁剪为固定七档。
- 允许同一字段多个条件（AND）；区间优先用一行表达，不以“字段已经使用”禁止合理组合。
- 条件摘要示例：“价格80～120元 · 简单到期收益率≥0% · 评级AA+/AA”。不得把所有因子都命名为“排除”。

所有控件都走 `patchTemplate(id, patch)`，不得直接修改 props 或靠个别 `@change` 设置全页 dirty。删除条件只改变草稿，执行后才改变筛选结果。

### 2.5 结果区

当前源码14个业务列全部保留，新增三列后共17列，顺序如下：

| 顺序 | 列名 | 字段 | 规则 |
|---|---|---|---|
| 1 | 排名 | rank | 无评分时为默认排序序号，不暗示评分 |
| 2 | 代码 | code | 与名称一起固定左侧，窄屏只固定名称 |
| 3 | 名称 | name | 110px左右，完整名可提示 |
| 4 | 细分行业 | industry_name | 130px；降级映射必须说明层级 |
| 5 | 评级 | rating | 无评级显示“无评级” |
| 6 | 当前价格 | price | 元，2位小数 |
| 7 | 到期赎回价 | redeem_price | 元，2位小数 |
| 8 | 简单到期收益率 | simple_maturity_yield_pct | %，2位小数，表头说明公式、非年化、不含票息和税 |
| 9 | 双低 | dblow | 沿用现有业务精度 |
| 10 | 溢价率 | premium_rt | % |
| 11 | 剩余规模 | curr_iss_amt | 亿 |
| 12 | 转股价值 | convert_value | 沿用现有精度 |
| 13 | 剩余年限 | year_left | 年 |
| 14 | 市净率 | pb | 倍 |
| 15 | 强赎状态 | redeem | 沿用已有人类可读状态 |
| 16 | 得分 | total_score | 无评分因子显示“—” |
| 17 | 入选状态 | selected/holdable | 入选/容差保留/其余；纯筛选显示“符合条件” |

实施时以字段清单逐一确认，不能为了简化表格删掉市净率、评级或入选状态等旧列。

默认展示全部符合条件的债；切换“入选”只做结果内查看，不重新计算。纯筛选模板不显示“入选”Tab。排除明细保留代码、名称、价格、行业、简单到期收益率和完整原因。

表格点列头只改变展示排序，不重算评分、不修改 selected；恢复默认按 rank 升序。结果内查找代码/名称是视图操作，标注“当前结果内查找”，不创建第二套金融条件筛选器。空值统一排最后，不能当0。表格采用20/50/100条客户端分页，默认50条；切换视图或搜索时回第一页，翻页和排序不重新请求行情。

收益率负值正常显示；0显示 `0.00%`，null显示 `—`；不称“保本”或“保证收益”。保留所有列，移动端不能删掉新增字段以通过布局检查。

## 3. 指标和行业数据协议

### 3.1 公共计算函数

新增 `backend/services/cb_metrics.py`，对外函数确定为：

```python
import math

def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).strip().replace(',', ''))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None

def simple_maturity_yield_pct(price, redeem_price):
    p, r = finite_number(price), finite_number(redeem_price)
    if p is None or r is None or p <= 0 or r <= 0:
        return None
    return (r - p) / p * 100
```

计算、过滤、排序均用未格式化数值；只在前端显示时保留2位小数。不能先四舍五入再比较阈值。这里的赎回价只能来自 `redeem_price`，不得用 `force_redeem_price`（强赎触发价）替代。

用 `enrich_cell(cell, redeem_cell)` 返回新dict，不能原地污染抓取结果；把 `redeem_price` 和新收益率写入 cell 后再进行条件过滤和评分。结果DTO从同一 cell 取值，不能到 `_make()` 才计算。

旧 `cb_intraday._live_row()` 可调用公共函数后按原合同返回 `ytm_simple` 的3位精度。其旧字段仅供旧HTTP接口使用，新目录、新模板、新页面只使用 `simple_maturity_yield_pct`。

### 3.2 数据库与实时路径

- DB：取最新转债快照交易日 D；赎回快照取不晚于 D 的最近一天 R。必须把 `redeem_price` 纳入映射。
- R不存在或R≠D时：本轮采用严格同日策略，相关赎回字段标记缺失，不拿跨日强赎计数冒充同日数据。返回 D、R 和警告；前端可提示改用实时行情。
- 实时：复用 `fetch_live_snapshot()`；不新增逐债抓取，不写入日频行情快照。
- 当前 gateway 以空赎回列表表达降级，无法区分成功空集与失败。当前能力下统一提示“赎回数据不可用”，不得虚构失败原因。
- 有记录但部分债缺赎回价：单债 null、计数警告；相关筛选条件排除该债。
- 全市场收益率输入均无效且模板使用收益率/赎回价条件或评分：HTTP 503 `REDEEM_DATA_UNAVAILABLE`，不要返回一个假正常的空结果。
- 与赎回数据无关的模板可以继续执行，但返回质量警告。全量行情抓取失败返回502。DB没有转债快照返回200、`meta.data_status=no_snapshot`，区别于“0只符合条件”。

### 3.3 行业

复用已存在的静态映射。扩展 `industry_info_of(sw_cd)` 返回：

```json
{"industry_code":"610101","industry_name":"水泥","industry_level":2,"industry_mapped_code":"610100","industry_is_fallback":true}
```

上例为现有映射模块测试覆盖的三级码缺失、二级码回退案例。表格显示“水泥”并提示“使用申万二级映射”；不得把二级名称无标识地称为精确三级。原 `industry_name_of` 保持兼容。

筛选永远匹配原始 `industry_code`。选项文字附原始代码和回退层级，防止多个原始代码回退同名时误选。无代码或原始码无法映射时显示 `—`；目录仍保留未知原始码选项（“未映射 999999”），空码使用 `NONE`。不新增行业层级联动树，不逐债联网补全。

## 4. 模板 V3 与因子目录

### 4.1 一份规范模板

配置版本由当前2升级为3。保留现有字段 `id/name/description/target_count/hold_tolerance/strategy_factors`，把其他模板过滤项统一迁入 `conditions`。配置顶层version=3；执行请求顶层schema_version=3，二者是不同请求的版本标识，不在每个保存模板内重复写版本。

```json
{
  "version": 3,
  "revision": "opaque-server-revision",
  "active_id": "stable",
  "templates": [{
    "id": "stable",
    "name": "稳健筛选",
    "description": "示例条件，不是投资建议或内置推荐参数",
    "conditions": [
      {"id":"c1","field":"price","op":"between","value":[80,120],"enabled":true,"missing":"exclude"},
      {"id":"c2","field":"simple_maturity_yield_pct","op":"gte","value":0,"enabled":true,"missing":"exclude"},
      {"id":"c3","field":"redeem_price","op":"gte","value":103,"enabled":true,"missing":"exclude"},
      {"id":"c4","field":"industry_code","op":"not_in","value":["760201"],"enabled":true,"missing":"exclude"},
      {"id":"c5","field":"rating_cd","op":"in","value":["AA+","AA"],"enabled":true,"missing":"exclude"}
    ],
    "strategy_factors": [
      {"field":"dblow","ascending":true,"weight":1,"enabled":true},
      {"field":"simple_maturity_yield_pct","ascending":false,"weight":1,"enabled":true}
    ],
    "target_count":10,
    "hold_tolerance":0,
    "migration_issues":[]
  }]
}
```

`conditions=[]` 表示没有模板过滤条件；用户全局黑名单仍生效。空/停用的 strategy_factors 表示纯筛选：返回全部符合条件债，`selection_mode=filter_only`，`selected=false`、`holdable=false`、`selected_count=0`、`buffer_count=0`，UI不解读为零只符合。

启用评分时保持当前排名加权算法（不是归一化权重百分比）：每个有效值按方向排名，第1得 N×weight、第N得1×weight，缺失得0；总分降序，双低升序，最终code升序。同一因子相同值按code稳定打破平局，明确这是稳定性修复，测试记录同值排序与旧实现可能不同。纯筛选默认按双低升序、code升序，双低null最后；rank从1连续编号，top_n和keep_n返回0，total_score返回null。

target_count范围1～50、hold_tolerance范围0～20。它们只用于评分模式标记，不截断rows，不代表真实持仓和自动卖出。禁止沿用“跌出即卖”之类暗示实际交易的页面文案。

### 4.2 类型化目录

继续返回目录数组，每项新增 `type/operators/filterable/scorable/description`。以服务端目录为唯一事实源；前端不复制独立可选字段名单。

| 字段 | 类型 | 运算符 | 可评分 | 值/来源 |
|---|---|---|---|---|
| 原有数值字段除ytm_rt | number | gte/lte/gt/between | 是 | 当前目录，价格标题改“当前价格” |
| simple_maturity_yield_pct | number | gte/lte/gt/between | 是 | 公共公式，允许负数 |
| redeem_price | number | gte/lte/gt/between | 是 | 到期赎回价，正数 |
| industry_code | enum | in/not_in | 否 | 原始行业码，含NONE |
| rating_cd | enum | in/not_in | 否 | 评级目录，含NONE和未知评级 |
| redeem_icons | set | not_any | 否 | R/O/B/G |
| redeem_remain_days | number | gte/lte/gt/between | 否 | 距强赎触发天数 |
| listed_days | number | gte/lte/gt/between | 否 | 以DB交易日/实时时区日期计算，不统一用date.today |
| stock_is_st | boolean | eq | 否 | 复用当前ST识别定义 |
| code | enum | not_in | 否 | 转债代码归一为6位 |

新建模板默认包含 `redeem_icons not_any [R,O,B]` 和 `stock_is_st eq false`，明确显示两项初始条件，允许停用。新建评级不限，无数值阈值，无评分，target_count=10、hold_tolerance=0备用。现存三低模板通过迁移保留原条件和评分，不替换为这套新建默认值。

旧安全天数判断仅在0≤remain≤阈值时排除，因此迁移时必须保留负值通过这一已存在行为。给条件增加可选字段 `negative="compare"|"include"`，默认compare；只允许redeem_remain_days迁移条件使用include。它表示“兼容旧规则：负值不参与安全天数判断”，不推断负值在供应商处的业务含义。缺失仍按missing处理。新建安全天数条件使用compare，不能把-1自动当普通缺失。

### 4.3 输入强校验

保存和执行必须使用同一条件/评分 Pydantic schema，执行前先校验再抓取。

- 拒绝未知字段、非法运算符、错误类型、bool冒充数值、NaN/Infinity。
- 数值区间两端有限且lo≤hi；价格、规模、年限等原来非负限制保留。
- 每个条件必须有稳定唯一ID；开关必须为bool；字段和运算符必须来自目录。
- enabled=false的条件也要求结构有效；迁移废弃项放migration_issues，不伪装为非法停用条件。
- 枚举不能传字符串替代数组；启用的in/not_in/not_any不能为空；去空白后拒绝空项和重复项。
- 评分只能使用scorable字段，weight为有限正数；同字段不能重复评分，避免覆盖旧score_key。
- 模板id唯一，active_id必须存在；未知顶层业务字段在V3拒绝，不再extra=allow放行。
- source只接受db/live，拼写错误返回422，不能默默回退DB。
- errors至少包含 `detail.code/message/path`；前端兼容旧字符串detail，新的path如 `templates.0.conditions.2.value`。

目录需为每个数字字段显式记录允许负数与否，不能仅依据type=number判断。简单到期收益率、溢价率、涨跌幅允许负数；价格、规模、价值、年限、成交额不允许负阈值。Pydantic使用strict数值/bool校验和finite校验，不能让默认类型强转把true变为1。

### 4.4 缺失规则

每条条件有 `missing=exclude|include`。新条件默认exclude；旧模板迁移为include以保持旧缺失放行行为，UI显示“缺失放行（旧模板兼容）”。简单收益率/赎回价的新条件必须exclude，不能配置include。

对于空评级/空行业，先归一为枚举NONE，再匹配；其他数值未知才应用missing。不把未知行业名称当有效行业码。启用收益率评分但缺少该债数据时得0分，并计入质量提示；是否完全排除由对应条件决定。

模板条件失败应返回结构化原因 `rule_id/field/actual/op/expected/reason_code` 和人类可读字符串。示例：“简单到期收益率缺失：无到期赎回价”；不能只写“到期收益率”。

## 5. 迁移与兼容

### 5.1 旧模板转换表

| V2 内容 | V3 内容 | 注意 |
|---|---|---|
| exclusion rule lt X | 同字段 gte X | 相等边界保留；missing=include |
| exclusion rule gt X | 同字段 lte X | 同上 |
| ratings非空 | rating_cd in数组 | 空数组不生成条件；未知评级保留 |
| excluded_redeem_icons非空 | redeem_icons not_any数组 | 缺省按旧逻辑R/O/B；显式[]不限 |
| redeem_safe_days ≥ 0 | redeem_remain_days gt阈值 | missing=include、negative=include，保留原来只排除0～阈值的逻辑 |
| min_listing_days > 0 | listed_days gte阈值 | missing=include；0不生成 |
| excluded_bond_codes | code not_in数组 | 归一6位，名称仅展示 |
| 后端固定ST排除 | stock_is_st eq false | 每个旧模板显式生成，初始行为不放宽 |
| strategy_factors | 原字段列表 | ytm_rt按特殊迁移处理 |

已有V1 `excluded_ratings` 先运行原来的读文件归一，再做V2→V3。新POST携带V1废弃字段仍422。V2 POST先按现有模型校验，使缺省ratings仍是[]，不能被读文件的七档回退污染。

旧模板未知字段、未知操作符或不合法阈值必须产生可见迁移问题，不能丢掉后无声执行。重复名字按文件顺序追加“（迁移2）”并展示迁移提示；缺少ID或重复ID同样生成稳定迁移ID并记提示，迁移重复执行须得到同一ID。

### 5.2 ytm_rt 特殊迁移

简单收益率不是集思录年化口径，不能原阈值无声平移。

```json
{
  "id":"legacy-yield-c1",
  "kind":"replaced_metric",
  "origin":"condition",
  "original":{"field":"ytm_rt","op":"lt","threshold":2},
  "replacement_field":"simple_maturity_yield_pct",
  "status":"pending"
}
```

旧条目从可执行conditions/strategy_factors移出，保存在 `migration_issues`。页面显示“旧集思录收益率条件需要确认”，提供“设置简单收益率条件”和“删除此旧条件”两种处理动作。前者输入新阈值/方向/权重后创建新条目，后者仅移除该旧约束；两者都把问题标记resolved。

存在任何pending问题，后端执行返回409 `TEMPLATE_REVIEW_REQUIRED`，GET active也一致。允许保存包含pending的模板，供分步处理；不能因保存而自动resolved。旧停用ytm条目只保留为不阻塞的迁移记录，不能恢复执行。这里的确认是产品内一次性模板升级步骤，不是开发过程反复询问用户。

### 5.3 文件可靠性及并发

仍用 `cb_factors.FACTORS_PATH`，现有测试通过monkeypatch重定向，不新建固定绝对路径。

1. GET只在内存迁移，不覆盖旧文件。
2. revision为当前磁盘原始字节SHA256，无文件时为固定 `missing`；返回给客户端。revision不写入配置正文，响应再补充。
3. V3保存必须携带revision。单进程 `threading.RLock` 内重新读文件比对，不一致返回409 `CONFIG_CONFLICT`，不覆盖。
4. 首次V3落盘前，以独占创建保存旧文件完整备份 `factors.pre-v3.<hash前12位>.json`；已存在则验证内容相同。
5. 同目录唯一临时文件写UTF-8、flush、fsync、close，再 `os.replace` 原子替换。Windows上替换前必须关闭句柄，失败保留原件并清理本次临时文件。
6. 文件不存在才用默认配置；存在但JSON损坏/版本不支持返回503 `CONFIG_UNREADABLE`，不能显示默认模板并允许覆盖损坏文件。
7. V2 POST仅在磁盘仍为V1/V2或不存在时兼容；磁盘升级V3后，缺少revision的旧客户端写入返回409 `CLIENT_UPGRADE_REQUIRED`。V2执行请求可内存转换，含旧YTM时按上节阻塞。

本轮JSON存储并发保障边界为一个后端进程内的多标签/多用户。执行前核对ECS service启动命令。若已经多worker，不能以RLock宣称跨进程可靠：必须增加操作系统跨进程文件锁及并发测试，或交接中列为部署阻塞；不得擅自调整线上worker配置。

冲突时前端保留本地草稿，提供重新加载服务器版本和复制本地配置文本；不得自动覆盖重试。重新加载前说明将放弃本地草稿。

## 6. 后端职责与接口

### 6.1 文件边界

| 文件 | 操作 | 唯一职责 |
|---|---|---|
| backend/services/cb_metrics.py | 新建 | 有限数解析、公式、统一cell补充 |
| backend/services/cb_conditions.py | 新建 | 已校验条件求值和结构化失败原因，纯函数 |
| backend/services/cb_template_migration.py | 新建 | V1/V2归一后到V3的确定性转换，不读写磁盘 |
| backend/services/cb_factors.py | 修改 | 目录、默认模板、读写、revision、备份；保留旧代码归一助手 |
| backend/api/schemas/cb_screen.py | 修改 | V3条件/模板/保存/执行模型，保留旧HTTP校验 |
| backend/services/cb_screen.py | 修改 | 现有ORM转cell、统一管线、评分、结果；不再在新路径执行旧硬编码条件 |
| backend/services/industry.py | 修改 | 复用映射，增加行业信息和目录，不联网 |
| backend/api/routes/cb_screen.py | 修改 | 请求校验、数据加载、黑名单、元数据和错误映射 |
| backend/services/cb_intraday.py | 小改 | 旧入口公式改调公共函数，旧返回保持兼容 |

沿用函数名 `screen_bonds`、`screen_bonds_live`；新增可选keyword-only的 `as_of_date`、`blacklist_ids`，业务输入相同则结果必须相同。黑名单在所有新模板执行入口生效，并作为排除原因返回；不要只从最终rows删除而破坏计数。

管线固定为：输入规范化 → 字段补充 → 全局黑名单及模板条件 → 评分 → 入选标记 → DTO。纯函数不读数据库、不联网、不按系统当天暗中计算上市时间。

### 6.2 路由合同

| 路由（含/api） | 合同 |
|---|---|
| GET /api/cb-list/factors/catalog | 类型化目录数组，移除ytm_rt，返回新指标 |
| GET /api/cb-list/factors/ratings | 保留已有评级目录合同 |
| GET /api/cb-list/factors/industries | 新增；静态映射+本地快照发现原始码，返回code/name/level/fallback，不触网 |
| GET /api/cb-list/factors | 返回V3配置、revision、迁移问题；不改磁盘 |
| POST /api/cb-list/factors | 版本化整份保存，校验后原子写；响应仍为 `{ok:true,data:...}` |
| POST /api/cb-list/screen | 保留平铺模板+source请求形状；模板携带schema_version=3；不要求先保存 |
| GET /api/cb-list/screen/active | 读取已保存默认模板，以DB执行同一新引擎 |
| GET /api/cb-list/screen/intraday | 旧兼容入口保留，新页面零调用 |
| /api/cb-list/blacklist* | 保留现有接口，新页面恢复管理入口 |

industry目录没列出的实时新代码：返回结果行的名称/原始码后可合并为本页选项，保存已观察到的合法原始码不因旧目录缺失被拒绝。

新执行响应保留 `total_all/total_filtered/total_excluded/top_n/keep_n/selected_count/buffer_count/rows/excluded_rows/source`，增加：

```json
{
  "selection_mode":"scored",
  "template_id":"stable",
  "template_name":"稳健筛选",
  "meta":{
    "data_status":"ready",
    "trade_date":"2026-09-09",
    "redeem_trade_date":"2026-09-09",
    "fetched_at":"2026-09-10T21:00:00+08:00",
    "quote_time":null,
    "redeem_loaded":true,
    "missing_yield_count":4,
    "blacklisted_count":2,
    "warnings":[]
  }
}
```

实时无法确认交易日期则trade_date=null，仅显示抓取时间及来源提供的行情时间；不要用当天日期冒充交易日。客户端拿到有值时间后明确标签“抓取于/行情时间”。

`total_all = total_filtered + total_excluded`；所有排除债只计一次，原因可多个；blacklisted_count是全量输入中命中黑名单的债数，与其他排除原因可能重叠，不再次从total减去。

### 6.3 全局黑名单和模板排除

已有黑名单是团队共享系统规则，模板中的 `code not_in` 仅属于该模板。两个概念不得混在同一个编辑输入。

结果区更多菜单提供“全局黑名单”抽屉，复用现有list/add/remove API。显示“影响所有模板”，保留查看、添加、移除和原因。修改成功使所有内存结果标记过期，不自动实时抓取。全局黑名单为空也需明确展示0。

## 7. 前端组件与状态机

### 7.1 文件结构

```text
frontend/src/pages/Factors.vue                       页面编排与响应式布局
frontend/src/components/cb-selection/TemplateToolbar.vue
frontend/src/components/cb-selection/FactorEditor.vue
frontend/src/components/cb-selection/SelectionResults.vue
frontend/src/components/cb-selection/BlacklistDrawer.vue
frontend/src/composables/useSelectionWorkspace.js    草稿、保存、执行及过期保护
frontend/src/utils/selectionTemplate.js              规范化、签名、名称和输入辅助
frontend/src/api/index.js                           沿用Axios实例和请求封装
```

已有Factor页面约700行，本次不要再把新逻辑全部堆进去。不引入新全局store；页面状态由composable持有，组件仅props/emits。不新增完整设计系统。

### 7.2 必要状态

```js
savedConfig       // 最新服务器配置及revision
draftsById        // 每个模板独立深拷贝草稿
editingId         // 当前编辑ID
source            // db/live
resultsById       // 每模板最近一次成功结果与执行签名
errorsById        // 每模板最新请求错误
latestRequestId   // 全局单调递增，保护晚到响应
blacklistRevision // 本页黑名单成功修改次数，计入运行签名
```

`dirty(id)`：模板规范化后与其saved版本比较；新建必dirty。名字/说明也计入dirty，但不计入运行签名。运行签名包含模板ID、启用conditions（含missing）、启用strategy_factors、target_count、hold_tolerance、source、blacklistRevision。

条件的negative兼容策略、迁移pending状态也参与签名/可执行性判断，不能只签field/op/value。执行结果存储时附带当时已保存/未保存标志，用户保存后可另显示“当前配置已保存”，不能篡改执行当时信息。

规范化函数显式选择字段，不能使用整个Vue响应对象序列化；数值保持数值，键排序稳定。纯筛选模式target等无效参数可统一从运行签名剔除。重命名不使数值结果过期，但结果头保留“执行时模板名”，避免伪造历史上下文。

### 7.3 执行规则

```js
async function runSelection() {
  // 实现时补入现有ref及API依赖；此代码定义请求关联算法。
  const id = editingId.value;
  const payload = toExecutableTemplate(draftsById.value[id]);
  const runSource = source.value;
  const signature = executionSignature(payload, runSource, blacklistRevision.value);
  const requestId = ++latestRequestId.value;
  pending.value = { id, requestId, signature };
  try {
    const data = await screenBonds(payload, runSource);
    if (requestId !== latestRequestId.value) return;
    resultsById.value[id] = { data, signature, templateName: payload.name };
    errorsById.value[id] = null;
  } catch (error) {
    if (requestId === latestRequestId.value) errorsById.value[id] = readableError(error);
  } finally {
    if (requestId === latestRequestId.value) pending.value = null;
  }
}
```

上述所用工具函数由 `selectionTemplate.js` 定义：`toExecutableTemplate`返回schema_version=3和规范业务字段；`executionSignature`返回稳定JSON字符串；`readableError`返回兼容detail对象/字符串的用户文案。pending新增为composable状态，页面卸载递增latestRequestId使旧请求作废。

仅当前正在请求的执行按钮禁用，仍允许改条件。修改后响应回来仍可存入原模板结果，但签名不同显示过期；切换模板不会把A结果当B结果。失败不清空上次成功结果，明确标注“上次成功结果”，错误信息常驻结果区，不只toast。

### 7.4 保存规则

保存时捕获当前草稿快照D、服务器revision R；以savedConfig的其他模板+D形成请求。禁止使用整个draftsById提交。

成功后更新savedConfig。若用户在请求期间继续修改，保留较新的草稿，不拿响应覆盖；通过当前草稿与D比较决定dirty是否仍为true。rename/删除/默认等服务器写操作串行，避免自身制造revision冲突；运行与保存可并行。

离开页面时有任意dirty，路由离开保护提示保存/放弃/取消；“保存”按模板逐项串行，任一失败则停留。刷新/关标签只使用beforeunload原生提示，不承诺异步保存；禁止把配置或结果静默写入localStorage。新草稿本次页面生命周期外不持久化。

## 8. 实施任务（按顺序，小批提交）

### T0：记录基线与执行环境

文件：交付记录 `docs/superpowers/plans/2026-09-10-cb-factor-selection-delivery.md`（实施者新建）。

**先固定解释器，再运行下面所有测试。** 本次现场检查：PATH中的python是 `C:\Python27\python.exe`（2.7），python3是3.7.2，项目 `.venv` 不存在；不能直接调用 PATH 中的 Python 跑 pytest。已确认本机Codex附带Python为3.12.14，可用它建立项目虚拟环境；此路径仅是本机开发环境，不写进业务代码或ECS配置。

> **修订记录②（主审复核）**：上一段的解释器探测结果与 `CbBootstrapPython` 路径是**设计时会话的环境快照**，不同会话/终端实测结果不同（例：主审会话 PATH python 即为 conda 3.13、依赖齐全，可直跑全量 pytest，无需建 .venv）。实施者必须先实测当前可用解释器：存在 Python 3.11+ 且依赖可用的解释器就直接用它；确需建 .venv 时才使用 bootstrap 流程。不要照抄 `CbBootstrapPython` 字面路径，也不要在已有合格解释器时重复创建 .venv。

```powershell
# 工作目录必须是仓库根目录；已存在合格的项目虚拟环境时跳过创建。
$CbBootstrapPython = 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $CbBootstrapPython -c 'import sys; assert sys.version_info >= (3, 11)'
if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11+' }
if (-not (Test-Path .venv/Scripts/python.exe)) {
    & $CbBootstrapPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '项目虚拟环境创建失败' }
}
$CbPython = (Resolve-Path .venv/Scripts/python.exe).Path
& $CbPython -c 'import sys; assert sys.version_info >= (3, 11)'
if ($LASTEXITCODE -ne 0) { throw '已有虚拟环境版本不足，不要直接删除，改用合格环境' }
& $CbPython -m pip install -r requirements.txt -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，记录原因' }
```

如果外部工具无法访问该附带运行时，使用其可用的Python 3.11+替换CbBootstrapPython；版本检查与项目虚拟环境保持一致。网络/权限失败按环境要求处理，不通过删除测试或修改业务代码绕过。每次新终端重新设置绝对路径CbPython；后续命令中的变量均指该项目解释器。本轮文档作者没有安装依赖或创建虚拟环境。

- [ ] 执行 `git status --short`、`git diff --stat`、`git diff --cached --stat`、`git log -5 --oneline`，记录基线。
- [ ] 阅读本文0～7节及任务所列源文件。禁止先全库重写或只读最后一页。
- [ ] 运行现有相关测试并记录真实结果，不能称为新功能已通过。
- [ ] 核对当前Python、pnpm和ECS进程模型；无法读取ECS时记录“未核实”，本地实施继续。

```powershell
& $CbPython -m pytest tests/test_cb_factors_contract.py tests/test_cb_screen_contract.py tests/test_industry_lookup.py tests/test_queries.py -q
```

前端命令工作目录为 `frontend`：

```powershell
pnpm test:unit tests/unit/Factors.test.js
```

完成标准：基线文件包含HEAD、预存改动、命令、通过/失败数、环境限制。已暂存删除文件不可被后续 `git add .` 顺手合并；按文件/必要时按hunk提交。

### T1：公共指标与行业补充

修改/新建：第6.1节的 `cb_metrics.py`、`industry.py`、`cb_intraday.py`；测试 `tests/test_cb_selection_metrics.py`、`tests/test_industry_lookup.py`。

- [ ] 先写以下公式反例测试，运行应在缺少函数时失败。
- [ ] 按3.1实现有限值解析和公共公式，enrich_cell先补指标再返回新对象。
- [ ] 增加industry_info_of，保持industry_name_of旧行为。
- [ ] 旧intraday调用同一公式，保留旧响应精度。
- [ ] 测试通过后提交本任务文件。

```python
import pytest
from backend.services.cb_metrics import simple_maturity_yield_pct

@pytest.mark.parametrize('price,redeem,expected', [(100,110,10),(125,110,-12),(110,110,0)])
def test_simple_yield_formula(price, redeem, expected):
    assert simple_maturity_yield_pct(price, redeem) == pytest.approx(expected)

@pytest.mark.parametrize('price,redeem', [(0,110),(-1,110),(100,None),(True,110),(100,float('nan')),(100,float('inf')),(100,0)])
def test_invalid_inputs_are_null(price, redeem):
    assert simple_maturity_yield_pct(price, redeem) is None
```

追加明确用例：输入cell内ytm_rt=99，结果仍由100/110计算为10；force_redeem_price=130但redeem_price缺失则null；enrich不修改原cell；行业三级、二级、回退、未知、空码均覆盖。

验证：`& $CbPython -m pytest tests/test_cb_selection_metrics.py tests/test_industry_lookup.py tests/test_cb_screen_contract.py -q`。

### T2：V3目录、强校验、条件引擎

修改：`cb_factors.py`目录、`backend/api/schemas/cb_screen.py`；新建 `cb_conditions.py`、`tests/test_cb_selection_conditions.py`。

- [ ] 定义第4节全部目录字段及类型，移除可选ytm_rt。
- [ ] 实现ConditionModel、ScoringFactorModel、SelectionTemplateModel、SelectionRunModel、SelectionConfigModel；字段仅采用本文命名。
- [ ] 新增 `evaluate_conditions(cell, conditions)` 返回原因列表；无原因通过；不读取DB。
- [ ] 为第4.3所有非法输入编写参数化测试；验证不触发抓取。
- [ ] 实现正向条件和missing规则，不在新引擎重复运行旧ST/评级硬编码过滤。
- [ ] 保留旧HTTP模型及原验证语义，回归现有非法输入测试。

核心测试输入/期望：

| 测试名 | 输入 | 期望 |
|---|---|---|
| test_between_is_inclusive | price=80/120，between[80,120] | 都通过；79.99/120.01失败 |
| test_yield_condition_uses_unrounded_value | 收益率0.004，gte0.005 | 失败，即使显示都是0.00% |
| test_missing_new_yield_excluded | null，gte0 | reason=missing |
| test_legacy_missing_include | null，原有price条件missing=include | 通过并可说明兼容策略 |
| test_enum_codes_not_names | industry_code=760201，in[760201] | 通过；中文名称提交422 |
| test_no_rating_filter_is_unrestricted | conditions无rating | AAA/BB+/NONE都通过 |
| test_empty_set_condition_rejected | enabled=true，in[] | 422 |
| test_multi_conditions_and | 价格通过但评级失败 | 排除且指出评级 |
| test_invalid_rule_prevents_fetch | 未知字段/NaN/weight0 | 422、抓取调用0次 |

验证：`& $CbPython -m pytest tests/test_cb_selection_conditions.py tests/test_cb_screen_contract.py -q`。

### T3：模板迁移、重命名与文件保存

修改：`cb_factors.py`、schema、factors GET/POST；新增 `cb_template_migration.py`、`tests/test_cb_selection_templates.py`。

- [ ] 实现 `migrate_config_to_v3(config)`，输入深拷贝，输出确定且幂等，不进行磁盘IO。
- [ ] 实现第5节每条迁移映射、pending问题和V1评级前置归一。
- [ ] 实现名字trim/长度/重名、ID唯一和active引用校验。
- [ ] 按5.3实现revision、锁、独占备份及原子保存；损坏文件报错。
- [ ] GET返回V3，POST响应保持ok/data外壳；修订现有合同测试断言到conditions，不删除其评级语义反例。
- [ ] V2旧客户端保存兼容和升级后的冲突分别测试。

必测案例：重命名只改name；相同名称带空格重命名无变动；空白名/41字符/归一重名422且文件不变；迁移两次完全一致；ytm条件和评分均pending；两个标签拿相同revision，第一次保存成功第二次409；mock os.replace抛错后原文件字节不变；JSON坏文件不能回默认；备份与原始文件逐字节一致。

验证：`& $CbPython -m pytest tests/test_cb_selection_templates.py tests/test_cb_factors_contract.py -q`。

### T4：接通统一执行和数据质量合同

修改：`cb_screen.py`服务及路由、`frontend/src/api/index.js`所需封装；新增 `tests/test_cb_selection_api.py`。

- [ ] _load_redeem_map补redeem_price与快照日期，按3.2选择日期。
- [ ] DB/live都先enrich，再evaluate_conditions，然后复用评分和DTO。
- [ ] 新run与active使用同一模板校验及黑名单；挂接迁移pending拒绝执行。
- [ ] 返回第6.2元信息，空快照/缺赎回/上游失败区分。
- [ ] 因子目录和行业目录可正常读取，行业不逐债联网。
- [ ] 使用真实ORM测试数据验证DB映射，不仅mock最终结果。

固定验收数据：A(code110001,price100,redeem110,行业760201)，B(code110002,price125,redeem110,行业610101)，C(code110003,price100,redeem缺失)。同一天快照与实时模拟输入相同。模板简单收益率gte0只保留A；B原因为-12不满足，C原因为赎回价缺失；不配置该条件时C可以出现在符合结果中。把A加入黑名单后A排除且原因可见。

必测名称：`test_db_live_same_input_same_selection`、`test_db_redeem_price_loaded_from_orm`、`test_pending_legacy_yield_cannot_run`、`test_active_applies_blacklist`、`test_no_snapshot_distinct_from_no_matches`、`test_redeem_unavailable_fails_dependent_template`、`test_redeem_date_mismatch_is_visible`、`test_selection_counts_partition_all_rows`、`test_filter_only_has_no_false_selected_badges`。

验证：`& $CbPython -m pytest tests/test_cb_selection_api.py tests/test_cb_selection_conditions.py tests/test_queries.py tests/test_cb_screen_contract.py -q`。

### T5：前端草稿、保存与请求状态

新增：`useSelectionWorkspace.js`、`selectionTemplate.js`、`frontend/tests/unit/selectionWorkspace.test.js`。

- [ ] 建立第7节状态与纯函数，先用模拟API测行为，尚不追求页面样式。
- [ ] 实现模板独立草稿及dirty比较，名字变更不影响执行签名。
- [ ] 实现按当前草稿执行、请求关联、错误保留上次结果和过期提示。
- [ ] 实现只保存当前模板、revision冲突保留草稿、保存中编辑不被覆盖。
- [ ] 新建/复制/重命名/删除/默认操作均满足2.3；离开保护涵盖所有草稿。

异步核心反例：A模板发请求后切B，A完成不能显示在B；第一次请求晚于第二次完成，第一次不能覆盖第二次；执行后改阈值，结果过期；仅改名，结果不因条件变化过期；保存发起后再改阈值，成功响应不能清掉新dirty。

验证（frontend目录）：`pnpm test:unit tests/unit/selectionWorkspace.test.js`。

### T6：模板工具栏与因子编辑器

新增：TemplateToolbar.vue、FactorEditor.vue；修改Factors.vue；测试 `frontend/tests/unit/SelectionEditor.test.js`。

- [ ] 按2.2实现顶部工具栏和可收起条件区；只使用Element Plus现有控件。
- [ ] 模板下拉支持键盘和搜索；重命名弹窗显示名称验证错误。
- [ ] 渲染类型化条件，不再保留“全局过滤条件”平行表单。
- [ ] 提供排序与入选折叠区及无评分模式摘要。
- [ ] 展示迁移问题卡并支持设置新收益率/删除旧项；pending时禁用运行且后端也拒绝。
- [ ] 所有修改通过统一patch，折叠开关不改变模板dirty。

必测：重命名取消/成功/重名；新建初始两项可见且评级不限；复制保留未知评级和空评级语义；启用开关使dirty；数值→行业切换清空旧数值；同字段可增加上下限；NONE可选；保存失败编辑内容保留。

验证：`pnpm test:unit tests/unit/SelectionEditor.test.js tests/unit/selectionWorkspace.test.js`。

### T7：结果表、黑名单、响应式和旧入口

新增SelectionResults.vue、BlacklistDrawer.vue；修改Factors.vue、AppLayout.vue、router/index.js；删除孤儿测试 `frontend/tests/unit/Bonds.test.js`；测试 `frontend/tests/unit/SelectionResults.test.js`、重写整页Factors.test.js中的UI定位方式。

- [ ] 第2.5表所有字段全部出现；3个新字段用实际row值渲染，不能硬编码。
- [ ] 收益率tooltip、空值、负值、0值格式正确；industry fallback可解释。
- [ ] 全部/入选/排除视图及结果内查找不触发API；排序不改变后端入选标记。
- [ ] 黑名单管理可访问且作用于所有模板，成功后结果过期。
- [ ] 条件抽屉和桌面收起功能落实，桌面/移动端保留结果横向滚动。
- [ ] 删除孤儿测试 `frontend/tests/unit/Bonds.test.js`（引用已删除的 Bonds.vue；不删则 T8 `pnpm test` 收集失败）。
- [ ] 可转债分组下筛选入口只保留一个：菜单「选债因子」更名为「转债选债」（路由仍 `/factors`）；「转债市场」（`/cb-market`）菜单与页面保留不动（用户已裁定，见修订记录③）。旧 `/cb-list` 路径加 redirect 到 `/factors`（旧书签兼容，修订前遗漏项 R10）；根路径 `/` 的 redirect 已在预存改动中指向 `/factors`，勿重复处理。
- [ ] `/cb-list`→`/factors`，新页面无intraday请求。
- [ ] 搜索旧页面引用，不能因为旧路由删除误删后端行情接口及测试。

现有EP测试桩不会真正渲染row数据（table-column给row={}），本轮必须修正为传入真实行的桩或挂载真实Element Plus，才能证明新增列值存在。按钮桩必须传递disabled，不能让禁用按钮测试仍能点击。

验证：`pnpm test:unit tests/unit/Factors.test.js tests/unit/SelectionResults.test.js tests/unit/SelectionEditor.test.js tests/unit/selectionWorkspace.test.js`。

### T8：集成验收与交付

- [ ] 根目录执行 `& $CbPython -m pytest -q`，记录结果和失败分类。
- [ ] frontend目录执行 `pnpm test`、`pnpm build`，记录退出码。
- [ ] 浏览器走默认加载→复制→重命名→改条件→执行→保存→刷新→切换→删除的完整流程。
- [ ] 浏览器检查1440×900、1280×800、768×1024、375×812；保存截图到交付记录指向的路径。
- [ ] 模拟DB缺赎回、实时失败、保存冲突、未迁移YTM、无评级、未知行业；不能只检查成功路径。
- [ ] 在隔离测试配置上验证迁移和回滚，不拿真实团队factors.json做破坏性测试。
- [ ] 核对git diff只包含任务范围；按任务提交，不覆盖已有CbMarket.vue修改。
- [ ] 完成交付记录；不得直接推送或部署ECS，最终由主审收尾。

如需本地服务，用已存在脚本，勿再临时拼接启动命令：

```powershell
.\scripts\app.ps1 status all
.\scripts\app.ps1 start all
```

是否需要restart由当前服务状态决定；本轮业务实现不修改运维脚本。

## 9. 给实施模型的派发提示（可直接复制）

```text
在 D:\gitub_codes\web 实施 docs/superpowers/plans/2026-09-10-cb-factor-selection-design-and-implementation.md。
先读第0～7节，再按T0～T8顺序执行。不要另起产品设计，不派生subagent。
每完成一个任务，运行其指定测试并更新交付记录，再开始下一个。
核心目标：因子配置驱动筛选；模板独立且可重命名；当前结果列保留，新增细分行业、到期赎回价、简单到期收益率。
统一字段 simple_maturity_yield_pct，公式(redeem_price-price)/price*100，不用集思录ytm_rt兜底。
复用现有DB/live入口和评分，不新增第二套筛选表单，不删除旧后端行情接口。
保护预存工作区与暂存区改动。预存改动中的 AppLayout/router/CbMarket 修改与已删除的 Bonds.vue、filterValidation.js、filterValidation.test.mjs 属于本任务 T7 前端收尾范围，允许随任务提交；但 CbMarket.vue 只随移除「转债筛选」链接，其市场图表逻辑不得改动（修订记录④）。测试使用隔离数据，不改团队真实模板或线上数据。
交付完整代码、实际测试输出、浏览器验证记录、已知问题和提交号；不要用“完成”代替证据。
实现结束停在本地待主审，不推送、不部署ECS。
```

## 10. 实施完成后的主审收尾清单

主审先看diff与目标，不以实施者说明替代验证。输出“达标 / 部分达标 / 不达标”，每条问题引用文件:行或测试名；遵守仓库的红队review原则。

- [ ] 验证新增指标在过滤前计算，DB/live同输入同输出。
- [ ] 用JSL ytm_rt=99的反例证明新因子没有读取或回退到原生YTM。
- [ ] 验证redeem_price字段没有混用force_redeem_price。
- [ ] 验证三列真正渲染数据，所有旧业务列仍存在。
- [ ] 验证模板重命名和刷新持久化、A/B草稿隔离、请求错序保护。
- [ ] 检查缺失值和跨日赎回降级，确认不把错误数据报告为正常零结果。
- [ ] 检查迁移备份、pending处理、revision冲突及原子文件替换。
- [ ] 检查所有模板路径应用黑名单、ST已显式模板化而不重复过滤。
- [ ] 检查新版UI零调用旧intraday；旧书签跳转；黑名单管理仍可进入。
- [ ] 在实际浏览器复验窄屏、错误、空数据；测试桩不能替代这一步。
- [ ] 复跑与改动相关的测试，确认交付中没有“未执行却标通过”。

交付记录必须包含：基线HEAD、最终提交列表、文件范围、T0～T8完成状态、测试命令/退出码/结果、浏览器截图、旧模板迁移样本、剩余问题、ECS worker是否核实。没有新问题的章节写“无”，未验证项如实写“未验证”，不可预勾选验收。

### 10.1 需求到任务的验收映射

| 用户目标 | 实施任务 | 硬性验收结果 |
|---|---|---|
| 只保留一个选债入口 | T7 | 菜单只有转债选债，旧链接可跳转 |
| 筛选由因子配置完成 | T2/T4/T6 | 请求仅带模板协议，修改因子真实改变后端结果 |
| 模板便于管理和重命名 | T3/T5/T6 | 稳定ID，保存刷新仍保留名字，多个草稿不互相覆盖 |
| 使用简单到期收益率 | T1/T2/T4 | 100/110=10%，125/110=-12%，原生ytm_rt不参与 |
| 当前结果列基础上新增三列 | T7 | 14旧列+3新列，实际数据可读，移动端仍可访问 |
| 内部使用稳定且操作高效 | T3/T5/T7/T8 | 冲突不覆盖、请求不串模板、失败保留结果、无每改一项就抓取 |

### 10.2 外部模型分批阅读建议

本文是一份完整规格，不要求每个小任务重复把全篇塞入上下文。首次读0～7节建立合同，之后每批读取对应业务节、任务及交付记录：

| 批次 | 阅读 | 输出 |
|---|---|---|
| 数据与协议 | 0、3、4、T0～T2 | 指标、目录、条件模型及纯函数测试 |
| 迁移与执行 | 4、5、6、T3～T4 | 配置升级、执行API和合同测试 |
| 前端实现 | 2、6.2、7、T5～T7 | 页面、状态管理及交互测试 |
| 集成 | 8的T8、10、11 | 测试和截图证据、交付记录 |

跨批次不重新命名已定字段，不用另一套JSON协议替换前一批输出。每批结束写清实际函数签名与测试状态，让下批从文件和记录继续，而非重新分析整个项目。任何偏离本文的实现选择必须在交付记录给出原因、受影响测试和用户可见变化。

## 11. 回滚与范围控制

前后端本轮作为配套版本发布，不能长期新前端接旧后端。未获部署授权前不操作ECS。

回滚代码前检查磁盘模板是否已升级V3。旧后端不能直接读取V3模板当作V2用；需恢复对应pre-v3备份。恢复前另外保留当前V3文件，避免丢失发布后新建模板。备份中没有的新增模板需要人工迁移，不能声称自动无损回滚。

本轮不迁移数据库表，不更改日频抓取任务，不增加真实年化YTM、策略回测、自动下单、历史结果存储、任意表达式执行、行业抓取器。新增行业和收益率的测试数据必须来自固定夹具或已存在数据，不能访问外部站点才能通过单元测试。
