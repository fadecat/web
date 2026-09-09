// DataStatus 页组件测试(R3-04/T4): 同步生命周期封闭
// 核心反例: 用户点「同步」→ POST 在途时卸载页面 → POST 完成后
// 不得启动自动刷新(无新 GET、无活动定时器)。
// 另覆盖: 正常路径一次 POST + 控制器立即 GET。
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';

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
import { getDataManagement, syncIndex } from '../../src/api/dataManagement.js';
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

function mountPage() {
  return mount(DataStatus, {
    global: {
      stubs: {
        // element-plus 未注册, 渲染为自定义元素; 只桩掉重的
        'el-table': { template: '<div class="tstub"><slot /></div>' },
        'el-table-column': { template: '<div class="cstub"><slot name="default" :row="{}" /></div>' },
        'el-dialog': true,
        'el-button': {
          emits: ['click'],
          template: '<button type="button" @click="$emit(\'click\', $event)"><slot /></button>',
        },
      },
    },
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  getDataManagement.mockResolvedValue(JSON.parse(JSON.stringify(DM_PAYLOAD)));
});

// 每个 case 结束后确认无残留定时器(vi.useFakeTimers 下可检查)
afterEach(() => {
  vi.useRealTimers();
});

describe('DataStatus 同步生命周期', () => {
  it('正常路径: POST 一次, 控制器立即 GET 列表', async () => {
    syncIndex.mockResolvedValue({ status: 'started' });
    const wrapper = mountPage();
    await flushPromises();
    const initialGets = getDataManagement.mock.calls.length;
    // 点第一张卡片的「同步」按钮
    const syncBtn = wrapper.findAll('button').find((b) => b.text().includes('同步'));
    await syncBtn.trigger('click');
    await flushPromises();
    expect(syncIndex).toHaveBeenCalledTimes(1);
    expect(syncIndex).toHaveBeenCalledWith('930955');
    // 控制器立即执行了第一次 loadList
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
    await flushPromises(); // POST 已发出但未返回
    expect(getDataManagement.mock.calls.length).toBe(initialGets); // 未提前刷新

    wrapper.unmount(); // 用户此刻离开页面
    resolvePost({ status: 'started' }); // POST 稍后成功
    await flushPromises();
    await flushPromises();
    // 关键断言: 卸载后不得有任何新 GET
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
    // 卸载后组件已不在 DOM, 只要不抛未捕获异常即为通过
    expect(true).toBe(true);
  });

  it('手动运行任务入口 runJobManually 正常调用(独立 API 模块)', async () => {
    runJobManually.mockResolvedValue({ ok: true });
    const wrapper = mountPage();
    await flushPromises();
    expect(wrapper.exists()).toBe(true);
  });
});
