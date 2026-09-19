// 标的库页面单测: 全部 mock API 层(后端并行开发中, 不许依赖真实服务)。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import ElementPlus from 'element-plus';

vi.mock('../../src/api/portfolio.js', () => ({
  probeAsset: vi.fn(),
  createAsset: vi.fn(),
  listAssets: vi.fn(),
  refreshAsset: vi.fn(),
}));

import { listAssets, refreshAsset } from '../../src/api/portfolio.js';
import AddAssetDialog from '../../src/components/portfolio/AddAssetDialog.vue';
import PortfolioAssets from '../../src/pages/PortfolioAssets.vue';

const asset = (over = {}) => ({
  id: 1, symbol: '600900.SH', name: '长江电力', security_type: 'STOCK', exchange: 'SSE',
  source: 'tencent', enabled: true, price_basis: 'HFQ', row_count: 1200,
  first_date: '2021-01-04', last_date: '2026-09-18', last_sync_at: '2026-09-18T16:00:00',
  last_sync_status: 'success', last_sync_error: null, last_sync_rows: 1200,
  ...over,
});

const mountPage = () => mount(PortfolioAssets, { global: { plugins: [ElementPlus] } });

describe('PortfolioAssets 标的库', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listAssets.mockResolvedValue([]);
    refreshAsset.mockResolvedValue({});
  });

  it('⑥ 组合起点取所有标的 first_date 中最晚的那个', async () => {
    listAssets.mockResolvedValue([
      asset({ id: 1, symbol: '600900.SH', name: '长江电力', first_date: '2021-01-04' }),
      asset({ id: 2, symbol: '510300.SH', name: '沪深300ETF', security_type: 'ETF', first_date: '2024-03-05' }),
    ]);
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find('.assets-start strong').text()).toBe('2024-03-05');
    expect(wrapper.text()).toContain('2024-03-05');
    // 更早的 2021-01-04 只出现在该行的数据区间里, 不能成为组合起点
    expect(wrapper.text()).toContain('2021-01-04');
    wrapper.unmount();
  });

  it('有标的缺 first_date 时起点显示「待抓取」', async () => {
    listAssets.mockResolvedValue([
      asset({ id: 1, first_date: '2021-01-04' }),
      asset({ id: 2, symbol: '510300.SH', row_count: 0, first_date: null, last_date: null, last_sync_status: 'failed', last_sync_error: '腾讯行情返回空' }),
    ]);
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find('.assets-start strong').text()).toBe('待抓取');
    expect(wrapper.text()).toContain('1 个标的尚未抓取');
    wrapper.unmount();
  });

  it('同步状态渲染: 就绪=绿 / 同步中 / 失败=红, 失败行可重试', async () => {
    listAssets.mockResolvedValue([
      asset({ id: 1, last_sync_status: 'success' }),
      asset({ id: 2, symbol: '510300.SH', last_sync_status: 'running', row_count: 0, first_date: null, last_date: null }),
      asset({ id: 3, symbol: '512880.SH', last_sync_status: 'failed', last_sync_error: '腾讯行情返回空', row_count: 0, first_date: null, last_date: null }),
    ]);
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.find('.status-success').text()).toBe('就绪');
    expect(wrapper.find('.status-running').text()).toContain('同步中');
    expect(wrapper.find('.status-failed').text()).toBe('失败');
    // 失败行的重试: 只作用于该行, 不牵连已就绪的行
    const retryButtons = wrapper.findAll('button').filter((btn) => btn.text().trim() === '重试');
    expect(retryButtons.length).toBe(1);
    await retryButtons[0].trigger('click');
    await flushPromises();
    expect(refreshAsset).toHaveBeenCalledWith(3);
    expect(wrapper.find('.status-success').exists()).toBe(true);
    wrapper.unmount();
  });

  it('新增后起点被推后时渲染警示条', async () => {
    listAssets.mockResolvedValue([asset({ id: 1, first_date: '2021-01-04' })]);
    const wrapper = mountPage();
    await flushPromises();

    const dialog = wrapper.findComponent(AddAssetDialog);
    expect(dialog.exists()).toBe(true);
    dialog.vm.$emit('start-change', {
      asset: asset({ id: 2, symbol: '510300.SH', first_date: '2024-03-05' }),
      firstDate: '2024-03-05',
      oldStart: '2021-01-04',
      newStart: '2024-03-05',
      shiftYears: 3.2,
      title: '⚠ 新的组合起点：2024-03-05（受 沪深300ETF 510300.SH 制约，其数据始于 2024-03-05）',
      detail: '原起点 2021-01-04 → 前移 3.2 年',
    });
    await flushPromises();

    expect(wrapper.find('.assets-notice').exists()).toBe(true);
    expect(wrapper.text()).toContain('新的组合起点：2024-03-05');
    expect(wrapper.text()).toContain('前移 3.2 年');
    wrapper.unmount();
  });

  it('接口失败时给出错误态与重试入口', async () => {
    listAssets.mockRejectedValue(new Error('boom'));
    const wrapper = mountPage();
    await flushPromises();

    expect(wrapper.text()).toContain('标的库接口暂时不可用');
    wrapper.unmount();
  });
});
