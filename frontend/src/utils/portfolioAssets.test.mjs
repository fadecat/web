// 标的库/添加标的共用的纯逻辑单测(node --test, 无 DOM 依赖)。
//
// 这个文件补的是一个**测试缺口**: `portfolioAssets.mjs` 里全是口径判断
// (起点 = 各标的首日最大值、未抓取不编造区间、状态 → 可用性), 但此前没有任何
// 纯函数测试 —— 只有组件测试间接覆盖, 口径类的错不会被发现。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  bareCode, buildStartShiftNotice, candidateKey, candidateRangeText, computePortfolioStart,
  confirmSubtitle, exchangeLabel, formatCount, formatRange, rowStatusOf, sourceLabel, usedByText,
} from './portfolioAssets.mjs';

test('computePortfolioStart: 取各标的首日最大值, 缺一个就是"待抓取"', () => {
  const got = computePortfolioStart([
    { symbol: 'A', first_date: '2003-12-02' },
    { symbol: 'B', first_date: '2013-04-26' },
    { symbol: 'C', first_date: '2011-05-09' },
  ]);
  assert.equal(got.startDate, '2013-04-26');
  assert.equal(got.pending, false);

  // 有标的还没抓到 → 起点未定, 页面必须显示「待抓取」而不是给个错的起点
  assert.deepEqual(
    computePortfolioStart([{ symbol: 'A', first_date: '2003-12-02' }, { symbol: 'B' }]),
    { startDate: '2003-12-02', pending: true },
  );
  assert.deepEqual(computePortfolioStart([]), { startDate: null, pending: false });
});

test('buildStartShiftNotice: 携带前移年数与制约标的', () => {
  const got = buildStartShiftNotice({
    oldStart: '2003-12-02',
    newStart: '2013-04-26',
    asset: { symbol: '513100.SH', name: '纳指ETF国泰', first_date: '2013-04-26' },
  });
  assert.match(got.title, /新的组合起点：2013-04-26/);
  assert.match(got.title, /纳指ETF国泰 513100\.SH/);
  assert.match(got.detail, /前移 9\.4 年/);
});

test('candidateKey: 同一裸代码的股票与场外基金不能撞车', () => {
  assert.equal(
    candidateKey({ symbol: '000001.SZ', security_type: 'STOCK' }),
    'STOCK:000001.SZ',
  );
  assert.notEqual(
    candidateKey({ symbol: '000001.OF', security_type: 'FUND' }),
    candidateKey({ symbol: '000001.SZ', security_type: 'STOCK' }),
  );
});

test('formatRange/formatCount: 未入库必须说「尚未抓取」, 不用 0 或空串冒充', () => {
  assert.equal(formatRange('2003-12-02', '2026-09-18', 5545), '2003-12-02 ~ 2026-09-18（5545 个交易日）');
  assert.equal(formatRange(null, null, null), '—（尚未抓取）');
  assert.equal(formatRange('2003-12-02', '2026-09-18', 0), '—（尚未抓取）');
  assert.equal(formatCount(5545), '5545');
  assert.equal(formatCount(null), '—');
  assert.equal(formatCount(0), '—');
});

test('candidateRangeText: 未抓取时不编造区间, 只回显已知的成立日/最新日', () => {
  assert.equal(
    candidateRangeText({ first_date: '2003-12-02', last_date: '2026-09-18', row_count: 5545 }),
    '2003-12-02 ~ 2026-09-18（5545 个交易日）',
  );
  // 场外基金未抓取: 有成立日 + 详情里的最新日
  assert.equal(
    candidateRangeText({ found_date: '2011-05-09', latest_date: '2026-09-17', row_count: null }),
    '成立 2011-05-09 · 最新 2026-09-17（待抓取）',
  );
  // 什么都还不知道 → 明说未知, 而不是编一个区间出来
  assert.equal(candidateRangeText({ row_count: null }), '数据区间未知（待抓取）');
});

test('confirmSubtitle: 代码 · 类型描述 · 基金经理(股票/ETF 不编造后两项)', () => {
  assert.equal(
    confirmSubtitle({ symbol: '161116.OF', type_desc: 'QDII-商品', manager: '殷春涛' }),
    '161116 · QDII-商品 · 殷春涛',
  );
  assert.equal(
    confirmSubtitle({ symbol: '600900.SH', type_desc: null, manager: null }),
    '600900',
  );
  // 只有代码时不能留下孤零零的分隔符
  assert.equal(confirmSubtitle({ symbol: '513100.OF', type_desc: 'QDII', manager: null }), '513100 · QDII');
  assert.equal(bareCode('000001.OF'), '000001');
});

test('rowStatusOf: 状态 → 可用性(blocked 就是"该行标红"的判据)', () => {
  // ⭐「有行数但 last_sync_status 为空」是就绪 —— 种子/脚本写入的标的就是这种,
  //    不能因为它没有状态字段就标红(那会把好数据说成不能用)。
  assert.deepEqual(
    rowStatusOf({ row_count: 6494, last_sync_status: null }),
    { key: 'ready', label: '就绪', tone: 'up', retry: false, blocked: false, detail: '' },
  );
  assert.deepEqual(
    rowStatusOf({ row_count: 0, last_sync_status: 'running' }),
    { key: 'running', label: '同步中', tone: 'wait', retry: false, blocked: false, detail: '' },
  );
  const failed = rowStatusOf({ row_count: 0, last_sync_status: 'failed', last_sync_error: 'HTTP 500' });
  assert.equal(failed.label, '失败');
  assert.equal(failed.retry, true);
  assert.equal(failed.blocked, true);
  assert.equal(failed.detail, 'HTTP 500');
  // 从没抓过 → 无数据, 可重试, 且要标红
  const empty = rowStatusOf({ row_count: null, last_sync_status: null });
  assert.equal(empty.key, 'empty');
  assert.equal(empty.retry, true);
  assert.equal(empty.blocked, true);
});

test('sourceLabel/exchangeLabel: 数据源与交易所都出中文, 不把 danjuan 直接丢给用户', () => {
  assert.equal(sourceLabel('danjuan'), '蛋卷');
  assert.equal(sourceLabel('tencent'), '腾讯');
  assert.equal(sourceLabel(null), '—');
  assert.equal(exchangeLabel('513100.SH'), '上交所');
  assert.equal(exchangeLabel('000001.SZ'), '深交所');
  assert.equal(exchangeLabel('161116.OF'), '场外');
});

test('usedByText: 没有被任何组合使用时给破折号, 而不是"0 个组合"', () => {
  assert.equal(usedByText({ used_by: [], used_by_count: 0 }), '—');
  assert.equal(usedByText({}), '—');
  assert.equal(
    usedByText({ used_by: ['基准组合', '基准组合（蛋卷口径）'], used_by_count: 2 }),
    '被 2 个组合使用：基准组合、基准组合（蛋卷口径）',
  );
});
