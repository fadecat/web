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
  return String(value).replace('T', ' ').replace(/:00$/, '');
};

export const isTriggered = (row, window) => {
  const signal = window ? row?.windows?.[window]?.signal : row?.current_status || row?.signal;
  return signal === 'high' || signal === 'low';
};

export const filtersFromQuery = (query = {}) => ({
  keyword: typeof query.keyword === 'string' ? query.keyword : '',
  category: typeof query.category === 'string' ? query.category : '',
  status: typeof query.status === 'string' ? query.status : '',
  window: typeof query.window === 'string' ? query.window : '',
  triggered: query.triggered === '1' || query.triggered === 'true',
});

export const queryFromFilters = (filters) => {
  const query = {};
  if (filters.keyword?.trim()) query.keyword = filters.keyword.trim();
  if (filters.category) query.category = filters.category;
  if (filters.status) query.status = filters.status;
  if (filters.window) query.window = filters.window;
  if (filters.triggered) query.triggered = '1';
  return query;
};

