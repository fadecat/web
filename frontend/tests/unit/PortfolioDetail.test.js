// 组合详情页组件测试(vitest + @vue/test-utils)。
//
// 覆盖区域①(收益条)与区域⑥(相关性矩阵)这两块"规则写在模板里、最容易被改错"的地方。
// 曲线(NavChart)不在此测: 它依赖 echarts 的 canvas 渲染, jsdom 下不可靠 ——
// 数值换算逻辑已由 src/utils/backtestView.test.mjs 覆盖。
import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import ElementPlus from 'element-plus';

import CorrelationMatrix from '../../src/components/portfolio/CorrelationMatrix.vue';
import ReturnBar from '../../src/components/portfolio/ReturnBar.vue';

const WINDOWS = {
  d1: { value: 1.08, actual_start: null, actual_end: '2026-09-18', composite: true },
  w1: { value: -0.41, actual_start: '2026-09-11', actual_end: '2026-09-18', composite: false },
  m1: { value: -0.62, actual_start: '2026-08-19', actual_end: '2026-09-18', composite: false },
  ytd: { value: 6.67, actual_start: '2026-01-02', actual_end: '2026-09-18', composite: false },
  y1: { value: 10.4, actual_start: '2025-09-18', actual_end: '2026-09-18', composite: false },
  y3: { value: 53.51, actual_start: '2023-09-18', actual_end: '2026-09-18', composite: false },
  inception: { value: 335.07, actual_start: '2013-04-26', actual_end: '2026-09-18', composite: false },
};

const mountWith = (Component, props) =>
  mount(Component, { props, global: { plugins: [ElementPlus] } });

describe('ReturnBar(区域① 收益条)', () => {
  it('渲染七格且标签顺序固定', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const labels = wrapper.findAll('.cell .label').map((n) => n.text().replace('ⓘ', '').trim());
    expect(labels).toEqual(['近1日', '近1周', '近1月', '今年来', '近1年', '近3年', '成立来']);
  });

  it('首格放大, 其余不放大', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const cells = wrapper.findAll('.cell');
    expect(cells[0].classes()).toContain('is-featured');
    expect(cells[1].classes()).not.toContain('is-featured');
  });

  it('涨红跌绿: 正数 trend-up, 负数 trend-down', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const values = wrapper.findAll('.cell .value');
    expect(values[0].classes()).toContain('trend-up');
    expect(values[1].classes()).toContain('trend-down');
    expect(values[0].text()).toBe('1.08%');
  });

  it('只有「近1日」带合成口径标记', () => {
    const wrapper = mountWith(ReturnBar, { windows: WINDOWS });
    const marks = wrapper.findAll('.cell .mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].element.parentElement.textContent).toContain('近1日');
  });

  it('空数据(不可回测)显示破折号而不是 0.00%', () => {
    const wrapper = mountWith(ReturnBar, { windows: null });
    const values = wrapper.findAll('.cell .value');
    expect(values).toHaveLength(7);
    values.forEach((v) => {
      expect(v.text()).toBe('—');
      expect(v.classes()).toContain('trend-flat');
    });
  });
});

describe('CorrelationMatrix(区域⑥ 相关性矩阵)', () => {
  const correlation = {
    start: '2016-09-19',
    end: '2026-09-18',
    symbols: ['100001.OF', '100002.OF', '100003.OF'],
    matrix: [
      [1, 0.43, 0.06],
      [0.43, 1, 0.09],
      [0.06, 0.09, 1],
    ],
  };
  const assets = [
    { symbol: '100001.OF', name: '测试基金A' },
    { symbol: '100002.OF', name: '测试基金B' },
    { symbol: '100003.OF', name: '测试基金C' },
  ];

  it('显式标注计算区间(与回测区间对齐方向不同)', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    expect(wrapper.find('.range').text()).toContain('2016-09-19');
    expect(wrapper.find('.range').text()).toContain('2026-09-18');
  });

  it('行列表头按 1..N 编号, 且带名称与代码', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const colHeads = wrapper.findAll('.col-head').map((n) => n.text());
    expect(colHeads).toEqual(['1', '2', '3']);
    expect(wrapper.findAll('.row-head')).toHaveLength(3);
    expect(wrapper.find('.row-head .name').text()).toBe('测试基金A');
    expect(wrapper.find('.row-head .code').text()).toBe('100001.OF');
  });

  it('对角线为 1 且带 is-diagonal 标记', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const firstRow = wrapper.findAll('tbody tr')[0];
    const cells = firstRow.findAll('.value');
    expect(cells[0].text()).toBe('1.00');
    expect(cells[0].classes()).toContain('is-diagonal');
    expect(cells[1].text()).toBe('0.43');
  });

  it('弱相关不转白字, 强相关才转(保证数字可读)', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation, assets });
    const firstRow = wrapper.findAll('tbody tr')[0];
    const cells = firstRow.findAll('.value');
    expect(cells[1].classes()).not.toContain('is-strong'); // 0.43 < 0.55
  });

  it('无相关性数据时不渲染表格', () => {
    const wrapper = mountWith(CorrelationMatrix, { correlation: null, assets: [] });
    expect(wrapper.find('table').exists()).toBe(false);
  });
});
