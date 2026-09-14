// 可转债等权指数日频: 前端契约与数据纯函数(T2)。
//
// 与后端 GET /api/cb-index/daily 对齐:
//   输入 = API 原始数组(后端已按 trade_date 降序, 这里不依赖顺序)。
//   normalizeCbMarketRows: 清洗/校验/升序, 输出 {rows, 计数}。
//   selectCbMarketWindow: 按自然年窗口裁剪(锚点=最新合法日期, 非今天)。
//
// 全部为纯函数: 不修改输入, 输出新对象。

// ---------------------------------------------------------------------------
// 日期与数值基础工具
// ---------------------------------------------------------------------------

/** 严格 ISO 日期校验: 必须是真实存在的 YYYY-MM-DD(2026-02-30 之类非法)。 */
function isValidIsoDate(s) {
  if (typeof s !== 'string') return false;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return false;
  const dt = new Date(y, mo - 1, d);
  // 回环校验: 溢出(如 02-30 → 03-02)即判非法
  return dt.getFullYear() === y && dt.getMonth() === mo - 1 && dt.getDate() === d;
}

/** 仅有限数(排除 NaN / Infinity / 非 number)。 */
function isFiniteNumber(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function fmtDate(y, mo, d) {
  return `${y}-${pad2(mo)}-${pad2(d)}`;
}

function isLeap(y) {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

/**
 * 自然年减 years: 锚点日期往前推 years 年。
 * 闰日 2/29 若目标年非闰年, 回退到该年 2 月末(2/28)。
 * 边界包含两端(调用方按 >= from 且 <= to 过滤)。
 */
function shiftYear(dateStr, years) {
  const [y, mo, d] = dateStr.split('-').map(Number);
  const ny = y - years;
  if (mo === 2 && d === 29 && !isLeap(ny)) {
    return fmtDate(ny, 2, 28);
  }
  return fmtDate(ny, mo, d);
}

// ---------------------------------------------------------------------------
// normalizeCbMarketRows
// ---------------------------------------------------------------------------

/**
 * 清洗/校验 API 原始数组, 输出升序 rows 与异常计数。
 *
 * @param {Array} raw API 原始数组(每个元素含 trade_date/median_price/avg_ytm/count)
 * @returns {{rows: Array, invalidDateCount: number, duplicateDateCount: number, invalidValueCount: number}}
 *   rows: 按 trade_date 升序, 每条 {trade_date, median_price, avg_ytm, count}
 *   invalidDateCount: 非法日期记录数(整条丢弃)
 *   duplicateDateCount: 重复日期整组丢弃的记录数(按丢弃记录数计)
 *   invalidValueCount: 保留记录中 median_price / avg_ytm 值异常计数(各字段各计 1)
 *
 * 规则:
 *   - trade_date 非法(非真实 ISO 日期) → 整条丢弃计 invalidDateCount
 *   - 同一日期出现多条 → 整组丢弃计 duplicateDateCount(按记录数)
 *   - median_price: 仅接受有限正数; null/非有限/非正数 → null 并计 invalidValueCount
 *   - avg_ytm: 接受有限数(含 0 与负数), 不乘 100; null/非有限 → null 并计 invalidValueCount
 *   - count: 仅接受非负整数; 异常 → null(不计入 invalidValueCount)
 *   - 原始响应非数组 → 抛 TypeError(契约错误)
 */
export function normalizeCbMarketRows(raw) {
  if (!Array.isArray(raw)) {
    throw new TypeError('cb-index/daily 响应必须是数组(契约错误)');
  }

  let invalidDateCount = 0;
  let duplicateDateCount = 0;
  let invalidValueCount = 0;

  // 按日期分组, 同时剔除非法日期
  const byDate = new Map();
  for (const rec of raw) {
    const td = rec && rec.trade_date;
    if (!isValidIsoDate(td)) {
      invalidDateCount += 1;
      continue;
    }
    if (!byDate.has(td)) byDate.set(td, []);
    byDate.get(td).push(rec);
  }

  // 重复日期: 整组丢弃, 按丢弃记录数计
  const unique = [];
  for (const [, recs] of byDate) {
    if (recs.length > 1) {
      duplicateDateCount += recs.length;
      continue;
    }
    unique.push(recs[0]);
  }

  // 清洗每条保留记录的字段(输出新对象, 不改输入)
  const rows = unique
    .map((rec) => {
      // median_price: 仅接受有限正数
      let median_price = rec.median_price;
      if (median_price === null) {
        median_price = null;
      } else if (!isFiniteNumber(median_price) || median_price <= 0) {
        median_price = null;
        invalidValueCount += 1;
      }

      // avg_ytm: 接受有限数(含 0 与负数), 不乘 100
      let avg_ytm = rec.avg_ytm;
      if (avg_ytm === null) {
        avg_ytm = null;
      } else if (!isFiniteNumber(avg_ytm)) {
        avg_ytm = null;
        invalidValueCount += 1;
      }

      // count: 仅接受非负整数
      let count = rec.count;
      if (!isFiniteNumber(count) || !Number.isInteger(count) || count < 0) {
        if (count !== null) invalidValueCount += 1;
        count = null;
      }

      return { trade_date: rec.trade_date, median_price, avg_ytm, count };
    })
    .sort((a, b) =>
      a.trade_date < b.trade_date ? -1 : a.trade_date > b.trade_date ? 1 : 0,
    );

  return { rows, invalidDateCount, duplicateDateCount, invalidValueCount };
}

// ---------------------------------------------------------------------------
// selectCbMarketWindow
// ---------------------------------------------------------------------------

const RANGE_YEARS = { '1y': 1, '3y': 3, '5y': 5 };

function cmpTradeDate(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * 按自然年窗口裁剪已规范化的 rows(升序)。
 *
 * @param {Array} rows normalizeCbMarketRows 输出的 rows(升序)
 * @param {'1y'|'3y'|'5y'|'all'} range 窗口
 * @returns {{rows: Array, from: string|null, to: string|null, insufficientHistory: boolean}}
 *   锚点 = rows 中最大 trade_date(最新合法日期, 非今天)。
 *   'all' → 返回全部, from/to 取实际区间, insufficientHistory=false。
 *   不足所选年数 → insufficientHistory=true 且返回全部记录(从实际最早到锚点)。
 *   空 rows → {rows:[], from:null, to:null, insufficientHistory:false}。
 */
export function selectCbMarketWindow(rows, range) {
  const list = Array.isArray(rows) ? rows : [];
  if (list.length === 0) {
    return { rows: [], from: null, to: null, insufficientHistory: false };
  }

  // 升序副本, 不修改输入
  const sorted = [...list].sort((a, b) => cmpTradeDate(a.trade_date, b.trade_date));
  const anchor = sorted[sorted.length - 1].trade_date; // 最新合法日期
  const earliest = sorted[0].trade_date;

  if (range === 'all') {
    return { rows: sorted, from: earliest, to: anchor, insufficientHistory: false };
  }

  const years = RANGE_YEARS[range];
  if (years == null) {
    throw new TypeError(`未知窗口范围: ${range}`);
  }

  const from = shiftYear(anchor, years);
  // 最早可用日期晚于窗口下界 → 历史不足所选年数
  if (earliest > from) {
    return { rows: sorted, from: earliest, to: anchor, insufficientHistory: true };
  }

  const sliced = sorted.filter(
    (r) => r.trade_date >= from && r.trade_date <= anchor,
  );
  return { rows: sliced, from, to: anchor, insufficientHistory: false };
}

// ---------------------------------------------------------------------------
// 历史分位
// ---------------------------------------------------------------------------

/** 分位最少有效样本数: 低于该值样本不可信, 一律返回 null(页面显示 —)。 */
const PERCENTILE_MIN_SAMPLES = 20;

/**
 * 分位核心: value 在样本中的百分位(0~100)。
 *
 * 口径 = "当前高于窗口内 p% 的交易日"(与估值页一致):
 *   p = (小于 value 的样本数 + 0.5 × 等于 value 的样本数) / 有效样本数 × 100,
 *   等值取中间秩(与估值详情页图表悬停口径一致)。
 *
 * @param {Array<number|null>} samples 样本(含 null 会被剔除)
 * @param {number|null} value 待定位的值
 * @param {{minSamples?: number}} [opts] 最低有效样本数(默认 20)
 * @returns {number|null} value 非有限数或有效样本不足 → null
 */
export function percentileRank(samples, value, { minSamples = PERCENTILE_MIN_SAMPLES } = {}) {
  if (!isFiniteNumber(value)) return null;
  const list = (Array.isArray(samples) ? samples : []).filter(isFiniteNumber);
  if (list.length < minSamples) return null;
  let below = 0;
  let equal = 0;
  for (const s of list) {
    if (s < value) below += 1;
    else if (s === value) equal += 1;
  }
  return ((below + equal / 2) / list.length) * 100;
}

/**
 * 摘要分位(固定 5 年口径): 末条记录 key 字段值在"锚点(末条日期)回看 5 年"内的分位。
 *
 * 与估值详情页"顶部固定 5 年 · 历史不足时按实际样本"先例一致:
 * 历史不足 5 年 → 用全部实际样本, since 返回窗口内实际最早日期。
 * 不随窗口按钮/游标变化(调用方应传全量升序 rows)。
 *
 * @param {Array} rows normalizeCbMarketRows 输出的 rows(顺序不依赖, 内部会升序排列)
 * @param {'median_price'|'avg_ytm'} key 指标字段
 * @param {{years?: number}} [opts] 回看年数(默认 5)
 * @returns {{percentile: number|null, since: string|null, samples: number}|null}
 *   rows 为空 → null; 末条该字段缺失或样本不足 → percentile=null
 *   (since/samples 仍返回, 供页面标注统计范围)。
 */
export function summaryPercentile(rows, key, { years = 5 } = {}) {
  const list = Array.isArray(rows) ? rows : [];
  if (list.length === 0) return null;
  const sorted = [...list].sort((a, b) => cmpTradeDate(a.trade_date, b.trade_date));
  const anchor = sorted[sorted.length - 1].trade_date;
  const from = shiftYear(anchor, years);
  const inWindow = sorted.filter((r) => r.trade_date >= from && r.trade_date <= anchor);
  const samples = inWindow.map((r) => r[key]).filter(isFiniteNumber);
  return {
    percentile: percentileRank(samples, sorted[sorted.length - 1][key]),
    since: inWindow.length > 0 ? inWindow[0].trade_date : null,
    samples: samples.length,
  };
}

/**
 * 读数分位(当前窗口口径): 指定日期的 key 字段值在传入窗口 rows 内的分位。
 *
 * 随 1y/3y/5y/all 窗口按钮变化, 与估值详情页悬停分位口径一致
 * ("当日值在当前所选窗口内的回看排名, 不是历史当日向前滚动的排名")。
 *
 * @param {Array} windowRows 已按窗口裁剪的 rows(通常来自 selectCbMarketWindow)
 * @param {string} tradeDate 游标日期
 * @param {'median_price'|'avg_ytm'} key 指标字段
 * @returns {number|null} 日期不存在/该行值缺失/有效样本不足 → null
 */
export function windowPercentile(windowRows, tradeDate, key) {
  const list = Array.isArray(windowRows) ? windowRows : [];
  const row = list.find((r) => r.trade_date === tradeDate);
  if (!row) return null;
  return percentileRank(list.map((r) => r[key]), row[key]);
}

/**
 * 分位数(线性插值): samples 的 q% 分位值, q ∈ [0, 100]。
 *
 * 与 ValuationChart 的 percentile 同算法(索引 = (n-1)×q/100, 相邻位线性插值),
 * 但面向原始样本: 内部过滤非有限数并升序排列。
 * 图上 30/70 分位参考线基于此计算(当前窗口口径, 随窗口切换重算)。
 *
 * @param {Array<number|null>} samples 样本(含 null 会被剔除)
 * @param {number} q 分位点(0~100)
 * @returns {number|null} q 非有限数或有效样本为空 → null
 */
export function quantile(samples, q) {
  if (!isFiniteNumber(q)) return null;
  const sorted = (Array.isArray(samples) ? samples : [])
    .filter(isFiniteNumber)
    .sort((a, b) => a - b);
  if (!sorted.length) return null;
  const idx = (sorted.length - 1) * (q / 100);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

// ---------------------------------------------------------------------------
// 转债-国债利差序列清洗
// ---------------------------------------------------------------------------

/**
 * 清洗 GET /api/cb-index/spread 响应为可绘图 rows。
 *
 * @param {Object|null} raw 响应体; null(后端样本不足)或无 series → 空 rows
 * @returns {{rows: Array, invalidDateCount: number, duplicateDateCount: number, invalidValueCount: number}}
 *   rows: 按 trade_date 升序, 每条 {trade_date, avg_ytm, bond_yield, spread};
 *   非法日期整条丢弃, 重复日期整组丢弃(与 normalizeCbMarketRows 同规则);
 *   数值仅接受有限数(允许 0 与负数), 异常置 null 并计数。
 */
export function normalizeCbSpreadRows(raw) {
  const empty = { rows: [], invalidDateCount: 0, duplicateDateCount: 0, invalidValueCount: 0 };
  const series = raw && Array.isArray(raw.series) ? raw.series : [];
  if (!series.length) return empty;

  let invalidDateCount = 0;
  let duplicateDateCount = 0;
  let invalidValueCount = 0;

  const byDate = new Map();
  for (const rec of series) {
    const td = rec && rec.trade_date;
    if (!isValidIsoDate(td)) {
      invalidDateCount += 1;
      continue;
    }
    if (!byDate.has(td)) byDate.set(td, []);
    byDate.get(td).push(rec);
  }

  const unique = [];
  for (const [, recs] of byDate) {
    if (recs.length > 1) {
      duplicateDateCount += recs.length;
      continue;
    }
    unique.push(recs[0]);
  }

  const rows = unique
    .map((rec) => {
      const clean = (v) => {
        if (v === null) return null;
        if (!isFiniteNumber(v)) {
          invalidValueCount += 1;
          return null;
        }
        return v;
      };
      return {
        trade_date: rec.trade_date,
        avg_ytm: clean(rec.avg_ytm),
        bond_yield: clean(rec.bond_yield),
        spread: clean(rec.spread),
      };
    })
    .sort((a, b) => cmpTradeDate(a.trade_date, b.trade_date));

  return { rows, invalidDateCount, duplicateDateCount, invalidValueCount };
}
