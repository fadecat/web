// 研究回放(次日 T 价位研究 V1)页面纯逻辑: 文案常量、URL-query 往返、
// 原因码/类别中文映射、参数比较网格构建。
//
// 措辞约束(设计 §8): 一律「信号回放 / 价位触达 / 模型价」, 禁「收益 / 回测收益率」——
// 所有面向用户的文案集中在 WORDING 与 *_LABELS, 页面组件不得另行造词。

export const WORDING = {
  pageKind: '信号回放',
  hitTerm: '价位触达',
  modelPrice: '模型价',
  modelPriceNote: '模型价，非可下单报价',
  retrospectiveNote:
    '回顾性局限：过去多年行情为今日首次下载的回顾性信号回放，非当年时点数据；触达≠成交，分位≠概率。',
  trainOnlyNote: '选参只看训练段',
  pathAmbiguousNote: '双侧同日触达，不假设先后、不计算盈亏',
};

// 禁用词: 单测断言本文件全部导出文案不含这些词, 防止措辞回退
export const FORBIDDEN_TERMS = ['收益', '回测收益率'];

// 原因码 → 中文(逐日明细/汇总可解释性; 设计 §6 全集)
export const REASON_LABELS = {
  INSUFFICIENT_HISTORY: '历史数据不足',
  EXTREME_Z: '极端乖离（|Z| 超限）',
  VOLATILITY_SPIKE: '波动尖峰',
  RAW_HFQ_MISMATCH: '复权口径不匹配',
  DATA_MISSING: '数据缺失',
  POSSIBLE_CORPORATE_ACTION: '疑似权益事件',
  CALENDAR_UNVERIFIED: '日历未覆盖',
  SUSPENSION_UNVERIFIED: '疑似停牌（未证实）',
  NEXT_DAY_CORPORATE_ACTION: '次日权益事件（排除）',
};

// 评价日类别 → 中文(设计 §7)
export const CATEGORY_LABELS = {
  BUY_ONLY: '买侧触达',
  SELL_ONLY: '卖侧触达',
  BOTH_HIT: '双侧触达（路径不明）',
  NO_HIT: '未触达',
  DISABLED: '计划停用',
  EXCLUDED_DATA: '数据不完整（排除）',
  EXCLUDED_CORP_ACTION: '次日权益事件（排除）',
  EXCLUDED_OPEN_INVALIDATION: '开盘失效（排除）',
  CALENDAR_UNVERIFIED: '日历未覆盖',
};

export const SIDE_LABELS = { buy: '买侧', sell: '卖侧' };
export const SEGMENT_LABELS = { train: '训练段', validation: '验证段', overall: '全区间' };

export const reasonLabel = (code) => REASON_LABELS[code] || code;
export const categoryLabel = (code) => CATEGORY_LABELS[code] || code;

// ---------------------------------------------------------------------------
// URL-query 筛选往返(照抄 commodity.mjs:109-131 的约定: 缺省字段不进 query)
// ---------------------------------------------------------------------------

const _dateText = (value) => (/^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? String(value) : '');

export const filtersFromQuery = (query = {}) => ({
  symbol: typeof query.symbol === 'string' ? query.symbol : '',
  startDate: _dateText(query.start_date),
  endDate: _dateText(query.end_date),
  lambda: typeof query.lambda === 'string' && query.lambda !== '' ? Number(query.lambda) : '',
  window: typeof query.window === 'string' && query.window !== '' ? Number(query.window) : '',
});

export const queryFromFilters = (filters) => {
  const query = {};
  if (filters.symbol) query.symbol = filters.symbol;
  if (filters.startDate) query.start_date = filters.startDate;
  if (filters.endDate) query.end_date = filters.endDate;
  // λ/窗口是「定位某个 run」的查看器筛选, 不参与后端计算(POST 固定 12 组合网格)
  if (filters.lambda !== '' && filters.lambda !== null && filters.lambda !== undefined) {
    query.lambda = String(filters.lambda);
  }
  if (filters.window !== '' && filters.window !== null && filters.window !== undefined) {
    query.window = String(filters.window);
  }
  return query;
};

// ---------------------------------------------------------------------------
// 数值/比例格式化
// ---------------------------------------------------------------------------

export const formatRate = (value, digits = 1) => {
  if (value === null || value === undefined) return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${(number * 100).toFixed(digits)}%`;
};

export const formatNumber = (value, digits = 4) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number.toFixed(digits);
};

// ---------------------------------------------------------------------------
// 汇总/参数比较网格构建
// ---------------------------------------------------------------------------

// 单段(overall/train/validation) → 三档×两侧的平铺行, 供汇总表渲染
export const buildSegmentRows = (segment) => {
  if (!segment) return [];
  const rows = [];
  for (const side of ['buy', 'sell']) {
    for (const tier of segment[side] || []) {
      rows.push({
        side,
        sideLabel: SIDE_LABELS[side],
        tier: tier.tier,
        numerator: tier.numerator,
        denominator: tier.denominator,
        rate: tier.rate,
        rateText: formatRate(tier.rate),
      });
    }
  }
  return rows;
};

// 参数比较: [{run_id, param_lambda, quantile_window, train, validation, sample_coverage}]
// → 按 λ 升序再窗口升序的平铺行; trainBuy/trainSell 等为「档位比例矩阵」键。
export const buildComparisonRows = (comparison) => {
  const rows = (Array.isArray(comparison) ? comparison : [])
    .slice()
    .sort((a, b) => (a.param_lambda - b.param_lambda) || (a.quantile_window - b.quantile_window));
  return rows.map((run) => {
    const row = {
      runId: run.run_id,
      lambda: run.param_lambda,
      window: run.quantile_window,
      lambdaText: `λ=${run.param_lambda}`,
      windowText: `${run.quantile_window}日`,
    };
    for (const segment of ['train', 'validation']) {
      const summary = run[segment] || {};
      for (const side of ['buy', 'sell']) {
        row[`${segment}_${side}`] = (summary[side] || []).map((tier) => ({
          tier: tier.tier,
          numerator: tier.numerator,
          denominator: tier.denominator,
          rateText: formatRate(tier.rate),
        }));
      }
      row[`${segment}_dayCount`] = summary.day_count ?? '—';
    }
    return row;
  });
};

// 从比较网格里挑出与筛选 λ/窗口匹配的行(无筛选时取第一行)
export const pickComparisonRow = (rows, { lambda, window } = {}) => {
  if (!rows.length) return null;
  if (lambda === '' || lambda === null || lambda === undefined) return rows[0];
  return rows.find((row) => String(row.lambda) === String(lambda)
    && (window === '' || window === null || window === undefined
      || String(row.window) === String(window))) || rows[0];
};
