// 组合列表/详情共用的格式化与视图模型: 纯函数单测(node --test, 无 DOM 依赖)。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  EMPTY, buildMetricCells, buildPortfolioCard, formatDate, formatReturnPct, formatWeight,
  readinessText, trendOf, weightSummaryText,
} from './portfolioList.mjs';

test('trendOf: 涨/跌/零/空', () => {
  assert.equal(trendOf(1.08), 'up');
  assert.equal(trendOf(-0.62), 'down');
  assert.equal(trendOf(0), 'flat');
  assert.equal(trendOf(null), 'flat');
  assert.equal(trendOf(undefined), 'flat');
});

test('formatReturnPct: 统一 2 位小数, 空值为破折号', () => {
  assert.equal(formatReturnPct(1.0829), '1.08%');
  assert.equal(formatReturnPct(-0.62), '-0.62%');
  assert.equal(formatReturnPct(6.67), '6.67%');
  // 空组合不能显示 0.00%(会被误读成"收益为零")
  assert.equal(formatReturnPct(null), EMPTY);
  assert.equal(formatReturnPct(undefined), EMPTY);
});

test('formatDate: 截到日, 缺失给占位符', () => {
  assert.equal(formatDate('2026-09-19T12:34:56'), '2026-09-19');
  assert.equal(formatDate(null), EMPTY);
});

test('formatWeight: 未设置给破折号', () => {
  assert.equal(formatWeight(25), '25.00%');
  assert.equal(formatWeight(null), EMPTY);
});

test('buildMetricCells: 三格顺序/文案/配色类', () => {
  const cells = buildMetricCells({
    cached_day_return: 1.08, cached_month_return: -0.62, cached_ytd_return: 6.67,
  });
  assert.deepEqual(cells.map((c) => c.label), ['日收益', '近一月', '今年以来']);
  assert.deepEqual(cells.map((c) => c.text), ['1.08%', '-0.62%', '6.67%']);
  // 涨红跌绿
  assert.deepEqual(cells.map((c) => c.className), ['trend-up', 'trend-down', 'trend-up']);
  assert.deepEqual(cells.map((c) => c.isEmpty), [false, false, false]);
});

test('buildMetricCells: 空组合三格都是破折号且标记 isEmpty', () => {
  const cells = buildMetricCells({});
  assert.deepEqual(cells.map((c) => c.text), [EMPTY, EMPTY, EMPTY]);
  assert.ok(cells.every((c) => c.isEmpty));
  assert.ok(cells.every((c) => c.className === 'trend-flat'));
});

test('buildPortfolioCard: 成立时间取 created_at, 收益时间取 cached_asof_date', () => {
  const card = buildPortfolioCard({
    id: 7, name: '我的组合2', created_at: '2026-09-19T10:00:00',
    cached_asof_date: '2026-09-18', asset_count: 4,
  });
  assert.equal(card.id, 7);
  assert.equal(card.name, '我的组合2');
  assert.equal(card.createdAt, '2026-09-19');
  assert.equal(card.asofDate, '2026-09-18');
  assert.equal(card.assetCount, 4);
  assert.equal(card.metrics.length, 3);
});

test('buildPortfolioCard: 无名时兜底不显示空白卡片', () => {
  assert.equal(buildPortfolioCard({}).name, '未命名组合');
});

test('weightSummaryText: 四种状态', () => {
  assert.match(weightSummaryText({ assets: [], weight_sum: 0 }), /还没有标的/);
  assert.match(
    weightSummaryText({ assets: [{ target_weight: 60 }, { target_weight: null }], weight_sum: 60 }),
    /还有 1 个标的未设权重/,
  );
  assert.match(
    weightSummaryText({ assets: [{ target_weight: 60 }, { target_weight: 30 }], weight_sum: 90 }),
    /需调整到 100%/,
  );
  assert.match(
    weightSummaryText({ assets: [{ target_weight: 60 }, { target_weight: 40 }], weight_sum: 100 }),
    /可以回测/,
  );
});

test('readinessText: 就绪时回显共同起点 T0', () => {
  assert.equal(
    readinessText({ all_ready: true, common_start: '2013-04-26', assets: [] }),
    '数据就绪，共同起点 2013-04-26',
  );
});

test('readinessText: 缺数据时列出标的代码(给「立即同步」出口)', () => {
  const text = readinessText({
    all_ready: false,
    common_start: null,
    assets: [{ symbol: '600900.SH', ok: false }, { symbol: '100018.OF', ok: true }],
  });
  assert.match(text, /600900\.SH/);
  assert.doesNotMatch(text, /100018\.OF/);
});
