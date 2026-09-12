// chartTheme.mjs 契约测试: node --test 前端工具测试
// 运行: node --test src/utils/chartTheme.test.mjs (或 pnpm test:node)
// 核心: ① 浅色档锁定现状基线(浅色模式视觉不变的约束落在测试里)
//      ② 深色档关键 token 必须翻转/覆盖(spread 墨线不翻会在深色表面隐形)
import test from 'node:test';
import assert from 'node:assert/strict';
import { chartTheme } from './chartTheme.mjs';

test('浅色档锁定现状基线: 与改造前硬编码逐值一致', () => {
  const t = chartTheme(false);
  assert.equal(t.ink, '#1f2937'); // spread 主线浅色仍是近黑墨色
  assert.equal(t.navy, '#274c77');
  assert.equal(t.amber, '#b45309');
  assert.equal(t.maAmber, '#f59e0b');
  assert.equal(t.red, '#dc2626');
  assert.equal(t.green, '#16a34a');
  assert.equal(t.blue, '#2563eb');
  assert.equal(t.indexBlue, '#185fa5');
  assert.equal(t.peOrange, '#ea580c');
  assert.equal(t.tooltipBg, 'rgba(255, 255, 255, 0.96)');
  assert.equal(t.splitLine, 'rgba(148, 163, 184, 0.18)');
});

test('深色档关键翻转: spread 墨线翻成浅墨, 否则深色表面上隐形', () => {
  const t = chartTheme(true);
  assert.equal(t.ink, '#e5eaf3');
  assert.notEqual(t.ink, chartTheme(false).ink);
});

test('深色档系列色全部换档(未被浅色基线漏掉)', () => {
  const light = chartTheme(false);
  const dark = chartTheme(true);
  for (const key of [
    'navy', 'amber', 'maAmber', 'red', 'green', 'blue', 'indexBlue', 'peOrange',
    'navyArea', 'redArea', 'greenArea', 'medianGray',
    'tooltipBg', 'tooltipBgWarm', 'tooltipText', 'axisLabel', 'labelTitle',
    'zoomFiller',
  ]) {
    assert.notEqual(dark[key], light[key], `深色档 ${key} 应覆盖浅色值`);
  }
});

test('两档 token 表键完全一致且无空值(组件取色不容 undefined)', () => {
  const light = chartTheme(false);
  const dark = chartTheme(true);
  assert.deepEqual(Object.keys(dark).sort(), Object.keys(light).sort());
  for (const [key, value] of Object.entries(dark)) {
    assert.ok(typeof value === 'string' && value.length > 0, `${key} 不能为空`);
  }
});

test('深色档柱色沿用 EP 主蓝(与全局强调色一致, 对比度单独达标)', () => {
  // 单系列柱不进分类调色板, 无需换档; 锁定该决定防止无意漂移
  assert.equal(chartTheme(true).bar, '#409eff');
});
