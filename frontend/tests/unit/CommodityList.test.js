import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import ElementPlus from 'element-plus';
import { reactive } from 'vue';

vi.mock('../../src/api/commodity.js', () => ({
  getCommodityOverview: vi.fn(),
  getCommodities: vi.fn(),
}));
vi.mock('vue-router', () => ({
  useRoute: vi.fn(),
  useRouter: vi.fn(),
}));

import { getCommodities, getCommodityOverview } from '../../src/api/commodity.js';
import { useRoute, useRouter } from 'vue-router';
import CommodityList from '../../src/pages/CommodityList.vue';

const ROW = {
  code: 'RB0', name: '螺纹钢', category: '黑色', market: '国内', latest_price: 3256,
  data_date: '2026-09-15', current_status: 'stale', status_label: '数据滞后',
  signal: 'stale', sync_status: 'stale',
  windows: {
    d21: { percentile: 95, sample_count: 21, signal: 'high' },
    d63: { percentile: null, sample_count: null, signal: null },
    y1: { percentile: 50, sample_count: 252, signal: 'neutral' },
    y3: { percentile: null, sample_count: null, signal: null },
    y5: { percentile: null, sample_count: null, signal: null },
    y10: { percentile: null, sample_count: null, signal: null },
  },
};

describe('CommodityList successful API render', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useRoute.mockReturnValue(reactive({ query: {} }));
    useRouter.mockReturnValue({ replace: vi.fn(), push: vi.fn() });
    getCommodityOverview.mockResolvedValue({
      data_date: '2026-09-15', instrument_total: 1, fresh_count: 0,
      high_count: 0, low_count: 0, stale_count: 1, failed_count: 0,
      last_run_at: '2026-09-15T16:00:00', last_run_status: 'success',
    });
    getCommodities.mockResolvedValue([ROW]);
    if (!window.ResizeObserver) window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  });

  it('renders a successful row and executes metricTone in the template', async () => {
    const renderErrors = [];
    const wrapper = mount(CommodityList, {
      global: {
        plugins: [ElementPlus],
        config: { errorHandler: (error) => renderErrors.push(error) },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain('螺纹钢');
    expect(wrapper.text()).toContain('95%');
    expect(wrapper.text()).toContain('3256');
    expect(wrapper.find('.tone-muted').exists()).toBe(true);
    expect(renderErrors).toEqual([]);
    wrapper.unmount();
  });
});
