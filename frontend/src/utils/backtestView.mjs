// 组合详情页(L2)的纯函数: 收益条七格 / 指标卡 / 相关性着色 / 曲线换算 / 口径文案。
// 抽成纯函数的理由: 这些规则来自 page-spec §二 的逐区域规定(格子口径、着色方向、
// 归一方式), 放在模板里既测不了也容易两处写不一致。

import { EMPTY, TREND_FLAT, TREND_UP, TREND_DOWN, trendOf, formatReturnPct } from './portfolioList.mjs';
import { isDate } from './portfolioAssets.mjs';

// ---------------------------------------------------------------------------
// 区间快捷选择(控制条「请选择」下拉)
// ---------------------------------------------------------------------------

/**
 * 事件锚点沿用韭圈儿「请选择」下拉里的 5 个点位(2026-09-19 由用户截图提出)。
 *
 * ⚠ 它们是**固定历史日期**, 不是"近 N 年" —— 对成立较晚的组合会早于 T0, 此时由后端的
 *    "起点前移"规则夹到 T0 并回显(`actual_start`), **前端不自己改口径**。
 */
export const RANGE_EVENT_PRESETS = [
  { key: 'evt-2015-crash', label: '2015 股灾以来(2015-06-12)', start: '2015-06-12' },
  { key: 'evt-2016-circuit', label: '2016 熔断以来(2016-01-17)', start: '2016-01-17' },
  { key: 'evt-2019-bull', label: '2019 牛市以来(2019-01-04)', start: '2019-01-04' },
  { key: 'evt-2020-covid', label: '2020 疫情以来(2020-01-17)', start: '2020-01-17' },
  { key: 'evt-2021-cny', label: '2021 春节以来(2021-02-18)', start: '2021-02-18' },
];

export const RANGE_INCEPTION_KEY = 'inception';

/**
 * 快捷区间条(照韭圈儿底部那一条搬运): 今年以来 / 近1月 / 近3月 / 近6月 / 近1年 / 近3年 / 近5年 / 近10年。
 * ⚠ 它们是**相对区间**(以数据最新日为末端回推), 与「事件锚点」那种固定历史日期不同:
 *    - 前端只算出起止日, 起点仍由后端"起点前移"规则夹到 T0(组合成立晚于该日时)。
 */
export const RANGE_SPAN_PRESETS = [
  { key: 'span-ytd', label: '今年以来' },
  { key: 'span-1m', label: '近1月', months: 1 },
  { key: 'span-3m', label: '近3月', months: 3 },
  { key: 'span-6m', label: '近6月', months: 6 },
  { key: 'span-1y', label: '近1年', months: 12 },
  { key: 'span-3y', label: '近3年', months: 36 },
  { key: 'span-5y', label: '近5年', months: 60 },
  { key: 'span-10y', label: '近10年', months: 120 },
];

/**
 * 自然月回推 → ISO 日期(与后端 `backtest.months_back` **同规则**: 日号溢出取目标月最后一天)。
 *
 * ⚠ 不能用 `date - 30×n 天`: 2026-09-18 按 30 天回推出 08-19、按自然月是 08-18, 差一个
 *    交易日, 实测会让「近1月」偏 0.28pp(后端已修过一次, 前端必须同规则)。
 */
export const monthsBack = (isoDate, months) => {
  if (!isDate(isoDate)) return null;
  const offset = Number(months);
  if (!Number.isFinite(offset)) return null;
  const [year, month, day] = isoDate.split('-').map(Number);
  const total = year * 12 + (month - 1) - offset;
  const targetYear = Math.floor(total / 12);
  const targetMonth = ((total % 12) + 12) % 12 + 1;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  const targetDay = Math.min(day, lastDay);
  return `${String(targetYear).padStart(4, '0')}-${String(targetMonth).padStart(2, '0')}-${String(targetDay).padStart(2, '0')}`;
};

/** 快捷区间 key → 起止日; 拿不到数据最新日(还没跑成过回测)返回 null。 */
export const resolveRangeSpan = (key, lastDataDate) => {
  if (!isDate(lastDataDate)) return null;
  const hit = RANGE_SPAN_PRESETS.find((preset) => preset.key === key);
  if (!hit) return null;
  if (!hit.months) {
    return { key: hit.key, start: `${lastDataDate.slice(0, 4)}-01-01`, end: null };
  }
  const start = monthsBack(lastDataDate, hit.months);
  return start ? { key: hit.key, start, end: null } : null;
};

/**
 * 按年份的区间: 从 T0 那年到数据最新日那年, **倒序**。
 * 当年/跨年的年末日期夹到 `lastDataDate`(未来日期没有意义, 后端也会前移)。
 */
export const buildYearRangePresets = (t0, lastDataDate) => {
  if (!isDate(t0) || !isDate(lastDataDate)) return [];
  const first = Number(t0.slice(0, 4));
  const last = Number(lastDataDate.slice(0, 4));
  if (!Number.isFinite(first) || !Number.isFinite(last) || last < first) return [];
  const out = [];
  for (let year = last; year >= first; year -= 1) {
    const yearEnd = `${year}-12-31`;
    out.push({
      key: `year-${year}`,
      label: `${year}年`,
      start: `${year}-01-01`,
      end: yearEnd < lastDataDate ? yearEnd : lastDataDate,
    });
  }
  return out;
};

/** 分组给 UI 用: 区间 / 事件锚点 / 按年份。 */
export const buildRangePresetGroups = ({ t0, lastDataDate } = {}) => {
  const years = buildYearRangePresets(t0, lastDataDate);
  return [
    {
      label: '区间',
      options: [{ key: RANGE_INCEPTION_KEY, label: '成立以来', start: isDate(t0) ? t0 : null }],
    },
    { label: '事件锚点', options: [...RANGE_EVENT_PRESETS] },
    ...(years.length ? [{ label: '按年份', options: years }] : []),
  ];
};

/**
 * 选中项 → 起止日(交给回测表单)。
 *
 * ⚠ 「成立以来」必须**显式给 T0**: `start=null` 会落到后端"末端回推 10 年"的默认,
 *    那不是"成立以来"。拿不到 T0 时返回 null(让 UI 什么都不做, 不猜)。
 */
export const resolveRangePreset = (key, { t0, lastDataDate } = {}) => {
  for (const group of buildRangePresetGroups({ t0, lastDataDate })) {
    const hit = group.options.find((option) => option.key === key);
    if (!hit) continue;
    if (hit.key === RANGE_INCEPTION_KEY && !isDate(t0)) return null;
    return { key: hit.key, start: hit.start, end: hit.end ?? null };
  }
  return null;
};

/**
 * 快捷区间的**全部候选**(strip 上的相对区间 + 下拉里的固定日期项)。
 * 用于两件事: ① 反推"当前选中的是哪一个" ② 保持 strip 与下拉的分工可见。
 */
export const buildRangeCandidates = ({ t0, lastDataDate } = {}) => {
  const spans = RANGE_SPAN_PRESETS
    .map((preset) => ({ ...preset, ...(resolveRangeSpan(preset.key, lastDataDate) ?? {}) }))
    .filter((preset) => Boolean(preset.start));
  const fixed = buildRangePresetGroups({ t0, lastDataDate }).flatMap((group) => group.options);
  return [...spans, ...fixed];
};

/**
 * 由表单里的起止日反推选中的快捷项。
 *
 * ⚠ 刻意**不另存"当前选中项"状态**: 用户手改日期时要自动取消高亮, 否则下拉/按钮
 *    显示"今年以来"、输入框却是别的区间 —— 两处数字打架比没高亮更糟。
 */
export const activeRangeKey = (start, end, ctx = {}) => {
  const hit = buildRangeCandidates(ctx).find(
    (option) => (option.start || '') === (start || '') && (option.end || '') === (end || ''),
  );
  return hit ? hit.key : '';
};

/** 一个入口解析所有快捷项(相对区间 + 固定日期), 避免调用方分别判断。 */
export const resolveRangeSelection = (key, { t0, lastDataDate } = {}) => (
  resolveRangeSpan(key, lastDataDate) ?? resolveRangePreset(key, { t0, lastDataDate })
);

// ---------------------------------------------------------------------------
// 收益条(区域①)
// ---------------------------------------------------------------------------

// 七格顺序 = 页面顺序(与韭圈儿一致)
export const WINDOW_CELLS = [
  { key: 'd1', label: '近1日' },
  { key: 'w1', label: '近1周' },
  { key: 'm1', label: '近1月' },
  { key: 'ytd', label: '今年来' },
  { key: 'y1', label: '近1年' },
  { key: 'y3', label: '近3年' },
  { key: 'inception', label: '成立来' },
];

const TREND_CLASS = {
  [TREND_UP]: 'trend-up',
  [TREND_DOWN]: 'trend-down',
  [TREND_FLAT]: 'trend-flat',
};

/**
 * 收益条七格 → 渲染模型。
 *
 * ⚠ 整条由**同一条净值序列**(不平衡持有至今)导出, 不给单个区间重新建仓 —— 这是
 *   规格 §二-① 的关键约束。`actual_start` 与请求起点不同时说明是"自动前移"(数据不足),
 *   hint 里如实标出, 不隐瞒。
 */
export const buildReturnCells = (windows) =>
  WINDOW_CELLS.map(({ key, label }, index) => {
    const cell = windows?.[key] ?? null;
    const value = cell?.value ?? null;
    return {
      key,
      label,
      value,
      text: formatReturnPct(value),
      className: TREND_CLASS[trendOf(value)],
      isEmpty: value === null || value === undefined,
      // 首格放大(规格 §二-①)
      featured: index === 0,
      // 「近1日」是合成口径(Σ wᵢ×rᵢ 各自最新日), 与其余六格不是同一套算法
      composite: Boolean(cell?.composite),
      actualStart: cell?.actual_start ?? null,
      actualEnd: cell?.actual_end ?? null,
      hint: cell?.composite
        ? '按各标的自身最新可用日涨跌幅、以当前占比合成'
        : cell?.actual_start
          ? `实际区间 ${cell.actual_start} ~ ${cell.actual_end}`
          : '',
    };
  });

// ---------------------------------------------------------------------------
// 控制条(区域③)
// ---------------------------------------------------------------------------

export const REBALANCE_OPTIONS = [
  { value: 'none', label: '不平衡' },
  { value: 'quarterly', label: '季平衡' },
  { value: 'yearly', label: '年平衡' },
];

// 照搬韭圈儿官方说明(规格 §二-③)
export const REBALANCE_TIP =
  '季平衡：每个季度 1 日（如遇节假日会自动延期至下个交易日），会将组合基金的比例调整至初始设定的比例；年平衡：每年 1 月 1 日（同上）。';

// 固定口径提示(规格 §二-③)
export const EXEC_PRICE_TIP = '成交价＝当日净值确认（T 日 15:00 前提交）。含 QDII 净值滞后补偿。';

// 底部固定文案(区域⑧, 照搬)
export const QDII_FOOTNOTE =
  '提示：如组合含有 QDII/FOF 基金，可能会以 QDII/FOF 前一日涨幅跌幅计算该组合最近更新日的涨跌幅';

export const rebalanceLabel = (mode) =>
  REBALANCE_OPTIONS.find((o) => o.value === mode)?.label ?? mode ?? EMPTY;

// ---------------------------------------------------------------------------
// 价格口径文案(三类资产口径不同, 必须逐列标注)
// ---------------------------------------------------------------------------

export const PRICE_BASIS_LABELS = {
  HFQ: '后复权价',
  NAV_ADJ: '分红再投净值',
  PRICE: '价格指数',
};

export const priceBasisLabel = (basis) => PRICE_BASIS_LABELS[basis] ?? basis ?? EMPTY;

// ---------------------------------------------------------------------------
// 指标卡(区域⑤, 我方增强)
// ---------------------------------------------------------------------------

const pct = (value, digits = 2) =>
  value === null || value === undefined || Number.isNaN(value) ? EMPTY : `${Number(value).toFixed(digits)}%`;

const num = (value, digits = 2) =>
  value === null || value === undefined || Number.isNaN(value) ? EMPTY : Number(value).toFixed(digits);

/**
 * 指标卡。⚠ 只列后端**真的算出来**的指标 —— 宁可少一格, 也不放置灰占位。
 * 覆盖规格 §二-⑤ / §三-4 的全部项: 年化 / 回撤 / 波动 / Sharpe / Sortino /
 * Calmar / 最差年度 / 最差月度 / 最长恢复 / 累计换手。
 */
export const buildMetricCards = (metrics, drawdown) => {
  const m = metrics ?? {};
  return [
    { key: 'cagr', label: '年化收益', value: pct(m.cagr), hint: '按区间首末净值年化' },
    {
      key: 'mdd',
      label: '最大回撤',
      value: pct(m.mdd),
      hint: drawdown?.trough_date ? `谷值 ${drawdown.trough_date}` : '',
    },
    { key: 'vol', label: '年化波动', value: pct(m.vol), hint: '日收益标准差 × √244' },
    { key: 'sharpe', label: 'Sharpe', value: num(m.sharpe), hint: '无风险利率取 0' },
    { key: 'sortino', label: 'Sortino', value: num(m.sortino), hint: '只惩罚下行波动' },
    { key: 'calmar', label: 'Calmar', value: num(m.calmar), hint: '年化收益 / |最大回撤|' },
    { key: 'worst_year', label: '最差年度', value: pct(m.worst_year), hint: '按自然年重采样' },
    { key: 'worst_month', label: '最差月度', value: pct(m.worst_month), hint: '按自然月重采样' },
    {
      key: 'recovery',
      label: '最长恢复',
      value: drawdown?.recovery_days === null || drawdown?.recovery_days === undefined
        ? EMPTY
        : `${drawdown.recovery_days} 天`,
      hint:
        drawdown?.recovery_days === null || drawdown?.recovery_days === undefined
          ? '至区间末端仍未回到前高'
          : `自 ${drawdown.trough_date} 起`,
    },
    {
      key: 'turnover',
      label: '累计换手',
      value: pct(m.turnover),
      hint: 'Σ|Δw|（只统计区间内的调仓）· 零成本口径下不参与收益计算，仅作实盘可行性参考',
    },
  ];
};

/** 回撤明细 → 人话横条文案(区域②「组合回撤」Tab)。 */
export const drawdownSummaryText = (drawdown) => {
  if (!drawdown) return '暂无回撤数据';
  const base = `最大回撤 ${Number(drawdown.value).toFixed(2)}%（峰值 ${drawdown.peak_date} → 谷值 ${drawdown.trough_date}）`;
  if (!drawdown.recovery_date) return `${base}，至区间末端仍未修复`;
  return `${base}，${drawdown.recovery_date} 修复，历时 ${drawdown.recovery_days} 个交易日`;
};

// ---------------------------------------------------------------------------
// 相关性矩阵(区域⑥)
// ---------------------------------------------------------------------------

const clampCorr = (value) => {
  // ⚠ 必须先挡 null: `Number(null) === 0`, 直接转型会把"无数据"画成"零相关"
  if (value === null || value === undefined || value === '') return null;
  const v = Number(value);
  if (Number.isNaN(v)) return null;
  return Math.max(-1, Math.min(1, v));
};

/**
 * 相关系数 → 单元格底色。
 * 规格: `+1` 深红 → `0` 白 → `−1` 蓝。实现用**透明度叠加**而不是硬编码白底,
 * 这样深色主题下也读得出来(叠在当前表面色上)。`strong` 表示底色够深、文字要转白。
 */
export const correlationCellStyle = (value) => {
  const v = clampCorr(value);
  if (v === null) return { background: 'transparent', strong: false };
  const rgb = v >= 0 ? '220, 38, 38' : '37, 99, 235';
  const alpha = Math.abs(v) * 0.85;
  return { background: `rgba(${rgb}, ${alpha.toFixed(3)})`, strong: Math.abs(v) > 0.55 };
};

/**
 * 相关性矩阵 → 渲染模型。标头按 `symbols` 顺序编号 1..N(规格 §二-⑥ 的 `1 2 3 4`)。
 * ⚠ 计算区间由后端单独给出(它与回测区间的对齐方向**不同**), 这里原样透传, 不自己推。
 */
export const buildCorrelationView = (correlation, assets) => {
  if (!correlation || !correlation.symbols?.length) return null;
  const nameOf = (symbol) => (assets ?? []).find((a) => a.symbol === symbol)?.name ?? symbol;
  const columns = correlation.symbols.map((symbol, index) => ({
    index: index + 1,
    symbol,
    name: nameOf(symbol),
  }));
  const rows = correlation.symbols.map((rowSymbol, rowIndex) => ({
    ...columns[rowIndex],
    cells: correlation.symbols.map((_, colIndex) => {
      const raw = correlation.matrix?.[rowIndex]?.[colIndex];
      const value = clampCorr(raw);
      // ⚠ ambiguity-audit D8: 相关性格子要**显示样本数 n** —— 标的不全同期时,
      //    pairwise 交集的对数不同, 只看相关系数会误判可信度。
      //    后端 key 规则: 字典序小的在前(`symbols` 已排序, 所以按下标拼即可)。
      const other = correlation.symbols[colIndex];
      const sampleKey = rowIndex <= colIndex ? `${rowSymbol}|${other}` : `${other}|${rowSymbol}`;
      const samples = rowIndex === colIndex ? null : (correlation.sample_sizes?.[sampleKey] ?? null);
      return {
        value,
        text: value === null ? EMPTY : value.toFixed(2),
        samples,
        sampleText: samples === null ? '' : `n=${samples}`,
        diagonal: rowIndex === colIndex,
        ...correlationCellStyle(value),
      };
    }),
  }));
  return { start: correlation.start, end: correlation.end, columns, rows };
};

// ---------------------------------------------------------------------------
// 净值曲线(区域④)
// ---------------------------------------------------------------------------

/**
 * 后端归一净值(起点 = 1.0) → 收益率 % 序列。
 * 规格 §二-④: 纵轴是收益率 % 且**以区间起点归一为 0%**, 写死不随再平衡变。
 */
export const normalizeToReturnPct = (values) =>
  (values ?? []).map((v) => (v === null || v === undefined ? null : Number(((Number(v) - 1) * 100).toFixed(4))));

/** 曲线数据 → echarts 视图模型(主曲线 + 可选基准, 同一条日期轴)。 */
export const buildChartData = (nav) => {
  if (!nav || !nav.dates?.length) return null;
  const benchmark = nav.benchmark
    ? {
        symbol: nav.benchmark.symbol,
        name: nav.benchmark.name || nav.benchmark.symbol,
        priceBasis: nav.benchmark.price_basis,
        values: normalizeToReturnPct(nav.benchmark.nav),
      }
    : null;
  return {
    dates: nav.dates,
    portfolio: normalizeToReturnPct(nav.nav),
    benchmark,
  };
};

// ---------------------------------------------------------------------------
// 口径提示(区域⑧)
// ---------------------------------------------------------------------------

/**
 * 本组合的口径**构成**(page-spec §三-3): 混合持仓必须说清由哪些口径组成,
 * 否则用户会拿"ETF 后复权价"与"场外分红再投净值"直接横向比较。
 *
 * 例: `后复权价 3 只 · 分红再投净值 1 只`；没有成员时给空串(不显示)。
 */
export const basisCompositionText = (assets = []) => {
  const counts = new Map();
  for (const asset of Array.isArray(assets) ? assets : []) {
    const basis = asset?.price_basis;
    if (!basis) continue;
    counts.set(basis, (counts.get(basis) ?? 0) + 1);
  }
  return [...counts.entries()].map(([basis, n]) => `${priceBasisLabel(basis)} ${n} 只`).join(' · ');
};

/** 后端给的逐条口径说明 → 追加一条基准口径提示(价格指数不含股息, 与组合不可直接比)。 */
export const buildBasisNotes = (result) => {
  const notes = [...(result?.price_basis_note ?? [])];
  const benchmark = result?.benchmark;
  if (benchmark?.price_basis === 'PRICE') {
    const line = `基准 ${benchmark.symbol}（${benchmark.name || '—'}）为价格指数，不含股息；本组合含分红，两者口径不同，对照仅供方向参考`;
    if (!notes.some((n) => n.includes('价格指数'))) notes.push(line);
  }
  return notes;
};
