// cbMarket.mjs 契约与纯函数测试: node --test 前端工具测试
// 运行: node --test src/utils/cbMarket.test.mjs (或 pnpm test:node)
// 覆盖方案 192-204 样例 + 非法日期/重复日期/非有限数/空数组/不改输入/
//       3年边界/闰日减年/历史不足/最新行缺值不回填
import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeCbMarketRows, selectCbMarketWindow } from './cbMarket.mjs';

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
