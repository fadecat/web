// 组合列表页(L1)与详情页(L2)共用的纯函数: 格式化 / 涨跌配色 / 卡片视图模型。
// 抽出来的理由: 列表页三格与详情页收益条必须显示同一批数字(规格 9.3),
// 格式化规则集中在这里, 避免两页各写一套导致不一致。

export const EMPTY = '—';

// 涨红跌绿(A 股习惯, 与欧美相反)
export const TREND_UP = 'up';
export const TREND_DOWN = 'down';
export const TREND_FLAT = 'flat';

/**
 * 收益数值 → 趋势类名。注意: 后端传的是**百分比数值**(1.08 表示 1.08%)。
 * null/undefined → flat(页面显示 —, 见 formatReturnPct)。
 */
export const trendOf = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return TREND_FLAT;
  if (value > 0) return TREND_UP;
  if (value < 0) return TREND_DOWN;
  return TREND_FLAT;
};

/**
 * 收益数值 → 显示文本。
 * ⚠ 规格 9.3: 韭圈儿日收益给 4 位、近一月/今年以来给 2 位(不一致);
 *   我方**统一 2 位**(9.3 的建议), 避免同一行里精度不齐。
 * ⚠ 规格 9.4-9: 空组合三格显示 `—`(灰), **不显示 `0.00%`**(否则会被误读成"收益为零")。
 */
export const formatReturnPct = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return `${Number(value).toFixed(digits)}%`;
};

/** ISO 日期时间 → `YYYY-MM-DD`; 缺失时给占位符。 */
export const formatDate = (value, fallback = EMPTY) => {
  if (!value) return fallback;
  return String(value).slice(0, 10);
};

const TREND_CLASS = {
  [TREND_UP]: 'trend-up',
  [TREND_DOWN]: 'trend-down',
  [TREND_FLAT]: 'trend-flat',
};

/** 三格指标(日收益 / 近一月 / 今年以来) → 渲染用视图模型。 */
export const buildMetricCells = (portfolio) => {
  const raw = [
    { key: 'day', label: '日收益', value: portfolio?.cached_day_return },
    { key: 'month', label: '近一月', value: portfolio?.cached_month_return },
    { key: 'ytd', label: '今年以来', value: portfolio?.cached_ytd_return },
  ];
  return raw.map((item) => ({
    ...item,
    text: formatReturnPct(item.value),
    className: TREND_CLASS[trendOf(item.value)],
    isEmpty: item.value === null || item.value === undefined,
  }));
};

/**
 * 卡片视图模型: 把后端组合对象转成页面直接可渲染的结构。
 * `成立时间` = 组合创建日; `收益时间` = 三格缓存的数据截止日(规格 9.4-6)。
 */
export const buildPortfolioCard = (portfolio) => ({
  id: portfolio?.id,
  name: portfolio?.name || '未命名组合',
  createdAt: formatDate(portfolio?.created_at),
  asofDate: formatDate(portfolio?.cached_asof_date),
  assetCount: portfolio?.asset_count ?? 0,
  metrics: buildMetricCells(portfolio),
});

/** 权重显示: 未设置显示 —, 已设置保留 2 位小数。 */
export const formatWeight = (value) => {
  if (value === null || value === undefined || Number.isNaN(value)) return EMPTY;
  return `${Number(value).toFixed(2)}%`;
};

/**
 * 权重合计状态 → 人话提示(列表底部常驻)。
 * 与后端 `ready` 同源: 必须每个成员都有权重 且 合计=100%(无现金腿)。
 */
export const weightSummaryText = (state) => {
  if (!state || !state.assets || state.assets.length === 0) return '还没有标的，先添加标的再设权重';
  const unset = state.assets.filter((a) => a.target_weight === null || a.target_weight === undefined);
  if (unset.length > 0) return `还有 ${unset.length} 个标的未设权重`;
  const sum = Number(state.weight_sum ?? 0);
  if (Math.abs(sum - 100) > 0.01) return `权重合计 ${sum.toFixed(2)}%，需调整到 100% 才能回测`;
  return '权重合计 100%，可以回测';
};

/** 数据就绪摘要 → 人话(回测前的检查, 不阻塞、给「立即同步」出口)。 */
export const readinessText = (readiness) => {
  if (!readiness) return '';
  if (readiness.all_ready) {
    return readiness.common_start ? `数据就绪，共同起点 ${readiness.common_start}` : '数据就绪';
  }
  const missing = (readiness.assets || []).filter((a) => !a.ok).map((a) => a.symbol);
  if (missing.length === 0) return '暂无标的数据';
  return `以下标的缺数据，需先同步：${missing.join('、')}`;
};
