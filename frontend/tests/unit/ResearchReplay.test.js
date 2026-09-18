import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import ElementPlus from 'element-plus';
import { reactive } from 'vue';

vi.mock('../../src/api/research.js', () => ({
  getResearchSecurities: vi.fn(),
  getResearchDataHealth: vi.fn(),
  getResearchReplays: vi.fn(),
  createResearchReplay: vi.fn(),
  getReplaySummary: vi.fn(),
  getReplayDays: vi.fn(),
  getReplayComparison: vi.fn(),
}));
vi.mock('vue-router', () => ({
  useRoute: vi.fn(),
  useRouter: vi.fn(),
}));

import { getReplayComparison, getReplayDays, getReplaySummary, getResearchSecurities } from '../../src/api/research';
import { useRoute, useRouter } from 'vue-router';
import ResearchReplay from '../../src/pages/ResearchReplay.vue';

const SEGMENT = (numerator, denominator) => ({
  day_count: numerator + denominator,
  buy: ['P50', 'P70', 'P85'].map((tier) => ({ tier, numerator, denominator, rate: numerator / denominator })),
  sell: ['P50', 'P70', 'P85'].map((tier) => ({ tier, numerator, denominator, rate: numerator / denominator })),
  day_categories: { BUY_ONLY: numerator, NO_HIT: denominator },
});

describe('ResearchReplay signal-replay page', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    useRoute.mockReturnValue(reactive({
      query: { symbol: '600900.SH', start_date: '2023-09-18', end_date: '2026-09-18' },
    }));
    useRouter.mockReturnValue({ replace: vi.fn(), push: vi.fn() });
    getResearchSecurities.mockResolvedValue([
      { symbol: '600900.SH', name: '长江电力', security_type: 'STOCK', data_ready: true },
    ]);
    getReplayComparison.mockResolvedValue([
      {
        run_id: 1, param_lambda: 0, quantile_window: 60,
        train: SEGMENT(50, 100), validation: SEGMENT(20, 60),
        sample_coverage: { train_days: 150, validation_days: 80 },
      },
    ]);
    getReplaySummary.mockResolvedValue({
      run_id: 1, symbol: '600900.SH', param_lambda: 0, quantile_window: 60,
      train_end_date: '2025-03-01', retrospective: true,
      overall: SEGMENT(70, 160), train: SEGMENT(50, 100), validation: SEGMENT(20, 60),
    });
    getReplayDays.mockResolvedValue([
      {
        plan_date: '2026-09-10', eval_date: '2026-09-11', status: 'ACTIVE',
        day_category: 'BUY_ONLY',
        reason_codes: [],
        buy_levels_raw: [10.123456, 10.0, 9.9], sell_levels_raw: [10.4, 10.5, 10.6],
        next_open: 10.2, next_high: 10.3, next_low: 10.05, next_close: 10.25,
        buy_hits: [true, false, false], sell_hits: [false, false, false],
        buy_open_invalid: false, sell_open_invalid: false,
        evidence: { open_invalidation_lower: 9.5, open_invalidation_upper: 10.9 },
        z: 0.12, atr14: 0.08, ema20: 10.2, scale: 1.0,
      },
    ]);
    if (!window.ResizeObserver) window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  });

  it('renders comparison grid, summary cards and model-price disclaimers', async () => {
    const renderErrors = [];
    const wrapper = mount(ResearchReplay, {
      global: {
        plugins: [ElementPlus],
        config: { errorHandler: (error) => renderErrors.push(error) },
      },
    });
    await flushPromises();
    const text = wrapper.text();
    // 页面标题与措辞约束
    expect(text).toContain('信号回放');
    expect(text).toContain('选参只看训练段');
    expect(text).toContain('模型价，非可下单报价');
    // 禁用词不得出现
    expect(text).not.toContain('回测收益率');
    // 比较网格: 训练段比例(50/100=50.0%)与天数
    expect(text).toContain('50.0%');
    expect(text).toContain('150');
    // 汇总卡: 分子/分母明示
    expect(text).toContain('50 / 100');
    // 逐日明细: 类别中文 + 价位
    expect(text).toContain('买侧触达');
    // 回顾性声明
    expect(text).toContain('回顾性局限');
    expect(renderErrors).toEqual([]);
    wrapper.unmount();
  });

  it('renders empty state when comparison is empty and shows create hint', async () => {
    getReplayComparison.mockResolvedValue([]);
    const wrapper = mount(ResearchReplay, {
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();
    expect(wrapper.text()).toContain('暂无回放结果');
    expect(wrapper.text()).toContain('生成回放');
    wrapper.unmount();
  });
});
