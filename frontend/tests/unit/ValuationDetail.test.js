// ValuationDetail 股债 Tab 测试(Phase A2):
// 覆盖 ?tab=eb 直达、spread/ratio 切换、不同窗口同日对齐、国债字段缺失时主指标仍可用。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';

// 模拟 API 模块
const getValuationSnapshotMock = vi.fn();
const getEquityBondMock = vi.fn();
const getDividendYieldMock = vi.fn();

vi.mock('../../src/api/index.js', () => ({
  default: {},
  getValuationSnapshot: (...a) => getValuationSnapshotMock(...a),
  getEquityBond: (...a) => getEquityBondMock(...a),
  getDividendYield: (...a) => getDividendYieldMock(...a),
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
  });

  async function mountWith(query = {}) {
    // 允许 route query 变化: 通过动态 mock
    const useRouteMock = vi.mocked((await import('vue-router')).useRoute);
    useRouteMock.mockReturnValue({ params: { code: '930955' }, query });
    return shallowMount(ValuationDetail, { global: { stubs: EP_STUBS } });
  }

  it('默认加载并渲染股债序列到 chartData(含国债对照字段)', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'eb' });
    await flushPromises();
    await flushPromises();
    const chartData = wrapper.vm.chartData;
    // 默认近 3 年窗口: 30 天全在窗口内
    expect(chartData.dates.length).toBe(30);
    expect(chartData.values.length).toBe(30);
    // 国债对照字段
    expect(chartData.comparisonValues.length).toBe(30);
    expect(chartData.comparisonValues[0]).toBeCloseTo(2.5, 4);
    expect(chartData.comparisonLabel).toBe('十年期国债收益率');
    expect(chartData.primaryUnit).toBe('百分点');
  });

  it('切换 ratio 时主指标切换但国债对照保留, 窗口同日对齐', async () => {
    getValuationSnapshotMock.mockResolvedValue([
      { index_code: '930955', index_name: '红利低波100', rows: makeSnapshotRows(30) },
    ]);
    getEquityBondMock.mockResolvedValue([
      { ...EB_STATS, series: makeEbSeries(30) },
    ]);
    getDividendYieldMock.mockResolvedValue([]);
    const wrapper = await mountWith({ tab: 'eb' });
    await flushPromises();
    // 初始 spread
    expect(wrapper.vm.chartData.values[0]).toBeCloseTo(5, 4);
    expect(wrapper.vm.chartData.primaryUnit).toBe('百分点');
    // 切到 ratio
    wrapper.vm.ebMetric = 'ratio';
    await flushPromises();
    expect(wrapper.vm.chartData.values[0]).toBeCloseTo(2, 4);
    expect(wrapper.vm.chartData.primaryUnit).toBe('倍');
    // 日期与国债对照仍一一对应
    expect(wrapper.vm.chartData.dates.length).toBe(wrapper.vm.chartData.comparisonValues.length);
  });

  it('国债字段缺失(旧后端)时主指标仍可用, 对照为空', async () => {
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
    // 缺失字段 → null → 前端对照线全部缺失, 组件内部退回单轴
    expect(chartData.comparisonValues.every((v) => v == null)).toBe(true);
  });
});
