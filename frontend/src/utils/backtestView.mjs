// 组合详情页(L2)的纯函数: 收益条七格 / 指标卡 / 相关性着色 / 曲线换算 / 口径文案。
// 抽成纯函数的理由: 这些规则来自 page-spec §二 的逐区域规定(格子口径、着色方向、
// 归一方式), 放在模板里既测不了也容易两处写不一致。

import { EMPTY, TREND_FLAT, TREND_UP, TREND_DOWN, trendOf, formatReturnPct } from './portfolioList.mjs';

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
