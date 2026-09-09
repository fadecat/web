<script setup>
import { ref, reactive, onMounted } from 'vue';
import { getSettings, saveSettings, sendTestMail } from '../api';

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
    // 已配置过(授权码存在)默认锁定; 保存成功后重新上锁
    smtpLocked.value = smtpConfigured();
  } catch (e) {
    loadError.value = e?.response?.data?.detail || e?.message || '配置加载失败';
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
    await load(); // 重新拉取, 敏感项显示回脱敏状态, 且恢复锁定
    notify('配置已保存');
  } catch (e) {
    notify(e?.response?.data?.detail || '保存失败', 'bad');
  } finally {
    saving.value = false;
  }
}

function reconfigure() {
  smtpLocked.value = false;
}

// 取消编辑: 丢弃草稿改动, 恢复到服务端当前值并重新锁定
function onCancel() {
  for (const [key, item] of Object.entries(items.value)) {
    draft[key] = item.value || '';
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
  color: #9ca3af;
  font-size: 13px;
}

.hint.bad-text {
  color: #a32d2d;
}

.card {
  background: #fff;
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
  color: #6b7280;
  flex-shrink: 0;
}

.sum-value {
  color: #111827;
  word-break: break-all;
}

.sum-value.muted {
  color: #9ca3af;
}

.btn.sm {
  padding: 3px 12px;
  font-size: 12px;
}

.desc {
  margin: 0 0 12px;
  font-size: 12px;
  color: #6b7280;
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
  color: #4b5563;
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
  color: #9ca3af;
}

.input {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 6px;
  font-size: 13px;
  background: #fff;
  color: #111827;
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
  background: #fff;
  border: 1px solid rgba(59, 109, 17, 0.5);
  color: #3b6d11;
}

.btn.ghost:disabled {
  color: #9ca3af;
  border-color: rgba(148, 163, 184, 0.4);
  background: #f8f9fb;
}

.note {
  margin: 10px 0 0;
  font-size: 11px;
  color: #9ca3af;
  line-height: 1.6;
}
</style>
