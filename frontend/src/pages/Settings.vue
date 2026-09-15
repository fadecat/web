<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { getSettings, saveSettings, sendTestMail, getJisiluAccounts, createJisiluAccount, updateJisiluAccount, deleteJisiluAccount, checkJisiluAccount, resetJisiluAccountStatus } from '../api';
import { useThemeStore } from '../stores/theme';

// 外观: 三态(浅色/深色/跟随系统), 本地即时生效, 读写见 stores/theme.js
const theme = useThemeStore();
const themeMode = computed({
  get: () => theme.mode,
  set: (v) => theme.setMode(v),
});

const loading = ref(true);
const saving = ref(false);
const testing = ref(false);
const message = ref('');
const messageCls = ref('ok');

// items: key -> { label, value, configured, sensitive }
const items = ref({});
const loadError = ref(''); // 加载失败提示(失败时禁用编辑并允许重试)

// 表单草稿: 敏感项留空=不修改
const draft = reactive({});
// 最近一次成功读取的草稿快照(取消编辑恢复到这里)
const savedSnapshot = ref(null);

// SMTP 已配置(授权码已存在)时表单锁定, 点「重新配置」才解锁修改
const smtpLocked = ref(true);

const accounts = ref([]);
const accountSummary = ref({ total: 0, enabled: 0, ready: 0, cooldown: 0, invalid: 0, unknown: 0, disabled: 0, today_requests: 0, today_logins: 0 });
const accountsLoading = ref(true);
const accountsLoadError = ref('');
const accountDialog = ref(false);
const accountEditingId = ref(null);
const accountDraft = reactive({ name: '', username: '', password: '', enabled: true });
const accountSaving = ref(false);
const accountBusy = reactive({});

const STATUS_LABELS = { ready: '就绪', unknown: '未知', cooldown: '冷却中', invalid: '无效', disabled: '已停用' };
function maskUsername(value) {
  const s = String(value || '');
  if (s.includes('@')) {
    const [name, domain] = s.split('@');
    return `${name.length <= 2 ? `${name[0] || ''}*` : `${name[0]}${'*'.repeat(Math.min(4, name.length - 2))}${name.slice(-1)}`}@${domain}`;
  }
  if (/^\d{7,}$/.test(s)) return `${s.slice(0, 3)}****${s.slice(-3)}`;
  return s.length <= 2 ? `${s.slice(0, 1)}*` : `${s.slice(0, 1)}${'*'.repeat(Math.min(4, s.length - 2))}${s.slice(-1)}`;
}
function formatDate(value) { return value ? String(value).replace('T', ' ').replace(/\.\d+$/, '') : '—'; }
function statusLabel(status) { return STATUS_LABELS[status] || status || '未知'; }
function accountError(e) { return e?.response?.data?.detail || e?.message || '账号池加载失败'; }

async function loadAccounts() {
  accountsLoading.value = true;
  accountsLoadError.value = '';
  try {
    const data = await getJisiluAccounts();
    accounts.value = data.items || [];
    const derived = accounts.value.reduce((out, row) => {
      if (row.enabled === false || row.status === 'disabled') out.disabled += 1;
      if (row.status === 'invalid') out.invalid += 1;
      if (row.status === 'unknown') out.unknown += 1;
      return out;
    }, { invalid: 0, unknown: 0, disabled: 0 });
    accountSummary.value = { ...accountSummary.value, ...(data.summary || {}), ...derived };
    return true;
  } catch (e) {
    accountsLoadError.value = accountError(e);
    return false;
  } finally { accountsLoading.value = false; }
}
function resetAccountDraft(row = null) {
  accountEditingId.value = row?.id || null;
  accountDraft.name = row?.name || '';
  accountDraft.username = row?.username || '';
  accountDraft.password = '';
  accountDraft.enabled = row ? !!row.enabled : true;
  accountDialog.value = true;
}
async function saveAccount() {
  if (accountSaving.value) return;
  accountSaving.value = true;
  try {
    const data = { name: accountDraft.name.trim(), username: accountDraft.username.trim(), enabled: accountDraft.enabled };
    if (!accountEditingId.value || accountDraft.password) data.password = accountDraft.password;
    if (accountEditingId.value) await updateJisiluAccount(accountEditingId.value, data);
    else await createJisiluAccount(data);
    const successText = accountEditingId.value ? '账号已更新' : '账号已添加';
    accountDialog.value = false;
    notify(successText);
    if (!(await loadAccounts())) notify('操作已成功，但列表刷新失败，请重新加载', 'bad');
  } catch (e) { notify(accountError(e), 'bad'); }
  finally { accountSaving.value = false; }
}
async function accountAction(id, key, fn) {
  if (isRowBusy(id)) return;
  if (!accountBusy[id]) accountBusy[id] = {};
  accountBusy[id][key] = true;
  try {
    await fn();
    notify({ toggle: '账号启停已更新', delete: '账号已删除', check: '账号检测完成', reset: '账号状态已重置' }[key] || '操作已完成');
    if (!(await loadAccounts())) notify('操作已成功，但列表刷新失败，请重新加载', 'bad');
  }
  catch (e) { notify(accountError(e), 'bad'); }
  finally { accountBusy[id][key] = false; }
}
function isBusy(id, key) { return !!accountBusy[id]?.[key]; }
function isRowBusy(id) { return Object.values(accountBusy[id] || {}).some(Boolean); }
function statusClass(status) {
  return { ready: 'ok', cooldown: 'warning', invalid: 'danger', unknown: 'info', disabled: 'muted' }[status] || 'info';
}
async function toggleAccount(row) {
  await accountAction(row.id, 'toggle', () => updateJisiluAccount(row.id, { enabled: !row.enabled }));
}
async function removeAccount(row) {
  if (!window.confirm(`确定删除账号“${row.name}”吗？删除后不可恢复。`)) return;
  await accountAction(row.id, 'delete', () => deleteJisiluAccount(row.id));
}
async function checkAccount(row) { await accountAction(row.id, 'check', () => checkJisiluAccount(row.id)); }
async function resetAccount(row) { await accountAction(row.id, 'reset', () => resetJisiluAccountStatus(row.id)); }

// 是否 SMTP 已配置完成(锁定态的判定依据)
const smtpConfigured = () => !!items.value.smtp_password?.configured;

async function load() {
  loading.value = true;
  loadError.value = '';
  try {
    const data = await getSettings();
    items.value = data.items || {};
    for (const [key, item] of Object.entries(items.value)) {
      draft[key] = item.value || '';
    }
    // 成功读取后保存不可变快照, 取消编辑恢复到这里(而非内存中的旧值)
    savedSnapshot.value = { ...draft };
    // 已配置过(授权码存在)默认锁定; 保存成功后重新上锁
    smtpLocked.value = smtpConfigured();
    return { ok: true };
  } catch (e) {
    loadError.value = e?.response?.data?.detail || e?.message || '配置加载失败';
    return { ok: false, error: loadError.value };
  } finally {
    loading.value = false;
  }
}

function notify(text, cls = 'ok') {
  message.value = text;
  messageCls.value = cls;
  setTimeout(() => {
    message.value = '';
  }, 5000);
}

async function onSave() {
  saving.value = true;
  try {
    await saveSettings({ ...draft });
    // 保存成功 ≠ 重读成功: 两者分开提示, 避免"已保存"被重读失败掩盖
    const reload = await load();
    if (reload.ok) {
      notify('配置已保存');
    } else {
      notify('配置已保存, 但页面重新读取失败, 请点「重新加载」查看最新配置', 'bad');
    }
  } catch (e) {
    notify(e?.response?.data?.detail || '保存失败', 'bad');
  } finally {
    saving.value = false;
  }
}

function reconfigure() {
  smtpLocked.value = false;
}

// 取消编辑: 丢弃草稿改动, 恢复到最近一次成功读取的快照并重新锁定
function onCancel() {
  if (!savedSnapshot.value) return; // 未成功读取过配置, 不提供取消恢复
  for (const [key, value] of Object.entries(savedSnapshot.value)) {
    draft[key] = value;
  }
  smtpLocked.value = smtpConfigured();
  notify('已放弃修改');
}

async function onTestMail() {
  testing.value = true;
  try {
    await sendTestMail();
    notify('测试邮件已发送, 请查收(注意垃圾箱)');
  } catch (e) {
    notify(e?.response?.data?.detail || '发送失败', 'bad');
  } finally {
    testing.value = false;
  }
}

const EMAIL_KEYS = ['smtp_host', 'smtp_port', 'smtp_ssl', 'smtp_user', 'smtp_password', 'notify_emails'];

onMounted(() => { load(); loadAccounts(); });
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2>系统设置</h2>
      <span v-if="message" class="msg" :class="messageCls">{{ message }}</span>
    </div>

    <!-- 外观(本地偏好, 不走后端配置, 加载失败也不受影响) -->
    <div class="card appearance-card">
      <div class="card-head">
        <div class="card-title">外观</div>
      </div>
      <p class="desc">
        深浅色主题仅保存在当前浏览器, 与邮件等其他设置互不影响;
        「跟随系统」时随操作系统外观自动切换(顶栏 🌙/☀️ 按钮为快速切换)。
      </p>
      <el-radio-group v-model="themeMode">
        <el-radio-button value="light">浅色</el-radio-button>
        <el-radio-button value="dark">深色</el-radio-button>
        <el-radio-button value="auto">跟随系统</el-radio-button>
      </el-radio-group>
    </div>

    <p v-if="loading" class="hint">加载中...</p>

    <!-- 加载失败: 明确报错 + 重试, 不再静默渲染空表单 -->
    <template v-else-if="loadError">
      <p class="hint bad-text">{{ loadError }}</p>
      <div class="actions">
        <button class="btn ghost" @click="load">重新加载</button>
      </div>
    </template>

    <template v-else>
      <!-- 邮件通知配置 -->
      <div class="card">
        <div class="card-head">
          <div class="card-title">邮件通知(SMTP)</div>
          <span v-if="smtpLocked && smtpConfigured()" class="tag ok">已配置</span>
          <button
            v-if="smtpLocked && smtpConfigured()"
            class="btn ghost sm"
            @click="reconfigure"
          >
            重新配置
          </button>
        </div>

        <!-- 锁定态: 只显示摘要, 不可编辑 -->
        <template v-if="smtpLocked && smtpConfigured()">
          <div class="summary">
            <div class="sum-row">
              <span class="sum-label">发件邮箱</span>
              <span class="sum-value">{{ items.smtp_user?.value }}</span>
            </div>
            <div class="sum-row">
              <span class="sum-label">SMTP 服务器</span>
              <span class="sum-value">{{ items.smtp_host?.value }}:{{ items.smtp_port?.value }} ({{ items.smtp_ssl?.value === 'true' ? 'SSL' : 'STARTTLS' }})</span>
            </div>
            <div class="sum-row">
              <span class="sum-label">通知邮箱</span>
              <span class="sum-value">{{ items.notify_emails?.value }}</span>
            </div>
            <div class="sum-row">
              <span class="sum-label">授权码</span>
              <span class="sum-value muted">已保存, 不显示</span>
            </div>
          </div>
          <div class="actions">
            <button
              class="btn ghost"
              :disabled="testing"
              @click="onTestMail"
            >
              {{ testing ? '发送中...' : '发送测试邮件' }}
            </button>
          </div>
        </template>

        <!-- 编辑态: 未配置过, 或点了「重新配置」 -->
        <template v-else>
          <p class="desc">
            用于指标触发、任务失败等系统通知。默认 QQ 邮箱: 服务器 smtp.qq.com / 端口 465(SSL),
            授权码在 QQ 邮箱「设置 - 账户 - POP3/IMAP/SMTP 服务」生成后填入。
          </p>

          <div v-for="key in EMAIL_KEYS" :key="key" class="field">
            <label>
              {{ items[key]?.label }}
              <span v-if="items[key]?.sensitive && items[key]?.configured" class="tag ok">已配置</span>
            </label>
            <template v-if="key === 'smtp_ssl'">
              <select v-model="draft[key]" class="input">
                <option value="true">SSL(465 端口常用)</option>
                <option value="false">STARTTLS(587 端口常用)</option>
              </select>
            </template>
            <input
              v-else
              v-model="draft[key]"
              class="input"
              :type="items[key]?.sensitive ? 'password' : 'text'"
              :placeholder="
                items[key]?.sensitive && items[key]?.configured
                  ? '已配置, 留空保持不变'
                  : key === 'notify_emails'
                    ? '多个邮箱用英文逗号分隔'
                    : ''
              "
              autocomplete="off"
            />
          </div>

          <div class="actions">
            <button class="btn" :disabled="saving" @click="onSave">
              {{ saving ? '保存中...' : '保存配置' }}
            </button>
            <button
              v-if="smtpConfigured()"
              class="btn ghost"
              :disabled="saving"
              @click="onCancel"
            >
              取消
            </button>
            <button
              class="btn ghost"
              :disabled="testing || !(items.smtp_user?.configured || draft.smtp_user)"
              @click="onTestMail"
            >
              {{ testing ? '发送中...' : '发送测试邮件' }}
            </button>
          </div>
        </template>
        <p class="note">
          测试收件人用「通知邮箱」。修改配置需先点「重新配置」解锁; 授权码保存后不回显, 留空即保持原值。
        </p>
      </div>
    </template>

    <div class="card account-card">
      <div class="card-head">
        <div class="card-title">集思录账号池</div>
        <button data-test="add-account" class="btn sm" :disabled="accountsLoading || accounts.some((row) => isRowBusy(row.id))" @click="resetAccountDraft()">添加账号</button>
      </div>
      <div v-if="accountsLoading" class="hint">账号池加载中...</div>
      <template v-else-if="accountsLoadError">
        <p class="hint bad-text">{{ accountsLoadError }}</p>
        <button data-test="retry-accounts" class="btn ghost" @click="loadAccounts">重新加载</button>
      </template>
      <template v-else>
        <div class="account-summary">
          <span>总数 {{ accountSummary.total }}</span><span>启用 {{ accountSummary.enabled }}</span>
          <span>可用 {{ accountSummary.ready }}</span><span>冷却 {{ accountSummary.cooldown }}</span><span>无效 {{ accountSummary.invalid }}</span><span>未知 {{ accountSummary.unknown }}</span><span>停用 {{ accountSummary.disabled }}</span>
          <span>今日请求 {{ accountSummary.today_requests }}</span><span>今日登录 {{ accountSummary.today_logins }}</span>
        </div>
        <div class="account-table-wrap">
          <table class="account-table">
            <thead><tr><th>名称</th><th>用户名</th><th>启用</th><th>状态</th><th>今日调用</th><th>今日登录</th><th>最近成功</th><th>冷却截止</th><th>最近错误</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="row in accounts" :key="row.id">
                <td>{{ row.name }}</td><td>{{ maskUsername(row.username) }}</td>
                <td><button data-test="account-enabled" class="toggle" :class="{ active: row.enabled }" :disabled="isRowBusy(row.id)" @click="toggleAccount(row)">{{ row.enabled ? '是' : '否' }}</button></td>
                <td><span class="tag" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span></td>
                <td>{{ row.daily_request_count || 0 }}</td><td>{{ row.daily_login_count || 0 }}</td>
                <td>{{ formatDate(row.last_success_at) }}</td><td>{{ formatDate(row.cooldown_until) }}</td><td class="error-cell">{{ row.last_error || '—' }}</td>
                <td class="row-actions">
                  <button data-test="edit-account" class="btn ghost sm" :disabled="accountSaving || isRowBusy(row.id)" @click="resetAccountDraft(row)">编辑</button>
                  <button data-test="check-account" class="btn ghost sm" :disabled="isRowBusy(row.id)" @click="checkAccount(row)">{{ isBusy(row.id, 'check') ? '检测中...' : '检测' }}</button>
                  <button data-test="reset-account" class="btn ghost sm" :disabled="isRowBusy(row.id)" @click="resetAccount(row)">重置</button>
                  <button data-test="delete-account" class="btn danger sm" :disabled="isRowBusy(row.id)" @click="removeAccount(row)">删除</button>
                </td>
              </tr>
              <tr v-if="!accounts.length"><td colspan="10" class="empty">暂无账号</td></tr>
            </tbody>
          </table>
        </div>
        <div data-test="account-mobile-list" class="account-mobile-list">
          <article v-for="row in accounts" :key="`mobile-${row.id}`" data-test="account-mobile-card" class="account-mobile-card">
            <div class="mobile-title"><strong>{{ row.name }}</strong><span>{{ maskUsername(row.username) }}</span><span class="tag" :class="statusClass(row.status)">{{ statusLabel(row.status) }}</span></div>
            <div class="mobile-grid"><span>启用：{{ row.enabled ? '是' : '否' }}</span><span>今日请求：{{ row.daily_request_count || 0 }}</span><span>今日登录：{{ row.daily_login_count || 0 }}</span><span>最近成功：{{ formatDate(row.last_success_at) }}</span><span>冷却截止：{{ formatDate(row.cooldown_until) }}</span><span class="mobile-error">错误：{{ row.last_error || '—' }}</span></div>
            <div class="row-actions"><button class="btn ghost sm" :disabled="accountSaving || isRowBusy(row.id)" @click="resetAccountDraft(row)">编辑</button><button class="btn ghost sm" :disabled="isRowBusy(row.id)" @click="toggleAccount(row)">启停</button><button class="btn ghost sm" :disabled="isRowBusy(row.id)" @click="checkAccount(row)">检测</button><button class="btn ghost sm" :disabled="isRowBusy(row.id)" @click="resetAccount(row)">重置</button><button class="btn danger sm" :disabled="isRowBusy(row.id)" @click="removeAccount(row)">删除</button></div>
          </article>
        </div>
      </template>
    </div>

    <div v-if="accountDialog" data-test="account-dialog" class="dialog-mask" @click.self="accountDialog = false">
      <div class="dialog">
        <h3>{{ accountEditingId ? '编辑集思录账号' : '添加集思录账号' }}</h3>
        <div class="field"><label>名称</label><input data-test="account-name" v-model="accountDraft.name" class="input" autocomplete="off" /></div>
        <div class="field"><label>用户名</label><input data-test="account-username" v-model="accountDraft.username" class="input" autocomplete="off" /></div>
        <div class="field"><label>密码</label><input data-test="account-password" v-model="accountDraft.password" class="input" type="password" autocomplete="new-password" :placeholder="accountEditingId ? '留空保持原密码' : ''" /></div>
        <label class="enabled-field"><input type="checkbox" v-model="accountDraft.enabled" /> 启用账号</label>
        <div class="actions"><button data-test="account-submit" class="btn" :disabled="accountSaving || !accountDraft.name.trim() || !accountDraft.username.trim() || (!accountEditingId && !accountDraft.password)" @click="saveAccount">{{ accountSaving ? '保存中...' : '保存' }}</button><button class="btn ghost" :disabled="accountSaving" @click="accountDialog = false">取消</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  max-width: 1100px;
  margin: 0 auto;
  padding: 4px 0 24px;
}

.page-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 12px;
}

.page-head h2 {
  margin: 0;
  font-size: 18px;
}

.msg {
  font-size: 12px;
}

.msg.ok {
  color: #3b6d11;
}

.msg.bad {
  color: #a32d2d;
}

.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.hint.bad-text {
  color: #a32d2d;
}

.card {
  background: var(--el-bg-color);
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 10px;
  padding: 14px 16px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.card-head .card-title {
  margin-bottom: 0;
  flex: 1;
}

/* 锁定态摘要卡 */
.summary {
  border: 1px dashed rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  padding: 4px 12px;
  margin-bottom: 4px;
}

.sum-row {
  display: flex;
  padding: 8px 0;
  font-size: 13px;
  border-top: 1px dashed rgba(148, 163, 184, 0.25);
}

.sum-row:first-child {
  border-top: none;
}

.sum-label {
  width: 110px;
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}

.sum-value {
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.sum-value.muted {
  color: var(--el-text-color-secondary);
}

.btn.sm {
  padding: 3px 12px;
  font-size: 12px;
}

.desc {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.field {
  margin-bottom: 12px;
}

.field label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  margin-bottom: 4px;
}

.tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 999px;
}

.tag.ok {
  background: #eaf3de;
  color: #3b6d11;
}

.tag.none {
  background: #f1efe8;
  color: var(--el-text-color-secondary);
}
.tag.warning { background: #fff3cd; color: #946200; }
.tag.danger { background: #fde2e2; color: #b42318; }
.tag.info { background: #e8f1fb; color: #2563a8; }
.tag.muted { background: #f1f1f1; color: #777; }

.input {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 6px;
  font-size: 13px;
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
}

.input:focus {
  outline: none;
  border-color: #378add;
}

.actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.btn {
  padding: 7px 18px;
  border: none;
  border-radius: 6px;
  background: #3b6d11;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn.ghost {
  background: var(--el-bg-color);
  border: 1px solid rgba(59, 109, 17, 0.5);
  color: #3b6d11;
}

.btn.ghost:disabled {
  color: var(--el-text-color-secondary);
  border-color: rgba(148, 163, 184, 0.4);
  background: var(--el-fill-color-light);
}

.note {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}

.appearance-card {
  margin-bottom: 12px;
}

.account-card { margin-top: 12px; }
.account-summary { display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 4px 0 14px; color: var(--el-text-color-secondary); font-size: 12px; }
.account-table-wrap { overflow-x: auto; }
.account-table { width: 100%; min-width: 980px; border-collapse: collapse; font-size: 12px; }
.account-table th, .account-table td { padding: 8px 6px; border-top: 1px solid rgba(148, 163, 184, 0.2); text-align: left; white-space: nowrap; vertical-align: middle; }
.account-table th { color: var(--el-text-color-secondary); font-weight: 500; }
.account-table .error-cell { max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-actions { display: flex; gap: 5px; }
.btn.danger { background: #b42318; }
.empty { text-align: center !important; color: var(--el-text-color-secondary); }
.dialog-mask { position: fixed; inset: 0; z-index: 20; display: flex; align-items: center; justify-content: center; background: rgba(15, 23, 42, 0.45); padding: 16px; }
.dialog { width: min(420px, 100%); box-sizing: border-box; padding: 18px; border-radius: 10px; background: var(--el-bg-color); box-shadow: 0 12px 36px rgba(0,0,0,.2); }
.dialog h3 { margin: 0 0 16px; font-size: 16px; }
.enabled-field { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--el-text-color-regular); }
.account-mobile-list { display: none; }
.account-mobile-card { border-top: 1px solid rgba(148, 163, 184, 0.2); padding: 12px 0; }
.mobile-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.mobile-title span:not(.tag) { color: var(--el-text-color-secondary); }
.mobile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 12px; margin: 10px 0; color: var(--el-text-color-secondary); font-size: 12px; }
.mobile-error { overflow-wrap: anywhere; word-break: break-word; min-width: 0; }

@media (max-width: 700px) {
  .page { padding-left: 10px; padding-right: 10px; }
  .account-card { padding: 12px; }
  .account-table-wrap { display: none; }
  .account-mobile-list { display: block; }
}

/* ---------- 深色模式微调(品牌绿/状态色只提亮不换色相) ---------- */
html.dark .msg.ok { color: #95d475; }
html.dark .msg.bad,
html.dark .hint.bad-text { color: #f87171; }
html.dark .tag.ok { background: rgba(103, 194, 58, 0.15); color: #95d475; }
html.dark .tag.none { background: rgba(148, 163, 184, 0.12); }
html.dark .tag.warning { background: rgba(245, 158, 11, 0.22); color: #fbbf24; }
html.dark .tag.danger { background: rgba(239, 68, 68, 0.24); color: #fca5a5; }
html.dark .tag.info { background: rgba(59, 130, 246, 0.22); color: #93c5fd; }
html.dark .tag.muted { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }
html.dark .btn.ghost { border-color: rgba(149, 212, 117, 0.5); color: #95d475; }
</style>
