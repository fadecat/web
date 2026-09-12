// CbMarketChart 双图同步测试(T3):
// 覆盖两图同日期轴、缩放作用于两轴、负值与 null 保持、单点可见、无分位线、
// date-select 事件有效、卸载释放。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// jsdom 无 ResizeObserver, 提供空实现
let resizeCallback = null;
globalThis.ResizeObserver = class {
  constructor(callback) { resizeCallback = callback; }
  observe() {}
  unobserve() {}
  disconnect() {}
};

// ECharts 模拟: 只验证 buildOption 产物, 不渲染真实 canvas
const setOptionMock = vi.fn();
const { useMock } = vi.hoisted(() => ({ useMock: vi.fn() }));
const disposeMock = vi.fn();
const resizeMock = vi.fn();
const onMock = vi.fn();
const zrOnMock = vi.fn();
const zrOffMock = vi.fn();
const convertFromPixelMock = vi.fn(() => 1); // 默认命中第 2 个点
const containPixelMock = vi.fn(() => true);
const getZrMock = vi.fn(() => ({ on: zrOnMock, off: zrOffMock }));

vi.mock('echarts/core', () => ({
  use: useMock,
  init: (el, ...rest) => initMock(el, ...rest),
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));
vi.mock('echarts/charts', () => ({ LineChart: {} }));
vi.mock('echarts/components', () => ({
  AxisPointerComponent: {},
  DataZoomComponent: {},
  GridComponent: {},
  LegendComponent: {},
  TooltipComponent: {},
  TitleComponent: {},
  MarkLineComponent: {},
}));

// initMock 必须在使用前声明(被上面 vi.mock 工厂引用)
const initMock = vi.fn(() => ({
  setOption: setOptionMock,
  dispose: disposeMock,
  resize: resizeMock,
  on: onMock,
  getZr: getZrMock,
  convertFromPixel: convertFromPixelMock,
  containPixel: containPixelMock,
}));

import CbMarketChart from '../../src/components/CbMarketChart.vue';

// 捕获每次 render 的 option
let lastOption = null;
setOptionMock.mockImplementation((opt) => {
  lastOption = opt;
});

const ROWS = [
  { trade_date: '2024-01-01', median_price: 132.5, avg_ytm: -8.25, count: 400 },
  { trade_date: '2024-01-02', median_price: null, avg_ytm: 0, count: 399 },
  { trade_date: '2024-01-03', median_price: 128.0, avg_ytm: -5.5, count: 401 },
];

async function mountChart(props = {}) {
  const wrapper = shallowMount(CbMarketChart, {
    props: { rows: ROWS, ...props },
    attachTo: document.body,
    // 图表组件依赖主题 store(Pinia) 取配色 token
    global: { plugins: [createPinia()] },
  });
  await flushPromises();
  return wrapper;
}

describe('CbMarketChart 双图同步', () => {
  beforeEach(() => {
    setOptionMock.mockClear();
    disposeMock.mockClear();
    zrOnMock.mockClear();
    lastOption = null;
    convertFromPixelMock.mockReturnValue(1);
    containPixelMock.mockReturnValue(true);
  });

  it('注册标题和定位线组件以支持实际浏览器渲染', async () => {
    await mountChart();
    const registered = useMock.mock.calls.at(-1)[0];
    expect(registered).toHaveLength(9);
  });

  it('两图使用同一排序后日期轴', async () => {
    await mountChart();
    expect(Array.isArray(lastOption.xAxis)).toBe(true);
    expect(lastOption.xAxis).toHaveLength(2);
    expect(lastOption.xAxis[0].data).toEqual(['2024-01-01', '2024-01-02', '2024-01-03']);
    expect(lastOption.xAxis[1].data).toEqual(lastOption.xAxis[0].data);
    // 两条 series 分别挂在两个 grid
    expect(lastOption.series[0].xAxisIndex).toBe(0);
    expect(lastOption.series[1].xAxisIndex).toBe(1);
  });

  it('缩放条作用于两个 xAxis, 游标 link 同步两图', async () => {
    await mountChart();
    expect(lastOption.dataZoom[0].xAxisIndex).toEqual([0, 1]);
    expect(lastOption.dataZoom[1].xAxisIndex).toEqual([0, 1]);
    expect(lastOption.axisPointer.link).toEqual([{ xAxisIndex: [0, 1] }]);
  });

  it('跨断点重排后同步调整 canvas 尺寸', async () => {
    await mountChart();
    window.innerWidth = 375;
    resizeCallback();
    expect(resizeMock).toHaveBeenCalled();
  });

  it('仅选中日期变化时保留 dataZoom 窗口', async () => {
    const wrapper = await mountChart();
    setOptionMock.mockClear();
    await wrapper.setProps({ selectedDate: '2024-01-02' });
    await flushPromises();
    expect(setOptionMock.mock.calls.at(-1)[1]).toBe(false);
  });

  it('负值与 null 保持, 不补 0 不插值', async () => {
    await mountChart();
    const top = lastOption.series[0].data;
    const bottom = lastOption.series[1].data;
    // 负值 -8.25 保持, null 保持
    expect(top).toEqual([132.5, null, 128.0]);
    expect(bottom).toEqual([-8.25, 0, -5.5]);
    for (const s of lastOption.series) {
      expect(s.connectNulls).toBe(false);
      expect(s.smooth).toBe(false);
    }
    // 均价线为蓝色实线, 收益率为橙色虚线
    expect(lastOption.series[0].lineStyle.color).toBe('#2563eb');
    expect(lastOption.series[0].lineStyle.type).toBeUndefined();
    expect(lastOption.series[0].itemStyle.color).toBe('#2563eb');
    expect(lastOption.series[1].lineStyle.color).toBe('#ea580c');
    expect(lastOption.series[1].lineStyle.type).toBe('dashed');
    expect(lastOption.series[1].itemStyle.color).toBe('#ea580c');
  });

  it('y 轴自适应(scale), 不固定 95-145', async () => {
    await mountChart();
    expect(lastOption.yAxis[0].scale).toBe(true);
    expect(lastOption.yAxis[1].scale).toBe(true);
    expect(lastOption.yAxis[0].min).toBeUndefined();
  });

  it('单点可见: 显示点标记', async () => {
    await mountChart({ rows: [{ trade_date: '2024-01-01', median_price: 130, avg_ytm: -3, count: 400 }] });
    expect(lastOption.series[0].symbol).toBe('circle');
    expect(lastOption.series[1].symbol).toBe('circle');
    expect(lastOption.series[0].symbolSize).toBeGreaterThan(0);
  });

  it('无分位/均线/买卖区间参考线(未选日期时)', async () => {
    await mountChart();
    // 没有 percentile / moving average 之类的 markLine
    for (const s of lastOption.series) {
      expect(s.markLine).toBeUndefined();
    }
    // 只有两条业务线, 不附加额外 series
    expect(lastOption.series).toHaveLength(2);
    // 图例仅识别(选中模式关闭), 不提供隐藏
    expect(lastOption.legend.selectedMode).toBe(false);
  });

  it('选中日期时仅绘制定位竖线, 仍非分位线', async () => {
    await mountChart({ selectedDate: '2024-01-02' });
    expect(lastOption.series[0].markLine.data[0].xAxis).toBe('2024-01-02');
    expect(lastOption.series[1].markLine.data[0].xAxis).toBe('2024-01-02');
  });

  it('点击/触摸图表回传 ISO 日期(date-select)', async () => {
    const wrapper = await mountChart();
    const clickHandler = zrOnMock.mock.calls.find((c) => c[0] === 'click')?.[1];
    expect(typeof clickHandler).toBe('function');
    convertFromPixelMock.mockReturnValue(2); // 命中点索引 2 → 2024-01-03
    clickHandler({ offsetX: 100, offsetY: 100 });
    expect(wrapper.emitted('date-select')).toBeTruthy();
    expect(convertFromPixelMock.mock.calls[0][1]).toBe(100);
    const calls = wrapper.emitted('date-select');
    expect(calls[0]).toEqual(['2024-01-03']);
  });

  it('点击缩放条或图表外部不回传日期', async () => {
    const wrapper = await mountChart();
    containPixelMock.mockReturnValue(false);
    const clickHandler = zrOnMock.mock.calls.find((c) => c[0] === 'click')?.[1];
    clickHandler({ offsetX: 100, offsetY: 100 });
    expect(wrapper.emitted('date-select')).toBeFalsy();
  });

  it('卸载时断开 observer 并 dispose 实例', async () => {
    const wrapper = await mountChart();
    expect(disposeMock).not.toHaveBeenCalled();
    wrapper.unmount();
    expect(disposeMock).toHaveBeenCalledTimes(1);
    // zr 点击处理器被移除
    expect(zrOffMock).toHaveBeenCalled();
  });
});
