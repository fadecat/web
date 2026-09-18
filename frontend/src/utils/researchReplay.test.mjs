import test from 'node:test';
import assert from 'node:assert/strict';
import {
  WORDING, FORBIDDEN_TERMS, REASON_LABELS, CATEGORY_LABELS,
  reasonLabel, categoryLabel, filtersFromQuery, queryFromFilters,
  formatRate, formatNumber, buildSegmentRows, buildComparisonRows, pickComparisonRow,
} from './researchReplay.mjs';

// 全部面向用户文案禁「收益/回测收益率」(措辞约束)
test('research replay wording stays within signal-replay vocabulary', () => {
  const allText = JSON.stringify({ WORDING, REASON_LABELS, CATEGORY_LABELS });
  for (const term of FORBIDDEN_TERMS) {
    assert.ok(!allText.includes(term), `文案不得出现禁用词: ${term}`);
  }
  assert.equal(WORDING.pageKind, '信号回放');
  assert.equal(WORDING.hitTerm, '价位触达');
  assert.equal(WORDING.modelPrice, '模型价');
});

test('reason and category codes map to Chinese labels with fallback', () => {
  assert.equal(reasonLabel('INSUFFICIENT_HISTORY'), '历史数据不足');
  assert.equal(reasonLabel('NEXT_DAY_CORPORATE_ACTION'), '次日权益事件（排除）');
  assert.equal(reasonLabel('UNKNOWN_CODE'), 'UNKNOWN_CODE');
  assert.equal(categoryLabel('BOTH_HIT'), '双侧触达（路径不明）');
  assert.equal(categoryLabel('EXCLUDED_OPEN_INVALIDATION'), '开盘失效（排除）');
  assert.equal(categoryLabel('NO_SUCH'), 'NO_SUCH');
});

test('filters round trip to URL query with date validation and defaults', () => {
  const filters = filtersFromQuery({
    symbol: '600900.SH', start_date: '2023-09-01', end_date: '2026-09-01',
    lambda: '0.2', window: '120',
  });
  assert.deepEqual(queryFromFilters(filters), {
    symbol: '600900.SH', start_date: '2023-09-01', end_date: '2026-09-01',
    lambda: '0.2', window: '120',
  });
  // 缺省字段不进 query
  assert.deepEqual(queryFromFilters(filtersFromQuery({})), {});
  // 非法日期/非字符串被清空
  const bad = filtersFromQuery({ symbol: '600900.SH', start_date: '20260901', lambda: 'abc' });
  assert.equal(bad.startDate, '');
  assert.ok(Number.isNaN(bad.lambda));
  assert.deepEqual(queryFromFilters(bad), { symbol: '600900.SH', lambda: 'NaN' });
});

test('rate and number formatting handle null and fraction digits', () => {
  assert.equal(formatRate(0.3571), '35.7%');
  assert.equal(formatRate(null), '—');
  assert.equal(formatRate(0), '0.0%');
  assert.equal(formatNumber(12.345678), '12.3457');
  assert.equal(formatNumber(null), '—');
});

test('buildSegmentRows flattens buy/sell tiers with explicit numerator/denominator', () => {
  const rows = buildSegmentRows({
    day_count: 100,
    buy: [
      { tier: 'P50', numerator: 60, denominator: 100, rate: 0.6 },
      { tier: 'P70', numerator: 30, denominator: 100, rate: 0.3 },
      { tier: 'P85', numerator: 10, denominator: 100, rate: 0.1 },
    ],
    sell: [
      { tier: 'P50', numerator: 55, denominator: 98, rate: 55 / 98 },
    ],
  });
  assert.equal(rows.length, 4);
  assert.deepEqual(rows[0], {
    side: 'buy', sideLabel: '买侧', tier: 'P50',
    numerator: 60, denominator: 100, rate: 0.6, rateText: '60.0%',
  });
  assert.equal(rows[3].sideLabel, '卖侧');
  assert.equal(rows[3].rateText, `${(55 / 98 * 100).toFixed(1)}%`);
  assert.deepEqual(buildSegmentRows(null), []);
});

test('buildComparisonRows sorts by lambda then window and nests segments', () => {
  const rows = buildComparisonRows([
    { run_id: 2, param_lambda: 0.2, quantile_window: 120,
      train: { day_count: 300, buy: [{ tier: 'P50', numerator: 1, denominator: 2, rate: 0.5 }], sell: [] },
      validation: { day_count: 200, buy: [], sell: [] } },
    { run_id: 1, param_lambda: 0, quantile_window: 180,
      train: { day_count: 300, buy: [], sell: [] },
      validation: { day_count: 200, buy: [], sell: [] } },
  ]);
  assert.deepEqual(rows.map((r) => r.runId), [1, 2]); // λ=0 在前
  assert.equal(rows[1].lambdaText, 'λ=0.2');
  assert.equal(rows[1].windowText, '120日');
  assert.equal(rows[1].train_dayCount, 300);
  assert.equal(rows[1].validation_dayCount, 200);
  assert.equal(rows[1].train_buy[0].rateText, '50.0%');
  assert.deepEqual(rows[0].train_buy, []);
  assert.deepEqual(buildComparisonRows(null), []);
});

test('pickComparisonRow honors lambda/window filters with fallback to first', () => {
  const rows = buildComparisonRows([
    { run_id: 1, param_lambda: 0, quantile_window: 60 },
    { run_id: 2, param_lambda: 0.2, quantile_window: 120 },
    { run_id: 3, param_lambda: 0.2, quantile_window: 180 },
  ]);
  assert.equal(pickComparisonRow(rows, { lambda: '0.2', window: '120' }).runId, 2);
  assert.equal(pickComparisonRow(rows, { lambda: '0.2' }).runId, 2);
  assert.equal(pickComparisonRow(rows, {}).runId, 1);
  assert.equal(pickComparisonRow(rows, { lambda: '9.9' }).runId, 1); // 找不到回退首行
  assert.equal(pickComparisonRow([], {}), null);
});
