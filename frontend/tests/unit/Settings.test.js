// Settings 页状态组合测试(P2-R05/R06): vitest + @vue/test-utils
// 覆盖: GET 初始失败 / 保存失败 / 保存成功+重读成功 / 保存成功+重读失败 / 取消恢复快照 / 敏感值留空
// GET 与 PUT 使用独立 mock, 不访问真实后端。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia } from 'pinia';

// mock API 模块: getSettings/saveSettings 分别可控行为
vi.mock('../../src/api/index.js', () => ({
  default: {},
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  sendTestMail: vi.fn(),
  getJisiluAccounts: vi.fn(),
  createJisiluAccount: vi.fn(),
  updateJisiluAccount: vi.fn(),
  deleteJisiluAccount: vi.fn(),
  checkJisiluAccount: vi.fn(),
  resetJisiluAccountStatus: vi.fn(),
}));

import {
  getSettings,
  saveSettings,
  getJisiluAccounts,
  createJisiluAccount,
  updateJisiluAccount,
  deleteJisiluAccount,
  checkJisiluAccount,
  resetJisiluAccountStatus,
} from '../../src/api/index.js';
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

const ACCOUNT_PAYLOAD = {
  items: [{
    id: 1, name: '主账号', username: '13800138000', enabled: true, status: 'ready',
    daily_request_count: 4, daily_login_count: 1, last_success_at: '2026-09-15T08:00:00',
    cooldown_until: null, last_error: null, password_configured: true, cookie_configured: true,
  }],
  summary: { total: 1, enabled: 1, ready: 1, cooldown: 0, today_requests: 4, today_logins: 1 },
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
  getJisiluAccounts.mockResolvedValue(JSON.parse(JSON.stringify(ACCOUNT_PAYLOAD)));
  createJisiluAccount.mockResolvedValue({ id: 2 });
  updateJisiluAccount.mockResolvedValue({});
  deleteJisiluAccount.mockResolvedValue(undefined);
  checkJisiluAccount.mockResolvedValue({ status: 'ready' });
  resetJisiluAccountStatus.mockResolvedValue({ status: 'ready' });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
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

  it('账号池独立加载并显示汇总、脱敏用户名和状态', async () => {
    const wrapper = await mountSettings();
    expect(wrapper.text()).toContain('集思录账号池');
    expect(wrapper.text()).toContain('总数 1');
    expect(wrapper.text()).toContain('启用 1');
    expect(wrapper.text()).toContain('就绪');
    expect(wrapper.text()).toContain('138****000');
    expect(wrapper.text()).not.toContain('password');
  });

  it('添加账号提交密码，编辑账号密码留空保持原值', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('[data-test="add-account"]').trigger('click');
    const dialog = wrapper.find('[data-test="account-dialog"]');
    await dialog.find('[data-test="account-name"]').setValue('备用');
    await dialog.find('[data-test="account-username"]').setValue('backup');
    await dialog.find('[data-test="account-password"]').setValue('secret');
    await dialog.find('[data-test="account-submit"]').trigger('click');
    await flushPromises();
    expect(createJisiluAccount).toHaveBeenCalledWith({ name: '备用', username: 'backup', password: 'secret', enabled: true });
    await wrapper.find('[data-test="edit-account"]').trigger('click');
    const editDialog = wrapper.find('[data-test="account-dialog"]');
    expect(editDialog.find('[data-test="account-password"]').element.value).toBe('');
    await editDialog.find('[data-test="account-submit"]').trigger('click');
    await flushPromises();
    expect(updateJisiluAccount.mock.calls.at(-1)[1]).not.toHaveProperty('password');
  });

  it('启停、删除确认、检测和重置均调用对应 API', async () => {
    const wrapper = await mountSettings();
    await wrapper.find('[data-test="account-enabled"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-test="delete-account"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-test="check-account"]').trigger('click');
    await flushPromises();
    await wrapper.find('[data-test="reset-account"]').trigger('click');
    await flushPromises();
    expect(updateJisiluAccount).toHaveBeenCalledWith(1, { enabled: false });
    expect(window.confirm).toHaveBeenCalled();
    expect(deleteJisiluAccount).toHaveBeenCalledWith(1);
    expect(checkJisiluAccount).toHaveBeenCalledWith(1);
    expect(resetJisiluAccountStatus).toHaveBeenCalledWith(1);
  });

  it('账号池加载失败可单独重试，且操作进行中按钮禁用', async () => {
    getJisiluAccounts.mockRejectedValueOnce(new Error('账号池断开'));
    const wrapper = await mountSettings();
    expect(wrapper.text()).toContain('账号池断开');
    expect(wrapper.find('[data-test="retry-accounts"]').exists()).toBe(true);
    await wrapper.find('[data-test="retry-accounts"]').trigger('click');
    await flushPromises();
    expect(getJisiluAccounts).toHaveBeenCalledTimes(2);
    checkJisiluAccount.mockImplementation(() => new Promise(() => {}));
    await wrapper.find('[data-test="check-account"]').trigger('click');
    expect(wrapper.find('[data-test="check-account"]').attributes('disabled')).toBeDefined();
  });

  it('同一账号任一操作期间整行操作全部禁用', async () => {
    const wrapper = await mountSettings();
    checkJisiluAccount.mockImplementation(() => new Promise(() => {}));
    await wrapper.find('[data-test="check-account"]').trigger('click');
    expect(wrapper.find('[data-test="account-enabled"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-test="edit-account"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-test="reset-account"]').attributes('disabled')).toBeDefined();
    expect(wrapper.find('[data-test="delete-account"]').attributes('disabled')).toBeDefined();
  });

  it('状态使用对应颜色类，汇总包含无效未知停用并可由列表派生', async () => {
    getJisiluAccounts.mockResolvedValueOnce({ items: [
      { id: 1, name: 'a', username: 'aa', enabled: true, status: 'cooldown' },
      { id: 2, name: 'b', username: 'bb', enabled: true, status: 'invalid' },
      { id: 3, name: 'c', username: 'cc', enabled: true, status: 'unknown' },
      { id: 4, name: 'd', username: 'dd', enabled: false, status: 'disabled' },
    ], summary: { total: 4, enabled: 3, ready: 0, cooldown: 1 } });
    const wrapper = await mountSettings();
    expect(wrapper.find('.tag.warning').exists()).toBe(true);
    expect(wrapper.find('.tag.danger').exists()).toBe(true);
    expect(wrapper.find('.tag.info').exists()).toBe(true);
    expect(wrapper.find('.tag.muted').exists()).toBe(true);
    expect(wrapper.text()).toContain('无效 1');
    expect(wrapper.text()).toContain('未知 1');
    expect(wrapper.text()).toContain('停用 1');
  });

  it('操作 API 成功但刷新失败时提示成功并保留重试', async () => {
    const wrapper = await mountSettings();
    getJisiluAccounts.mockRejectedValueOnce(new Error('刷新超时'));
    await wrapper.find('[data-test="check-account"]').trigger('click');
    await flushPromises();
    expect(checkJisiluAccount).toHaveBeenCalledWith(1);
    expect(wrapper.text()).toContain('操作已成功，但列表刷新失败，请重新加载');
    expect(wrapper.find('[data-test="retry-accounts"]').exists()).toBe(true);
  });

  it('移动端渲染账号卡片', async () => {
    const wrapper = await mountSettings();
    expect(wrapper.find('[data-test="account-mobile-list"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="account-mobile-card"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="account-mobile-card"]').text()).toContain('主账号');
  });

  it('移动错误字段有专用换行 class，媒体查询切换桌面表格与移动列表', async () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/Settings.vue'), 'utf8');
    expect(source).toContain('class="mobile-error"');
    expect(source).toMatch(/\.mobile-error[^{]*\{[^}]*overflow-wrap:\s*anywhere[^}]*word-break:\s*break-word[^}]*min-width:\s*0/s);
    expect(source).toMatch(/@media\s*\(max-width:\s*700px\)[\s\S]*?\.account-table-wrap\s*\{\s*display:\s*none;\s*\}[\s\S]*?\.account-mobile-list\s*\{\s*display:\s*block;\s*\}/);
  });

  it('深色主题为全部非就绪状态提供高对比颜色', async () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/Settings.vue'), 'utf8');
    expect(source).toMatch(/html\.dark[\s\S]*?\.tag\.warning\s*\{[^}]*background:[^}]*color:/s);
    expect(source).toMatch(/html\.dark[\s\S]*?\.tag\.danger\s*\{[^}]*background:[^}]*color:/s);
    expect(source).toMatch(/html\.dark[\s\S]*?\.tag\.info\s*\{[^}]*background:[^}]*color:/s);
    expect(source).toMatch(/html\.dark[\s\S]*?\.tag\.muted\s*\{[^}]*background:[^}]*color:/s);
  });
});
