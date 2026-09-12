// Settings 页状态组合测试(P2-R05/R06): vitest + @vue/test-utils
// 覆盖: GET 初始失败 / 保存失败 / 保存成功+重读成功 / 保存成功+重读失败 / 取消恢复快照 / 敏感值留空
// GET 与 PUT 使用独立 mock, 不访问真实后端。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// mock API 模块: getSettings/saveSettings 分别可控行为
vi.mock('../../src/api/index.js', () => ({
  default: {},
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  sendTestMail: vi.fn(),
}));

import { getSettings, saveSettings } from '../../src/api/index.js';
import Settings from '../../src/pages/Settings.vue';

// 构造 GET /settings 的标准响应
const SETTINGS_PAYLOAD = {
  items: {
    smtp_host: { label: 'SMTP 服务器', value: 'smtp.qq.com', configured: true, sensitive: false },
    smtp_port: { label: '端口', value: '465', configured: true, sensitive: false },
    smtp_ssl: { label: '加密', value: 'true', configured: true, sensitive: false },
    smtp_user: { label: '发件邮箱', value: 'a@qq.com', configured: true, sensitive: false },
    smtp_password: { label: '授权码', value: '', configured: true, sensitive: true },
    notify_emails: { label: '通知邮箱', value: 'b@qq.com', configured: true, sensitive: false },
  },
};

async function mountSettings() {
  // 外观卡依赖主题 store(Pinia), 挂载需提供实例
  const wrapper = mount(Settings, { global: { plugins: [createPinia()] } });
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  // resetAllMocks(而非 clearAllMocks): 清掉残留的 once 队列, 避免用例间泄漏
  vi.resetAllMocks();
  getSettings.mockResolvedValue(JSON.parse(JSON.stringify(SETTINGS_PAYLOAD)));
  saveSettings.mockResolvedValue({ status: 'saved' });
});

describe('Settings 页面状态组合', () => {
  it('初始 GET 失败: 显示阻断错误和重新加载按钮, 不渲染编辑表单', async () => {
    getSettings.mockRejectedValue(new Error('网络断开'));
    const wrapper = await mountSettings();
    expect(wrapper.text()).toContain('网络断开');
    expect(wrapper.find('input').exists()).toBe(false);
    expect(wrapper.text()).toContain('重新加载');
  });

  it('初始 GET 成功: 草稿填入服务端值, 敏感项已配置则锁定', async () => {
    const wrapper = await mountSettings();
    // smtp_password.configured=true → 锁定态, 显示摘要而非输入框
    expect(wrapper.text()).toContain('已配置');
    expect(wrapper.text()).toContain('已保存, 不显示');
  });

  it('保存失败: 显示保存错误, 不提示已保存', async () => {
    saveSettings.mockRejectedValue({ response: { data: { detail: '授权码错误' } } });
    const wrapper = await mountSettings();
    // 解锁进入编辑态
    await wrapper.find('button.ghost.sm').trigger('click');
    await flushPromises();
    await wrapper.find('.actions .btn:not(.ghost)').trigger('click'); // 保存配置
    await flushPromises();
    expect(wrapper.text()).toContain('授权码错误');
    expect(wrapper.text()).not.toContain('配置已保存');
  });

  it('保存成功且重读成功: 提示"配置已保存"', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('button.ghost.sm').trigger('click');
    await flushPromises();
    await wrapper.find('.actions .btn:not(.ghost)').trigger('click');
    await flushPromises();
    expect(saveSettings).toHaveBeenCalledTimes(1);
    expect(getSettings).toHaveBeenCalledTimes(2); // 初始 + 保存后重读
    expect(wrapper.text()).toContain('配置已保存');
    expect(wrapper.text()).not.toContain('重新读取失败');
  });

  it('保存成功但重读失败: 保留"已保存"事实, 明确提示重新读取失败', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('button.ghost.sm').trigger('click');
    await flushPromises();
    // 只让保存后的重读(下一次 GET)失败, 初始加载用默认成功 mock
    getSettings.mockRejectedValueOnce(new Error('重读超时'));
    await wrapper.find('.actions .btn:not(.ghost)').trigger('click');
    await flushPromises();
    expect(saveSettings).toHaveBeenCalledTimes(1);
    const text = wrapper.text();
    expect(text).toContain('配置已保存');
    expect(text).toContain('重新读取失败');
  });

  it('取消编辑: 恢复到最近一次成功读取的快照', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('button.ghost.sm').trigger('click');
    await flushPromises();
    // 修改发件邮箱草稿(按初值定位 smtp_user 输入框, 避开 host/port 等其他 text 框)
    const input = wrapper
      .findAll('input')
      .find((i) => i.element.type === 'text' && i.element.value === 'a@qq.com');
    await input.setValue('changed@qq.com');
    // 取消
    const cancelBtn = wrapper.findAll('button').find((b) => b.text() === '取消');
    await cancelBtn.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('已放弃修改');
    // 取消后页面重新上锁(切回摘要态); 再解锁验证草稿已恢复为快照值
    await wrapper.find('button.ghost.sm').trigger('click'); // 重新配置
    await flushPromises();
    const restored = wrapper
      .findAll('input')
      .find((i) => i.element.type === 'text' && i.element.value === 'a@qq.com');
    expect(restored).toBeTruthy();
  });

  it('敏感值留空: 保存请求里授权码为空字符串(后端语义=保持不变)', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('button.ghost.sm').trigger('click');
    await flushPromises();
    await wrapper.find('.actions .btn:not(.ghost)').trigger('click');
    await flushPromises();
    const payload = saveSettings.mock.calls[0][0];
    expect(payload.smtp_password).toBe('');
  });
});
