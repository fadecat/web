// CbMarket 页面状态与交互测试(T4):
// 覆盖首次加载失败重试、空数据、最新字段缺失、刷新失败保留、按钮切换不请求网络、
// 切窗口重置读数、最新摘要不随历史选择改变、API 调用次数。
// 依赖 getCbIndexDaily(API) 用 mock; normalizeCbMarketRows / selectCbMarketWindow 用真实实现。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { shallowMount, flushPromises } from '@vue/test-utils';

// API 模块 mock(仅 getCbIndexDaily 命中网络)
const getCbIndexDailyMock = vi.fn();
vi.mock('../../src/api/index.js', () => ({
  getCbIndexDaily: (...a) => getCbIndexDailyMock(...a),
  default: {},
}));

// Vue Router 桩(页面用到 router-link 与 useRouter)
vi.mock('vue-router', () => ({
  useRoute: vi.fn(() => ({ params: {}, query: {} })),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn() })),
}));

import CbMarket from '../../src/pages/CbMarket.vue';

// 生成跨越约 4 年的月度记录(升序), 便于 1/3/5年/全部窗口均为真实子集
function makeRows(count = 50, startYear = 2022) {
  const rows = [];
  let y = startYear;
  let mo = 1;
  for (let i = 0; i < count; i++) {
    const d = new Date(Date.UTC(y, mo - 1, 1)).toISOString().slice(0, 10);
    rows.push({ trade_date: d, median_price: 120 + i, avg_ytm: -5 + i * 0.1, count: 380 + i });
    mo += 1;
    if (mo > 12) {
      mo = 1;
      y += 1;
    }
  }
  return rows;
}

async function mountPage() {
  return shallowMount(CbMarket, {
    global: { stubs: { 'router-link': { template: '<a><slot /></a>' } } },
  });
}

describe('CbMarket 页面状态闭环', () => {
  beforeEach(() => {
    getCbIndexDailyMock.mockReset();
  });

  it('默认窗口为 3 年, 首次加载只请求一次 API', async () => {
    getCbIndexDailyMock.mockResolvedValue(makeRows());
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.range).toBe('3y');
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(1);
    expect(wrapper.vm.allRows.length).toBe(50);
  });

  it('首次加载失败: 明确失败提示 + 重试, 不显示 0 值图', async () => {
    getCbIndexDailyMock.mockRejectedValueOnce(new Error('boom'));
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.errorMsg).toContain('boom');
    expect(wrapper.find('.retry-btn').exists()).toBe(true);
    expect(wrapper.find('.empty-state').exists()).toBe(false);
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(1);

    // 重试成功
    getCbIndexDailyMock.mockResolvedValue([
      { trade_date: '2026-09-09', median_price: 132.5, avg_ytm: -8.25, count: 400 },
    ]);
    await wrapper.find('.retry-btn').trigger('click');
    await flushPromises();
    expect(wrapper.vm.errorMsg).toBe('');
    expect(wrapper.vm.allRows.length).toBe(1);
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(2);
  });

  it('返回空数组: 显示“暂无转债市场历史数据”', async () => {
    getCbIndexDailyMock.mockResolvedValue([]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.allRows.length).toBe(0);
    expect(wrapper.find('.empty-state').exists()).toBe(true);
    expect(wrapper.find('.empty-state').text()).toContain('暂无转债市场历史数据');
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(1);
  });

  it('最新字段缺失: 摘要显示 —, 不从其他日期补值', async () => {
    getCbIndexDailyMock.mockResolvedValue([
      { trade_date: '2026-09-09', median_price: null, avg_ytm: null, count: null },
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.latest.median_price).toBeNull();
    expect(wrapper.vm.latest.avg_ytm).toBeNull();
    expect(wrapper.vm.latest.count).toBeNull();
    const sumTexts = wrapper.findAll('.sum-value').map((e) => e.text());
    expect(sumTexts).toEqual(['—', '—', '—']);
  });

  it('摘要标注每项指标最后有效日期', async () => {
    getCbIndexDailyMock.mockResolvedValue([
      { trade_date: '2026-09-08', median_price: 130, avg_ytm: -8, count: 399 },
      { trade_date: '2026-09-09', median_price: null, avg_ytm: -7, count: 400 },
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    const dates = wrapper.findAll('.sum-date').map((e) => e.text());
    expect(dates[0]).toContain('2026-09-08');
    expect(dates[1]).toContain('2026-09-09');
  });

  it('刷新失败且已有数据: 保留上次成功结果与截至日期, 不伪装新数据', async () => {
    getCbIndexDailyMock.mockResolvedValueOnce([
      { trade_date: '2026-09-09', median_price: 132.5, avg_ytm: -8.25, count: 400 },
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    const rowsBefore = wrapper.vm.allRows.length;
    const asOfBefore = wrapper.vm.asOfDate;

    getCbIndexDailyMock.mockRejectedValueOnce(new Error('refresh fail'));
    await wrapper.find('.refresh-btn').trigger('click');
    await flushPromises();
    expect(wrapper.vm.refreshError).toBe(true);
    expect(wrapper.vm.allRows.length).toBe(rowsBefore); // 保留
    expect(wrapper.vm.asOfDate).toBe(asOfBefore);
    expect(wrapper.find('.banner.warn').exists()).toBe(true);
  });

  it('刷新请求进行中禁用刷新按钮并保持 loading 状态', async () => {
    let resolveRefresh;
    getCbIndexDailyMock.mockResolvedValueOnce([
      { trade_date: '2026-09-09', median_price: 132.5, avg_ytm: -8.25, count: 400 },
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    getCbIndexDailyMock.mockReturnValueOnce(new Promise((resolve) => { resolveRefresh = resolve; }));
    await wrapper.find('.refresh-btn').trigger('click');
    expect(wrapper.vm.loading).toBe(true);
    expect(wrapper.find('.refresh-btn').attributes('disabled')).toBeDefined();
    resolveRefresh([
      { trade_date: '2026-09-10', median_price: 133, avg_ytm: -8, count: 401 },
    ]);
    await flushPromises();
    expect(wrapper.vm.loading).toBe(false);
  });

  it('时间按钮切换不触发网络请求(本地过滤)', async () => {
    getCbIndexDailyMock.mockResolvedValue(makeRows());
    const wrapper = await mountPage();
    await flushPromises();
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(1);
    wrapper.vm.setRange('1y');
    await flushPromises();
    wrapper.vm.setRange('5y');
    await flushPromises();
    wrapper.vm.setRange('all');
    await flushPromises();
    expect(getCbIndexDailyMock).toHaveBeenCalledTimes(1); // 仍只 1 次
  });

  it('切换窗口重置读数到窗口末条(非历史游标位置)', async () => {
    getCbIndexDailyMock.mockResolvedValue(makeRows());
    const wrapper = await mountPage();
    await flushPromises();
    // 用户先把游标移到窗口第一条
    const firstDate = wrapper.vm.windowRows[0].trade_date;
    wrapper.vm.selectedDate = firstDate;
    await flushPromises();
    expect(wrapper.vm.selectedDate).toBe(firstDate);
    // 切换窗口
    wrapper.vm.setRange('5y');
    await flushPromises();
    const lastDate = wrapper.vm.windowRows[wrapper.vm.windowRows.length - 1].trade_date;
    expect(wrapper.vm.selectedDate).toBe(lastDate); // 重置到窗口末条
    expect(wrapper.vm.selectedDate).not.toBe(firstDate);
  });

  it('最新摘要不随历史游标/窗口选择改变', async () => {
    getCbIndexDailyMock.mockResolvedValue(makeRows());
    const wrapper = await mountPage();
    await flushPromises();
    const latestMedian = wrapper.vm.fmtPrice(wrapper.vm.latest.median_price);

    // 把游标改到最早一条 + 切到全部窗口
    wrapper.vm.onChartSelect(wrapper.vm.windowRows[0].trade_date);
    await flushPromises();
    wrapper.vm.setRange('all');
    await flushPromises();

    // 摘要第一项始终等于全量最新 median_price
    const sumTexts = wrapper.findAll('.sum-value').map((e) => e.text());
    expect(sumTexts[0]).toBe(latestMedian);
    expect(wrapper.vm.latest.trade_date).toBe(
      wrapper.vm.allRows[wrapper.vm.allRows.length - 1].trade_date,
    );
  });

  it('选择日期后读数行显示该日两个值', async () => {
    getCbIndexDailyMock.mockResolvedValue(makeRows());
    const wrapper = await mountPage();
    await flushPromises();
    const target = wrapper.vm.windowRows[10];
    wrapper.vm.onChartSelect(target.trade_date);
    await flushPromises();
    const readLine = wrapper.find('.read-text').text();
    expect(readLine).toContain(target.trade_date);
    expect(readLine).toContain('价格中位数');
    expect(readLine).toContain('平均到期收益率');
  });
});
