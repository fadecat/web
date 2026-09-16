export const COMMODITY_WINDOWS = ['d21', 'd63', 'y1', 'y3', 'y5', 'y10'];
export const WINDOW_LABELS = {
  d21: '21日', d63: '63日', y1: '1年', y3: '3年', y5: '5年', y10: '10年',
};

export const statusLabel = (status) => ({
  high: '高位', low: '低位', neutral: '中性', divergent: '周期分化',
  insufficient: '数据不足', stale: '数据滞后', failed: '抓取失败', never: '未初始化',
}[status] || status || '未初始化');

export const statusTone = (status) => ({
  high: 'high', low: 'low', neutral: 'neutral', divergent: 'divergent',
  insufficient: 'muted', stale: 'muted', failed: 'muted', never: 'muted',
}[status] || 'muted');

export const statusColor = (status) => ({
  high: '#ef4444', low: '#2563eb', neutral: 'var(--el-text-color-regular)',
  divergent: '#f97316', stale: '#909399', failed: '#909399', insufficient: '#909399',
}[status] || '#909399');

export const metricTone = (overallStatus, signal) =>
  ['stale', 'failed', 'suspicious'].includes(overallStatus) ? 'muted' : statusTone(signal);

export const isUninitialized = (overview, { active = false, rows = [] } = {}) =>
  !active && rows.length > 0 && rows.length === Number(overview?.instrument_total || 0)
    && !overview?.data_date && rows.every((row) => row.latest_price == null);

export const formatNumber = (value, digits = 2) => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return number.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

export const formatPercentile = (value) =>
  value === null || value === undefined ? '—' : `${formatNumber(value, 1)}%`;

export const formatDateTime = (value) => {
  if (!value) return '—';
  const text = String(value).replace('T', ' ');
  return /(?:Z|[+-]\d\d:\d\d)$/.test(text) ? text : text.replace(/:00$/, '');
};

export const isTriggered = (row, window) => {
  const signal = window ? row?.windows?.[window]?.signal : row?.current_status || row?.signal;
  return signal === 'high' || signal === 'low';
};

export const buildCommodityChartOption = (prices = [], signalsByDate = {}) => ({
  animation: false,
  grid: { left: 42, right: 18, top: 22, bottom: 30 },
  tooltip: {
    trigger: 'axis',
    formatter(params) {
      const point = params?.[0];
      const date = point?.axisValue;
      const signals = signalsByDate[date] || [];
      const signalText = signals.length
        ? `<br/>信号：${signals.map((signal) => `${WINDOW_LABELS[signal.window] || signal.window} ${statusLabel(signal.signal)}${signal.percentile == null ? '' : ` ${formatPercentile(signal.percentile)}`}`).join('、')}`
        : '';
      return `${date}<br/>收盘价：${formatNumber(point?.value)}${signalText}`;
    },
  },
  xAxis: { type: 'category', data: prices.map((item) => item.date), boundaryGap: false, axisLabel: { hideOverlap: true } },
  yAxis: { type: 'value', scale: true },
  series: [{
    name: '收盘价', type: 'line', showSymbol: false, connectNulls: false,
    data: prices.map((item) => item.close),
    lineStyle: { color: '#2563eb', width: 2 }, itemStyle: { color: '#2563eb' },
  }],
});

export const renderCommodityChart = ({ echarts, element, instance, prices, signalsByDate }) => {
  if (!element) return instance;
  const chart = instance || echarts.init(element);
  chart.setOption(buildCommodityChartOption(prices, signalsByDate), true);
  return chart;
};

export const filtersFromQuery = (query = {}) => ({
  keyword: typeof query.keyword === 'string' ? query.keyword : '',
  category: typeof query.category === 'string' ? query.category : '',
  status: typeof query.status === 'string' ? query.status : '',
  window: typeof query.window === 'string' ? query.window : '',
  triggered: query.triggered === '1' || query.triggered === 'true',
  sortBy: typeof query.sort_by === 'string' ? query.sort_by : 'signal',
  sortOrder: query.sort_order === 'asc' ? 'asc' : 'desc',
});

export const queryFromFilters = (filters) => {
  const query = {};
  if (filters.keyword?.trim()) query.keyword = filters.keyword.trim();
  if (filters.category) query.category = filters.category;
  if (filters.status) query.status = filters.status;
  if (filters.window) query.window = filters.window;
  if (filters.triggered) query.triggered = '1';
  if (filters.sortBy && (filters.sortBy !== 'signal' || filters.sortOrder !== 'desc')) {
    query.sort_by = filters.sortBy;
    query.sort_order = filters.sortOrder === 'asc' ? 'asc' : 'desc';
  }
  return query;
};
