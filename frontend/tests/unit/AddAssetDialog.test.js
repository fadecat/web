// AddAssetDialog 组件单测: 全部 mock API 层(后端并行开发中, 不许依赖真实服务)。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import ElementPlus from 'element-plus';

vi.mock('../../src/api/portfolio.js', () => ({
  probeAsset: vi.fn(),
  createAsset: vi.fn(),
  listAssets: vi.fn(),
  refreshAsset: vi.fn(),
}));

import { createAsset, probeAsset } from '../../src/api/portfolio.js';
import AddAssetDialog from '../../src/components/portfolio/AddAssetDialog.vue';

// 000001 的真实歧义: 平安银行(股票·深市) / 华夏成长混合(场外基金) / 上证指数(指数)
const CANDIDATE_STOCK = {
  symbol: '000001.SZ', name: '平安银行', security_type: 'STOCK', price_basis: 'HFQ',
  source: 'tencent', resolved: true, latest_date: '2026-09-18', registered: false,
  row_count: null, first_date: null, last_date: null,
};
const CANDIDATE_FUND = {
  symbol: '000001.OF', name: '华夏成长混合', security_type: 'FUND', price_basis: 'NAV_ADJ',
  source: 'danjuan', resolved: false, latest_date: null, registered: false,
  row_count: null, first_date: null, last_date: null,
  note: '蛋卷详情暂不可用（场内 ETF 常见），不影响净值同步',
};
const CANDIDATE_INDEX = {
  symbol: '000001.SH', name: '上证指数', security_type: 'STOCK', price_basis: 'PRICE',
  source: 'tencent', resolved: true, latest_date: '2026-09-18', registered: false,
  row_count: null, first_date: null, last_date: null,
};

const SINGLE = {
  symbol: '600900.SH', name: '长江电力', security_type: 'STOCK', price_basis: 'HFQ',
  source: 'tencent', resolved: true, latest_date: '2026-09-18', registered: false,
  row_count: 1200, first_date: '2021-01-04', last_date: '2026-09-18',
};

const DEBOUNCE_WAIT = 450;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// el-dialog 的内容是异步渲染的(overlay 的 visible/rendered 要等一轮 tick 才翻转):
// 挂载后必须先 flushPromises + nextTick, 否则输入框还不在 DOM 里。
const mountDialog = async (props = {}) => {
  const wrapper = mount(AddAssetDialog, {
    props: { modelValue: true, ...props },
    global: { plugins: [ElementPlus] },
  });
  await flushPromises();
  await nextTick();
  return wrapper;
};

// 走完整链路: 输入 → 等防抖 → 等 probe 落定
// 注意: 类型 chip 也是 input(radio), 必须限定在代码输入框内查找
const codeInput = (wrapper) => wrapper.find('.add-asset-input input');

const typeCode = async (wrapper, code) => {
  await codeInput(wrapper).setValue(code);
  await wait(DEBOUNCE_WAIT);
  await flushPromises();
};

const buttonByText = (wrapper, text) => wrapper
  .findAll('button')
  .find((btn) => btn.text().trim() === text);

describe('AddAssetDialog 解析候选', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    probeAsset.mockResolvedValue([]);
  });

  it('① 裸 6 位数字返回 3 个候选时渲染候选列表且条数正确(且防抖只请求一次)', async () => {
    probeAsset.mockResolvedValue([CANDIDATE_STOCK, CANDIDATE_FUND, CANDIDATE_INDEX]);
    const wrapper = await mountDialog();
    await codeInput(wrapper).setValue('00000');
    await codeInput(wrapper).setValue('000001');
    await wait(DEBOUNCE_WAIT);
    await flushPromises();

    expect(probeAsset).toHaveBeenCalledTimes(1);
    expect(probeAsset).toHaveBeenCalledWith('000001', null);
    expect(wrapper.findAll('.candidate-item').length).toBe(3);
    expect(wrapper.text()).toContain('平安银行');
    expect(wrapper.text()).toContain('华夏成长混合');
    expect(wrapper.text()).toContain('上证指数');
    // 每行展示「类型 · 交易所/来源」
    expect(wrapper.text()).toContain('股票 · 深交所/tencent');
    // 多选时不直接进确认卡
    expect(wrapper.find('.confirm-card').exists()).toBe(false);
    wrapper.unmount();
  });

  it('② 单候选直接出确认卡, 且含「复权口径」一行', async () => {
    probeAsset.mockResolvedValue([SINGLE]);
    const wrapper = await mountDialog();
    await typeCode(wrapper, '600900');
    await flushPromises();

    const card = wrapper.find('.confirm-card');
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain('长江电力');
    expect(card.text()).toContain('600900.SH · 股票');
    expect(card.text()).toContain('复权口径');
    expect(wrapper.find('.basis-row').text()).toContain('后复权价');
    expect(card.text()).toContain('2021-01-04 ~ 2026-09-18（1200 个交易日）');
    wrapper.unmount();
  });

  it('③ 0 候选(404 / 空数组)时显示「未找到该代码」并列出原因', async () => {
    probeAsset.mockRejectedValue({ response: { status: 404 } });
    const wrapper = await mountDialog();
    await typeCode(wrapper, '999999');

    expect(wrapper.text()).toContain('未找到该代码');
    expect(wrapper.text()).toContain('数据源不覆盖');
    expect(wrapper.text()).toContain('场外基金用 6 位代码');
    wrapper.unmount();

    probeAsset.mockResolvedValue([]);
    const wrapper2 = await mountDialog();
    await typeCode(wrapper2, '999999');
    expect(wrapper2.text()).toContain('未找到该代码');
    wrapper2.unmount();
  });

  it('③b 代码非法(422)时显示后端给出的原因', async () => {
    probeAsset.mockRejectedValue({ response: { status: 422, data: { detail: '代码长度不支持' } } });
    const wrapper = await mountDialog();
    await typeCode(wrapper, 'abc');

    expect(wrapper.text()).toContain('代码写法无法识别');
    expect(wrapper.text()).toContain('代码长度不支持');
    wrapper.unmount();
  });
});

describe('AddAssetDialog 添加', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    probeAsset.mockResolvedValue([SINGLE]);
  });

  it('④ 点击「添加」触发 POST 并 emit added / start-change', async () => {
    const asset = { ...SINGLE, id: 7, row_count: null, first_date: null, last_sync_status: 'running' };
    createAsset.mockResolvedValue(asset);
    const wrapper = await mountDialog({ currentStart: null });
    await typeCode(wrapper, '600900');
    await buttonByText(wrapper, '添加').trigger('click');
    await flushPromises();

    expect(createAsset).toHaveBeenCalledTimes(1);
    expect(createAsset).toHaveBeenCalledWith({
      symbol: '600900.SH', security_type: 'STOCK', name: '长江电力',
    });
    expect(wrapper.emitted('added')).toBeTruthy();
    expect(wrapper.emitted('added')[0][0]).toEqual(asset);
    // 起点事件携带新标的 first_date(后端行尚未抓取时退回候选的解析结果)
    expect(wrapper.emitted('start-change')).toBeTruthy();
    expect(wrapper.emitted('start-change')[0][0].newStart).toBe('2021-01-04');
    wrapper.unmount();
  });

  it('⑤ 501 兜底: 展示后端给出的原因(P1 起场外基金已可注册, 此分支仅防旧后端)', async () => {
    probeAsset.mockResolvedValue([CANDIDATE_FUND]);
    createAsset.mockRejectedValue({
      response: { status: 501, data: { detail: '该类型标的暂不支持注册' } },
    });
    const wrapper = await mountDialog();
    await typeCode(wrapper, '000001');
    await buttonByText(wrapper, '添加').trigger('click');
    await flushPromises();

    expect(createAsset).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('该类型标的暂不支持注册');
    wrapper.unmount();
  });

  it('已 registered 的候选不重复请求, 提示「已在组合中」', async () => {
    probeAsset.mockResolvedValue([{ ...SINGLE, registered: true }]);
    const wrapper = await mountDialog();
    await typeCode(wrapper, '600900');
    await buttonByText(wrapper, '添加').trigger('click');
    await flushPromises();

    expect(createAsset).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('已在组合中');
    wrapper.unmount();
  });

  it('未解析到(resolved=false)的非基金候选禁止添加', async () => {
    probeAsset.mockResolvedValue([{ ...SINGLE, resolved: false, row_count: null }]);
    const wrapper = await mountDialog();
    await typeCode(wrapper, '600900');

    expect(wrapper.text()).toContain('数据源未解析到该标的');
    expect(buttonByText(wrapper, '添加').attributes('disabled')).toBeDefined();
    await buttonByText(wrapper, '添加').trigger('click');
    await flushPromises();
    expect(createAsset).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('未解析到但是场外基金(显式 .OF)仍可添加: 详情不可用 ≠ 净值不可同步', async () => {
    probeAsset.mockResolvedValue([CANDIDATE_FUND]);
    createAsset.mockResolvedValue({ id: 9, symbol: '000001.OF' });
    const wrapper = await mountDialog();
    await typeCode(wrapper, '000001.OF');

    expect(wrapper.text()).toContain('不影响净值同步');
    const addBtn = buttonByText(wrapper, '添加');
    expect(addBtn.attributes('disabled')).toBeUndefined();
    await addBtn.trigger('click');
    await flushPromises();
    expect(createAsset).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it('起点被推后时 start-change 带前移年数', async () => {
    const asset = { ...SINGLE, id: 7, first_date: '2024-03-05' };
    createAsset.mockResolvedValue(asset);
    const wrapper = await mountDialog({ currentStart: '2021-01-04' });
    await typeCode(wrapper, '600900');
    await buttonByText(wrapper, '添加').trigger('click');
    await flushPromises();

    const payload = wrapper.emitted('start-change')[0][0];
    expect(payload.oldStart).toBe('2021-01-04');
    expect(payload.newStart).toBe('2024-03-05');
    expect(payload.shiftYears).toBeGreaterThan(3);
    expect(payload.title).toContain('新的组合起点：2024-03-05');
    expect(payload.detail).toContain('前移');
    wrapper.unmount();
  });
});
