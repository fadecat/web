import test from 'node:test';
import assert from 'node:assert/strict';
import { percentileTone, percentilePosition, fmtNum } from './valuation.js';

test('percentile tone follows indicator direction and strict boundaries', () => {
  assert.equal(percentileTone(29.9, 'pe'), 'green');
  assert.equal(percentileTone(30, 'pe'), 'neutral');
  assert.equal(percentileTone(70, 'pe'), 'neutral');
  assert.equal(percentileTone(70.1, 'pe'), 'red');
  assert.equal(percentileTone(29.9, 'spread'), 'red');
  assert.equal(percentileTone(70.1, 'spread'), 'green');
});

test('invalid percentiles and values stay neutral or missing', () => {
  assert.equal(percentileTone(Infinity, 'pb'), 'neutral');
  assert.equal(percentilePosition(NaN), '暂无分位');
  assert.equal(fmtNum(Infinity), '—');
});
