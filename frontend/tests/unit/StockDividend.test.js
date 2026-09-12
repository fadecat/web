// StockDividend 页面状态与交互测试(P2):
// 覆盖首次加载只请求一次、默认排序、空数据、失败重试、刷新失败保留、
// 筛选表单变化零网络请求(本地过滤)、筛选结果正确、页码重置、API 调用次数、
// 服务端预设闭环(默认套用/仅国资/重置回已保存值/保存全量 POST/切换预设)。
// 依赖 getStockDividendSnapshot/getDividendPresets/saveDividendPresets(API) 用 mock;
// stockDividend.mjs 纯函数用真实实现。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import ElementPlus from 'element-plus';

// API 模块 mock(仅快照/预设 API 命中网络)
const getStockDividendSnapshotMock = vi.fn();
const getDividendPresetsMock = vi.fn();
const saveDividendPresetsMock = vi.fn();
vi.mock('../../src/api/index.js', () => ({
  getStockDividendSnapshot: (...a) => getStockDividendSnapshotMock(...a),
  getDividendPresets: (...a) => getDividendPresetsMock(...a),
  saveDividendPresets: (...a) => saveDividendPresetsMock(...a),
  default: {},
}));

// Vue Router 桩(页面用到 router-link)
vi.mock('vue-router', () => ({
  useRoute: vi.fn(() => ({ params: {}, query: {} })),
  useRouter: vi.fn(() => ({ push: vi.fn(), replace: vi.fn() })),
}));

import StockDividend from '../../src/pages/StockDividend.vue';

// 造行工厂: 与后端 48 键契约同形(测试只填关注字段)
function makeRow(over = {}) {
  return {
    trade_date: '2026-09-11',
    stock_id: '600001',
    stock_nm: '示例股份',
    enterprise_nature: '',
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

// 空预设(表单全空): 让旧用例保持「空表单全过」语义
const EMPTY_PRESETS = {
  version: 1,
  active_id: 'p1',
  presets: [{ id: 'p1', name: '不限', form: {} }],
};

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
    // 预设默认给空表单; 保存 mock 像后端一样回显入参(规范化由后端负责, 此处原样)
    getDividendPresetsMock.mockReset().mockResolvedValue(EMPTY_PRESETS);
    saveDividendPresetsMock.mockReset().mockImplementation(async (cfg) => cfg);
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

    wrapper.vm.form.provinces = ['北京'];
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

  it('进入页面套用默认预设: 表单生效且 soeOnly 过滤无国资标注行, 初始非脏', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', enterprise_nature: '中央国有企业', pe: 8 }),
      makeRow({ stock_id: '600002', pe: 8 }), // 无央国企标注
      makeRow({ stock_id: '600003', enterprise_nature: '地方国有企业', pe: 20 }), // PE 超限
    ]);
    getDividendPresetsMock.mockResolvedValue({
      version: 1,
      active_id: 'p1',
      presets: [{ id: 'p1', name: '邮件口径', form: { peMax: 15, soeOnly: true } }],
    });
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.editingId).toBe('p1');
    expect(wrapper.vm.form.peMax).toBe(15);
    expect(wrapper.vm.form.soeOnly).toBe(true);
    expect(wrapper.vm.dirty).toBe(false);
    expect(wrapper.vm.filteredRows.map((r) => r.stock_id)).toEqual(['600001']);
  });

  it('修改表单变脏, 重置回到当前预设已保存值(而非清空)', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([makeRow()]);
    getDividendPresetsMock.mockResolvedValue({
      version: 1,
      active_id: 'p1',
      presets: [{ id: 'p1', name: '邮件口径', form: { peMax: 15 } }],
    });
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.dirty).toBe(false);

    wrapper.vm.form.peMax = 30;
    wrapper.vm.form.soeOnly = true;
    await flushPromises();
    expect(wrapper.vm.dirty).toBe(true);

    wrapper.vm.resetForm();
    await flushPromises();
    expect(wrapper.vm.form.peMax).toBe(15);
    expect(wrapper.vm.form.soeOnly).toBe(false);
    expect(wrapper.vm.dirty).toBe(false);
  });

  it('保存预设: 全量 POST(version/active_id/当前编辑预设携带新表单), 保存后归为非脏', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([makeRow()]);
    const wrapper = await mountPage();
    await flushPromises();

    wrapper.vm.form.peMax = 12;
    await flushPromises();
    expect(wrapper.vm.dirty).toBe(true);

    await wrapper.vm.savePreset();
    await flushPromises();
    expect(saveDividendPresetsMock).toHaveBeenCalledTimes(1);
    expect(saveDividendPresetsMock.mock.calls[0][0]).toEqual({
      version: 1,
      active_id: 'p1',
      presets: [{ id: 'p1', name: '不限', form: wrapper.vm.form }],
    });
    expect(wrapper.vm.dirty).toBe(false);
  });

  it('切换预设(非脏): 表单切到新预设已保存值', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([makeRow()]);
    getDividendPresetsMock.mockResolvedValue({
      version: 1,
      active_id: 'p1',
      presets: [
        { id: 'p1', name: '不限', form: {} },
        { id: 'p2', name: '宽口径', form: { peMax: 99, provinces: ['北京'] } },
      ],
    });
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.editingId).toBe('p1');

    await wrapper.vm.onSwitchPreset('p2');
    await flushPromises();
    expect(wrapper.vm.editingId).toBe('p2');
    expect(wrapper.vm.form.peMax).toBe(99);
    expect(wrapper.vm.form.provinces).toEqual(['北京']);
    expect(wrapper.vm.dirty).toBe(false);
  });

  it('行业全选/清空: 写入全部一级码, 再点变清空(一级前缀即覆盖子树)', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', sw_cd: '801160', industry_nm2: '交通运输-铁路公路-铁路运输' }),
      makeRow({ stock_id: '600002', sw_cd: '110000', industry_nm2: '能源-煤炭-动煤' }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.industryRootCodes).toEqual(['11', '80']);
    expect(wrapper.vm.industriesAllSelected).toBe(false);

    wrapper.vm.toggleAllIndustries();
    await flushPromises();
    expect([...wrapper.vm.form.industries].sort()).toEqual(['11', '80']);
    expect(wrapper.vm.industriesAllSelected).toBe(true);

    wrapper.vm.toggleAllIndustries();
    await flushPromises();
    expect(wrapper.vm.form.industries).toEqual([]);
    expect(wrapper.vm.industriesAllSelected).toBe(false);

    // 排除行业独立全选
    wrapper.vm.toggleAllExcludeIndustries();
    await flushPromises();
    expect([...wrapper.vm.form.excludeIndustries].sort()).toEqual(['11', '80']);
    expect(wrapper.vm.excludeIndustriesAllSelected).toBe(true);
    expect(wrapper.vm.form.industries).toEqual([]); // 不串扰
  });

  it('地域一键沿海组合: 点选江苏+浙江+广东+福建, 再点清空', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([
      makeRow({ stock_id: '600001', province: '江苏' }),
      makeRow({ stock_id: '600002', province: '浙江' }),
      makeRow({ stock_id: '600003', province: '广东' }),
      makeRow({ stock_id: '600004', province: '福建' }),
      makeRow({ stock_id: '600005', province: '北京' }),
    ]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.provincesQuick).toBe(false);

    wrapper.vm.toggleQuickProvinces();
    await flushPromises();
    expect(wrapper.vm.form.provinces).toEqual(['江苏', '浙江', '广东', '福建']);
    expect(wrapper.vm.provincesQuick).toBe(true);
    expect(wrapper.vm.filteredRows.map((r) => r.stock_id)).toEqual(['600001', '600002', '600003', '600004']);

    wrapper.vm.toggleQuickProvinces();
    await flushPromises();
    expect(wrapper.vm.form.provinces).toEqual([]);
    expect(wrapper.vm.filteredRows.length).toBe(5);
  });

  it('高级筛选计数: 收起状态下统计高级区激活条件数', async () => {
    getStockDividendSnapshotMock.mockResolvedValue([makeRow()]);
    const wrapper = await mountPage();
    await flushPromises();
    expect(wrapper.vm.advancedOpen).toBe(false);
    expect(wrapper.vm.advancedCount).toBe(0);

    wrapper.vm.form.pbMax = 1.5; // 高级区阈值 +1
    wrapper.vm.form.markets = ['sh']; // 高级区市场 +1
    wrapper.vm.form.peMax = 15; // 主区条件不计入
    await flushPromises();
    expect(wrapper.vm.advancedCount).toBe(2);
  });
});
