import test from 'node:test';
import assert from 'node:assert/strict';
import {
  formatNumber, filtersFromQuery, queryFromFilters, statusColor, statusLabel, isTriggered,
} from './commodity.mjs';

test('commodity status and formatting preserve semantic colors and null placeholders', () => {
  assert.equal(statusLabel('high'), '高位');
  assert.equal(statusLabel('divergent'), '周期分化');
  assert.equal(statusColor('low'), '#2563eb');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatNumber(12.345, 1), '12.3');
});

test('commodity filters round trip to URL query and detect window triggers', () => {
  const filters = filtersFromQuery({ keyword: '铜', category: '金属', window: 'd21', triggered: '1' });
  assert.deepEqual(queryFromFilters(filters), {
    keyword: '铜', category: '金属', window: 'd21', triggered: '1',
  });
  assert.equal(isTriggered({ windows: { d21: { signal: 'high' } } }, 'd21'), true);
  assert.equal(isTriggered({ current_status: 'divergent' }), false);
});

