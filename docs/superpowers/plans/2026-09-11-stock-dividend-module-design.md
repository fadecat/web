# 集思录高股息模块设计（股票高股息快照 + 筛选）

> 状态: 设计稿 v2 (2026-09-11)。上游研究记录见本文附录 A。
> 传承: market-daily `src/valuation/dividend/`(邮件形态) → web 交互形态。
> v2 变更: P1 收敛为纯数据侧(抓取+表+任务); 列全量使用集思录字段; 节假日
> 双重闸门; 模型文件 models/jisilu_stock.py; 前端/API/菜单延后至 P2 讨论。

## 1. 背景与目标

- 数据源: 集思录股息率排行页 `https://www.jisilu.cn/data/stock/dividend_rate/#cn`,
  接口 `POST /data/stock/dividend_rate_list/`。
- 目标: 把 market-daily 里「固定规则跑出邮件」的高股息观察, 改造成 web 的
  **日频快照 + 前端自定筛选**(形态对齐转债选债: 数据落库, 条件在筛选层)。
- 分期: **P1 = 纯数据侧**(fetcher + 表 + 定时任务, 本文 §2~§5 即实施范围);
  筛选交互(前端/API/菜单)待 P2 单独讨论设计。

## 2. 抓取方案(已定, 实测验证)

**行业(申万一级) × 总市值≥200亿 组合覆盖, 登录态, 普通会员(cap=100),
时点 15:08。**

| 实测项(2026-09-11 盘后) | 数值 |
|---|---|
| 全市场 ≥200亿 | 967 只(空条件 count_Info 与 Σ行业数一致) |
| 请求 | 37 个(31 个一级行业 + 电子 173 只下钻 6 个二级) |
| 耗时 | ~1 分钟(1.5s 间隔), 0 重试 0 限流 |
| 登录态行为 | 与游客计数完全一致, is_member=0, cap 仍 100; 会话复用不消耗登录额度 |

算法(自适应, 不硬编码节点):

```
cap = 300 if response.is_member else 100     # 普通会员=100; 未来升级自动生效
对行业树每个待查节点:
    POST {industry: <节点值>, total_value_a: 200, ...}
    total = count_Info 首数字; rows = rows[]
    len(rows) >= total  → 收下(完整)
    total > cap 且有子节点 → 下钻子节点(递归)
    total > cap 且是叶子 → total_value 区间递归二分(实测 135→62+39+34 无缝)
```

关键事实(研究结论, 实现时遵守):
- `page` / `rp` / `market[]` 服务端硬顶或忽略, 无旁路; 100 行是普通会员上限。
- `count_Info` 携带真实总数、`has_more` 标记截断 —— 每个响应都可自校验完整性。
- cell 共 48 字段; `last_dt`/`last_time` 给出交易日+行情时间, 收盘后抓即当日定稿。
- 数据瑕疵: `pledge_rt`/`stdevry` 偶为 `'buy'` 徽标串; `industry`/`industry2` 恒空。
  数值列解析一律防御(非数值→None)。

## 3. Fetcher(`backend/services/fetchers/stock_dividend.py`)

分层遵守仓库约定: **fetcher 是纯函数** —— 入参=会话/参数, 出参=结构化数据,
允许网络请求, 但不写库、不写 data/state(参照 fetchers/ 既有 jisilu 抓取函数)。
树缓存与落库等副作用归任务层/store 层(§5、§9)。

- `fetch_industry_tree(session) -> list[节点]`
  GET 页面 HTML → 解析 `<select id="select_industry">` 内嵌的 511 个 `<option>`
  (value/申万码/层级/计数/名称路径)。**说明: 集思录无行业树 JSON 接口, 树只在
  页面服务端渲染的 HTML 里**(浏览器自身也用这份标记)。
  只负责抓与解析, 不落盘(月度缓存见 §5 任务层)。
- `fetch_dividend_snapshot(session, tree, *, min_total_value=200) -> dict`
  执行 §2 自适应覆盖(树由调用方作入参传入); 返回
  `{rows: [cell...], meta: {trade_date, node_count, request_count,
  warnings[], tree_drift: bool}}`。
  - 每请求 1.5s 间隔; 单请求失败退避重试 3 次(10/20/40s); 节点连续失败计入
    warnings 不中断整轮。
  - trade_date 取本次抓取中占多数的 `last_dt`(停牌股较旧值保留在 raw_json)。
  - 对账: Σ节点 total vs 去重 stock_id 数, 不一致写 warnings。
  - tree_drift 置位: 节点 API total 与缓存树 cnts 明显不一致, 或抓回行的
    `sw_cd` 不以任何已查节点 value 为前缀(树陈旧缺新节点的信号)。
- 登录: 复用 `backend/services/jisilu.py` 的 `get_cookie()` 会话落盘复用;
  整轮共用同一 cookie, 不重复登录。
- min_total_value 做成参数(默认 200): 算法对阈值不敏感, 未来要全量(5568 只,
  206 请求/5.5 分钟)或调整门槛只改配置。

## 4. 数据模型(`backend/models/jisilu_stock.py` 新文件)

对齐 `CbDailySnapshot` 宽表模式。**列取舍(已定): 集思录返回的 48 个字段全部
建结构化列, 不做裁剪**; `raw_json` 仍保留全 cell —— 集思录未来新增字段会自动
进 raw_json, 零成本前向兼容。追加式全量保存, `(stock_id, trade_date)` 唯一
约束幂等。

```python
class StockDividendDaily(Base):
    """股票高股息日频快照(每只 ≥200亿 股票每天一行)。源: 集思录 dividend_rate_list。"""
    __tablename__ = "stock_dividend_daily"

    id, trade_date(Date), created_at(DateTime)
    stock_id(String16), stock_nm(String64)

    # ---- 集思录 48 字段全量建列(分组注释, 类型按实测样本) ----
    # 行业/地域
    sw_cd, industry, industry2, industry_nm, industry_nm2, province
    # 行情
    price, pre_close, increase_rt, volume, adj_rt, price_5year
    # 规模
    total_value, float_value, shares
    # 估值
    pe, pb, roe, roe_average, pe_temperature, pb_temperature
    # 股息
    dividend_rate, dividend_rate2, dividend_rate5, dividend_rate_average,
    dividend_rate_base, accu_dividend, aft_dividend
    # 财务质量
    debt_rate, int_debt_rate, pledge_rt, eps_growth, eps_growth_ttm,
    revenue_average, profit_average, cashflow_average
    # 元数据/标志
    ipo_date(String32), last_dt(String32), last_time(String32),
    audit_info(String), active_flg(String8), margin_flg(String8), pb_flag(String8),
    stdevry  # 实测偶为 'buy' 徽标串, 列类型 String 容纳
    # 账号自选态(登录态下为账号维度, 实现按现值存)
    owned(Integer), holded(Integer)
    # 兜底
    raw_json(String)

    UniqueConstraint("stock_id", "trade_date"), Index("trade_date"), Index("sw_cd")
```

数值列解析防御: 空串/`'-'`/徽标串 → None(`pledge_rt`/`stdevry` 实测有此情况;
`stdevry` 直接建 String 列保留原样)。

## 5. 任务编排(`backend/tasks/stock_dividend_tasks.py`)

- `run_stock_dividend_daily()`: **交易日双重闸门** → fetch → **按日追加落库**
  (仅当出现同 trade_date 重复数据时对该日期先删后插以保证幂等, 不触碰其他日期)
  → 失败经既有 run_logger 体系呈现(数据管理页可见)。
- **节假日处理(双重闸门)**:
  1. **前置闸门(省请求)**: 沿用 `backend/utils.py::is_trading_day(today)`
     (周末 + 手工节假日表, 2026 已齐/2027 待公布), 非交易日直接跳过,
     **零请求**。例: 周一放假 → 调度器 15:08 照常触发 → 闸门判非交易日 →
     记日志跳过。
  2. **数据闸门(安全网, 兜节假日表缺漏)**: fetch 完成后若占多数的
     `last_dt` ≠ 今天(东八区) → 判定"节假日表缺漏或数据未更新",
     **跳过落库**并记 warning(该日期数据大概率已存在, 重写无价值且可能用
     降级数据覆盖好数据)。最坏情况 = 浪费一轮 ~38 请求, 数据不受污染。
- 注册进 `DAILY_JOBS`(与 scheduler/data_status 同源):
  `("stock_dividend_daily", run_stock_dividend_daily, "高股息股票快照抓取", 15, 8)`
  —— 紧跟转债任务(15:03/15:04/15:06)之后; 周一至周五触发。
  不进 `EVERYDAY_JOB_IDS`(当日快照类)。
- 树缓存与漂移重试归**任务层**: 缓存文件 `data/state/stock_dividend_industry_tree.json`
  (节点列表 + fetched_at, 路径可注入便于测试); 任务加载缓存, 缺失或超 30 天才
  调 `fetch_industry_tree()` 重取并写缓存。日常轮次零页面依赖、零解析。
  **漂移自愈(两层)**:
  ① fetch 返回 `meta.tree_drift=true` → 重取树并**整轮重跑一次**(单轮最多
  重跑一次防循环; 仍漂移则照常落库, warnings 记录);
  ② 算法不强依赖树的 counts —— cnts 仅为规划优化, 缺失/失真时退化为
  「查一级→count_Info 超 cap→下钻」纯自适应模式, 功能不损(树层级编码在
  value 前缀: 一级2位/二级4位/三级6位, 子=父前缀)。
- 无独立树刷新任务(月度刷新内嵌在日任务里)。

## 6. API 与前端(P2, 2026-09-12 交付)

「暂缓」状态结束, P2 已实施并部署。要点(完整方案见会话计划
`magical-swimming-flurry.md`, 已按用户评审意见修正):

- **复刻范围**: 完全复刻集思录股息率排行页的筛选与列展示, 视觉用本仓库风格
  (Element Plus + 现有页面惯例)。**会员占位列不复刻** —— 波动率(stdevry)/
  质押比例(pledge_rt) 非会员账号恒为 'buy' 无数据, 直接不渲染列; 其余有意义的
  列全保留不漏, 筛选逻辑与集思录一致(两字段本来就不是筛选控件, 不受影响)。
  API 载荷仍全量 47 键(数据保真, 供后续差异化使用)。
- **只看最新交易日**(无日期切换), 菜单新建「股票」分组 +「高股息」项。
- 后端: `GET /api/stock-dividend/latest?trade_date=`(薄路由 + 查询服务,
  股息率降序 null 沉底, stock_id tie-break), 10 项契约测试。
- 前端: `utils/stockDividend.mjs` 纯函数(25 列配置/筛选/行业树/排序/色阶) +
  `pages/StockDividend.vue`(三态/骨架/reqToken + 13 阈值筛选区 + 宽表 + 分页)。
- 渲染语义均从保存的 jisilu 页面源码逐项对齐: 温度四档色阶
  (<25 青/<50 绿/<75 橙/≥75 红, 负值显示'—'但参与筛选排序)、aft_dividend
  黄底强调、pb_flag='Y' 灰色口径 tooltip、margin_flg='R' 橙色 R 上标、
  audit_info 红色警示、代码列外链 `https://www.jisilu.cn/data/stock/{id}`。
- 测试: node 34 项(抓出并修复 sortRows null 方向翻转、buildIndustryTree
  children 未挂接两个真 bug) + vitest 11 项(真 mount, 含渲染冒烟:
  无会员占位列断言) + 后端全量基线持平(524 passed)。

## 7. 回测能力与边界

按日全量快照天然支持**截面回放式回测**: 任意历史日的完整成员+字段截面可精确
还原, 筛选条件可在历史快照上重放; 每行每日带 price/pre_close, 前瞻收益可从
本表计算。边界三条:

1. **累积从上线日开始, 无法回补**——集思录无历史快照接口, 回测素材只能逐日
   积累(尽快上线即尽早积累)。
2. **成分股跌破 200亿 后价格序列断链**——持有期内掉出池子的股票无后续价格行,
   前瞻收益算不全。可选: 容忍 / 外部行情源补价 / 降 min_total_value 重抓
   (参数已预留; 切换前的历史仍按当时门槛)。
3. **当时成员口径 = 池内无幸存者偏差**——后来跌出 200亿 的股票, 其池内期间的
   历史完整保留, 这是烤入门槛对回测的加分面。

## 8. 风险与边界

- **cap=100 是硬约束**: 普通会员升级会员后 cap=300 自动生效(算法已自适应);
  未升级则维持 ~37 请求/轮。
- **≥200亿 烤进数据**: 快照成员随市值穿越 200亿 边界进出(每日 ~±十几只量级),
  历史查询是「当时成员」口径(回测视角见 §7)。
- **节假日表 2027 待公布**: 国务院一般在前一年 11 月发布次年安排, 届时补
  `utils.py` 的 `_HOLIDAYS_2027`; 缺漏期间由数据闸门兜底(最坏浪费一轮请求)。
- 请求礼貌性: ~38 请求/轮 × 1.5s, 失败退避; 登录态比游客更稳妥(用户已定)。
- 测试约束: 全部走 mock(测试禁外网), 用 `.test-artifacts/ggx-research/` 里的
  真实响应样本做 fixture 蓝本。

## 9. 实施清单(P1)

1. `backend/models/jisilu_stock.py`: StockDividendDaily(48 列 + raw_json);
   **接入汇聚导入 `backend/models/database.py` 的 models 导入行(约 :45,
   `from backend.models import ...`), 否则 create_all 不建表**
2. `backend/services/fetchers/stock_dividend.py`: `fetch_industry_tree` +
   `fetch_dividend_snapshot`(算法原型: `.test-artifacts/ggx-research/sweep.py`,
   近乎照搬, 注意清理其中 bisect 切点处一处死表达式残留)
3. `backend/services/stock_dividend_store.py`: 按日追加落库(同 trade_date
   先删后插幂等; 对齐 `cb_list_store.py` 模式)
4. `backend/tasks/stock_dividend_tasks.py`: 双重闸门 + 树缓存/月度刷新/
   漂移重试 + 调 store
5. `backend/tasks/registry.py`: 注册 `("stock_dividend_daily", ..., 15, 8)`;
   检查 tests 中对任务集合的断言(引用 data_status 的 8 个测试文件, 如
   test_data_status.py)按需更新
6. 测试: fetcher 单测(mock 响应: 直接收下/下钻/叶子二分/树漂移四分支)、
   任务闸门单测(非交易日跳过零请求 / last_dt 数据闸门跳过落库)、
   落库幂等单测(重跑同日不影响他日)

接线涟漪(实施中发现, 设计时漏写, 已完成):
- 模型汇聚导入行 `from backend.models import app_setting, data_status, valuation`
  在仓库有 11 处副本(init_db / migrations env / 3 个 scripts / conftest×2 /
  5 个测试), 新模型须逐处补 `jisilu_stock`, 否则各链路看不到新表;
- `migrations/versions/0001_initial_schema.py` 需加 stock_dividend_daily 建表
  (test_migration_baseline 比对 alembic 产物与 ORM create_all 等价性;
  过渡状态: 生产建表走 init_db, 基线供 adopt_db_copy 接管链使用);
- `backend/services/data_status.py` 的 JOBS 镜像字典须同步(有守卫测试
  test_registry_covers_service_jobs), 快照新鲜度分组循环加入新表,
  test_data_status 形状断言 7→8 组/6→7 任务;
- `backend/services/data_catalog.py` 的 catalog() 须登记「高股息股票快照」
  (Policy jisilu/stock_dividend_daily/15:30), 否则状态页该组永远
  unmanaged + no_data(评审 P1-1; 已加回归测试锁 managed/job_id/source);
- `backend/services/data_integrity.py` 的 DAILY_TABLE_REGISTRY 须登记
  (mode=global, entity_attr=None —— 成分随市值门槛进出, 只扫全表日期序列),
  test_data_integrity 表数断言 7→8(评审 P2-1);
- `backend/scheduler.py` 启动日志的任务时刻清单加 stock_dividend@15:08
  (纯可读性, 评审 P2-4);
- `tests/test_task_results.py` 非交易日契约参数化加入新任务。

评审追记(2026-09-11 主审修复): fetcher 全市场模式(min_total_value=0)下
叶子二分的开下界 a=None 曾在退化守卫 `t <= a` 处 TypeError, 已改为
`if a and t <= a`(P1-2); meta 补 failed_queries/cap 两个观测字段
(P2-2/P2-3), 均有回归测试。

## 10. 派发提示(外部实施者唯一入口)

> 本节与 §1~§9 共同构成实施合同; 实施者另需遵守项目 CLAUDE.md。

你是实施者, 在 `D:\gitub_codes\web`(Windows, git 仓库, 分支 main)实施「股票
高股息日频快照」P1 数据层。**唯一设计依据: 本文档**——先通读全文含附录 A;
与提示冲突处以文档为准, 有疑问按最保守解释处理并在交付说明中记录。

环境与流程:
- 从仓库根目录操作; PATH python 依赖齐全(先 `python -c "import fastapi,
  sqlalchemy, requests, bs4"` 自证, bs4 缺失就用标准库 re 解析 HTML, 不新增
  依赖), 不建 venv。
- 流程: ①跑全量 `python -m pytest tests -q -p no:cacheprovider` 记录基线
  失败集 → ②按 §9 清单实施 → ③补测试 → ④重跑全量 pytest 与基线对照,
  **不得新增失败** → ⑤输出交付报告。
- conftest 在 socket 层禁外网、禁真实 data/, jisilu 交互一律 mock。

真实数据蓝本(只读, `.test-artifacts/ggx-research/`, 已 gitignore, 不入 repo):
- `jisilu_dividend_page.html` 行业树页面样本(测树解析);
- `jisilu_industries.json` 511 节点树(测下钻规划);
- `sweep_cells.jsonl` 5568 行 × 48 字段真实 cell(测字段解析与防御, 抽子集
  做 fixture);
- `sweep.py` 覆盖算法原型(query/accept/bisect/fetch_node)。

参考实现(对齐模式而非照抄): `backend/tasks/cb_list_tasks.py`(任务+交易日
闸门)、`backend/services/cb_list_store.py`(落库幂等)、`backend/models/
valuation.py` 的 CbDailySnapshot(宽表)、`backend/tasks/registry.py`
(DAILY_JOBS)、`backend/services/jisilu.py`(会话复用, 禁止新增登录逻辑)、
fetchers/ 内任意 jisilu 函数(请求头与表单姿势)。

硬约束:
- **禁止 git commit/push、禁止部署** —— 全部改动留在工作区交主审。
- 工作区其他未提交内容(如本文档)不触碰、不还原。
- fetcher 纯函数: 不写库、不写 data/state; 树缓存/落库在任务层与 store 层。
- 注释与 docstring 中文, 风格对齐邻近代码; 测试产物只写 .test-artifacts/
  或 pytest tmp_path。
- 接口数据事实(研究已证, 勿再探测): page/rp/market[] 服务端无效;
  count_Info 首数字=真实总数; is_member 决定 cap(普通会员=100);
  pledge_rt/stdevry 偶为 'buy' 徽标串; industry/industry2 恒空;
  last_dt/last_time=行情日期/时间。

交付报告(最终输出, 缺一不可): 变更/新增文件清单(每文件一句话说明)、测试
命令与结果摘要、基线 vs 终态失败对照、与设计文档的全部偏离及理由、遗留问题。

## 11. 主审复核清单(主会话执行)

1. 分层: fetcher 无落盘/落库副作用; 树缓存与落库在任务/store 层; 签名纯函数。
2. 闸门①: 非交易日跳过且**零请求**(mock 计数断言); 闸门②: 多数 last_dt ≠
   今天(东八区) → 跳过落库并记 warning。
3. 幂等: 同 trade_date 重跑先删后插; 预置两日数据重跑其中一日, 另一日不受影响。
4. 覆盖算法四分支测试齐备(直接收下/下钻/叶子二分/树漂移); cap 随 is_member
   自适应; sweep.py 的死表达式未带入。
5. 模型: 48 字段列齐全 + raw_json; (stock_id, trade_date) 唯一约束;
   database.py 汇聚导入已加; 数值防御('buy'/'-'/空串 → None)。
6. registry 注册 15:08、不进 EVERYDAY_JOB_IDS; 受影响测试的更新理由成立。
7. 全量 pytest 与主审独立基线对照无新增失败; 本任务不触前端(确认无前端文件
   被改动即可)。
8. 代码风格: 中文 docstring、命名与邻近一致。
9. 通过后: 主会话 commit → push → ECS 管线(git pull → restart backend),
   需用户授权; 实施者不得自行部署。

## 附录 A: 研究实测记录(2026-09-11)

- 接口: 游客可用(is_member=0, cap=100); `count_Info` 带真实总数, `has_more` 标截断;
  `page` 恒 1、`rp` 无视、`market[]` 忽略 —— 无分页旁路。
- 全量(无市值下限): 行业树自适应覆盖 206 请求/330s/5568 只(0 重试 0 限流),
  其中 4 个三级行业叶子超 100(IT服务135/汽车零部件112/化学制剂110/通信设备101),
  市值二分无缝解决(135→62+39+34)。
- ≥200亿: 967 只; 31 个一级行业仅电子(173)超限, 下钻 6 个二级(半导体94/元件27/
  光学光电子15/消费电子5/电子化学品21/其他11)全覆盖 → 37 请求。
- 登录态(普通会员): 计数与游客一致, is_member=0, cap=100; 会话 cookie 一周仍有效。
- 阈值刻度: ≥100亿 → 1799 只; ≥50亿 → 3172 只; 全量 → 5568 只。
- 行业树: 页面 HTML 服务端渲染(无 JSON 接口), `<option>` 携带 value/申万码/
  层级/计数/路径名; 树结构编码在 value 前缀(2/4/6 位)。
- 研究产物: `.test-artifacts/ggx-research/`(jisilu_industries.json 511 节点树 /
  sweep_cells.jsonl 5568×48 字段全量样本 / sweep.py fetcher 原型)。
