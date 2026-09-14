// ValuationDetail 股债 Tab 测试(Phase A2):
// 覆盖 ?tab=eb 直达、spread/ratio 切换、不同窗口同日对齐、国债对照线已移除后的图表数据。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';

// 模拟 API 模块
const getValuationSnapshotMock = vi.fn();
const getEquityBondMock = vi.fn();
const getDividendYieldMock = vi.fn();
const getIndexQuotesMock = vi.fn();

vi.mock('../../src/api/index.js', () => ({
  default: {},
  getValuationSnapshot: (...a) => getValuationSnapshotMock(...a),
  getEquityBond: (...a) => getEquityBondMock(...a),
  getDividendYield: (...a) => getDividendYieldMock(...a),
  getIndexQuotes: (...a) => getIndexQuotesMock(...a),
}));

// Element Plus 组件桩
vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus');
  return {
    ...actual,
    ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
    ElMessageBox: { prompt: vi.fn() },
  };
});

// 简单桩: 全局注册需要的 Element Plus 组件标签
import { h } from 'vue';
const EP_STUBS = {
  'el-card': { template: '<div class="el-card"><slot /></div>' },
  'el-empty': { template: '<div class="el-empty"><slot /></div>' },
};

// Vue Router 桩
vi.mock('vue-router', () => ({
  useRoute: vi.fn(() => ({ params: { code: '930955' }, query: {} })),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn() })),
}));

import ValuationDetail from '../../src/pages/ValuationDetail.vue';

// 生成股债序列: n 个日期, 每个含 spread/ratio/cn_10y_bond_yield
function makeEbSeries(n, withBond = true) {
  const out = [];
  const start = Date.UTC(2020, 0, 1);
  for (let i = 0; i < n; i++) {
    const d = new Date(start + i * 86400000).toISOString().slice(0, 10);
    const point = { date: d, spread: 5 + i * 0.1, ratio: 2 + i * 0.01 };
    if (withBond) point.cn_10y_bond_yield = 2.5 + (i % 3) * 0.1;
    out.push(point);
  }
  return out;
}

function makeSnapshotRows(n) {
  const out = [];
  const start = Date.UTC(2020, 0, 1);
  for (let i = 0; i < n; i++) {
    const d = new Date(start + i * 86400000).toISOString().slice(0, 10);
    out.push({ trade_date: d, pe: 10 + i, pb: 1 + i * 0.1, ps: 0.5 });
  }
  return out;
}

// 生成指数日线收盘价序列(与 makeSnapshotRows 同日期集合, 便于按日对齐)
function makeQuoteRows(n, dates) {
  const src = dates || makeSnapshotRows(n).map((r) => r.trade_date);
  return src.map((trade_date, i) => ({ index_code: '930955', trade_date, close: 4000 + i }));
}

// 快照 mock 响应(真实接口形状: 行数组、trade_date 降序)
function makeSnapshotResponse(n) {
  return makeSnapshotRows(n)
    .map((r) => ({ index_code: '930955', index_name: '红利低波100', ...r }))
    .reverse();
}

const EB_STATS = {
  index_code: '930955',
  index_name: '红利低波100',
  date: '2020-01-25',
  pe: 10,
  cn_10y_bond_yield: 2.5,
  spread: { current: 7.5, percentiles: {}, average_5y: 7.0 },
  ratio: { current: 4.0, percentiles: {}, average_5y: 3.8 },
};

describe('ValuationDetail 股债 Tab', () => {
  beforeEach(() => {
    getValuationSnapshotMock.mockReset();
    getEquityBondMock.mockReset();
    getDividendYieldMock.mockReset();
    getIndexQuotesMock.mockReset();
    // 默认无日线数据(各用例可覆盖); 拉取失败也走 catch 回退空数组
    getIndexQuotesMock.mockResolvedValue([]);
  });

  async function mountWith(query = {}) {
    // 允许 route query 变化: 通过动态 mock
    const useRouteMock = vi.mocked((await import('vue-router')).useRoute);
    useRouteMock.mockReturnValue({ params: { code: '930955' }, query });
    return shallowMount(ValuationDetail, { global: { stubs: EP_STUBS } });
  }

  it('顶部收益指标中性显示数值、按方向标记分位，默认五年且缺失均值不拼单位', async () => {
    getValuationSnapshotMock.mockResolvedValue([{trade_date:'2026-09-09',pe:9.22,pb:0.88,pe_percentile:{'5y':90.4},pb_percentile:{'5y':85.6}}]);
    getDividendYieldMock.mockResolvedValue([{trade_date:'2026-09-09',dividend_yield:0,percentile:{'5y':4.3}}]);
    getEquityBondMock.mockResolvedValue([{spread:{current:-1,percentiles:{'5y':11.2}},ratio:{current:6.45,percentiles:{'5y':60.5}},series:[]}]);
    const wrapper=await mountWith();await flushPromises();
    expect(wrapper.vm.rangeYears).toBe(5);
    const items=wrapper.findAll('.focus-item');
    expect(items[0].text()).toContain('0.00%');
    expect(items[1].text()).toContain('-1.00百分点');
    expect(items[1].find('.focus-meta span').attributes('style')).toContain('--el-color-danger');
    expect(items[2].find('.focus-meta span').attributes('style')).toContain('--el-text-color-regular');
    expect(wrapper.text()).not.toMatch(/—(?:倍|百分点|%)/);
    wrapper.unmount();
  });

  it('PE 图叠加同期指数收盘价, 按快照日期对齐, 缺日线为 null', async () => {
    getValuationSnapshotMock.mockResolvedValue(makeSnapshotResponse(30));
    getEquityBondMock.mockResolvedValue([{ ...EB_STATS, series: [] }]);
    getDividendYieldMock.mockResolvedValue([]);
    // 只有前 20 个交易日有日线, 后 10 天缺 → null
    const snapshotDates = makeSnapshotRows(30).map((r) => r.trade_date);
    getIndexQuotesMock.mockResolvedValue(makeQuoteRows(20, snapshotDates.slice(0, 20)));
    const wrapper = await mountWith({ tab: 'pe' });
    await flushPromises();
    await flushPromises();
    const chartData = wrapper.vm.chartData;
    expect(chartData.dates.length).toBe(30);
    expect(chartData.comparisonLabel).toBe('指数收盘价');
    expect(chartData.comparisonValues).toHaveLength(30);
    expect(chartData.comparisonValues[0]).toBeCloseTo(4000, 6);
    expect(chartData.comparisonValues[19]).toBeCloseTo(4019, 6);
    expect(chartData.comparisonValues[20]).toBeNull(); // 缺日线日期 → null, 图表端跨接连线不留缺口
    // 切到 PB: 对照保留, 主指标切到 pb 字段
    wrapper.vm.metric = 'pb';
    await flushPromises();
    expect(wrapper.vm.chartData.values[0]).toBeCloseTo(1, 4);
    expect(wrapper.vm.chartData.comparisonValues[0]).toBeCloseTo(4000, 6);
    wrapper.unmount();
  });

  it('日线拉取失败时估值页仍可用, PE 图对照为全 null', async () => {
    getValuationSnapshotMock.mockResolvedValue(makeSnapshotResponse(30));
    getEquityBondMock.mockResolvedValue([{ ...EB_STATS, series: [] }]);
    getDividendYieldMock.mockResolvedValue([]);
    getIndexQuotesMock.mockRejectedValue(new Error('network'));
    const wrapper = await mountWith({ tab: 'pe' });
    await flushPromises();
    await flushPromises();
    const chartData = wrapper.vm.chartData;
    expect(chartData.values.length).toBe(30); // 主指标不受影响
    expect(chartData.comparisonValues.every((v) => v == null)).toBe(true);
    wrapper.unmount();
  });

  it('默认加载并渲染股债序列到 chartData(不再叠加国债对照)', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'spread' });
    await flushPromises();
    await flushPromises();
    const chartData = wrapper.vm.chartData;
    // 默认近 5 年窗口: 30 天全在窗口内
    expect(chartData.dates.length).toBe(30);
    expect(chartData.values.length).toBe(30);
    // 国债对照线已移除
    expect(chartData.comparisonValues).toBeUndefined();
    expect(chartData.comparisonLabel).toBeUndefined();
    expect(chartData.primaryUnit).toBe('百分点');
  });

  it('旧链接 ?tab=eb 兼容映射到股债差', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'eb' });
    await flushPromises();
    expect(wrapper.vm.metric).toBe('spread');
    expect(wrapper.vm.chartData.primaryUnit).toBe('百分点');
  });

  it('切换 ratio 时主指标切换, 窗口同日对齐', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'spread' });
    await flushPromises();
    // 初始 spread
    expect(wrapper.vm.chartData.values[0]).toBeCloseTo(5, 4);
    expect(wrapper.vm.chartData.primaryUnit).toBe('百分点');
    // 切到 ratio(一级平级 Tab)
    wrapper.vm.metric = 'ratio';
    await flushPromises();
    expect(wrapper.vm.chartData.values[0]).toBeCloseTo(2, 4);
    expect(wrapper.vm.chartData.primaryUnit).toBe('倍');
  });

  it('国债字段缺失(旧后端)时主指标仍可用', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    // 旧后端: series 无 cn_10y_bond_yield 字段
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30, false) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'eb' });
    await flushPromises();
    const chartData = wrapper.vm.chartData;
    expect(chartData.values.length).toBe(30); // 主指标可用
    // 国债对照线已移除, 不再有对照字段
    expect(chartData.comparisonValues).toBeUndefined();
  });
});
