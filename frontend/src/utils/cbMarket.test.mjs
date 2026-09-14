// cbMarket.mjs 契约与纯函数测试: node --test 前端工具测试
// 运行: node --test src/utils/cbMarket.test.mjs (或 pnpm test:node)
// 覆盖方案 192-204 样例 + 非法日期/重复日期/非有限数/空数组/不改输入/
//       3年边界/闰日减年/历史不足/最新行缺值不回填
//       + 历史分位(中间秩/样本不足/5年固定口径/窗口内排名)
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normalizeCbMarketRows,
  selectCbMarketWindow,
  percentileRank,
  summaryPercentile,
  windowPercentile,
  quantile,
  normalizeCbSpreadRows,
} from './cbMarket.mjs';

// ---------------------------------------------------------------------------
// normalizeCbMarketRows
// ---------------------------------------------------------------------------

test('方案样例: 合法输入升序, null 不变成 0, 负收益率不缩放', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: 132.5, avg_ytm: -8.25, count: 400 },
    { trade_date: '2026-09-08', median_price: null, avg_ytm: 0, count: 399 },
  ]);
  assert.equal(out.rows[0].trade_date, '2026-09-08');
  assert.equal(out.rows[0].median_price, null); // null 原样, 不补 0
  assert.equal(out.rows[1].avg_ytm, -8.25); // 负收益率原值
  assert.equal(out.rows[0].avg_ytm, 0); // 0 合法, 不为 null
});

test('正常 null 字段保持缺失且不计为异常值', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: null, avg_ytm: null, count: null },
  ]);
  assert.equal(out.rows[0].median_price, null);
  assert.equal(out.rows[0].avg_ytm, null);
  assert.equal(out.invalidValueCount, 0);
});

test('升序输出: 输入降序也转升序', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-10', median_price: 130, avg_ytm: 1, count: 10 },
    { trade_date: '2026-09-09', median_price: 131, avg_ytm: 2, count: 11 },
    { trade_date: '2026-09-08', median_price: 132, avg_ytm: 3, count: 12 },
  ]);
  assert.deepEqual(
    out.rows.map((r) => r.trade_date),
    ['2026-09-08', '2026-09-09', '2026-09-10'],
  );
});

test('非法日期 2026-02-30: 整条丢弃并计 invalidDateCount', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-02-30', median_price: 100, avg_ytm: 1, count: 10 }, // 非法
    { trade_date: '2026-09-09', median_price: 100, avg_ytm: 1, count: 10 },
  ]);
  assert.equal(out.invalidDateCount, 1);
  assert.equal(out.rows.length, 1);
  assert.equal(out.rows[0].trade_date, '2026-09-09');
});

test('重复日期组剔除: 同日期多条整组丢弃, 按记录数计 duplicateDateCount', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: 100, avg_ytm: 1, count: 10 },
    { trade_date: '2026-09-09', median_price: 101, avg_ytm: 2, count: 11 }, // 重复
    { trade_date: '2026-09-08', median_price: 102, avg_ytm: 3, count: 12 },
  ]);
  assert.equal(out.duplicateDateCount, 2);
  assert.equal(out.rows.length, 1);
  assert.equal(out.rows[0].trade_date, '2026-09-08');
});

test('非有限数: median_price=Infinity 与 avg_ytm=NaN 置 null 并计 invalidValueCount', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: Infinity, avg_ytm: NaN, count: 10 },
  ]);
  assert.equal(out.rows[0].median_price, null);
  assert.equal(out.rows[0].avg_ytm, null);
  assert.equal(out.invalidValueCount, 2);
});

test('median_price 非正数(0/负数)置 null 并计 invalidValueCount', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: 0, avg_ytm: 1, count: 10 },
    { trade_date: '2026-09-08', median_price: -5, avg_ytm: 1, count: 10 },
  ]);
  assert.equal(out.rows[0].median_price, null);
  assert.equal(out.rows[1].median_price, null);
  assert.equal(out.invalidValueCount, 2);
});

test('count 异常(负/非整数/null)置 null, null 不计但其他异常计入 invalidValueCount', () => {
  const out = normalizeCbMarketRows([
    { trade_date: '2026-09-09', median_price: 100, avg_ytm: 1, count: -1 },
    { trade_date: '2026-09-08', median_price: 100, avg_ytm: 1, count: 3.5 },
    { trade_date: '2026-09-07', median_price: 100, avg_ytm: 1, count: null },
  ]);
  assert.equal(out.rows[0].count, null);
  assert.equal(out.rows[1].count, null);
  assert.equal(out.rows[2].count, null);
  assert.equal(out.invalidValueCount, 2); // null 缺失不计，负数和非整数计数
  // 合法整数值(含浮点整型 400.0)保留
  const ok = normalizeCbMarketRows([
    { trade_date: '2026-09-06', median_price: 100, avg_ytm: 1, count: 400.0 },
  ]);
  assert.equal(ok.rows[0].count, 400);
});

test('空数组: 全 0 计数, rows=[]', () => {
  const out = normalizeCbMarketRows([]);
  assert.deepEqual(out.rows, []);
  assert.equal(out.invalidDateCount, 0);
  assert.equal(out.duplicateDateCount, 0);
  assert.equal(out.invalidValueCount, 0);
});

test('非数组输入: 抛 TypeError(契约错误)', () => {
  assert.throws(() => normalizeCbMarketRows(null), TypeError);
  assert.throws(() => normalizeCbMarketRows({ rows: [] }), TypeError);
});

test('不改变输入: 原始数组与元素对象不被修改', () => {
  const raw = [
    { trade_date: '2026-09-09', median_price: 132.5, avg_ytm: -8.25, count: 400 },
    { trade_date: '2026-09-08', median_price: null, avg_ytm: 0, count: 399 },
  ];
  const snapshot = JSON.parse(JSON.stringify(raw));
  const out = normalizeCbMarketRows(raw);
  // 输入数组未变
  assert.deepEqual(raw, snapshot);
  // 输出元素是新对象
  assert.notEqual(out.rows[0], raw[0]);
  assert.notEqual(out.rows[1], raw[1]);
});

// ---------------------------------------------------------------------------
// selectCbMarketWindow
// ---------------------------------------------------------------------------

test("3年窗口包含边界: 最早=锚点-3年均被包含, 不足则 insufficientHistory", () => {
  const rows = [];
  for (let y = 2023; y <= 2026; y += 1) {
    rows.push({ trade_date: `${y}-09-09`, median_price: 100, avg_ytm: 1, count: 10 });
  }
  const out = selectCbMarketWindow(rows, '3y');
  assert.equal(out.insufficientHistory, false);
  assert.equal(out.from, '2023-09-09');
  assert.equal(out.to, '2026-09-09');
  assert.equal(out.rows[0].trade_date, '2023-09-09'); // 起止均包含
  assert.equal(out.rows[out.rows.length - 1].trade_date, '2026-09-09');
  assert.equal(out.rows.length, 4);
});

test('闰日减年: 2024-02-29 减 1 年回退到 2023-02-28', () => {
  const rows = [
    { trade_date: '2024-02-29', median_price: 100, avg_ytm: 1, count: 10 },
    { trade_date: '2023-02-28', median_price: 99, avg_ytm: 0, count: 9 }, // 达窗口下界, 不触发不足
  ];
  const out = selectCbMarketWindow(rows, '1y');
  assert.equal(out.insufficientHistory, false);
  assert.equal(out.from, '2023-02-28'); // 2023 非闰年 → 2 月末
  assert.equal(out.to, '2024-02-29');
  assert.equal(out.rows.length, 2);
});

test('闰日减年: 2020-02-29 减 4 年保持 2016-02-29(闰年)', () => {
  const rows = [
    { trade_date: '2020-02-29', median_price: 100, avg_ytm: 1, count: 10 },
  ];
  const out = selectCbMarketWindow(rows, '5y');
  // 5y 从 2020-02-29 → 2015-02-28(非闰), 不足则全返回
  assert.equal(out.insufficientHistory, true);
  assert.equal(out.to, '2020-02-29');
  assert.equal(out.rows[0].trade_date, '2020-02-29');
});

test('历史不足: 仅 3 天数据请求 5 年 → 返回全部且 insufficientHistory=true', () => {
  const rows = [
    { trade_date: '2026-09-07', median_price: 100, avg_ytm: 1, count: 10 },
    { trade_date: '2026-09-08', median_price: 101, avg_ytm: 2, count: 11 },
    { trade_date: '2026-09-09', median_price: 102, avg_ytm: 3, count: 12 },
  ];
  const out = selectCbMarketWindow(rows, '5y');
  assert.equal(out.insufficientHistory, true);
  assert.equal(out.from, '2026-09-07');
  assert.equal(out.to, '2026-09-09');
  assert.equal(out.rows.length, 3); // 返回全部
});

test('all: 返回全部, 区间为实际最早到锚点', () => {
  const rows = [
    { trade_date: '2026-09-07', median_price: 100, avg_ytm: 1, count: 10 },
    { trade_date: '2026-09-09', median_price: 102, avg_ytm: 3, count: 12 },
  ];
  const out = selectCbMarketWindow(rows, 'all');
  assert.equal(out.insufficientHistory, false);
  assert.equal(out.from, '2026-09-07');
  assert.equal(out.to, '2026-09-09');
  assert.equal(out.rows.length, 2);
});

test('最新行缺值不回填: 锚点取最大日期记录, 即使字段缺失也不回退旧日', () => {
  const rows = [
    { trade_date: '2026-09-08', median_price: 105, avg_ytm: 2.5, count: 11 },
    { trade_date: '2026-09-09', median_price: null, avg_ytm: null, count: null }, // 最新, 缺值
  ];
  const out = selectCbMarketWindow(rows, '1y');
  assert.equal(out.to, '2026-09-09'); // 锚点是最新日
  const latest = out.rows[out.rows.length - 1];
  assert.equal(latest.trade_date, '2026-09-09');
  assert.equal(latest.median_price, null); // 不回填旧日 105
  assert.equal(latest.avg_ytm, null);
});

test('空 rows: 返回空且 insufficientHistory=false', () => {
  const out = selectCbMarketWindow([], '1y');
  assert.deepEqual(out.rows, []);
  assert.equal(out.from, null);
  assert.equal(out.to, null);
  assert.equal(out.insufficientHistory, false);
});

test('未知 range: 抛 TypeError', () => {
  assert.throws(() => selectCbMarketWindow([{ trade_date: '2026-09-09' }], '2y'), TypeError);
});

test('窗口锚点是最新合法日期而非今天(今天无数据也不影响)', () => {
  // 数据最新为 2020-09-09, 锚点据此减 1 年 = 2019-09-09, 与今天无关
  const rows = [
    { trade_date: '2020-09-09', median_price: 100, avg_ytm: 1, count: 10 },
    { trade_date: '2019-09-09', median_price: 99, avg_ytm: 0, count: 9 }, // 恰为窗口下界
  ];
  const out = selectCbMarketWindow(rows, '1y');
  assert.equal(out.to, '2020-09-09'); // 锚点是最新日
  assert.equal(out.from, '2019-09-09'); // 由锚点减年, 与今天无关
  assert.equal(out.insufficientHistory, false);
  assert.equal(out.rows.length, 2);
});

// ---------------------------------------------------------------------------
// 历史分位: percentileRank / summaryPercentile / windowPercentile
// ---------------------------------------------------------------------------

/** 按月生成升序 ISO 日期数组(每月 1 日), 供构造足量样本。 */
function monthlyDates(startYear, startMonth, count) {
  const out = [];
  let y = startYear;
  let m = startMonth;
  for (let i = 0; i < count; i += 1) {
    out.push(`${y}-${String(m).padStart(2, '0')}-01`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

test('percentileRank: 基本排名 1..100 中 value=50 → 49.5(自身计入等值)', () => {
  const samples = Array.from({ length: 100 }, (_, i) => i + 1);
  assert.equal(percentileRank(samples, 50), 49.5);
});

test('percentileRank: 等值取中间秩(8×10, 8×20, 8×30, value=20 → 50)', () => {
  const samples = [...Array(8).fill(10), ...Array(8).fill(20), ...Array(8).fill(30)];
  assert.equal(percentileRank(samples, 20), 50);
});

test('percentileRank: 剔除 null 样本; value 无效或非数组 → null', () => {
  const samples = [null, 1, 2, 3]; // 有效 3 个
  assert.equal(percentileRank(samples, 2, { minSamples: 3 }), 50); // (1+0.5)/3
  assert.equal(percentileRank([1, 2, 3], null, { minSamples: 3 }), null);
  assert.equal(percentileRank(null, 2, { minSamples: 3 }), null);
});

test('percentileRank: 有效样本不足默认 20 → null; 恰好 20 可算', () => {
  const few = Array.from({ length: 19 }, (_, i) => i + 1);
  assert.equal(percentileRank(few, 10), null);
  const enough = Array.from({ length: 20 }, (_, i) => i + 1);
  assert.equal(percentileRank(enough, 10), 47.5); // (9 + 0.5)/20
});

test('summaryPercentile: 固定 years 回看, since=窗口实际起点, 末条值参与自身排名', () => {
  // 锚 2026-09-01, years=2 → from=2024-09-01(边界包含); 25 条值 100..124 递增
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  const out = summaryPercentile(rows, 'median_price', { years: 2 });
  assert.equal(out.since, '2024-09-01');
  assert.equal(out.samples, 25);
  assert.equal(out.percentile, 98); // 末条 124 最大: below=24, equal=1(自身) → 24.5/25
});

test('summaryPercentile: 窗口外旧样本不计入排名', () => {
  const old = monthlyDates(2023, 1, 12).map((d, i) => ({
    trade_date: d,
    median_price: 1 + i, // 更小的值, 若误入会拉低分位
    avg_ytm: 1,
    count: 10,
  }));
  const recent = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  const out = summaryPercentile([...old, ...recent], 'median_price', { years: 2 });
  assert.equal(out.samples, 25);
  assert.equal(out.since, '2024-09-01');
  assert.equal(out.percentile, 98);
});

test('summaryPercentile: 历史不足回看年数 → 用全部实际样本, since=实际最早', () => {
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  const out = summaryPercentile(rows, 'median_price'); // 默认 years=5, from=2021-09-01
  assert.equal(out.since, '2024-09-01');
  assert.equal(out.samples, 25);
  assert.equal(out.percentile, 98);
});

test('summaryPercentile: 末条值缺失 → percentile=null, since/samples 仍返回', () => {
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  rows[rows.length - 1].median_price = null; // 最新行缺值
  const out = summaryPercentile(rows, 'median_price', { years: 2 });
  assert.equal(out.percentile, null);
  assert.equal(out.samples, 24);
  assert.equal(out.since, '2024-09-01');
});

test('summaryPercentile: 空 rows → null', () => {
  assert.equal(summaryPercentile([], 'median_price'), null);
});

test('summaryPercentile: 输入乱序结果一致(内部升序排列)', () => {
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  const a = summaryPercentile(rows, 'median_price', { years: 2 });
  const b = summaryPercentile([...rows].reverse(), 'median_price', { years: 2 });
  assert.deepEqual(b, a);
});

test('windowPercentile: 指定日在窗口内的回看排名', () => {
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  // 第 13 条值 112: below=12, equal=1 → (12+0.5)/25 = 50
  assert.equal(windowPercentile(rows, rows[12].trade_date, 'median_price'), 50);
});

test('windowPercentile: 日期不存在或该行值缺失 → null', () => {
  const rows = monthlyDates(2024, 9, 25).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  assert.equal(windowPercentile(rows, '2030-01-01', 'median_price'), null);
  const withNull = [...rows];
  withNull[5] = { ...withNull[5], median_price: null };
  assert.equal(windowPercentile(withNull, withNull[5].trade_date, 'median_price'), null);
});

test('windowPercentile: 窗口有效样本不足 20 → null', () => {
  const rows = monthlyDates(2026, 1, 5).map((d, i) => ({
    trade_date: d,
    median_price: 100 + i,
    avg_ytm: 1,
    count: 10,
  }));
  assert.equal(windowPercentile(rows, rows[2].trade_date, 'median_price'), null);
});

test('quantile: 线性插值 [10,20,30,40] q=50 → 25, q=0/100 取端点', () => {
  assert.equal(quantile([10, 20, 30, 40], 50), 25);
  assert.equal(quantile([10, 20, 30, 40], 0), 10);
  assert.equal(quantile([10, 20, 30, 40], 100), 40);
});

test('quantile: 剔除非有限数并升序; 乱序输入结果一致', () => {
  assert.equal(quantile([null, 30, 10, 20], 50), 20); // 有效 [10,20,30] → 中位 20
  assert.equal(quantile([40, 10, 30, 20], 50), 25);
});

test('quantile: 单样本任意分位返回该值; 空样本/非数组/q 无效 → null', () => {
  assert.equal(quantile([5], 30), 5);
  assert.equal(quantile([], 30), null);
  assert.equal(quantile(null, 30), null);
  assert.equal(quantile([1, 2, 3], NaN), null);
});

// ---------------------------------------------------------------------------
// normalizeCbSpreadRows(转债-国债利差序列清洗)
// ---------------------------------------------------------------------------

test('spread: 响应 null / 无 series / series 非数组 → 空 rows 且计数为 0', () => {
  const empty = { rows: [], invalidDateCount: 0, duplicateDateCount: 0, invalidValueCount: 0 };
  assert.deepEqual(normalizeCbSpreadRows(null), empty);
  assert.deepEqual(normalizeCbSpreadRows({ trade_date: '2026-09-09' }), empty); // 无 series
  assert.deepEqual(normalizeCbSpreadRows({ series: 'oops' }), empty); // series 非数组
  assert.deepEqual(normalizeCbSpreadRows({ series: [] }), empty); // 空序列
});

test('spread: 升序输出且只保留四字段, 负值与 0 原样保留', () => {
  const out = normalizeCbSpreadRows({
    series: [
      { trade_date: '2026-09-10', avg_ytm: -6.9, bond_yield: 2.5, spread: -9.4, extra: 1 },
      { trade_date: '2026-09-09', avg_ytm: 0, bond_yield: 0, spread: 0 },
    ],
  });
  assert.equal(out.rows.length, 2);
  assert.equal(out.rows[0].trade_date, '2026-09-09'); // 升序
  assert.deepEqual(Object.keys(out.rows[0]).sort(), ['avg_ytm', 'bond_yield', 'spread', 'trade_date']);
  assert.equal(out.rows[1].spread, -9.4); // 负利差合法
  assert.equal(out.rows[0].spread, 0); // 0 合法
  assert.equal(out.invalidValueCount, 0);
});

test('spread: 非法日期整条丢弃, 重复日期整组丢弃(规则同主序列)', () => {
  const out = normalizeCbSpreadRows({
    series: [
      { trade_date: '2026-02-30', avg_ytm: 1, bond_yield: 1, spread: 0 }, // 非法日期
      { trade_date: '2026-09-09', avg_ytm: 1, bond_yield: 1, spread: 0 },
      { trade_date: '2026-09-09', avg_ytm: 2, bond_yield: 1, spread: 1 }, // 重复 → 整组丢弃
      { trade_date: '2026-09-08', avg_ytm: 3, bond_yield: 1, spread: 2 },
    ],
  });
  assert.equal(out.invalidDateCount, 1);
  assert.equal(out.duplicateDateCount, 2);
  assert.deepEqual(
    out.rows.map((r) => r.trade_date),
    ['2026-09-08'], // 2026-09-09 两条 → 整组丢弃
  );
});

test('spread: NaN/Infinity 数值置 null 并计 invalidValueCount, null 原样不计', () => {
  const out = normalizeCbSpreadRows({
    series: [
      { trade_date: '2026-09-10', avg_ytm: NaN, bond_yield: 1, spread: Infinity },
      { trade_date: '2026-09-09', avg_ytm: null, bond_yield: 1, spread: 0 },
    ],
  });
  // 升序后 2026-09-09 在前(合法 null), 2026-09-10 在后(NaN/Infinity)
  assert.equal(out.rows[0].avg_ytm, null); // null 原样
  assert.equal(out.rows[1].avg_ytm, null); // NaN → null
  assert.equal(out.rows[1].spread, null); // Infinity → null
  assert.equal(out.invalidValueCount, 2); // NaN + Infinity 各计 1, null 不计
});

test('spread: 输出可直通 selectCbMarketWindow(按 trade_date 裁剪)', () => {
  const series = monthlyDates(2024, 9, 14).map((d, i) => ({
    trade_date: d,
    avg_ytm: -8 + i * 0.1,
    bond_yield: 2.5,
    spread: -10.5 + i * 0.1,
  }));
  const { rows } = normalizeCbSpreadRows({ series });
  const win = selectCbMarketWindow(rows, '1y'); // 锚点 2025-10-01, from 2024-10-01
  assert.equal(win.rows.length, 13); // 2024-10 ~ 2025-10 含两端
  assert.equal(win.insufficientHistory, false);
});
