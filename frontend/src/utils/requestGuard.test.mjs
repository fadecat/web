// requestGuard 单元测试: 用 Node 内置测试运行器, 无需 vitest/jsdom/网络。
// 运行: node --test frontend/src/utils/requestGuard.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequestGuard } from './requestGuard.js';

test('next() 单调递增, 仅最新版本 isLatest', () => {
  const g = createRequestGuard();
  const a = g.next();
  const b = g.next();
  assert.equal(a, 1);
  assert.equal(b, 2);
  assert.equal(g.isLatest(b), true);
  assert.equal(g.isLatest(a), false); // A 已过期
});

test('乱序: A 慢 B 快, 迟到 A 被丢弃', () => {
  const g = createRequestGuard();
  const va = g.next(); // A 先发起
  const vb = g.next(); // B 后发起
  // B 先返回 -> 是最新
  assert.equal(g.isLatest(vb), true);
  // A 后返回 -> 过期, 不应更新 UI
  assert.equal(g.isLatest(va), false);
});

test('卸载: invalidate 使在途响应失效, 新阶段请求仍有效', () => {
  const g = createRequestGuard();
  const v = g.next();
  g.invalidate(); // 组件卸载
  assert.equal(g.isLatest(v), false); // 卸载后迟到响应不算最新
  const w = g.next(); // 新阶段(通常不会发生, 但守卫须自洽)
  assert.equal(g.isLatest(w), true);
});

test('loading 收敛: 仅最新版本可清除 loading', () => {
  const g = createRequestGuard();
  let loading = true;
  const v1 = g.next();
  const v2 = g.next();
  if (g.isLatest(v1)) loading = false; // 过期请求不得清除
  assert.equal(loading, true);
  if (g.isLatest(v2)) loading = false; // 最新请求清除
  assert.equal(loading, false);
});

test('valuationLoading 收敛(展示估值对): 仅最新版本清除', () => {
  const g = createRequestGuard();
  let valuationLoading = true;
  const v1 = g.next();
  const v2 = g.next();
  if (g.isLatest(v1)) valuationLoading = false;
  assert.equal(valuationLoading, true);
  if (g.isLatest(v2)) valuationLoading = false;
  assert.equal(valuationLoading, false);
});
