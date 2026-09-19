// 组合实验室(P0 标的注册与按需抓取)纯逻辑: 类型/口径/状态中文映射、
// 数据区间文案、组合共同起点计算与起点前移警示文案。
//
// 与页面解耦(不 import vue)：node:test 可直接跑, 也便于组合详情 L2 复用同一套口径。

export const SECURITY_TYPE_LABELS = {
  STOCK: '股票',
  ETF: 'ETF',
  FUND: '场外基金',
};

// 复权口径必须显式摆到用户面前: 不同口径的曲线不可直接比较
export const PRICE_BASIS_LABELS = {
  HFQ: '后复权价',
  NAV_ADJ: '分红再投净值',
  PRICE: '价格指数',
};

export const SYNC_STATUS_LABELS = {
  running: '同步中',
  success: '就绪',
  failed: '失败',
};

// 类型选择 chip: 值为后端 type 查询参数, auto 表示不传(交由后端判定)
export const TYPE_HINT_OPTIONS = [
  { value: 'auto', label: '自动' },
  { value: 'stock', label: '股票' },
  { value: 'etf', label: 'ETF' },
  { value: 'fund', label: '场外基金' },
];

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export const isDate = (value) => typeof value === 'string' && DATE_RE.test(value);

export const securityTypeLabel = (type) =>
  SECURITY_TYPE_LABELS[String(type || '').toUpperCase()] || String(type || '—');

export const priceBasisLabel = (basis) =>
  PRICE_BASIS_LABELS[basis] || (basis ? String(basis) : '—');

export const syncStatusLabel = (status) => SYNC_STATUS_LABELS[status] || '未知';

// 候选代码后缀 → 交易所(候选对象不带 exchange 字段, 只能从规范代码推)
const EXCHANGE_BY_SUFFIX = {
  SH: '上交所',
  SZ: '深交所',
  BJ: '北交所',
  OF: '场外',
  HK: '港交所',
};

export const exchangeLabel = (symbol) => {
  const suffix = String(symbol || '').split('.').pop().toUpperCase();
  return EXCHANGE_BY_SUFFIX[suffix] || (suffix && suffix !== String(symbol) ? suffix : '—');
};

// 数据区间: 未入库(row_count 为空/0)时必须显式说「尚未抓取」, 不能显示空区间
export const formatRange = (firstDate, lastDate, rowCount) => {
  const count = Number(rowCount);
  if (!Number.isFinite(count) || count <= 0) return '—（尚未抓取）';
  return `${firstDate || '—'} ~ ${lastDate || '—'}（${count} 个交易日）`;
};

export const formatCount = (rowCount) => {
  const count = Number(rowCount);
  return Number.isFinite(count) && count > 0 ? String(count) : '—';
};

// 组合共同起点 = 所有标的数据可得区间的共同起点 = first_date 里最晚的那个。
// 任一标的还没抓到数据(缺 first_date)则整体起点未定: pending=true, 页面显示「待抓取」。
export const computePortfolioStart = (assets = []) => {
  const list = Array.isArray(assets) ? assets : [];
  if (!list.length) return { startDate: null, pending: false };
  const dates = list.map((row) => row?.first_date).filter(isDate);
  const pending = dates.length !== list.length;
  const startDate = dates.length ? dates.reduce((max, cur) => (cur > max ? cur : max)) : null;
  return { startDate, pending };
};

// 跨年份差(保留 1 位小数): 用于「起点前移 N 年」
export const yearsBetween = (from, to) => {
  if (!isDate(from) || !isDate(to)) return null;
  const days = (Date.parse(to) - Date.parse(from)) / 86400000;
  return Math.round((days / 365.25) * 10) / 10;
};

// 起点前移警示文案(P0 最容易被忽略、但影响最大的一条)
export const buildStartShiftNotice = ({ oldStart, newStart, asset = {} } = {}) => {
  const name = asset.name || asset.symbol || '该标的';
  const symbol = asset.symbol ? ` ${asset.symbol}` : '';
  const since = asset.first_date || newStart || '—';
  const title = `⚠ 新的组合起点：${newStart || '—'}（受 ${name}${symbol} 制约，其数据始于 ${since}）`;
  const shiftYears = yearsBetween(oldStart, newStart);
  const detail = oldStart && shiftYears !== null
    ? `原起点 ${oldStart} → 前移 ${shiftYears} 年`
    : `原起点 ${oldStart || '—（首个标的）'}`;
  return { title, detail, shiftYears };
};

// 候选唯一键: 同一裸代码可能同时命中股票/场外基金, 只用 symbol 会撞车
export const candidateKey = (candidate) =>
  `${candidate?.security_type || ''}:${candidate?.symbol || ''}`;
