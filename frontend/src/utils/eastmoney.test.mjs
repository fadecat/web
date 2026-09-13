// eastmoney.mjs 纯函数测试: node --test 前端工具测试
// 运行: node --test src/utils/eastmoney.test.mjs (或 pnpm test:node)
// 覆盖: 沪/深市场前缀拼接、北交所等无映射与空值返回 null
import test from 'node:test';
import assert from 'node:assert/strict';
import { eastmoneyF10Url } from './eastmoney.mjs';

test('沪市代码拼 SH 前缀(60/68 开头)', () => {
  assert.equal(
    eastmoneyF10Url('600795'),
    'https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code=SH600795&color=b#/cpbd',
  );
  assert.equal(eastmoneyF10Url('688981').includes('code=SH688981'), true);
});

test('深市代码拼 SZ 前缀(00/30 开头)', () => {
  assert.equal(eastmoneyF10Url('000001').includes('code=SZ000001'), true);
  assert.equal(eastmoneyF10Url('300750').includes('code=SZ300750'), true);
});

test('北交所等无前缀映射与空值返回 null(调用方退回纯文本)', () => {
  assert.equal(eastmoneyF10Url('835185'), null);
  assert.equal(eastmoneyF10Url('430047'), null);
  assert.equal(eastmoneyF10Url('920001'), null);
  assert.equal(eastmoneyF10Url(''), null);
  assert.equal(eastmoneyF10Url(null), null);
  assert.equal(eastmoneyF10Url(undefined), null);
});
