import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCommodityChartOption, formatDateTime, formatNumber, formatPercentile, formatPrice,
  filtersFromQuery, groupBySection, isTriggered, isUninitialized,
  metricTone, queryFromFilters, renderCommodityChart, statusColor, statusLabel,
} from './commodity.mjs';

test('commodity status and formatting preserve semantic colors and null placeholders', () => {
  assert.equal(statusLabel('high'), '高位');
  assert.equal(statusLabel('divergent'), '周期分化');
  assert.equal(statusColor('low'), '#2563eb');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(12.345, 1), '12.3');
  assert.equal(formatDateTime('2026-09-16T00:00:00+08:00'), '2026-09-16 00:00:00+08:00');
});

test('email-aligned price precision strips trailing zeros by magnitude', () => {
  assert.equal(formatPrice(null), '—');
  assert.equal(formatPrice(12345.6), '12346');      // >=10000: 0 位小数
  assert.equal(formatPrice(3256), '3256');          // >=1000: 2 位再去尾零
  assert.equal(formatPrice(4000.5), '4000.5');
  assert.equal(formatPrice(98.67), '98.67');        // >=1: 4 位再去尾零
  assert.equal(formatPrice(712.5), '712.5');        // >=100: 3 位再去尾零
  assert.equal(formatPrice(15.425), '15.425');
  assert.equal(formatPrice(0.5234), '0.5234');      // <1: 6 位再去尾零
  assert.equal(formatPrice(0.5), '0.5');
});

test('percentile digits default keeps detail page while list passes 0', () => {
  assert.equal(formatPercentile(95), '95.0%');
  assert.equal(formatPercentile(95, 0), '95%');
  assert.equal(formatPercentile(93.6, 0), '94%');
  assert.equal(formatPercentile(91.4, 0), '91%');
  assert.equal(formatPercentile(null, 0), '—');
});

test('commodity filters round trip to URL query and detect window triggers', () => {
  const filters = filtersFromQuery({ keyword: '铜', category: '金属', window: 'd21', triggered: '1' });
  assert.deepEqual(queryFromFilters(filters), {
    keyword: '铜', category: '金属', window: 'd21', triggered: '1',
  });
  assert.deepEqual(queryFromFilters(filtersFromQuery({ sort_by: 'y1', sort_order: 'asc' })), { sort_by: 'y1', sort_order: 'asc' });
  assert.equal(isTriggered({ windows: { d21: { signal: 'high' } } }, 'd21'), true);
  assert.equal(isTriggered({ current_status: 'divergent' }), false);
});

test('empty states only call a no-data instrument set uninitialized', () => {
  assert.equal(isUninitialized({ data_date: null, instrument_total: 75 }, { active: false, rows: Array(75) }), true);
  assert.equal(isUninitialized({ data_date: null, instrument_total: 75 }, { active: true, rows: [] }), false);
  assert.equal(isUninitialized({ data_date: '2026-09-15', instrument_total: 75 }, { active: false, rows: [] }), false);
});

test('groupBySection follows email section order and folds unknown into 其他', () => {
  const rows = [
    { code: 'RB0', category: '黑色建材' },
    { code: 'CL', category: '能源与化工' },
    { code: 'LH0', category: '其他' },
    { code: 'XX', category: '未知分类' },
    { code: 'AU0', category: '有色贵金属' },
    { code: 'M0', category: '农产品' },
  ];
  const groups = groupBySection(rows);
  assert.deepEqual(groups.map((group) => group.key), ['能源与化工', '黑色建材', '有色贵金属', '农产品', '其他']);
  assert.equal(groups.find((group) => group.key === '能源与化工').emoji, '🛢');
  assert.deepEqual(groups.find((group) => group.key === '其他').rows.map((row) => row.code), ['LH0', 'XX']);
  assert.deepEqual(groupBySection([]), []);
});

test('sync degradation mutes metric colors regardless of persisted signal', () => {
  assert.equal(metricTone('failed', 'high'), 'muted');
  assert.equal(metricTone('stale', 'low'), 'muted');
  assert.equal(metricTone('suspicious', 'high'), 'muted');
  assert.equal(metricTone('success', 'high'), 'high');
});

test('chart renderer option matches signals by exact date', () => {
  const option = buildCommodityChartOption(
    [{ date: '2026-09-15', close: 10 }],
    { '2026-09-15': [{ window: 'd21', percentile: 91, signal: 'high' }] },
  );
  assert.equal(option.series[0].data[0], 10);
  assert.match(option.tooltip.formatter([{ axisValue: '2026-09-15', value: 10 }]), /21日/);
  assert.doesNotMatch(option.tooltip.formatter([{ axisValue: '2026-09-14', value: 9 }]), /21日/);
});

test('chart lifecycle initializes only after a container exists', () => {
  const calls = [];
  const instance = renderCommodityChart({
    echarts: { init(element) { calls.push(element); return { setOption() {} }; } },
    element: {}, instance: null, prices: [], signalsByDate: {},
  });
  assert.equal(calls.length, 1);
  assert.ok(instance);
  renderCommodityChart({ echarts: { init() { throw new Error('must reuse'); } }, element: {}, instance, prices: [], signalsByDate: {} });
  assert.equal(calls.length, 1);
});
