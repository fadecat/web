// 组合列表页(L1)组件测试: 全部 mock API 与路由, 不依赖真实服务。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';

const push = vi.fn();
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }));

const confirmMock = vi.fn();
vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus');
  return {
    ...actual,
    ElMessage: { success: vi.fn(), error: vi.fn() },
    ElMessageBox: { confirm: (...args) => confirmMock(...args) },
  };
});

vi.mock('../../src/api/portfolio.js', () => ({
  listPortfolios: vi.fn(),
  createPortfolio: vi.fn(),
  deletePortfolio: vi.fn(),
  patchPortfolio: vi.fn(),
  listAssets: vi.fn(),
  refreshAsset: vi.fn(),
  probeAsset: vi.fn(),
  createAsset: vi.fn(),
  getPortfolio: vi.fn(),
}));

import ElementPlus from 'element-plus';
import PortfolioList from '../../src/pages/PortfolioList.vue';
import {
  createPortfolio, deletePortfolio, listPortfolios, patchPortfolio,
} from '../../src/api/portfolio.js';

const P1 = {
  id: 1, name: '我的组合1', status: 'active',
  created_at: '2026-09-19T09:00:00', cached_asof_date: '2026-09-18',
  cached_day_return: 1.08, cached_month_return: -0.62, cached_ytd_return: 6.67, asset_count: 4,
};
const P2 = {
  id: 2, name: '空组合', status: 'active',
  created_at: '2026-09-19T09:05:00', cached_asof_date: null,
  cached_day_return: null, cached_month_return: null, cached_ytd_return: null, asset_count: 0,
};

const mountPage = async () => {
  const wrapper = mount(PortfolioList, { global: { plugins: [ElementPlus] } });
  await flushPromises();
  await nextTick();
  return wrapper;
};

describe('PortfolioList 渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPortfolios.mockResolvedValue([P1, P2]);
  });

  it('① 渲染两张卡片, 两列网格', async () => {
    const wrapper = await mountPage();
    expect(wrapper.findAll('.portfolio-card').length).toBe(2);
  });

  it('② 卡片三行: 名字 / 成立时间+收益时间 / 三格收益', async () => {
    const wrapper = await mountPage();
    const card = wrapper.findAll('.portfolio-card')[0];
    expect(card.find('.card-name').text()).toBe('我的组合1');
    const meta = card.find('.card-meta').text();
    expect(meta).toContain('成立时间：2026-09-19');   // = 创建日
    expect(meta).toContain('收益时间：2026-09-18');   // = 缓存的数据截止日
    const labels = card.findAll('.metric-label').map((n) => n.text());
    expect(labels).toEqual(['日收益', '近一月', '今年以来']);
  });

  it('③ 涨红跌绿 + 精度统一 2 位', async () => {
    const wrapper = await mountPage();
    const values = wrapper.findAll('.portfolio-card')[0].findAll('.metric-value');
    expect(values.map((n) => n.text())).toEqual(['1.08%', '-0.62%', '6.67%']);
    expect(values[0].classes()).toContain('trend-up');
    expect(values[1].classes()).toContain('trend-down');
  });

  it('④ 空组合三格显示 — (不是 0.00%)', async () => {
    const wrapper = await mountPage();
    const values = wrapper.findAll('.portfolio-card')[1].findAll('.metric-value');
    expect(values.map((n) => n.text())).toEqual(['—', '—', '—']);
    expect(values.every((n) => n.classes().includes('trend-flat'))).toBe(true);
  });

  it('⑤ 空列表给引导文案', async () => {
    listPortfolios.mockResolvedValue([]);
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain('还没有组合');
    expect(wrapper.findAll('.portfolio-card').length).toBe(0);
  });

  it('⑥ 加载失败不静默: 显示错误而不是空列表', async () => {
    listPortfolios.mockRejectedValue({ response: { data: { detail: '后端不可用' } } });
    const wrapper = await mountPage();
    expect(wrapper.text()).toContain('后端不可用');
  });
});

describe('PortfolioList 交互', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPortfolios.mockResolvedValue([P1]);
  });

  it('⑦ 点卡片本体进详情', async () => {
    const wrapper = await mountPage();
    await wrapper.find('.portfolio-card').trigger('click');
    expect(push).toHaveBeenCalledWith({ name: 'portfolio-detail', params: { id: 1 } });
  });

  it('⑧ 「创建组合」直接创建(不弹输入框), 默认名由后端给', async () => {
    createPortfolio.mockResolvedValue({ id: 9, name: '我的组合2' });
    const wrapper = await mountPage();
    await wrapper.find('.create-btn').trigger('click');
    await flushPromises();
    expect(createPortfolio).toHaveBeenCalledWith({});
  });

  it('⑨ ✎ 就地改名走 PATCH', async () => {
    patchPortfolio.mockResolvedValue({ id: 1, name: '新名' });
    const wrapper = await mountPage();
    await wrapper.find('.pencil').trigger('click');
    const input = wrapper.find('.rename-input input');
    expect(input.exists()).toBe(true);
    await input.setValue('新名');
    await wrapper.find('.rename-input input').trigger('keyup.enter');
    await flushPromises();
    expect(patchPortfolio).toHaveBeenCalledWith(1, { name: '新名' });
  });

  it('⑩ 删除需二次确认; 取消则不调接口', async () => {
    confirmMock.mockRejectedValueOnce(new Error('cancel'));
    const wrapper = await mountPage();
    // hover 才显示的操作, 直接触发点击即可
    await wrapper.find('.delete-link').trigger('click');
    await flushPromises();
    expect(confirmMock).toHaveBeenCalled();
    expect(deletePortfolio).not.toHaveBeenCalled();
  });

  it('⑪ 确认后执行软删并刷新列表', async () => {
    confirmMock.mockResolvedValueOnce('confirm');
    deletePortfolio.mockResolvedValue({ id: 1, status: 'archived' });
    const wrapper = await mountPage();
    await wrapper.find('.delete-link').trigger('click');
    await flushPromises();
    expect(deletePortfolio).toHaveBeenCalledWith(1);
    expect(listPortfolios).toHaveBeenCalledTimes(2); // 初次 + 删除后刷新
  });

  it('⑫ 复制是卡片级操作, 传 from_id 自动命名由后端给', async () => {
    createPortfolio.mockResolvedValue({ id: 10, name: '复制_我的组合1' });
    const wrapper = await mountPage();
    await wrapper.find('.copy-link').trigger('click');
    await flushPromises();
    expect(createPortfolio).toHaveBeenCalledWith({ fromId: 1 });
  });
});
