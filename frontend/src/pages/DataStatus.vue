<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue';
import { runJobManually } from '../api';
import {
  getDataManagement,
  getRuns,
  probeIndex,
  addIndex,
  syncIndex,
  setIndexEnabled,
} from '../api/dataManagement';
import { createRequestGuard } from '../utils/requestGuard.js';
import { createAutoRefresh } from '../utils/dataManagementPolling.js';
import {
  indexNeedsAttention,
  filterIndexes,
  capabilitySelectable,
  capabilityStatusLabel,
  STATE_CLASS,
  STATE_LABEL,
  RUN_CLASS,
  RUN_LABEL,
} from '../utils/dataManagementView.js';

// ---- 通用格式化(沿用原页) ----
function fmtDuration(sec) {
  if (sec == null) return '-';
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  return `${m}m ${Math.round(sec % 60)}s`;
}
function fmtTime(iso) {
  if (!iso) return '-';
  return iso.replace('T', ' ').slice(5, 16); // MM-DD HH:mm
}
function fmtRate(rate) {
  if (rate == null) return '-';
  return `${Math.round(rate * 100)}%`;
}
function badgeClass(state) {
  return STATE_CLASS[state] || 'none';
}
function stateLabel(state) {
  return STATE_LABEL[state] || state || '-';
}
function runClass(status) {
  return RUN_CLASS[status] || 'none';
}
function runLabel(status) {
  return RUN_LABEL[status] || status || '-';
}

// ---- tab 与全局状态 ----
const TABS = [
  { key: 'mine', label: '我的数据' },
  { key: 'runs', label: '抓取记录' },
  { key: 'sources', label: '数据源' },
];
const activeTab = ref('mine');

const listGuard = createRequestGuard();
const runsGuard = createRequestGuard();
const probeGuard = createRequestGuard();

const dm = ref(null); // GET /data-management 响应
const listLoading = ref(true);
const listError = ref('');
const notice = ref(''); // 顶部提示(保存/同步结果)

const runs = ref([]);
const runsLoading = ref(false);
const runsError = ref('');
const pendingRuns = reactive({}); // job_id -> true(轮询中)
const runMsg = ref('');

let listTimer = null;
let runsTimer = null;
const syncingCodes = reactive({}); // code -> true(同步中)

// ---- 我的数据: 搜索 / 只看问题 / 展开 ----
const searchText = ref('');
const onlyProblems = ref(false);
const expanded = reactive({}); // code -> true

const visibleIndexes = computed(() =>
  filterIndexes(dm.value?.indexes || [], {
    search: searchText.value,
    onlyProblems: onlyProblems.value,
  })
);
const nonIndexGroups = computed(() => dm.value?.non_index_groups || []);
const sources = computed(() => dm.value?.sources || []);

// 返回 true=本次成功更新, false=请求失败; 供自动刷新决定是否继续
async function loadList() {
  const v = listGuard.next();
  listLoading.value = true;
  listError.value = '';
  try {
    const res = await getDataManagement();
    if (!listGuard.isLatest(v)) return true; // 已被更新请求取代, 视为不再继续
    dm.value = res;
    return true;
  } catch (e) {
    if (!listGuard.isLatest(v)) return true;
    listError.value = e?.response?.data?.detail || e?.message || '加载失败';
    return false;
  } finally {
    if (listGuard.isLatest(v)) listLoading.value = false;
  }
}

async function loadRuns() {
  const v = runsGuard.next();
  runsLoading.value = true;
  runsError.value = '';
  try {
    const res = await getRuns(50);
    if (!runsGuard.isLatest(v)) return;
    runs.value = (res && res.runs) || [];
  } catch (e) {
    if (!runsGuard.isLatest(v)) return;
    runsError.value = e?.response?.data?.detail || e?.message || '加载失败';
  } finally {
    if (runsGuard.isLatest(v)) runsLoading.value = false;
  }
}

// 任务卡片列表来自 /data-management 的 jobs(含 name/schedule/success_rate/next_run_at)
const jobs = computed(() => dm.value?.jobs || []);

// ---- 启停(暂停/恢复) ----
async function toggleEnabled(index) {
  const next = !index.enabled;
  const prev = index.enabled;
  index.enabled = next; // 乐观更新
  try {
    await setIndexEnabled(index.code, next);
  } catch (e) {
    index.enabled = prev; // 回滚
    notice.value = e?.response?.data?.detail || '启停失败';
  }
}

// ---- 同步(初始补抓 / 稍后重试) ----
// POST 成功后: 立即 GET 一次 + 启动自动刷新(慢任务期间持续看到数据变化)
// isDisposed 哨兵(R3-04): POST 是跨 await 的, 用户点完同步立即离开页面时,
// POST 完成的续体会在卸载后执行——此时绝不能再启动刷新或发新请求。
let isDisposed = false;

async function syncOne(code) {
  if (syncingCodes[code]) return;
  syncingCodes[code] = true;
  try {
    await syncIndex(code);
    if (!isDisposed) startSyncPoll(); // 卸载后迟到 POST: 静默放弃
  } catch (e) {
    if (!isDisposed) notice.value = e?.response?.data?.detail || '同步触发失败';
  } finally {
    syncingCodes[code] = false;
  }
}

// 同步后自动刷新(P2-R03): POST 只发一次触发, 之后只 GET 列表刷新展示数据。
// 语义定位是"自动刷新"而非"轮询任务终态"——数据新鲜度(fresh/stale 等)
// 只反映数据新旧, 不能证明本次抓取是否完成; 任务真实结果以抓取记录为准。
// 两条入口(手动「同步」/ 添加指数成功)共用同一控制器实例;
// 实现细节(串行不重叠/墙钟 5 分钟截止/失败即停)见 dataManagementPolling.js。
const syncRefresh = createAutoRefresh({
  load: loadList,
  intervalMs: 3000,
  deadlineMs: 5 * 60 * 1000,
  onStop: (reason) => {
    notice.value =
      reason === 'load_failed'
        ? '自动刷新失败, 可手动点「同步」或刷新页面重试'
        : '已停止自动刷新, 任务结果请查看抓取记录';
  },
});

function startSyncPoll() {
  syncRefresh.start();
}

// ---- 抓取记录: 手动运行(复用现有 /data-status/run) ----
async function triggerRun(jobId) {
  if (pendingRuns[jobId]) return;
  try {
    await runJobManually(jobId);
  } catch (e) {
    if (isDisposed) return; // 卸载后迟到响应: 不再更新界面
    const detail = e?.response?.data?.detail;
    runMsg.value = detail || '触发失败';
    if (e?.response?.status === 409) return; // 运行中, 仍进入轮询
  }
  if (isDisposed) return; // 卸载后不得启动轮询
  pendingRuns[jobId] = true;
  if (!runsLoading.value) await loadRuns();
  if (isDisposed) return; // loadRuns 期间卸载
  startRunsPoll();
}
function startRunsPoll() {
  if (runsTimer) return;
  runsTimer = setInterval(async () => {
    await loadRuns();
    let anyPending = false;
    for (const j of jobs.value) {
      if (pendingRuns[j.job_id] && j.status === 'running') {
        anyPending = true;
      } else if (pendingRuns[j.job_id]) {
        delete pendingRuns[j.job_id];
      }
    }
    if (!anyPending) stopRunsPoll();
  }, 3000);
}
function stopRunsPoll() {
  if (runsTimer) clearInterval(runsTimer);
  runsTimer = null;
}

function onTabChange(tab) {
  if (tab === 'runs' && !runs.value.length && !runsLoading.value) loadRuns();
}

// ---- 添加指数弹窗 ----
const addVisible = ref(false);
const form = reactive({
  code: '',
  source: 'efunds',
  name: '',
  probeToken: null,
  caps: [],
  selected: [],
  probing: false,
  probeError: '',
  saving: false,
  saveMsg: '',
});

function openAdd() {
  form.code = '';
  form.source = 'efunds';
  form.name = '';
  form.probeToken = null;
  form.caps = [];
  form.selected = [];
  form.probing = false;
  form.probeError = '';
  form.saving = false;
  form.saveMsg = '';
  addVisible.value = true;
}

// 改代码/来源: 立即清空探测结果与选择, 并使在途探测失效(防迟到响应覆盖)
function onCodeOrSourceChange() {
  probeGuard.invalidate();
  form.probeToken = null;
  form.caps = [];
  form.selected = [];
  form.probeError = '';
  form.saveMsg = '';
}

async function doProbe() {
  const code = (form.code || '').trim();
  if (!/^\d{6}$/.test(code)) {
    form.probeError = '请输入 6 位指数代码';
    return;
  }
  const v = probeGuard.next();
  form.probing = true;
  form.probeError = '';
  form.caps = [];
  form.selected = [];
  form.probeToken = null;
  form.name = '';
  try {
    const res = await probeIndex(code, form.source);
    if (!probeGuard.isLatest(v)) return; // 迟到响应丢弃
    form.probeToken = res.probe_token || null;
    form.caps = res.capabilities || [];
    // 默认全选所有「支持」项, 不支持的(error/unavailable)不勾
    form.selected = (res.capabilities || [])
      .filter((c) => capabilitySelectable(c))
      .map((c) => c.key);
    if (res.name) form.name = res.name;
  } catch (e) {
    if (!probeGuard.isLatest(v)) return;
    form.probeError = e?.response?.data?.detail || '探测失败, 可重试';
  } finally {
    if (probeGuard.isLatest(v)) form.probing = false;
  }
}

// quote 标签随来源变化: tencent->日K, efunds->收盘价
function capLabel(cap) {
  if (cap.key === 'quote') return form.source === 'tencent' ? '日K' : '收盘价';
  if (cap.key === 'valuation') return 'PE / PB / 股息率';
  if (cap.key === 'dividend') return '股息率';
  return cap.label || cap.key;
}
function isChecked(key) {
  return form.selected.includes(key);
}
function toggleCap(key) {
  const cap = form.caps.find((c) => c.key === key);
  if (!capabilitySelectable(cap)) return; // 仅 available 可勾选
  const i = form.selected.indexOf(key);
  if (i >= 0) form.selected.splice(i, 1);
  else form.selected.push(key);
}

async function saveIndex() {
  if (!/^\d{6}$/.test((form.code || '').trim())) {
    form.saveMsg = '请输入 6 位指数代码';
    return;
  }
  if (!form.probeToken) {
    form.saveMsg = '请先完成探测';
    return;
  }
  const datasets = form.selected.filter((k) => {
    const cap = form.caps.find((c) => c.key === k);
    return capabilitySelectable(cap);
  });
  if (!datasets.length) {
    form.saveMsg = '请至少勾选一项可用数据';
    return;
  }
  form.saving = true;
  form.saveMsg = '';
  try {
    const res = await addIndex({
      code: form.code.trim(),
      name: form.name || form.code.trim(),
      source: form.source,
      datasets,
      probe_token: form.probeToken,
    });
    // res: { code, status:'saved', sync_status:'started'|'busy'|'failed', message }
    if (res.status === 'saved') {
      if (isDisposed) return; // 卸载后迟到保存: 不再更新界面/启动刷新
      await loadList();
      if (isDisposed) return; // loadList 期间卸载
      const sync = res.sync_status;
      if (sync === 'busy' || sync === 'failed') {
        // 名单已保存但抓取未完成: 明确说明, 不显示全部成功
        form.saveMsg =
          (res.message || '指数已加入名单') + '，但数据抓取尚未完成，可在列表稍后点「同步」重试。';
      } else {
        form.saveMsg = '已保存，正在初始抓取…';
        addVisible.value = false;
        startSyncPoll(); // 与手动同步共用同一自动刷新控制器
      }
    } else {
      form.saveMsg = res.message || '保存未确认';
    }
  } catch (e) {
    form.saveMsg = e?.response?.data?.detail || '保存失败';
  } finally {
    form.saving = false;
  }
}

// ---- 生命周期 ----
onMounted(() => {
  loadList();
});
onBeforeUnmount(() => {
  isDisposed = true; // R3-04: 卸载后所有 await 续体不得启动刷新/发请求
  listGuard.invalidate();
  runsGuard.invalidate();
  probeGuard.invalidate();
  syncRefresh.dispose(); // 永久失效: 之后任何 start(含迟到调用)被忽略
  stopRunsPoll();
  if (listTimer) clearInterval(listTimer);
});
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2>数据管理</h2>
      <el-tabs v-model="activeTab" class="tabs" @tab-change="onTabChange">
        <el-tab-pane v-for="t in TABS" :key="t.key" :name="t.key" :label="t.label" />
      </el-tabs>
    </div>

    <p v-if="notice" class="notice-bar">{{ notice }}</p>

    <!-- ============ 我的数据 ============ -->
    <template v-if="activeTab === 'mine'">
      <div class="toolbar">
        <el-input
          v-model="searchText"
          placeholder="搜索代码 / 名称"
          clearable
          class="search"
          size="small"
        />
        <label class="chk">
          <input type="checkbox" v-model="onlyProblems" /> 只看问题
        </label>
        <el-button type="primary" size="small" class="add-btn" @click="openAdd">
          + 添加指数
        </el-button>
      </div>

      <p v-if="listLoading" class="hint">加载中…</p>
      <p v-else-if="listError" class="hint bad-text">{{ listError }}</p>

      <template v-else>
        <!-- 指数卡片: 一指数一次 -->
        <div v-for="idx in visibleIndexes" :key="idx.code" class="card idx-card">
          <div class="idx-head">
            <div class="idx-title" @click="expanded[idx.code] = !expanded[idx.code]">
              <span class="caret">{{ expanded[idx.code] ? '▾' : '▸' }}</span>
              <span class="name">{{ idx.name || idx.code }}</span>
              <span class="code">{{ idx.code }}</span>
            </div>
            <div class="idx-actions">
              <el-button
                size="small"
                text
                :loading="syncingCodes[idx.code]"
                @click="syncOne(idx.code)"
              >
                同步
              </el-button>
              <label class="switch">
                <input type="checkbox" :checked="idx.enabled !== false" @change="toggleEnabled(idx)" />
                启用
              </label>
            </div>
          </div>

          <div class="ds-list">
            <div v-for="d in idx.datasets" :key="d.key" class="ds-row">
              <span class="ds-name">{{ d.label || d.key }}</span>
              <span class="ds-src">{{ d.source || '-' }}</span>
              <span class="ds-date">{{ d.latest_date || '暂无' }}</span>
              <span class="badge sm" :class="badgeClass(d.state)">{{ stateLabel(d.state) }}</span>
            </div>
            <div v-if="!idx.datasets || !idx.datasets.length" class="ds-empty">暂无数据集</div>
          </div>

          <div v-if="expanded[idx.code]" class="ds-detail">
            <div v-for="d in idx.datasets" :key="'e' + d.key" class="ds-row sub">
              <span class="ds-name">{{ d.label || d.key }}</span>
              <span class="ds-src">起始 {{ d.first_date || '-' }}</span>
              <span class="ds-src">条目 {{ d.count ?? '-' }}</span>
            </div>
          </div>
        </div>

        <p v-if="!visibleIndexes.length" class="hint">无匹配指数</p>

        <!-- 非指数分组(转债/国债等) -->
        <template v-if="nonIndexGroups.length">
          <div class="group-title">其他数据</div>
          <div class="card">
            <div v-for="g in nonIndexGroups" :key="g.label" class="ds-row">
              <span class="ds-name">{{ g.label }}</span>
              <span class="ds-date">{{ g.latest_date || '暂无' }}</span>
              <span class="badge sm" :class="badgeClass(g.state)">{{ stateLabel(g.state) }}</span>
            </div>
          </div>
        </template>
      </template>
    </template>

    <!-- ============ 抓取记录 ============ -->
    <template v-else-if="activeTab === 'runs'">
      <p class="hint sub">每任务最近一次运行结果(非完整历史)</p>
      <p v-if="runsLoading" class="hint">加载中…</p>
      <p v-else-if="runsError" class="hint bad-text">{{ runsError }}</p>
      <template v-else>
        <div v-for="j in jobs" :key="j.job_id" class="card job">
          <div class="job-line">
            <span class="job-name">{{ j.name || j.job_id }}</span>
            <span class="badge" :class="runClass(j.status)">{{ runLabel(j.status) }}</span>
            <el-button size="small" text :disabled="!!pendingRuns[j.job_id]" @click="triggerRun(j.job_id)">
              {{ pendingRuns[j.job_id] ? '运行中…' : '手动运行' }}
            </el-button>
          </div>
          <div class="job-meta">
            <span v-if="j.schedule">计划 {{ j.schedule }}</span>
            <span>最近 {{ fmtTime(j.started_at) }}</span>
            <span>耗时 {{ fmtDuration(j.duration_sec) }}</span>
            <span v-if="j.success_rate != null">成功率 {{ fmtRate(j.success_rate) }}</span>
          </div>
          <p v-if="j.error" class="bad-text job-error">
            关联任务失败: {{ j.error }}
          </p>
        </div>

        <div class="group-title">最近 50 条日志</div>
        <div class="card">
          <div v-for="(r, i) in runs" :key="i" class="log-row">
            <span class="log-job">{{ r.job_id }}</span>
            <span class="badge sm" :class="runClass(r.status)">{{ runLabel(r.status) }}</span>
            <span class="log-time">{{ fmtTime(r.started_at) }}</span>
            <span class="log-dur">{{ fmtDuration(r.duration_sec) }}</span>
          </div>
          <p v-if="!runs.length" class="hint">暂无记录</p>
        </div>
        <p v-if="runMsg" class="hint bad-text">{{ runMsg }}</p>
      </template>
    </template>

    <!-- ============ 数据源 ============ -->
    <template v-else-if="activeTab === 'sources'">
      <p class="hint sub">各数据源能力 / 关联任务 / 接入指数数(只读)</p>
      <div v-for="s in sources" :key="s.id" class="card src-card">
        <div class="idx-head">
          <div class="idx-title">
            <span class="name">{{ s.name || s.id }}</span>
            <span class="code">{{ s.id }}</span>
          </div>
          <span class="src-count">接入 {{ s.index_count ?? '-' }} 只</span>
        </div>
        <div class="ds-list">
          <div v-for="c in s.capabilities || []" :key="c.key" class="ds-row">
            <span class="ds-name">{{ c.label || c.key }}</span>
            <span class="ds-src">关联任务 {{ (s.job_ids || []).join(', ') || '-' }}</span>
            <span v-if="s.next_run_at" class="ds-date">下次 {{ fmtTime(s.next_run_at) }}</span>
          </div>
          <div v-if="!s.capabilities || !s.capabilities.length" class="ds-empty">无能力信息</div>
        </div>
      </div>
      <p v-if="!sources.length" class="hint">暂无数据源</p>
    </template>

    <!-- ============ 添加指数弹窗 ============ -->
    <el-dialog v-model="addVisible" title="添加指数" width="420px" align-center>
      <div class="dlg">
        <div class="dlg-row">
          <label>代码</label>
          <el-input v-model="form.code" maxlength="6" placeholder="6 位指数代码" size="small" @input="onCodeOrSourceChange" />
        </div>
        <div class="dlg-row">
          <label>来源</label>
          <el-select v-model="form.source" size="small" @change="onCodeOrSourceChange">
            <el-option label="易方达(eFunds)" value="efunds" />
            <el-option label="腾讯(Tencent)" value="tencent" />
          </el-select>
        </div>
        <el-button size="small" :loading="form.probing" @click="doProbe">检查</el-button>

        <p v-if="form.probeError" class="bad-text dlg-msg">{{ form.probeError }}</p>

        <div v-if="form.caps.length" class="caps">
          <div v-for="c in form.caps" :key="c.key" class="cap-row">
            <label :class="{ disabled: !capabilitySelectable(c) }">
              <input
                type="checkbox"
                :checked="isChecked(c.key)"
                :disabled="!capabilitySelectable(c)"
                @change="toggleCap(c.key)"
              />
              {{ capLabel(c) }}
            </label>
            <span class="cap-st" :class="{ err: c.status === 'error', off: c.status === 'unavailable' }">
              {{ capabilityStatusLabel(c.status) }}
              <span v-if="c.message && c.status !== 'available'">：{{ c.message }}</span>
            </span>
          </div>
          <div v-for="c in form.caps" v-show="c.overview && c.status === 'available'" :key="c.key + '-ov'" class="cap-ov">
            {{ capLabel(c) }}：{{ c.overview.first_date.slice(0, 4) }} 年起 · 最新 {{ c.overview.latest_date.slice(5) }} · 共 {{ c.overview.count }} 条
          </div>
          <p class="hint dlg-tip">error 表示检查失败(可重试), 不代表来源不支持; 仅「支持」项可勾选。</p>
        </div>

        <div v-if="form.probeToken" class="dlg-row">
          <label>名称</label>
          <el-input v-model="form.name" placeholder="指数名称(可改)" size="small" />
        </div>

        <p v-if="form.saveMsg" class="dlg-msg" :class="{ warn: form.saveMsg.includes('尚未完成') }">
          {{ form.saveMsg }}
        </p>
      </div>
      <template #footer>
        <el-button size="small" @click="addVisible = false">关闭</el-button>
        <el-button type="primary" size="small" :loading="form.saving" :disabled="!form.probeToken" @click="saveIndex">
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page {
  max-width: 820px;
  margin: 0 auto;
  padding: 4px 0 24px;
}
.page-head {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.page-head h2 {
  margin: 0;
  font-size: 18px;
}
.tabs {
  flex: 1;
  min-width: 240px;
}
.notice-bar {
  background: #eaf3de;
  border: 1px solid rgba(59, 109, 17, 0.3);
  color: #3b6d11;
  font-size: 12px;
  border-radius: 8px;
  padding: 6px 10px;
  margin: 0 0 10px;
}
.hint {
  color: #9ca3af;
  font-size: 13px;
}
.hint.sub {
  margin: 0 0 10px;
}
.bad-text {
  color: #a32d2d;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.search {
  width: 200px;
}
.chk {
  font-size: 12px;
  color: #4b5563;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.add-btn {
  margin-left: auto;
}
.card {
  background: #fff;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.idx-card .idx-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.idx-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  min-width: 0;
}
.caret {
  color: #9ca3af;
  font-size: 12px;
}
.name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}
.code {
  font-size: 12px;
  color: #9ca3af;
}
.idx-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.switch {
  font-size: 12px;
  color: #4b5563;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.ds-list {
  margin-top: 8px;
}
.ds-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 0;
  border-top: 1px dashed rgba(148, 163, 184, 0.22);
  font-size: 12px;
  color: #4b5563;
}
.ds-row.sub {
  color: #9ca3af;
  font-size: 11px;
}
.ds-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ds-src {
  width: 110px;
  color: #6b7280;
}
.ds-date {
  width: 96px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.ds-empty {
  font-size: 12px;
  color: #9ca3af;
  padding: 4px 0;
}
.ds-detail {
  margin-top: 4px;
}
.group-title {
  font-size: 13px;
  font-weight: 500;
  color: #374151;
  margin: 14px 0 8px;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  white-space: nowrap;
}
.badge.sm {
  padding: 1px 7px;
  font-size: 10px;
}
.badge.ok {
  background: #eaf3de;
  color: #3b6d11;
}
.badge.warn {
  background: #faeeda;
  color: #854f0b;
}
.badge.bad {
  background: #fcebeb;
  color: #a32d2d;
}
.badge.none {
  background: #f1efe8;
  color: #6b7280;
}
.badge.run {
  background: #e6f1fb;
  color: #185fa5;
}
.job-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.job-name {
  font-size: 13px;
  font-weight: 500;
}
.job-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 5px;
  font-size: 11px;
  color: #6b7280;
}
.job-error {
  margin: 6px 0 0;
  font-size: 11px;
}
.log-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  border-top: 1px dashed rgba(148, 163, 184, 0.22);
  font-size: 12px;
  color: #4b5563;
}
.log-job {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.log-time {
  width: 96px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #6b7280;
}
.log-dur {
  width: 70px;
  text-align: right;
  color: #6b7280;
}
.src-card .src-count {
  font-size: 12px;
  color: #6b7280;
}
.dlg {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.dlg-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.dlg-row label {
  width: 44px;
  font-size: 13px;
  color: #4b5563;
  flex-shrink: 0;
}
.dlg-row :deep(.el-input),
.dlg-row :deep(.el-select) {
  flex: 1;
}
.caps {
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 8px;
  padding: 8px 10px;
}
.cap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.cap-row label.disabled {
  color: #9ca3af;
}
.cap-st {
  font-size: 11px;
  color: #3b6d11;
}
.cap-st.err {
  color: #a32d2d;
}
.cap-st.off {
  color: #9ca3af;
}
.cap-ov {
  font-size: 11px;
  color: #6b7280;
  padding: 0 0 4px 24px;
}
.dlg-tip {
  font-size: 11px;
  margin: 4px 0 0;
}
.dlg-msg {
  font-size: 12px;
  color: #3b6d11;
  background: #eaf3de;
  border-radius: 6px;
  padding: 6px 8px;
}
.dlg-msg.warn {
  color: #854f0b;
  background: #faeeda;
}

/* 移动端: 工具栏换行, 列宽收窄 */
@media (max-width: 767px) {
  .toolbar {
    gap: 8px;
  }
  .search {
    width: 100%;
  }
  .add-btn {
    margin-left: 0;
  }
  .ds-src {
    width: 88px;
    font-size: 11px;
  }
  .ds-date {
    width: 82px;
  }
}
</style>
