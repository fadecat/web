// filterValidation.js 回归测试: node --test 前端工具测试
// 运行: node --test frontend/src/utils/filterValidation.test.mjs (或 pnpm test)
// 覆盖: 有限数(R3)/空值/0/负值/区间倒置/非法文本
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseFilterNumber, validateFilters } from './filterValidation.js';

const FIELDS = {
  price_min: { label: '价格最低', min: 0 },
  price_max: { label: '价格最高', min: 0 },
  ytm_min: { label: '到期收益率' },
};

test('parseFilterNumber: 基础转换', () => {
  assert.equal(parseFilterNumber('0'), 0);
  assert.equal(parseFilterNumber(''), null);
  assert.equal(parseFilterNumber('  '), null);
  assert.equal(parseFilterNumber('120'), 120);
  assert.equal(parseFilterNumber('-3.5'), -3.5);
  assert.equal(parseFilterNumber('.5'), 0.5);
});

test('parseFilterNumber: 非法输入返回 NaN', () => {
  assert.equal(Number.isNaN(parseFilterNumber('abc')), true);
  assert.equal(Number.isNaN(parseFilterNumber('120abc')), true);
  assert.equal(Number.isNaN(parseFilterNumber('1.2.3')), true);
});

test('R3: 超大数字(400 个 9)是非有限值, 必须拒绝', () => {
  const big = '9'.repeat(400);
  assert.equal(Number.isNaN(parseFilterNumber(big)), true);
  const out = validateFilters({ price_max: big }, { price_max: { label: '价格最高', min: 0 } });
  assert.equal(out.ok, false);
  assert.ok(out.errors[0].includes('价格最高'));
});

test('validateFilters: 合法条件通过且空条件省略', () => {
  const out = validateFilters(
    { price_min: '110', price_max: '120', ytm_min: '' },
    FIELDS,
    [['price_min', 'price_max', '价格']],
  );
  assert.equal(out.ok, true);
  assert.equal(out.values.price_min, 110);
  assert.equal(out.values.price_max, 120);
  assert.ok(!('ytm_min' in out.values));
});

test('validateFilters: 区间倒置报错', () => {
  const out = validateFilters({ price_min: '130', price_max: '120' }, FIELDS, [
    ['price_min', 'price_max', '价格'],
  ]);
  assert.equal(out.ok, false);
  assert.ok(out.errors[0].includes('价格'));
});

test('validateFilters: 非负约束(价格不能为负, 收益率可以)', () => {
  const negPrice = validateFilters({ price_max: '-1' }, FIELDS);
  assert.equal(negPrice.ok, false);
  const negYtm = validateFilters({ ytm_min: '-3.5' }, FIELDS);
  assert.equal(negYtm.ok, true);
  assert.equal(negYtm.values.ytm_min, -3.5);
});

test('validateFilters: 0 是合法值不当空处理', () => {
  const out = validateFilters({ price_max: '0' }, FIELDS);
  assert.equal(out.ok, true);
  assert.equal(out.values.price_max, 0);
});

test('validateFilters: 评级原样透传', () => {
  const out = validateFilters({ ratings: ['AA', 'NONE'] }, FIELDS);
  assert.deepEqual(out.values.ratings, ['AA', 'NONE']);
});
