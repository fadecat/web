// Bonds 页组件测试(R3-03/T3): vitest + @vue/test-utils + jsdom
// 覆盖: 默认评级不限 / 重置不限 / localStorage 已有选择保留 / 目录失败保持不限
// / 查询请求次数与参数序列化 / 非法文本不调用筛选接口
// 注意: 评级编码走页面导入的 API 模块 mock, 序列化逻辑在页面内 join(',')
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';

vi.mock('../../src/api/index.js', () => ({
  default: {},
  screenBondsIntraday: vi.fn(),
  getBlacklist: vi.fn().mockResolvedValue([]),
  addBlacklist: vi.fn(),
  removeBlacklist: vi.fn(),
  getRatingCatalog: vi.fn().mockResolvedValue([]),
}));

// Element Plus 消息组件 mock(避免依赖其渲染细节)
vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus');
  return {
    ...actual,
    ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
    ElMessageBox: { prompt: vi.fn() },
  };
});

import { screenBondsIntraday, getRatingCatalog, getBlacklist } from '../../src/api/index.js';
import Bonds from '../../src/pages/Bonds.vue';

const STORAGE_KEY = 'cb-intraday-filters';

// Element Plus 组件未全局注册时渲染为未解析的自定义元素, 不可交互。
// 提供功能桩: el-input 转发 v-model, el-button 转发 click 并保留透传 class,
// el-table 列桩渲染 default 插槽并注入 row(否则页内 #default="{ row }" 解构报错)。
const EP_STUBS = {
  'el-input': {
    props: ['modelValue', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<input :value="modelValue" :placeholder="placeholder" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'el-button': {
    emits: ['click'],
    template: '<button type="button" @click="$emit(\'click\', $event)"><slot /></button>',
  },
  'el-table': { template: '<div class="tstub"><slot /></div>' },
  'el-table-column': { template: '<div class="cstub"><slot name="default" :row="{}" /></div>' },
  'el-dialog': true,
  'el-empty': true,
};

function mountBonds() {
  return mount(Bonds, { global: { stubs: EP_STUBS } });
}

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  getBlacklist.mockResolvedValue([]);
  getRatingCatalog.mockResolvedValue([]);
  screenBondsIntraday.mockResolvedValue({
    total_all: 1, total_filtered: 1, rows: [{ code: '110001', name: '测试债' }],
    intraday: { fetched_at: '10:00:00', quote_time: '10:00:00', total_live: 1, redeem_loaded: true },
  });
});

describe('Bonds 页评级默认与查询合同', () => {
  it('首次进入: 评级默认不限(空数组), 不是全选 14 项', async () => {
    const wrapper = mountBonds();
    await flushPromises();
    // 通过组件内部状态断言: 展开组件实例不方便, 改走行为——
    // 立即点查询, 请求参数里 ratings 应为空串(不限)
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    expect(screenBondsIntraday).toHaveBeenCalledTimes(1);
    const payload = screenBondsIntraday.mock.calls[0][0];
    expect(payload.ratings).toBe('');
  });

  it('重置: 恢复默认(评级不限)', async () => {
    const wrapper = mountBonds();
    await flushPromises();
    const resetBtn = wrapper.findAll('button').find((b) => b.text() === '重置');
    await resetBtn.trigger('click');
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    const payload = screenBondsIntraday.mock.calls[0][0];
    expect(payload.ratings).toBe('');
  });

  it('localStorage 已有评级选择: 恢复用户偏好, 不被默认值覆盖', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      _v: 'v2',
      price_min: '', price_max: '115', premium_rt_max: '20',
      curr_iss_amt_max: '', ytm_min: '', year_left_min: '', year_left_max: '',
      ratings: ['AA+', 'NONE'],
    }));
    const wrapper = mountBonds();
    await flushPromises();
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    const payload = screenBondsIntraday.mock.calls[0][0];
    expect(payload.ratings).toBe('AA+,NONE');
    expect(payload.price_max).toBe(115); // 数字转换
    expect(payload.premium_rt_max).toBe(20);
  });

  it('目录加载失败: 保持不限语义, 页面可正常查询', async () => {
    getRatingCatalog.mockRejectedValue(new Error('目录接口挂了'));
    const wrapper = mountBonds();
    await flushPromises();
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    expect(screenBondsIntraday).toHaveBeenCalledTimes(1);
    expect(screenBondsIntraday.mock.calls[0][0].ratings).toBe('');
  });

  it('目录返回未知评级 BB+: 仅作为可选项, 默认仍不限', async () => {
    getRatingCatalog.mockResolvedValue([
      { value: 'AAA', label: 'AAA' }, { value: 'BB+', label: 'BB+' }, { value: 'NONE', label: '无评级' },
    ]);
    const wrapper = mountBonds();
    await flushPromises();
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    expect(screenBondsIntraday.mock.calls[0][0].ratings).toBe('');
  });

  it('非法数值文本: 不调用筛选接口, loading 归零', async () => {
    const wrapper = mountBonds();
    await flushPromises();
    // 价格"最高"输入框(功能桩渲染为原生 input, 按 placeholder 定位)
    const priceInput = wrapper.findAll('input').find((i) => i.attributes('placeholder') === '最高');
    await priceInput.setValue('120abc');
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    expect(screenBondsIntraday).not.toHaveBeenCalled();
  });

  it('评级含 NONE 与 AA+: 逗号序列化保持占位符', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      _v: 'v2',
      price_min: '', price_max: '', premium_rt_max: '',
      curr_iss_amt_max: '', ytm_min: '', year_left_min: '', year_left_max: '',
      ratings: ['AA+', 'NONE'],
    }));
    const wrapper = mountBonds();
    await flushPromises();
    await wrapper.find('.btn-query').trigger('click');
    await flushPromises();
    expect(screenBondsIntraday.mock.calls[0][0].ratings).toBe('AA+,NONE');
  });
});
