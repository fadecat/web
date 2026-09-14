// ValuationChart 双轴对照线测试(Phase A2):
// 覆盖右轴单位、tooltip 双值、null 跨接连线、主指标分位不受对照线影响、移除对照序列后恢复单轴。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// ECharts 模拟: 只验证 buildOption 产物, 不渲染真实 canvas
const setOptionMock = vi.fn();
const disposeMock = vi.fn();
const resizeMock = vi.fn();
const initMock = vi.fn(() => ({ setOption: setOptionMock, dispose: disposeMock, resize: resizeMock }));

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
import { chartTheme } from '../../src/utils/chartTheme';

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
  it('跨手机断点时重新适配画布尺寸', async () => {
    const original = window.innerWidth;
    const wrapper = await mountChart({});
    resizeMock.mockClear();
    window.innerWidth = original >= 768 ? 375 : 1280;
    window.dispatchEvent(new Event('resize'));
    expect(resizeMock).toHaveBeenCalled();
    wrapper.unmount();
    window.innerWidth = original;
  });
  it('切换指标方向和窗口后更新参考线与悬停文案', async () => {
    const wrapper = await mountChart({ metricKey: 'pe' });
    const lowPe = lastOption.series[0].markLine.data[0].lineStyle.color;
    const highPe = lastOption.series[0].markLine.data[2].lineStyle.color;
    await wrapper.setProps({ metricKey: 'spread', windowYears: 3 });
    await flushPromises();
    expect(lastOption.series[0].markLine.data[0].lineStyle.color).toBe(highPe);
    expect(lastOption.series[0].markLine.data[2].lineStyle.color).toBe(lowPe);
    expect(lastOption.tooltip.formatter([{seriesName:'股债差',data:11,dataIndex:1,axisValue:DATES[1],marker:''}])).toContain('当日分位（近3年窗口）');
    wrapper.unmount();
  });
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
    expect(bondSeries.lineStyle.type).toBeFalsy(); // 实线(指数收盘价叠加不虚线)
    expect(bondSeries.connectNulls).toBe(true); // 个别日期缺数据跨接连线, 不留缺口
    expect(bondSeries.areaStyle).toBeUndefined(); // 对照线不带面积
    // 配色对齐券商惯例(同 PeChart 定版): 估值指标=橘黄 / 指数收盘价=蓝
    const t = chartTheme(false);
    expect(lastOption.series[0].lineStyle.color).toBe(t.peOrange);
    expect(lastOption.series[0].areaStyle.color).toBe(t.orangeArea);
    expect(bondSeries.lineStyle.color).toBe(t.indexBlue);
    // itemStyle 与线同色: 图例图标/tooltip 圆点不再与线颜色错位
    expect(lastOption.series[0].itemStyle.color).toBe(lastOption.series[0].lineStyle.color);
    expect(bondSeries.itemStyle.color).toBe(bondSeries.lineStyle.color);
    // 图例图标纯线段, 无中间圆点
    expect(lastOption.legend.icon).toBe('rect');
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
    expect(html).toContain('当日分位（近5年窗口）');
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
    const p50 = markLineData.find((l) => l.name === '中位');
    expect(p50.yAxis).toBeCloseTo(11, 6);
    expect(p50.label.position).toBe('start');
    expect(p50.label.align).toBe('left');
    expect(p50.label.offset).toEqual([6, 0]);
    expect(lastOption.grid.right).toBe(60);
    // 对照线 series 不应有 markLine
    expect(lastOption.series[1].markLine).toBeUndefined();
  });

  it('tooltip 显示主指标当前点在窗口内的分位, 不把国债线纳入计算', async () => {
    await mountChart({
      comparisonValues: [1000, 1000, 1000],
      comparisonLabel: '十年期国债收益率',
      primaryUnit: '倍',
      comparisonUnit: '%',
    });
    const html = lastOption.tooltip.formatter([
      { seriesName: '股债差', data: 11, dataIndex: 1, axisValue: '2024-01-02', marker: '' },
      { seriesName: '十年期国债收益率', data: 1000, dataIndex: 1, axisValue: '2024-01-02', marker: '' },
    ]);
    expect(html).toContain('当日分位（近5年窗口） <b>50.0%</b>');
  });

  it('指数收盘价对照不传单位时点位裸显, 不再拼 % 后缀', async () => {
    // 回归: 旧默认 comparisonUnit='%' 是国债对照遗留, 指数点位被显示成 11353.77%
    await mountChart({
      comparisonValues: [4000, 4010, 4020],
      comparisonLabel: '指数收盘价',
    });
    expect(lastOption.yAxis[1].name).toBe(''); // 右轴不显示 % 轴名
    const html = lastOption.tooltip.formatter([
      { seriesName: '股债差', data: 10, dataIndex: 0, axisValue: DATES[0], marker: '' },
      { seriesName: '指数收盘价', data: 4000, dataIndex: 0, axisValue: DATES[0], marker: '' },
    ]);
    expect(html).toContain('指数收盘价 <b>4000.00</b>');
    expect(html).not.toContain('4000.00%'); // 点位不带百分号(当日分位的 % 不受影响)
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
