// CbMarketChart 双图同步测试(T3):
// 覆盖两图同日期轴、缩放作用于两轴、负值与 null 保持、单点可见、30/70 分位参考线、
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

  it('未选日期时仅 30/70 分位参考线, 无定位竖线', async () => {
    await mountChart();
    // ROWS: median 有效 [132.5, 128.0], ytm 有效 [-8.25, 0, -5.5]
    for (const s of lastOption.series) {
      expect(s.markLine).toBeDefined();
      expect(s.markLine.data).toHaveLength(2);
      for (const d of s.markLine.data) {
        expect(d.xAxis).toBeUndefined(); // 无选中日期竖线
        expect(typeof d.yAxis).toBe('number');
      }
    }
    // 价格中位数: p30 绿 / p70 红(分位低=便宜, 同估值页语义)
    const price = lastOption.series[0].markLine.data;
    expect(price[0].name).toBe('30分位');
    expect(price[0].lineStyle.color).toBe('#16a34a');
    expect(price[0].yAxis).toBeCloseTo(129.35, 10); // 128 + 4.5 × 0.3
    expect(price[1].name).toBe('70分位');
    expect(price[1].lineStyle.color).toBe('#dc2626');
    expect(price[1].yAxis).toBeCloseTo(131.15, 10); // 128 + 4.5 × 0.7
    // 平均到期收益率: 方向相反(p30 红 / p70 绿)
    const ytm = lastOption.series[1].markLine.data;
    expect(ytm[0].lineStyle.color).toBe('#dc2626');
    expect(ytm[0].yAxis).toBeCloseTo(-6.6, 10); // -8.25 + 2.75 × 0.6
    expect(ytm[1].lineStyle.color).toBe('#16a34a');
    expect(ytm[1].yAxis).toBeCloseTo(-3.3, 10); // -5.5 + 5.5 × 0.4
    // 只有两条业务线, 不附加额外 series
    expect(lastOption.series).toHaveLength(2);
    // 图例仅识别(选中模式关闭), 不提供隐藏
    expect(lastOption.legend.selectedMode).toBe(false);
  });

  it('选中日期时定位竖线在前, 分位参考线随其后', async () => {
    await mountChart({ selectedDate: '2024-01-02' });
    expect(lastOption.series[0].markLine.data[0].xAxis).toBe('2024-01-02');
    expect(lastOption.series[1].markLine.data[0].xAxis).toBe('2024-01-02');
    // 竖线 + 30/70 两条分位线
    expect(lastOption.series[0].markLine.data).toHaveLength(3);
    expect(lastOption.series[0].markLine.data[1].yAxis).toBeCloseTo(129.35, 10);
  });

  it('悬浮 tooltip 展示当日窗口内百分位(样本足够时)', async () => {
    // 25 条递增数据(日期均在 1 月内, 合法), 中位 100..124 / YTM -25..-1 均为精确整数
    const rows = [];
    for (let i = 0; i < 25; i += 1) {
      rows.push({
        trade_date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        median_price: 100 + i,
        avg_ytm: -25 + i,
        count: 400,
      });
    }
    await mountChart({ rows });
    const out = lastOption.tooltip.formatter([
      { axisValue: '2024-01-25', seriesName: '价格中位数（元）', marker: '', data: 124 },
      { axisValue: '2024-01-25', seriesName: '平均到期收益率（集思录口径，%）', marker: '', data: -1 },
    ]);
    expect(out).toContain('2024-01-25');
    // 两条系列的末值均为窗口最大: below=24 + equal=1 → 24.5/25 = 98%
    expect(out).toContain('（98.0% 分位）');
    expect(out.match(/（98\.0% 分位）/g)).toHaveLength(2);
    // 价格 98 分位为"贵"方向 → 红色标注; YTM 98 分位为"便宜"方向 → 绿色标注
    expect(out).toContain('color:#dc2626');
    expect(out).toContain('color:#16a34a');
  });

  it('样本不足 20 条时悬浮 tooltip 不展示分位', async () => {
    await mountChart(); // ROWS 仅 3 条
    const out = lastOption.tooltip.formatter([
      { axisValue: '2024-01-01', seriesName: '价格中位数（元）', marker: '', data: 132.5 },
    ]);
    expect(out).toContain('132.50元');
    expect(out).not.toContain('分位');
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
