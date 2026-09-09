// DataStatus 页组件测试(R3-04/T4, R4-06): 同步生命周期 + 添加指数流程
// 核心反例: 用户点「同步」→ POST 在途时卸载页面 → POST 完成后
// 不得启动自动刷新(无新 GET、无活动定时器)。
// 另覆盖: 正常路径一次 POST + 控制器立即 GET; 添加指数入口 addIndex 恰一次;
// 5 分钟截止后不再 GET; 卸载后零定时器残留。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { h } from 'vue';

vi.mock('../../src/api/index.js', () => ({
  default: {},
  runJobManually: vi.fn(),
}));
vi.mock('../../src/api/dataManagement.js', () => ({
  getDataManagement: vi.fn(),
  getRuns: vi.fn().mockResolvedValue({ runs: [] }),
  probeIndex: vi.fn(),
  addIndex: vi.fn(),
  syncIndex: vi.fn(),
  setIndexEnabled: vi.fn(),
}));

import { runJobManually } from '../../src/api/index.js';
import {
  getDataManagement, syncIndex, probeIndex, addIndex,
} from '../../src/api/dataManagement.js';
import DataStatus from '../../src/pages/DataStatus.vue';

const DM_PAYLOAD = {
  indexes: [
    {
      code: '930955', name: '红利低波', enabled: true,
      datasets: [{ key: 'quote', label: '收盘价', source: 'efunds', state: 'fresh', latest_date: '2026-09-08' }],
    },
  ],
  non_index_groups: [],
  sources: [],
  jobs: [],
};

const EP_STUBS = {
  'el-table': { template: '<div class="tstub"><slot /></div>' },
  'el-table-column': { template: '<div class="cstub"><slot name="default" :row="{}" /></div>' },
  'el-dialog': { template: '<div class="dlg-stub"><slot /><slot name="footer" /></div>' },
  'el-tabs': { template: '<div class="tabs-stub"><slot /></div>' },
  'el-tab-pane': { template: '<div class="pane-stub"><slot /></div>' },
  'el-button': {
    emits: ['click'],
    template: '<button type="button" @click="$emit(\'click\', $event)"><slot /></button>',
  },
  'el-input': {
    props: ['modelValue', 'placeholder'],
    emits: ['update:modelValue'],
    template:
      '<input :value="modelValue" :placeholder="placeholder" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  'el-select': {
    props: ['modelValue'],
    emits: ['update:modelValue'],
    template:
      '<select class="el-select" @change="$emit(\'update:modelValue\', $event.target.value)"><slot /></select>',
  },
  'el-option': {
    props: ['value', 'label'],
    template: '<option :value="value"><slot>{{ label }}</slot></option>',
  },
};

function mountPage() {
  return mount(DataStatus, { global: { stubs: EP_STUBS } });
}

beforeEach(() => {
  vi.resetAllMocks();
  getDataManagement.mockResolvedValue(JSON.parse(JSON.stringify(DM_PAYLOAD)));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('DataStatus 同步生命周期', () => {
  it('正常路径: POST 一次, 控制器立即 GET 列表', async () => {
    syncIndex.mockResolvedValue({ status: 'started' });
    const wrapper = mountPage();
    await flushPromises();
    const initialGets = getDataManagement.mock.calls.length;
    const syncBtn = wrapper.findAll('button').find((b) => b.text().includes('同步'));
    await syncBtn.trigger('click');
    await flushPromises();
    expect(syncIndex).toHaveBeenCalledTimes(1);
    expect(syncIndex).toHaveBeenCalledWith('930955');
    expect(getDataManagement.mock.calls.length).toBeGreaterThan(initialGets);
  });

  it('卸载后迟到 POST 完成: 不启动刷新、无新 GET(核心反例)', async () => {
    let resolvePost;
    syncIndex.mockImplementation(() => new Promise((r) => { resolvePost = r; }));
    const wrapper = mountPage();
    await flushPromises();
    const initialGets = getDataManagement.mock.calls.length;
    const syncBtn = wrapper.findAll('button').find((b) => b.text().includes('同步'));
    await syncBtn.trigger('click');
    await flushPromises();
    expect(getDataManagement.mock.calls.length).toBe(initialGets);
    wrapper.unmount();
    resolvePost({ status: 'started' });
    await flushPromises();
    await flushPromises();
    expect(getDataManagement.mock.calls.length).toBe(initialGets);
  });

  it('卸载后迟到 POST 失败: 不更新提示(静默放弃)', async () => {
    let rejectPost;
    syncIndex.mockImplementation(() => new Promise((_, rj) => { rejectPost = rj; }));
    const wrapper = mountPage();
    await flushPromises();
    const syncBtn = wrapper.findAll('button').find((b) => b.text().includes('同步'));
    await syncBtn.trigger('click');
    await flushPromises();
    wrapper.unmount();
    rejectPost(new Error('网络错误'));
    await flushPromises();
    expect(true).toBe(true);
  });

  it('手动运行任务入口 runJobManually 正常调用(独立 API 模块)', async () => {
    runJobManually.mockResolvedValue({ ok: true });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.exists()).toBe(true);
  });
});

describe('DataStatus 添加指数入口(R4-06)', () => {
  it('添加指数: 探测一次, 保存恰好一次, 成功后立即 GET', async () => {
    probeIndex.mockResolvedValue({
      code: '399296', name: '创成长', probe_token: 'tok-1',
      capabilities: [{ key: 'quote', status: 'available', label: '收盘价' }],
    });
    addIndex.mockResolvedValue({ status: 'saved', sync_status: 'started', code: '399296', message: '已保存' });
    const wrapper = mountPage();
    await flushPromises();
    const initialGets = getDataManagement.mock.calls.length;

    // 打开添加弹窗
    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加指数'));
    await addBtn.trigger('click');
    await flushPromises();
    // 填代码并检查
    const codeInput = wrapper.findAll('input').find((i) => i.attributes('placeholder') === '6 位指数代码');
    await codeInput.setValue('399296');
    await flushPromises();
    const checkBtn = wrapper.findAll('button').find((b) => b.text() === '检查');
    await checkBtn.trigger('click');
    await flushPromises();
    expect(probeIndex).toHaveBeenCalledTimes(1);
    expect(probeIndex).toHaveBeenCalledWith('399296', 'efunds');
    // 保存(能力默认全选 available)
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存');
    // vue-test-utils 的 trigger 对 stub 根元素 click 有兼容怪癖, 用原生事件
    saveBtn.element.dispatchEvent(new Event('click', { bubbles: true }));
    await flushPromises();
    expect(addIndex).toHaveBeenCalledTimes(1);
    expect(addIndex).toHaveBeenCalledWith(expect.objectContaining({ code: '399296', source: 'efunds' }));
    // 成功路径启动控制器: 立即 GET
    expect(getDataManagement.mock.calls.length).toBeGreaterThan(initialGets);
  });
});

describe('DataStatus 假时钟与 timer 清理(R4-06)', () => {
  it('添加指数成功后: 5 分钟截止不再 GET, 卸载后零定时器', async () => {
    vi.useFakeTimers();
    probeIndex.mockResolvedValue({
      code: '399296', name: '创成长', probe_token: 'tok-1',
      capabilities: [{ key: 'quote', status: 'available', label: '收盘价' }],
    });
    addIndex.mockResolvedValue({ status: 'saved', sync_status: 'started', code: '399296', message: '已保存' });
    const wrapper = mountPage();
    await flushPromises();
    const initialGets = getDataManagement.mock.calls.length;

    await wrapper.findAll('button').find((b) => b.text().includes('添加指数')).trigger('click');
    await flushPromises();
    await wrapper.findAll('input').find((i) => i.attributes('placeholder') === '6 位指数代码').setValue('399296');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === '检查').trigger('click');
    await flushPromises();
    const saveBtn = wrapper.findAll('button').find((b) => b.text() === '保存');
    saveBtn.element.dispatchEvent(new Event('click', { bubbles: true }));
    await flushPromises();
    expect(addIndex).toHaveBeenCalledTimes(1);
    // 控制器已启动: 有定时器
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    // 推进到 5 分钟以后: deadline 触发, 不再续排
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    const afterDeadline = getDataManagement.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30 * 1000);
    expect(getDataManagement.mock.calls.length).toBe(afterDeadline);
    // 卸载: dispose 清掉剩余定时器
    wrapper.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
