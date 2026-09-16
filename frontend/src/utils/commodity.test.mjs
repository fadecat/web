import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCommodityChartOption, formatDateTime, formatNumber, filtersFromQuery, isTriggered, isUninitialized,
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
