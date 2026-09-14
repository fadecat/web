// CbSpreadChart 利差图测试(T3):
// 覆盖双序列(利差+国债对照)、零轴与 30/70 分位参考线、负值保持、
// 悬浮 tooltip 分位(仅利差序列)、样本不足不显示分位、卸载释放。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// jsdom 无 ResizeObserver, 提供空实现
globalThis.ResizeObserver = class {
  constructor() {}
  observe() {}
  unobserve() {}
  disconnect() {}
};

// ECharts 模拟: 只验证 buildOption 产物, 不渲染真实 canvas
const setOptionMock = vi.fn();
const { useMock } = vi.hoisted(() => ({ useMock: vi.fn() }));
const disposeMock = vi.fn();
const resizeMock = vi.fn();

vi.mock('echarts/core', () => ({
  use: useMock,
  init: (el, ...rest) => initMock(el, ...rest),
}));
vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));
vi.mock('echarts/charts', () => ({ LineChart: {} }));
vi.mock('echarts/components', () => ({
  DataZoomComponent: {},
  GridComponent: {},
  LegendComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));

// initMock 必须在使用前声明(被上面 vi.mock 工厂引用)
const initMock = vi.fn(() => ({
  setOption: setOptionMock,
  dispose: disposeMock,
  resize: resizeMock,
  on: vi.fn(),
}));

import CbSpreadChart from '../../src/components/CbSpreadChart.vue';

// 捕获每次 render 的 option
let lastOption = null;
setOptionMock.mockImplementation((opt) => {
  lastOption = opt;
});

const ROWS = [
  { trade_date: '2024-01-01', avg_ytm: -8.25, bond_yield: 2.5, spread: -10.75 },
  { trade_date: '2024-01-02', avg_ytm: -5.5, bond_yield: 2.4, spread: -7.9 },
  { trade_date: '2024-01-03', avg_ytm: -1.0, bond_yield: 2.3, spread: -3.3 },
];

async function mountChart(props = {}) {
  const wrapper = shallowMount(CbSpreadChart, {
    props: { rows: ROWS, ...props },
    attachTo: document.body,
    // 图表组件依赖主题 store(Pinia) 取配色 token
    global: { plugins: [createPinia()] },
  });
  await flushPromises();
  return wrapper;
}

describe('CbSpreadChart 利差图', () => {
  beforeEach(() => {
    setOptionMock.mockClear();
    disposeMock.mockClear();
    lastOption = null;
  });

  it('单 grid 双序列: 利差蓝实线为主, 10Y 国债橙虚线对照', async () => {
    await mountChart();
    expect(Array.isArray(lastOption.xAxis)).toBe(false); // 单图非数组
    expect(lastOption.series).toHaveLength(2);
    const [spread, bond] = lastOption.series;
    expect(spread.name).toBe('转债-国债利差（百分点）');
    expect(spread.lineStyle.color).toBe('#2563eb');
    expect(spread.lineStyle.type).toBeUndefined(); // 实线
    expect(spread.lineStyle.width).toBe(1.8);
    expect(bond.name).toBe('10Y国债收益率（%）');
    expect(bond.lineStyle.color).toBe('#ea580c');
    expect(bond.lineStyle.type).toBe('dashed');
    // 负利差原样保留, 不补 0
    expect(spread.data).toEqual([-10.75, -7.9, -3.3]);
    expect(bond.data).toEqual([2.5, 2.4, 2.3]);
  });

  it('零轴恒在 + 30/70 分位线(正向指标: p30 红 / p70 绿)', async () => {
    await mountChart();
    const data = lastOption.series[0].markLine.data;
    expect(data).toHaveLength(3);
    // 零轴: 灰色实线, 标签固定 '0'
    expect(data[0].yAxis).toBe(0);
    expect(data[0].lineStyle.color).toBe('#6b7280');
    expect(data[0].lineStyle.type).toBe('solid');
    expect(data[0].label.formatter()).toBe('0');
    // spread 有效 [-10.75, -7.9, -3.3], n=3 线性插值:
    // p30: idx=0.6 → -10.75 + 2.85×0.6 = -9.04; p70: idx=1.4 → -7.9 + 4.6×0.4 = -6.06
    expect(data[1].name).toBe('30分位');
    expect(data[1].lineStyle.color).toBe('#dc2626'); // 低分位 = 偏贵 → 红
    expect(data[1].yAxis).toBeCloseTo(-9.04, 10);
    expect(data[2].name).toBe('70分位');
    expect(data[2].lineStyle.color).toBe('#16a34a'); // 高分位 = 偏便宜 → 绿
    expect(data[2].yAxis).toBeCloseTo(-6.06, 10);
    // markLine 静默且无箭头
    expect(lastOption.series[0].markLine.silent).toBe(true);
    expect(lastOption.series[0].markLine.symbol).toBe('none');
    // 国债对照线不携带 markLine
    expect(lastOption.series[1].markLine).toBeUndefined();
  });

  it('悬浮 tooltip 仅利差序列带分位, 国债序列无分位', async () => {
    // 25 条递增利差(样本足够), 国债恒定
    const rows = [];
    for (let i = 0; i < 25; i += 1) {
      const avg = -25 + i;
      rows.push({
        trade_date: `2024-01-${String(i + 1).padStart(2, '0')}`,
        avg_ytm: avg,
        bond_yield: 2.5,
        spread: avg - 2.5,
      });
    }
    await mountChart({ rows });
    const out = lastOption.tooltip.formatter([
      {
        axisValue: '2024-01-25',
        seriesName: '转债-国债利差（百分点）',
        marker: '',
        data: -3.5, // 序列末条 spread = (-1) - 2.5
      },
      { axisValue: '2024-01-25', seriesName: '10Y国债收益率（%）', marker: '', data: 2.5 },
    ]);
    expect(out).toContain('2024-01-25');
    // 利差末值为窗口最大: below=24 + equal=1 → 98%, 正向指标高分位 → 绿
    expect(out.match(/（98\.0% 分位）/g)).toHaveLength(1);
    expect(out).toContain('color:#16a34a');
    // 国债序列只有数值无分位
    expect(out).toContain('10Y国债收益率');
    const bondLine = out.split('<br/>').find((l) => l.includes('10Y国债收益率'));
    expect(bondLine).not.toContain('分位');
  });

  it('样本不足 20 条时悬浮 tooltip 不展示分位', async () => {
    await mountChart(); // ROWS 仅 3 条
    const out = lastOption.tooltip.formatter([
      {
        axisValue: '2024-01-01',
        seriesName: '转债-国债利差（百分点）',
        marker: '',
        data: -10.75,
      },
    ]);
    expect(out).toContain('-10.75%');
    expect(out).not.toContain('分位');
  });

  it('空数据时渲染占位标题且无序列', async () => {
    await mountChart({ rows: [] });
    expect(lastOption.title.text).toBe('暂无数据');
    expect(lastOption.series).toHaveLength(0);
  });

  it('卸载时断开 observer 并 dispose 实例', async () => {
    const wrapper = await mountChart();
    expect(disposeMock).not.toHaveBeenCalled();
    wrapper.unmount();
    expect(disposeMock).toHaveBeenCalledTimes(1);
  });
});
