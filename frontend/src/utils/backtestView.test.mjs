// 组合详情页纯函数测试(node --test, 与 portfolioList.test.mjs 同一套跑法)。
// 覆盖的都是"规格里写死了、后来又容易被顺手改掉"的规则:
// 收益条七格口径标记、涨红跌绿、相关性着色方向、区间归一方式、口径提示。
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  basisCompositionText,
  buildBasisNotes,
  buildChartData,
  buildCorrelationView,
  buildMetricCards,
  buildReturnCells,
  correlationCellStyle,
  drawdownSummaryText,
  normalizeToReturnPct,
  priceBasisLabel,
  rebalanceLabel,
} from './backtestView.mjs';

const WINDOWS = {
  d1: { value: 1.08, actual_start: null, actual_end: '2026-09-18', composite: true },
  w1: { value: -0.41, actual_start: '2026-09-11', actual_end: '2026-09-18', composite: false },
  m1: { value: -0.62, actual_start: '2026-08-19', actual_end: '2026-09-18', composite: false },
  ytd: { value: 6.67, actual_start: '2026-01-02', actual_end: '2026-09-18', composite: false },
  y1: { value: 10.4, actual_start: '2025-09-18', actual_end: '2026-09-18', composite: false },
  y3: { value: 53.51, actual_start: '2023-09-18', actual_end: '2026-09-18', composite: false },
  inception: { value: 335.07, actual_start: '2013-04-26', actual_end: '2026-09-18', composite: false },
};

test('收益条: 七格顺序与标签固定', () => {
  const cells = buildReturnCells(WINDOWS);
  assert.equal(cells.length, 7);
  assert.deepEqual(
    cells.map((c) => c.label),
    ['近1日', '近1周', '近1月', '今年来', '近1年', '近3年', '成立来'],
  );
  assert.equal(cells[0].featured, true, '首格应放大');
  assert.equal(cells[1].featured, false);
});

test('收益条: 涨红跌绿, 数值统一 2 位', () => {
  const cells = buildReturnCells(WINDOWS);
  const byKey = Object.fromEntries(cells.map((c) => [c.key, c]));
  assert.equal(byKey.d1.className, 'trend-up');
  assert.equal(byKey.w1.className, 'trend-down');
  assert.equal(byKey.d1.text, '1.08%');
  assert.equal(byKey.w1.text, '-0.41%');
});

test('收益条: 只有「近1日」是合成口径, 其余是账本区间收益', () => {
  const cells = buildReturnCells(WINDOWS);
  assert.equal(cells[0].composite, true);
  assert.ok(cells[0].hint.includes('合成'));
  assert.equal(cells[1].composite, false);
  assert.ok(cells[1].hint.includes('2026-09-11'), '区间提示应回显实际起止');
});

test('收益条: 缺失格显示破折号而不是 0.00%', () => {
  const cells = buildReturnCells({ d1: { value: null, composite: true } });
  assert.equal(cells[0].text, '—');
  assert.equal(cells[0].isEmpty, true);
  assert.equal(cells[0].className, 'trend-flat');
  assert.equal(cells[1].text, '—');
});

test('相关性着色: 正相关红、负相关蓝、零值透明', () => {
  assert.ok(correlationCellStyle(1).background.includes('220, 38, 38'));
  assert.ok(correlationCellStyle(-1).background.includes('37, 99, 235'));
  assert.equal(correlationCellStyle(0).background, 'rgba(220, 38, 38, 0.000)');
  assert.equal(correlationCellStyle(null).background, 'transparent');
});

test('相关性着色: 强相关时底色够深, 文字要转白', () => {
  assert.equal(correlationCellStyle(1).strong, true);
  assert.equal(correlationCellStyle(-0.9).strong, true);
  assert.equal(correlationCellStyle(0.2).strong, false, '弱相关不该盖住文字');
});

test('相关性矩阵: 行列按 1..N 编号, 对角线为 1', () => {
  const view = buildCorrelationView(
    {
      start: '2016-09-19',
      end: '2026-09-18',
      symbols: ['100001.OF', '100002.OF'],
      matrix: [
        [1, 0.43],
        [0.43, 1],
      ],
    },
    [{ symbol: '100001.OF', name: '测试基金A' }, { symbol: '100002.OF', name: '测试基金B' }],
  );
  assert.equal(view.columns[0].index, 1);
  assert.equal(view.columns[1].index, 2);
  assert.equal(view.rows[0].name, '测试基金A');
  assert.equal(view.rows[0].cells[0].diagonal, true);
  assert.equal(view.rows[0].cells[0].text, '1.00');
  assert.equal(view.rows[0].cells[1].text, '0.43');
  // ⚠ 区间由后端给(它与回测区间对齐方向不同), 前端不自己推
  assert.equal(view.start, '2016-09-19');
});

test('相关性矩阵: 无数据返回 null 而不是空表', () => {
  assert.equal(buildCorrelationView(null, []), null);
  assert.equal(buildCorrelationView({ symbols: [] }, []), null);
});

test('曲线换算: 归一值 1.0 → 0%, 1.105 → 10.5%', () => {
  assert.deepEqual(normalizeToReturnPct([1, 1.105, 0.9]), [0, 10.5, -10]);
  assert.deepEqual(normalizeToReturnPct([1, null, 1.2]), [0, null, 20]);
});

test('曲线数据: 主曲线与基准共用同一条日期轴', () => {
  const data = buildChartData({
    dates: ['2026-01-05', '2026-01-06'],
    nav: [1, 1.1],
    benchmark: {
      symbol: '000300',
      name: '沪深300',
      price_basis: 'PRICE',
      nav: [1, 1.2],
    },
  });
  assert.deepEqual(data.portfolio, [0, 10]);
  assert.equal(data.benchmark.name, '沪深300');
  assert.equal(data.benchmark.priceBasis, 'PRICE');
  assert.deepEqual(data.benchmark.values, [0, 20]);
});

test('曲线数据: 无曲线时返回 null', () => {
  assert.equal(buildChartData(null), null);
  assert.equal(buildChartData({ dates: [] }), null);
});

test('指标卡: 缺值给破折号, 不拿 0 顶替', () => {
  const cards = buildMetricCards({ cagr: 8.9, mdd: -16.5, sharpe: null }, null);
  const byKey = Object.fromEntries(cards.map((c) => [c.key, c]));
  assert.equal(byKey.cagr.value, '8.90%');
  assert.equal(byKey.mdd.value, '-16.50%');
  assert.equal(byKey.sharpe.value, '—');
});

test('指标卡: 最长恢复按"天"展示, 未修复时说明原因', () => {
  const fixed = buildMetricCards({}, { recovery_days: 418, trough_date: '2020-03-23' });
  const last = fixed.find((c) => c.key === 'recovery');
  assert.equal(last.value, '418 天');

  const open = buildMetricCards({}, { recovery_days: null, trough_date: '2020-03-23' });
  assert.equal(open.find((c) => c.key === 'recovery').value, '—');
  assert.ok(open.find((c) => c.key === 'recovery').hint.includes('仍未回到前高'));
});

test('回撤文案: 修复与未修复两种说法', () => {
  assert.ok(
    drawdownSummaryText({
      value: -16.5, peak_date: '2020-02-19', trough_date: '2020-03-23',
      recovery_date: '2020-07-01', recovery_days: 418,
    }).includes('2020-07-01 修复'),
  );
  assert.ok(
    drawdownSummaryText({
      value: -16.5, peak_date: '2020-02-19', trough_date: '2020-03-23',
      recovery_date: null, recovery_days: null,
    }).includes('仍未修复'),
  );
  assert.equal(drawdownSummaryText(null), '暂无回撤数据');
});

test('口径提示: 基准为价格指数时必须写明"不含股息、不可直接比"', () => {
  const notes = buildBasisNotes({
    price_basis_note: ['HFQ: 600900.SH'],
    benchmark: { symbol: '000300', name: '沪深300', price_basis: 'PRICE' },
  });
  assert.equal(notes.length, 2);
  assert.ok(notes[1].includes('不含股息'));
  assert.ok(notes[1].includes('沪深300'));
});

test('口径提示: 后端已给过同类提示就不重复追加', () => {
  const notes = buildBasisNotes({
    price_basis_note: ['基准 000300 为价格指数(不含股息), 与含分红的组合口径不同, 对照仅供方向参考'],
    benchmark: { symbol: '000300', name: '沪深300', price_basis: 'PRICE' },
  });
  assert.equal(notes.length, 1);
});

test('口径构成: 混合持仓说清由哪些口径组成(page-spec §三-3)', () => {
  const text = basisCompositionText([
    { price_basis: 'HFQ' }, { price_basis: 'HFQ' }, { price_basis: 'HFQ' },
    { price_basis: 'NAV_ADJ' },
  ]);
  assert.equal(text, '后复权价 3 只 · 分红再投净值 1 只');
  // 没有成员 / 字段缺失 → 空串(图例不显示这一段), 不编造
  assert.equal(basisCompositionText([]), '');
  assert.equal(basisCompositionText([{ symbol: 'A' }]), '');
});

test('文案: 复权口径与再平衡名称', () => {
  assert.equal(priceBasisLabel('HFQ'), '后复权价');
  assert.equal(priceBasisLabel('NAV_ADJ'), '分红再投净值');
  assert.equal(priceBasisLabel('PRICE'), '价格指数');
  assert.equal(priceBasisLabel(null), '—');
  assert.equal(rebalanceLabel('quarterly'), '季平衡');
});

test('指标卡: 覆盖规格 §二-⑤ 的全部项(含最差年度与累计换手)', () => {
  const cards = buildMetricCards(
    {
      cagr: 8.9, mdd: -16.5, vol: 11.2, sharpe: 0.79, sortino: 1.1, calmar: 0.54,
      worst_year: -6.2, worst_month: -3.1, turnover: 312.4,
    },
    { recovery_days: 418, trough_date: '2020-03-23' },
  );
  const keys = cards.map((c) => c.key);
  for (const key of ['cagr', 'mdd', 'vol', 'sharpe', 'sortino', 'calmar',
    'worst_year', 'worst_month', 'recovery', 'turnover']) {
    assert.ok(keys.includes(key), `指标卡缺 ${key}`);
  }
  const byKey = Object.fromEntries(cards.map((c) => [c.key, c]));
  assert.equal(byKey.worst_year.value, '-6.20%');
  assert.equal(byKey.turnover.value, '312.40%');
  // 换手不参与收益计算 —— 提示里必须写明, 否则会被误读成成本
  assert.ok(byKey.turnover.hint.includes('不参与收益计算'));
});

test('指标卡: 后端没算出来的项显示破折号, 不编造', () => {
  const cards = buildMetricCards({}, null);
  for (const card of cards) {
    assert.equal(card.value, '—', `${card.key} 应为 —`);
  }
});

test('相关性矩阵: 非对角格带样本数 n(ambiguity-audit D8)', () => {
  const view = buildCorrelationView(
    {
      start: '2016-09-19',
      end: '2026-09-18',
      symbols: ['100001.OF', '100002.OF'],
      matrix: [
        [1, 0.43],
        [0.43, 1],
      ],
      // 后端 key 规则: 字典序小的在前
      sample_sizes: { '100001.OF|100002.OF': 2438 },
    },
    [],
  );
  assert.equal(view.rows[0].cells[1].samples, 2438);
  assert.equal(view.rows[0].cells[1].sampleText, 'n=2438');
  // 对角线不显示样本数(自己跟自己)
  assert.equal(view.rows[0].cells[0].sampleText, '');
  // 对称: 第 2 行第 1 列要取到同一个 key
  assert.equal(view.rows[1].cells[0].sampleText, 'n=2438');
});

test('相关性矩阵: 后端没给样本数时不显示 n(而不是显示 n=0)', () => {
  const view = buildCorrelationView(
    { start: 'a', end: 'b', symbols: ['A', 'B'], matrix: [[1, 0.1], [0.1, 1]] },
    [],
  );
  assert.equal(view.rows[0].cells[1].sampleText, '');
  assert.equal(view.rows[0].cells[1].samples, null);
});
