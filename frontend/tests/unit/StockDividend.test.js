// StockDividend 页面状态与交互测试(P2):
// 覆盖首次加载只请求一次、默认排序、空数据、失败重试、刷新失败保留、
// 筛选表单变化零网络请求(本地过滤)、筛选结果正确、页码重置、API 调用次数。
// 依赖 getStockDividendSnapshot(API) 用 mock; stockDividend.mjs 纯函数用真实实现。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';

// API 模块 mock(仅 getStockDividendSnapshot 命中网络)
const getStockDividendSnapshotMock = vi.fn();
vi.mock('../../src/api/index.js', () => ({
  getStockDividendSnapshot: (...a) => getStockDividendSnapshotMock(...a),
  default: {},
}));

// Vue Router 桩(页面用到 router-link)
vi.mock('vue-router', () => ({
  useRoute: vi.fn(() => ({ params: {}, query: {} })),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn() })),
}));

import StockDividend from '../../src/pages/StockDividend.vue';

// 造行工厂: 与后端 47 键契约同形(测试只填关注字段)
function makeRow(over = {}) {
  return {
    trade_date: '2026-09-11',
    stock_id: '600001',
    stock_nm: '示例股份',
    sw_cd: '801160',
    industry_nm: '铁路运输',
    industry_nm2: '交通运输-铁路公路-铁路运输',
    province: '北京',
    price: 10.5,
    increase_rt: 1.23,
    volume: 123456,
    total_value: 300,
    float_value: 280,
    pe: 8.5,
    pb: 0.9,
    pb_flag: '',
    margin_flg: '',
    roe: 12,
    roe_average: 11,
    pe_temperature: 30,
    pb_temperature: 45,
    dividend_rate: 5,
    dividend_rate2: 4.8,
    aft_dividend: 4.5,
    revenue_average: 5,
    profit_average: 4,
    cashflow_average: 3,
    eps_growth_ttm: 2,
    int_debt_rate: 25,
    debt_rate: 40,
    audit_info: null,
    ...over,
  };
}

async function mountPage() {
  return mount(StockDividend, {
    global: {
      plugins: [ElementPlus],
      stubs: { 'router-link': { template: '<a><slot /></a>' } },
    },
  });
}

describe('StockDividend 页面状态闭环', () => {
  beforeEach(() => {
    getStockDividendSnapshotMock.mockReset();
  });

  it('首次加载只请求一次 API, 数据落 allRows, 页码回第一页', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', dividend_rate: 5 }),
      makeRow({ stock_id: '000002', dividend_rate: 6 }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(1);
    expect(wrapper.vm.allRows.length).toBe(2);
    expect(wrapper.vm.page).toBe(1);
    expect(wrapper.vm.size).toBe(50);
    expect(wrapper.vm.asOfDate).toBe('2026-09-11');
  });

  it('默认排序: 股息率降序 + null 沉底(输入乱序)', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: 'S3', dividend_rate: 3.2 }),
      makeRow({ stock_id: 'S4', dividend_rate: null }),
      makeRow({ stock_id: 'S1', dividend_rate: 9.1 }),
      makeRow({ stock_id: 'S2', dividend_rate: 8.5 }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.sort).toEqual({ prop: 'dividend_rate', order: 'descending' });
    expect(wrapper.vm.sortedRows.map((r) => r.stock_id)).toEqual(['S1', 'S2', 'S3', 'S4']);
  });

  it('返回空数组: 显示空态, 不显示表格内容', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.allRows.length).toBe(0);
    expect(wrapper.find('.empty-state').exists()).toBe(true);
    expect(wrapper.find('.empty-state').text()).toContain('暂无高股息快照数据');
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(1);
  });

  it('首次加载失败: 失败提示 + 重试, 重试成功后恢复', async () => {
    getStockDividendSnapshotMock.mockRejectedValueOnce(new Error('boom'));
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.errorMsg).toContain('boom');
    expect(wrapper.find('.retry-btn').exists()).toBe(true);
    expect(wrapper.find('.empty-state').exists()).toBe(false);

    getStockDividendSnapshotMock.mockResolvedValueOnce([
      makeRow({ stock_id: '600001', dividend_rate: 5 }),
    ]);
    await wrapper.find('.retry-btn').trigger('click');
    await flushPromises();
    expect(wrapper.vm.errorMsg).toBe('');
    expect(wrapper.vm.allRows.length).toBe(1);
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(2);
  });

  it('刷新失败且已有数据: 保留上次成功结果与截至日期', async () => {
    getStockDividendSnapshotMock.mockResolvedValueOnce([
      makeRow({ stock_id: '600001', dividend_rate: 5 }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    const rowsBefore = wrapper.vm.allRows.length;
    const asOfBefore = wrapper.vm.asOfDate;

    getStockDividendSnapshotMock.mockRejectedValueOnce(new Error('refresh fail'));
    await wrapper.find('.refresh-btn').trigger('click');
    await flushPromises();
    expect(wrapper.vm.refreshError).toBe(true);
    expect(wrapper.vm.allRows.length).toBe(rowsBefore);
    expect(wrapper.vm.asOfDate).toBe(asOfBefore);
    expect(wrapper.find('.banner.warn').exists()).toBe(true);
  });

  it('筛选表单变化不发网络请求(本地过滤), 结果正确', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', pe: 8.5, province: '北京' }),
      makeRow({ stock_id: '000002', pe: 12, province: '上海' }),
      makeRow({ stock_id: '600003', pe: null, province: '北京' }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.filteredRows.length).toBe(3); // 空表单全过(含 null PE 行)

    wrapper.vm.form.peMax = 10; // 启用 PE ≤ 10
    await flushPromises();
    expect(wrapper.vm.filteredRows.map((r) => r.stock_id)).toEqual(['600001']); // 12 超限, null 被滤
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(1); // 零新增请求

    wrapper.vm.form.province = '北京';
    await flushPromises();
    expect(wrapper.vm.filteredRows.map((r) => r.stock_id)).toEqual(['600001']);
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(1);

    wrapper.vm.resetForm();
    await flushPromises();
    expect(wrapper.vm.filteredRows.length).toBe(3);
    expect(getStockDividendSnapshotMock).toHaveBeenCalledTimes(1);
  });

  it('筛选变化重置页码到 1', async () => {
    const rows = [];
    for (let i = 0; i < 60; i += 1) {
      rows.push(makeRow({ stock_id: String(600000 + i), dividend_rate: (i % 10) + 1 }));
    }
    getStockDividendSnapshotMock.mockResolvedValue(rows);
    const wrapper = await mountPage();
    await flushPromises();

    wrapper.vm.page = 2;
    await flushPromises();
    expect(wrapper.vm.page).toBe(2);

    wrapper.vm.form.markets = ['sh'];
    await flushPromises();
    expect(wrapper.vm.page).toBe(1); // form 变化触发重置
  });

  it('分页尺寸变化也重置页码', async () => {
    const rows = [];
    for (let i = 0; i < 60; i += 1) {
      rows.push(makeRow({ stock_id: String(600000 + i), dividend_rate: (i % 10) + 1 }));
    }
    getStockDividendSnapshotMock.mockResolvedValue(rows);
    const wrapper = await mountPage();
    await flushPromises();

    wrapper.vm.page = 2;
    await flushPromises();
    wrapper.vm.size = 20;
    await flushPromises();
    expect(wrapper.vm.page).toBe(1);
    expect(wrapper.vm.pagedRows.length).toBe(20);
  });

  it('单元格分发: 温度/涨跌/百分比/空值与 pb 灰色口径', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({
        pe_temperature: 80, increase_rt: 2.5, dividend_rate: null, pb: 1.2, pb_flag: 'Y',
      }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    const row = wrapper.vm.allRows[0];
    const byField = (f) => wrapper.vm.dynamicColumns.find((c) => c.field === f);

    expect(wrapper.vm.cellText(row, byField('pe_temperature'))).toBe('80.00');
    expect(wrapper.vm.cellClass(row, byField('pe_temperature'))).toBe('t-red');
    expect(wrapper.vm.cellText(row, byField('increase_rt'))).toBe('+2.50%');
    expect(wrapper.vm.cellClass(row, byField('increase_rt'))).toBe('up');
    expect(wrapper.vm.cellText(row, byField('dividend_rate'))).toBe('—'); // null
    expect(wrapper.vm.cellText(row, byField('volume'))).toBe('123,456');
    // pb_flag='Y' 走模板灰色分支(值本身照常格式化)
    expect(wrapper.vm.cellText(row, byField('pb'))).toBe('1.20');
  });

  it('代码外链指向集思录个股页', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([makeRow({ stock_id: '601398' })]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.jisiluStockUrl('601398')).toBe('https://www.jisilu.cn/data/stock/601398');
  });

  it('渲染冒烟: 表头无会员占位列, 名称列 R 徽标/审计警示, 黄底强调列', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', stock_nm: '徽标股', margin_flg: 'R', audit_info: '保留意见' }),
      makeRow({ stock_id: '000002', stock_nm: '普通股', margin_flg: '', audit_info: null }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    const text = wrapper.text();
    // 会员占位列不复刻(用户明确要求)
    expect(text).not.toContain('波动率');
    expect(text).not.toContain('质押');
    // 名称列徽标
    expect(wrapper.find('.badge-r').exists()).toBe(true);
    expect(wrapper.find('.audit-warn').exists()).toBe(true);
    // 5年平均股息率黄底强调列
    expect(wrapper.find('td.col-highlight').exists()).toBe(true);
    // 代码列外链(两行各有指向集思录个股页的链接)
    const hrefs = wrapper.findAll('.code-link').map((a) => a.attributes('href'));
    expect(hrefs).toEqual([
      'https://www.jisilu.cn/data/stock/000002',
      'https://www.jisilu.cn/data/stock/600001',
    ]);
  });
});
