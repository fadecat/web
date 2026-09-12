// 股票高股息快照: 筛选/排序/行业树/格式化纯函数(P2)。
//
// 与后端 GET /api/stock-dividend/latest 对齐:
//   输入 = API 原始数组(后端已按 dividend_rate 降序, 这里不依赖顺序)。
//   筛选语义复刻集思录股息率排行页(服务端 SQL 过滤的本地等价):
//   数值筛选启用时 null 行被过滤(NULL 比较恒假), 未启用时保留。
//
// 全部为纯函数: 不修改输入, 输出新对象; 不 import Vue/axios。

// ---------------------------------------------------------------------------
// 列配置(单一事实源, 页面模板 v-for 渲染)
// ---------------------------------------------------------------------------

/**
 * 25 列配置(对齐集思录展示列; 波动率/质押比例两列非会员账号恒无数据, 不复刻)。
 * 元素: {field, label, width, align, fmt, pct?, sortKey?, className?, headerTip?, fixed?}
 *   fmt: 'num2'两位小数 | 'volume'千分位整数 | 'signed'正负着色 | 'temp'温度色阶 | 'text'
 *   pct: 单元格数值后追加 '%'
 */
export const STOCK_DIVIDEND_COLUMNS = [
  { field: 'stock_id', label: '代码', width: 80, align: 'center', fmt: 'text', fixed: 'left' },
  { field: 'stock_nm', label: '名称', width: 110, align: 'left', fmt: 'text', fixed: 'left' },
  { field: 'price', label: '价格', width: 75, align: 'right', fmt: 'num2' },
  { field: 'increase_rt', label: '涨幅', width: 80, align: 'right', fmt: 'signed', pct: true },
  { field: 'volume', label: '成交额(万)', width: 95, align: 'right', fmt: 'volume' },
  { field: 'total_value', label: '总市值(亿)', width: 95, align: 'right', fmt: 'num2', headerTip: '按A股计价总市值(亿元)' },
  { field: 'float_value', label: '流通市值(亿)', width: 95, align: 'right', fmt: 'num2', headerTip: '按A股计价流通市值(亿元)' },
  { field: 'pe', label: 'PE-TTM', width: 80, align: 'right', fmt: 'num2' },
  { field: 'pe_temperature', label: 'PE温度', width: 80, align: 'right', fmt: 'temp' },
  { field: 'pb', label: 'PB', width: 70, align: 'right', fmt: 'num2' },
  { field: 'pb_temperature', label: 'PB温度', width: 80, align: 'right', fmt: 'temp' },
  { field: 'aft_dividend', label: '5年平均股息率', width: 120, align: 'right', fmt: 'num2', pct: true, className: 'col-highlight', headerTip: '5年平均股息率=(5年累计每股分红÷5)/现价*100%; 其中5年累计每股分红=5年累计分红/现总股本' },
  { field: 'dividend_rate', label: '股息率TTM', width: 100, align: 'right', fmt: 'num2', pct: true, headerTip: '到前一交易日为止最近4个季报每股分红与当前股价的比值' },
  { field: 'dividend_rate2', label: '静态股息率', width: 100, align: 'right', fmt: 'num2', pct: true, headerTip: '上一自然年度收到的每股分红与当前股价的比值' },
  { field: 'roe', label: 'ROE', width: 75, align: 'right', fmt: 'num2', pct: true, headerTip: '最新年报ROE' },
  { field: 'roe_average', label: '5年平均ROE', width: 110, align: 'right', fmt: 'signed', pct: true, headerTip: '5年平均ROE, 算术平均, 0为上市时间小于5年' },
  { field: 'revenue_average', label: '5年营收复合', width: 110, align: 'right', fmt: 'signed', pct: true, headerTip: '5年营收复合增长率' },
  { field: 'profit_average', label: '5年利润复合', width: 110, align: 'right', fmt: 'signed', pct: true, headerTip: '5年利润复合增长率(每年5.1前最新一年用两年前的, 5.1后用一年前的)' },
  { field: 'cashflow_average', label: '5年现金流复合', width: 115, align: 'right', fmt: 'signed', pct: true, headerTip: '5年经营现金流净额复合增长率' },
  { field: 'dividend_rate_average', label: '5年分红率复合', width: 115, align: 'right', fmt: 'signed', pct: true, headerTip: '5年分红率复合增长率' },
  { field: 'eps_growth_ttm', label: '净利同比增长', width: 110, align: 'right', fmt: 'signed', pct: true, headerTip: '最新报告期归母净利同比增长' },
  { field: 'int_debt_rate', label: '有息负债率', width: 100, align: 'right', fmt: 'num2', pct: true, headerTip: '有息负债率=有息负债/(所有者权益合计+有息负债)*100%' },
  { field: 'debt_rate', label: '资产负债率', width: 100, align: 'right', fmt: 'num2', pct: true, headerTip: '资产负债率=负债合计/(所有者权益合计+负债合计)*100%' },
  { field: 'industry_nm', label: '行业', width: 120, align: 'left', fmt: 'text', sortKey: 'sw_cd' },
  { field: 'province', label: '地域', width: 90, align: 'left', fmt: 'text' },
];

export const DEFAULT_SORT = { prop: 'dividend_rate', order: 'descending' };

export const PAGE_SIZES = [20, 50, 100];

/** 筛选表单初始状态(null = 未启用; 行业/省份 '' = 未启用)。每次返回新对象。 */
export function emptyForm() {
  return {
    markets: [],
    industries: [],
    excludeIndustries: [],
    province: '',
    peMax: null,
    pbMax: null,
    peTMax: null,
    pbTMax: null,
    intDebtMax: null,
    dividendMin: null,
    aftDividendMin: null,
    roeMin: null,
    roeAverageMin: null,
    revenueAvgMin: null,
    profitAvgMin: null,
    epsGrowthTtmMin: null,
    cashflowAvgMin: null,
    totalValueMin: null,
    totalValueMax: null,
    floatValueMin: null,
    floatValueMax: null,
    soeOnly: false,
  };
}

/**
 * 把任意来源(服务端预设/本地草稿)的表单收敛为合法形状:
 * 仅保留已知键, 逐键做类型防护, 缺失键回退 emptyForm 默认值。
 */
export function sanitizeForm(input) {
  const f = emptyForm();
  const src = input || {};
  if (Array.isArray(src.markets)) {
    f.markets = src.markets.filter((m) => m === 'sh' || m === 'sz');
  }
  if (Array.isArray(src.industries)) {
    f.industries = src.industries.filter((v) => typeof v === 'string' && v);
  }
  if (Array.isArray(src.excludeIndustries)) {
    f.excludeIndustries = src.excludeIndustries.filter((v) => typeof v === 'string' && v);
  }
  if (typeof src.province === 'string') f.province = src.province;
  for (const key of [
    'peMax', 'pbMax', 'peTMax', 'pbTMax', 'intDebtMax',
    'dividendMin', 'aftDividendMin', 'roeMin', 'roeAverageMin',
    'revenueAvgMin', 'profitAvgMin', 'epsGrowthTtmMin', 'cashflowAvgMin',
    'totalValueMin', 'totalValueMax', 'floatValueMin', 'floatValueMax',
  ]) {
    const v = src[key];
    if (typeof v === 'number' && Number.isFinite(v)) f[key] = v;
  }
  if (typeof src.soeOnly === 'boolean') f.soeOnly = src.soeOnly;
  return f;
}

// ---------------------------------------------------------------------------
// 市场判定
// ---------------------------------------------------------------------------

/** 股票代码前缀 → 市场: 60/68→沪, 00/30→深, 其余(北交所等, 防御)→null。 */
export function marketOf(stockId) {
  const s = String(stockId || '');
  if (s.startsWith('60') || s.startsWith('68')) return 'sh';
  if (s.startsWith('00') || s.startsWith('30')) return 'sz';
  return null;
}

// ---------------------------------------------------------------------------
// 筛选(复刻集思录服务端过滤语义)
// ---------------------------------------------------------------------------

function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 上限: 值存在且有限且 <= 阈值(null 字段在筛选启用时不过, 对齐 SQL NULL 比较恒假)。 */
function le(v, t) {
  return isNum(v) && v <= t;
}

function ge(v, t) {
  return isNum(v) && v >= t;
}

function inRange(v, min, max) {
  return isNum(v) && v >= (min == null ? -Infinity : min) && v <= (max == null ? Infinity : max);
}

/**
 * 单行是否命中全部筛选条件(AND 合取)。
 *
 * 语义对齐集思录(服务端 SQL 过滤):
 * - 控件未启用(阈值 null / 数组空 / 字符串空) → 该条件恒真;
 * - 数值筛选启用时字段为 null → 不命中(保守排除, 与源站一致);
 * - 负值正常参与比较(如 PE ≤ 10 时 -5 命中);
 * - 区间单边启用即开区间; min > max 按字面判定为空集;
 * - 行业/排除行业可多选(sw_cd 前缀匹配): 包含=任一命中即过(OR),
 *   排除=任一命中即剔(含整棵子树); sw_cd 为空的行: 包含启用时被拒, 排除不影响。
 */
export function matchStock(row, form) {
  const f = form || emptyForm();

  if (Array.isArray(f.markets) && f.markets.length > 0) {
    if (!f.markets.includes(marketOf(row.stock_id))) return false;
  }

  if (Array.isArray(f.industries) && f.industries.length > 0) {
    const sw = String(row.sw_cd || '');
    // 包含=多选任一命中即过(OR); sw_cd 为空的行在包含启用时被拒
    if (!sw || !f.industries.some((p) => sw.startsWith(p))) return false;
  }

  // 排除行业: 任一选中前缀命中(任意层级, 含整棵子树)即剔除; sw_cd 为空的行不受影响
  if (Array.isArray(f.excludeIndustries) && f.excludeIndustries.length > 0) {
    const sw = String(row.sw_cd || '');
    if (sw && f.excludeIndustries.some((p) => sw.startsWith(p))) return false;
  }

  if (f.province && row.province !== f.province) return false;

  // 仅国资白名单: 行缺少央国企标注(名单缺失或未命中)则不通过
  if (f.soeOnly && !row.enterprise_nature) return false;

  if (f.peMax != null && !le(row.pe, f.peMax)) return false;
  if (f.pbMax != null && !le(row.pb, f.pbMax)) return false;
  if (f.peTMax != null && !le(row.pe_temperature, f.peTMax)) return false;
  if (f.pbTMax != null && !le(row.pb_temperature, f.pbTMax)) return false;
  if (f.intDebtMax != null && !le(row.int_debt_rate, f.intDebtMax)) return false;

  if (f.dividendMin != null && !ge(row.dividend_rate, f.dividendMin)) return false;
  if (f.aftDividendMin != null && !ge(row.aft_dividend, f.aftDividendMin)) return false;
  if (f.roeMin != null && !ge(row.roe, f.roeMin)) return false;
  if (f.roeAverageMin != null && !ge(row.roe_average, f.roeAverageMin)) return false;
  if (f.revenueAvgMin != null && !ge(row.revenue_average, f.revenueAvgMin)) return false;
  if (f.profitAvgMin != null && !ge(row.profit_average, f.profitAvgMin)) return false;
  if (f.epsGrowthTtmMin != null && !ge(row.eps_growth_ttm, f.epsGrowthTtmMin)) return false;
  if (f.cashflowAvgMin != null && !ge(row.cashflow_average, f.cashflowAvgMin)) return false;

  if ((f.totalValueMin != null || f.totalValueMax != null)
      && !inRange(row.total_value, f.totalValueMin, f.totalValueMax)) return false;
  if ((f.floatValueMin != null || f.floatValueMax != null)
      && !inRange(row.float_value, f.floatValueMin, f.floatValueMax)) return false;

  return true;
}

/** 过滤全部行(纯函数, 不改输入)。 */
export function filterRows(rows, form) {
  return (rows || []).filter((r) => matchStock(r, form));
}

// ---------------------------------------------------------------------------
// 行业树 / 省份选项(从数据行推导)
// ---------------------------------------------------------------------------

const SW_LEVELS = [
  [1, 2],
  [2, 4],
  [3, 6],
];

/**
 * 由数据行的 sw_cd(2/4/6 位层级码) + industry_nm2('一-二-三'路径) 推导级联树。
 *
 * 节点 {value, label, children}; label 取 industry_nm2 对应层级段,
 * 缺失时三级节点回退 industry_nm, 再回退码本身。
 * sw_cd 为空的行不进树; 同层按码字典序排序。输出直接作 el-cascader options。
 */
export function buildIndustryTree(rows) {
  const byValue = new Map(); // value -> node(带 children Map)

  const ensure = (value, level) => {
    if (!byValue.has(value)) {
      byValue.set(value, { value, level, label: '', children: new Map() });
    }
    return byValue.get(value);
  };

  for (const row of rows || []) {
    const sw = String(row.sw_cd || '').trim();
    if (!sw) continue;
    const segs = String(row.industry_nm2 || '')
      .split('-')
      .map((s) => s.trim());

    let parent = null;
    for (const [level, len] of SW_LEVELS) {
      if (sw.length < len) break;
      const node = ensure(sw.slice(0, len), level);
      if (parent) parent.children.set(node.value, node); // 挂到父节点
      if (!node.label) {
        const seg = segs[level - 1];
        if (seg) node.label = seg;
        else if (level === 3 && row.industry_nm) node.label = String(row.industry_nm);
        else if (level === 1) node.label = sw.slice(0, len);
      }
      parent = node;
    }
  }

  const toOptions = (node) => {
    const kids = [...node.children.values()]
      .sort((a, b) => (a.value < b.value ? -1 : a.value > b.value ? 1 : 0))
      .map(toOptions);
    return {
      value: node.value,
      label: node.label || node.value,
      children: kids,
    };
  };

  const roots = [...byValue.values()].filter((n) => n.level === 1)
    .sort((a, b) => (a.value < b.value ? -1 : a.value > b.value ? 1 : 0));
  return roots.map(toOptions);
}

/** 省份下拉选项: 非空去重 + 中文排序。 */
export function collectProvinceOptions(rows) {
  const set = new Set();
  for (const row of rows || []) {
    const p = row && row.province;
    if (p) set.add(p);
  }
  return [...set].sort((a, b) => a.localeCompare(b, 'zh'));
}

// ---------------------------------------------------------------------------
// 排序
// ---------------------------------------------------------------------------

function cmpCell(av, bv, numeric) {
  const aNull = av == null;
  const bNull = bv == null;
  if (aNull && bNull) return 0;
  if (aNull) return 1; // null 恒沉底(与 SelectionResults 惯例一致)
  if (bNull) return -1;
  if (numeric) return av - bv;
  return String(av).localeCompare(String(bv), 'zh');
}

/**
 * 前端排序: 按 {prop, order}(el-table sort-change 形态)。
 * null 恒沉底(不随升降序翻转); 同值按 stock_id 升序 tie-break; 纯函数。
 */
export function sortRows(rows, sort) {
  const list = [...(rows || [])];
  const { prop, order } = sort || {};
  if (!prop || !order) return list;

  const col = STOCK_DIVIDEND_COLUMNS.find((c) => c.field === prop);
  const key = col ? (col.sortKey || col.field) : prop;
  const numeric = col ? col.fmt !== 'text' : true;
  const dir = order === 'ascending' ? 1 : -1;
  const tie = (a, b) => String(a.stock_id || '').localeCompare(String(b.stock_id || ''));

  list.sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    // null 与方向无关恒沉底(降序时 null 不能浮到顶部)
    if (av == null || bv == null) {
      if (av == null && bv == null) return tie(a, b);
      return av == null ? 1 : -1;
    }
    const c = cmpCell(av, bv, numeric);
    if (c !== 0) return c * dir;
    return tie(a, b);
  });
  return list;
}

// ---------------------------------------------------------------------------
// 格式化 / 色阶
// ---------------------------------------------------------------------------

/** 数值: null/非有限 → '—', 否则 toFixed。 */
export function fmtNum(v, digits = 2) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return Number(v).toFixed(digits);
}

/** 成交额(万元): 千分位整数。 */
export function fmtVolume(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  return Math.round(Number(v)).toLocaleString('en-US');
}

/** 温度文本: 负值/空 → '—'(集思录口径, 仅显示层隐藏, 筛选排序照常参与)。 */
export function tempText(v) {
  if (v == null || !Number.isFinite(Number(v)) || Number(v) < 0) return '—';
  return Number(v).toFixed(2);
}

/** 温度色阶: <25 青 / <50 绿 / <75 橙 / ≥75 红(对齐集思录 liquidColour)。 */
export function tempClass(v) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n) || n < 0) return '';
  if (n < 25) return 't-cyan';
  if (n < 50) return 't-green';
  if (n < 75) return 't-orange';
  return 't-red';
}

/** 正负数文本: 正数带 '+'(与颜色双重编码), 负数原样, 空 → '—'。 */
export function signedText(v, digits = 2) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Number(v);
  const s = n.toFixed(digits);
  return n > 0 ? `+${s}` : s;
}

/** 正负着色类: 涨红跌绿(A股惯例)。 */
export function signedClass(v) {
  const n = Number(v);
  if (v == null || !Number.isFinite(n)) return '';
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return '';
}
