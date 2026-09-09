// Factors 页组件测试(R4-06/T4): 模板读取/保存/复制/预览/计数页面级合同
// 覆盖计划要求的五条: 保存不带 excluded_ratings / 复制保留空评级与未知评级 /
// 新建复制当前模板 / 预览 null 数值不抛异常 / 实际入选与缓冲计数使用后端值。
// Element Plus 组件未注册, 用功能桩(转发 v-model/click)模拟交互。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';

vi.mock('../../src/api/index.js', () => ({
  default: {},
  getFactorCatalog: vi.fn(),
  getFactors: vi.fn(),
  saveFactors: vi.fn(),
  screenBonds: vi.fn(),
  getRatingCatalog: vi.fn(),
}));

vi.mock('element-plus', async () => {
  const actual = await vi.importActual('element-plus');
  return {
    ...actual,
    ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
    ElMessageBox: { confirm: vi.fn().mockResolvedValue('confirm') },
  };
});

import {
  getFactorCatalog, getFactors, saveFactors, screenBonds, getRatingCatalog,
} from '../../src/api/index.js';
import Factors from '../../src/pages/Factors.vue';

const template = {
  id: 't1', name: '三低', target_count: 10, hold_tolerance: 0,
  exclusion_rules: [], strategy_factors: [], excluded_redeem_icons: [],
  redeem_safe_days: 2, excluded_bond_codes: [], ratings: ['AA+'],
  min_listing_days: 0,
};

const EP_STUBS = {
  'el-button': {
    emits: ['click'],
    template: '<button type="button" @click="$emit(\'click\', $event)"><slot /></button>',
  },
  'el-input-number': {
    props: ['modelValue', 'min', 'max', 'step'],
    emits: ['update:modelValue', 'change'],
    template:
      '<input type="number" :value="modelValue" :min="min" :max="max" :step="step" @input="$emit(\'update:modelValue\', Number($event.target.value)); $emit(\'change\', Number($event.target.value))" />',
  },
  'el-collapse': { template: '<div class="collapse"><slot /></div>' },
  'el-collapse-item': { template: '<div class="collapse-item"><slot /></div>' },
  'el-table': { template: '<div class="tstub"><slot /></div>' },
  'el-table-column': { template: '<div class="cstub"><slot name="default" :row="{}" /></div>' },
  'el-tag': { template: '<span class="tag"><slot /></span>' },
  'el-card': { template: '<div><slot name="header" /><slot /></div>' },
  'el-radio-group': { template: '<div class="radios"><slot /></div>' },
  'el-radio-button': { template: '<span class="radio"><slot /></span>' },
  'el-select': {
    props: ['modelValue', 'multiple'],
    emits: ['update:modelValue'],
    template: '<select class="el-select"><slot /></select>',
  },
  'el-option': { template: '<option :value="value"><slot /></option>' },
  'el-checkbox-group': { template: '<div class="checks"><slot /></div>' },
  'el-checkbox': { template: '<label><input type="checkbox" :checked="value" /><slot /></label>' },
  'el-switch': { template: '<input type="checkbox" />' },
};

function mountPage() {
  return mount(Factors, { global: { stubs: EP_STUBS } });
}

beforeEach(() => {
  vi.resetAllMocks();
  getFactorCatalog.mockResolvedValue([]);
  getRatingCatalog.mockResolvedValue([{ value: 'AA+', label: 'AA+' }]);
  getFactors.mockResolvedValue({ active_id: 't1', templates: [JSON.parse(JSON.stringify(template))] });
  screenBonds.mockResolvedValue({
    total_all: 3, total_filtered: 2, top_n: 1, keep_n: 1,
    rows: [{ bond_id: '110001', score: null }], excluded_rows: [],
  });
  saveFactors.mockImplementation(async (payload) => ({ ok: true, data: payload }));
});

describe('Factors 页配置工作流', () => {
  it('读取当前模板后保存不发送 excluded_ratings (R4-01 前端侧)', async () => {
    // 服务端清洗结果(含迁移后模板)不含 excluded_ratings, 前端原样提交
    const wrapper = mountPage();
    await flushPromises();
    // 初始 dirty=false(按钮显示「已保存」); 改持仓数量触发 dirty
    const countInput = wrapper.findAll('input[type="number"]').find((i) => i.attributes('min') === '1');
    await countInput.setValue(12);
    await flushPromises();
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存配置'));
    await saveBtn.trigger('click');
    await flushPromises();
    expect(saveFactors).toHaveBeenCalledTimes(1);
    const payload = saveFactors.mock.calls[0][0];
    expect(payload.templates[0].ratings).toEqual(['AA+']);
    expect(payload.templates[0]).not.toHaveProperty('excluded_ratings');
  });

  it('复制模板保留未知评级(R5-09: 目录未登记值)', async () => {
    const withUnknown = JSON.parse(JSON.stringify(template));
    withUnknown.ratings = ['BB+']; // 未知评级(目录里没有也要能复制)
    getFactors.mockResolvedValue({ active_id: 't1', templates: [withUnknown] });
    const wrapper = mountPage();
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === '复制').trigger('click');
    await flushPromises();
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存配置'));
    await saveBtn.trigger('click');
    await flushPromises();
    const payload = saveFactors.mock.calls[0][0];
    expect(payload.templates).toHaveLength(2);
    const copy = payload.templates.find((t) => t.name.includes('副本'));
    expect(copy.ratings).toEqual(['BB+']); // 未知评级被保留, 不是被清空
  });

  it('复制模板保留空评级(R5-09: 不限语义不清空为七档)', async () => {
    const withEmpty = JSON.parse(JSON.stringify(template));
    withEmpty.ratings = []; // 空评级 = 不限
    getFactors.mockResolvedValue({ active_id: 't1', templates: [withEmpty] });
    const wrapper = mountPage();
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === '复制').trigger('click');
    await flushPromises();
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存配置'));
    await saveBtn.trigger('click');
    await flushPromises();
    const payload = saveFactors.mock.calls[0][0];
    expect(payload.templates).toHaveLength(2);
    const copy = payload.templates.find((t) => t.name.includes('副本'));
    expect(copy.ratings).toEqual([]); // 空评级被保留, 不是固定七档
  });

  it('新建模板复制当前模板而非固定七档', async () => {
    const withEmptyRatings = JSON.parse(JSON.stringify(template));
    withEmptyRatings.ratings = []; // 空评级 = 不限
    getFactors.mockResolvedValue({ active_id: 't1', templates: [withEmptyRatings] });
    const wrapper = mountPage();
    await flushPromises();
    const newBtn = wrapper.findAll('button').find((b) => b.text().includes('新建模板'));
    await newBtn.trigger('click');
    await flushPromises();
    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('保存配置'));
    await saveBtn.trigger('click');
    await flushPromises();
    const payload = saveFactors.mock.calls[0][0];
    expect(payload.templates).toHaveLength(2);
    const created = payload.templates[1];
    expect(created.ratings).toEqual([]); // 继承当前模板的空评级, 不是固定七档
  });

  it('预览返回 null 数值时页面不抛异常', async () => {
    screenBonds.mockResolvedValue({
      total_all: 3, total_filtered: 2, top_n: 1, keep_n: 1,
      rows: [{ bond_id: '110001', name: '债', score: null, dblow: null, premium_rt: null }],
      excluded_rows: [],
    });
    const wrapper = mountPage();
    await flushPromises();
    const previewBtn = wrapper.findAll('button').find((b) => b.text() === '执行筛选');
    await previewBtn.trigger('click');
    await flushPromises();
    expect(screenBonds).toHaveBeenCalledTimes(1);
    // 不抛异常即为通过; 参数含模板与数据源
    const [tmplArg, sourceArg] = screenBonds.mock.calls[0];
    expect(tmplArg.ratings).toEqual(['AA+']);
    expect(sourceArg).toBe('db');
    expect(wrapper.text()).toContain('实际入选');
  });

  it('实际入选与缓冲计数使用后端 top_n 和 keep_n', async () => {
    screenBonds.mockResolvedValue({
      total_all: 20, total_filtered: 12, top_n: 10, keep_n: 2,
      selected_count: 7, buffer_count: 3, rows: [], excluded_rows: [],
    });
    const wrapper = mountPage();
    await flushPromises();
    const previewBtn = wrapper.findAll('button').find((b) => b.text() === '执行筛选');
    await previewBtn.trigger('click');
    await flushPromises();
    // 页面用后端 selected_count 展示实际入选, buffer_count 展示容差
    expect(wrapper.text()).toContain('7');
    expect(wrapper.text()).toContain('3');
  });
});
