<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { getSettings, saveSettings, sendTestMail } from '../api';
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

onMounted(load);
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
  </div>
</template>

<style scoped>
.page {
  max-width: 640px;
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

/* ---------- 深色模式微调(品牌绿/状态色只提亮不换色相) ---------- */
html.dark .msg.ok { color: #95d475; }
html.dark .msg.bad,
html.dark .hint.bad-text { color: #f87171; }
html.dark .tag.ok { background: rgba(103, 194, 58, 0.15); color: #95d475; }
html.dark .tag.none { background: rgba(148, 163, 184, 0.12); }
html.dark .btn.ghost { border-color: rgba(149, 212, 117, 0.5); color: #95d475; }
</style>
