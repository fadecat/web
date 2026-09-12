// ValuationChart 双轴对照线测试(Phase A2):
// 覆盖右轴单位、tooltip 双值、null 缺口、主指标分位不受对照线影响、移除对照序列后恢复单轴。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// ECharts 模拟: 只验证 buildOption 产物, 不渲染真实 canvas
const setOptionMock = vi.fn();
const disposeMock = vi.fn();
const initMock = vi.fn(() => ({ setOption: setOptionMock, dispose: disposeMock, resize: vi.fn() }));

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: (el, ...rest) => initMock(el, ...rest),
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));
vi.mock('echarts/charts', () => ({ LineChart: {} }));
vi.mock('echarts/components', () => ({
  AxisPointerComponent: {},
  DataZoomComponent: {},
  GridComponent: {},
  LegendComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));

import ValuationChart from '../../src/components/ValuationChart.vue';

// 捕获每次 render 的 option
let lastOption = null;
setOptionMock.mockImplementation((opt) => {
  lastOption = opt;
});

const DATES = ['2024-01-01', '2024-01-02', '2024-01-03'];
const VALUES = [10, 11, 12];
const BOND = [2.5, null, 2.7]; // 中间日期国债缺失

async function mountChart(props) {
  const wrapper = shallowMount(ValuationChart, {
    props: {
      dates: DATES,
      values: VALUES,
      metricLabel: '股债差',
      ...props,
    },
    attachTo: document.body,
    // 图表组件依赖主题 store(Pinia) 取配色 token
    global: { plugins: [createPinia()] },
  });
  await flushPromises();
  return wrapper;
}

describe('ValuationChart 双轴对照线', () => {
  beforeEach(() => {
    setOptionMock.mockClear();
    lastOption = null;
  });

  it('不传对照 props 时保持单轴单线(旧行为)', async () => {
    await mountChart({});
    expect(lastOption).not.toBeNull();
    expect(Array.isArray(lastOption.yAxis)).toBe(true);
    expect(lastOption.yAxis).toHaveLength(1);
    expect(lastOption.series).toHaveLength(1);
    expect(lastOption.legend).toBeUndefined();
    // 旧 tooltip 单值格式
    expect(lastOption.tooltip.formatter([{ seriesName: '股债差', data: 10, axisValue: '2024-01-01' }])).toContain('股债差');
  });

  it('传对照 props 时出现双轴与第二条对照线', async () => {
    await mountChart({
      comparisonValues: BOND,
      comparisonLabel: '十年期国债收益率',
      primaryUnit: '百分点',
      comparisonUnit: '%',
    });
    expect(lastOption.yAxis).toHaveLength(2);
    expect(lastOption.yAxis[1].name).toBe('%');
    expect(lastOption.yAxis[0].name).toBe('百分点');
    expect(lastOption.series).toHaveLength(2);
    const bondSeries = lastOption.series[1];
    expect(bondSeries.name).toBe('十年期国债收益率');
    expect(bondSeries.yAxisIndex).toBe(1);
    expect(bondSeries.lineStyle.type).toBe('dashed');
    expect(bondSeries.connectNulls).toBe(false); // 缺失点不连线
    expect(bondSeries.areaStyle).toBeUndefined(); // 对照线不带面积
    expect(lastOption.legend).toBeTruthy();
    expect(lastOption.legend.data).toEqual(['股债差', '十年期国债收益率']);
  });

  it('tooltip 同时显示两条序列值与单位, 缺失值为 —', async () => {
    await mountChart({
      comparisonValues: BOND,
      comparisonLabel: '十年期国债收益率',
      primaryUnit: '百分点',
      comparisonUnit: '%',
    });
    const html = lastOption.tooltip.formatter([
      { seriesName: '股债差', data: 10, axisValue: '2024-01-01', marker: '' },
      { seriesName: '十年期国债收益率', data: 2.5, axisValue: '2024-01-01', marker: '' },
    ]);
    expect(html).toContain('股债差');
    expect(html).toContain('10.00百分点');
    expect(html).toContain('十年期国债收益率');
    expect(html).toContain('2.50%');
    // 缺失值显示 —
    const html2 = lastOption.tooltip.formatter([
      { seriesName: '股债差', data: null, axisValue: '2024-01-02', marker: '' },
      { seriesName: '十年期国债收益率', data: null, axisValue: '2024-01-02', marker: '' },
    ]);
    expect(html2).not.toContain('undefined');
    expect(html2).not.toContain('NaN');
  });

  it('主指标分位参考线只取主指标, 不受对照线影响', async () => {
    await mountChart({
      comparisonValues: [1000, 1000, 1000], // 若被纳入分位会推高 p50
      comparisonLabel: '十年期国债收益率',
    });
    const markLineData = lastOption.series[0].markLine.data;
    // 主指标 [10,11,12] 的中位值应为 11
    const p50 = markLineData.find((l) => l.name === '中位值');
    expect(p50.yAxis).toBeCloseTo(11, 6);
    // 对照线 series 不应有 markLine
    expect(lastOption.series[1].markLine).toBeUndefined();
  });

  it('对照序列全部缺失时退回单轴, 不展示对照线', async () => {
    await mountChart({
      comparisonValues: [null, null, null],
      comparisonLabel: '十年期国债收益率',
      primaryUnit: '百分点',
    });
    expect(lastOption.yAxis).toHaveLength(1);
    expect(lastOption.series).toHaveLength(1);
    expect(lastOption.legend).toBeUndefined();
  });

  it('对照序列字段缺失(旧后端)时主图仍可用', async () => {
    // 模拟旧后端: comparisonValues 是 undefined → props 默认 []
    await mountChart({ comparisonValues: undefined });
    expect(lastOption.yAxis).toHaveLength(1);
    expect(lastOption.series).toHaveLength(1);
    expect(lastOption.series[0].data).toEqual(VALUES);
  });
});
