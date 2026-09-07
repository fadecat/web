<script setup>
import { ref, onMounted, onUnmounted, reactive } from 'vue';
import { getDataStatus, runJobManually } from '../api';

const loading = ref(true);
const error = ref('');
const status = ref(null);

// 手动运行状态: jobId -> 触发前的最近一次 started_at(用于检测新一轮运行完成)
const pendingJobs = reactive({});
const prevStarted = {};
let pollTimer = null;

const FRESHNESS = {
  fresh: { label: '已更新', cls: 'ok' },
  stale: { label: '滞后', cls: 'warn' },
  lagging: { label: '滞后多日', cls: 'bad' },
  no_data: { label: '暂无数据', cls: 'none' },
};

const RUN_STATUS = {
  success: { label: '成功', cls: 'ok' },
  partial: { label: '部分成功', cls: 'warn' },
  failed: { label: '失败', cls: 'bad' },
  never: { label: '暂无记录', cls: 'none' },
};

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

async function refresh() {
  try {
    status.value = await getDataStatus();
    error.value = '';
  } catch (e) {
    error.value = e?.message || '加载失败';
  }
  // 完成检测: 某任务出现了新的运行记录且已结束 → 清除运行中标记
  let stillPending = false;
  for (const j of status.value?.jobs || []) {
    if (!pendingJobs[j.job_id]) continue;
    const isNewRun = j.started_at && j.started_at !== prevStarted[j.job_id];
    if (isNewRun && j.finished_at) {
      delete pendingJobs[j.job_id];
    } else {
      stillPending = true;
    }
  }
  if (!stillPending && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function triggerJob(j) {
  if (pendingJobs[j.job_id]) return;
  try {
    await runJobManually(j.job_id);
  } catch (e) {
    const detail = e?.response?.data?.detail;
    error.value = detail || '触发失败';
    return;
  }
  prevStarted[j.job_id] = j.started_at;
  pendingJobs[j.job_id] = true;
  await refresh();
  if (!pollTimer) pollTimer = setInterval(refresh, 3000);
}

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});

onMounted(async () => {
  try {
    status.value = await getDataStatus();
  } catch (e) {
    error.value = e?.message || '加载失败';
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h2>数据状态</h2>
      <span v-if="status" class="meta">
        预期数据日期 {{ status.expected_date }} · 生成于 {{ fmtTime(status.generated_at) }}
      </span>
    </div>

    <p v-if="loading" class="hint">加载中...</p>
    <p v-else-if="error" class="hint bad-text">{{ error }}</p>

    <template v-else>
      <!-- 数据新鲜度: 分组展开到逐指数明细 -->
      <div class="card">
        <div class="card-title">数据新鲜度</div>
        <div class="row row-head">
          <span class="col-name">数据集 / 指数</span>
          <span class="col-first">起始</span>
          <span class="col-count">条目</span>
          <span class="col-date">最新</span>
          <span class="col-state">状态</span>
        </div>
        <div v-for="g in status.datasets" :key="g.name" class="group">
          <div class="group-head">
            <span class="col-name">{{ g.name }}</span>
            <span class="col-first"></span>
            <span class="col-count"></span>
            <span class="col-date"></span>
            <span class="col-state">
              <span class="badge" :class="FRESHNESS[g.state]?.cls">
                {{ FRESHNESS[g.state]?.label || g.state }}
              </span>
            </span>
          </div>
          <div v-if="!g.entities.length" class="row empty">
            <span class="col-name empty-text">暂无数据</span>
          </div>
          <div v-for="e in g.entities" :key="e.label" class="row entity">
            <span class="col-name">{{ e.label }}</span>
            <span class="col-first">{{ e.first_date || '-' }}</span>
            <span class="col-count">{{ e.count ?? '-' }}{{ e.unit }}</span>
            <span class="col-date">{{ e.latest_date || '-' }}</span>
            <span class="col-state">
              <span class="badge sm" :class="FRESHNESS[e.state]?.cls">
                {{ FRESHNESS[e.state]?.label || e.state }}
              </span>
            </span>
          </div>
        </div>
        <p class="note">
          周末与法定节假日数据源停更,预期日期已自动对齐交易日,不算滞后。转债类表条目按天数计。
        </p>
      </div>

      <!-- 定时任务 -->
      <div class="card">
        <div class="card-title">定时任务</div>
        <div v-for="j in status.jobs" :key="j.job_id" class="job">
          <div class="job-line">
            <span class="job-name">{{ j.name }}</span>
            <span class="badge" :class="RUN_STATUS[j.status]?.cls">
              {{ RUN_STATUS[j.status]?.label || j.status }}
            </span>
            <button
              class="run-btn"
              :class="{ running: pendingJobs[j.job_id] }"
              :disabled="pendingJobs[j.job_id]"
              @click="triggerJob(j)"
            >
              {{ pendingJobs[j.job_id] ? '运行中...' : '手动运行' }}
            </button>
          </div>
          <div v-if="pendingJobs[j.job_id]" class="job-meta running-hint">
            后台执行中, 完成后此处自动刷新
          </div>
          <div class="job-meta">
            <span>{{ j.schedule }}</span>
            <span>最近运行 {{ fmtTime(j.started_at) }}</span>
            <span>耗时 {{ fmtDuration(j.duration_sec) }}</span>
            <span>近{{ j.run_count || '-' }}次成功 {{ fmtRate(j.success_rate) }}</span>
          </div>
          <details v-if="j.summary" class="detail">
            <summary>运行摘要</summary>
            <pre>{{ j.summary }}</pre>
          </details>
          <p v-if="j.error" class="bad-text job-error">{{ j.error }}</p>
        </div>
        <p class="note">
          徽标 = 该任务最近一次运行的记录结果(运行记录自 2026-09-07
          上线起积累,之前的运行无记录)。任务每次运行自动记录,失败时可在服务器日志中查错误详情;交易日错过触发
          1 小时内重启服务会自动补跑。
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 760px;
  margin: 0 auto;
  padding: 4px 0 24px;
}

.page-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.page-head h2 {
  margin: 0;
  font-size: 18px;
}

.meta {
  font-size: 12px;
  color: #9ca3af;
}

.hint {
  color: #9ca3af;
  font-size: 13px;
}

.card {
  background: #fff;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 14px;
}

.card-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 10px;
}

.row {
  display: flex;
  align-items: center;
  padding: 6px 0;
  border-top: 1px dashed rgba(148, 163, 184, 0.25);
  font-size: 13px;
}

.row-head {
  border-top: none;
  font-size: 11px;
  color: #9ca3af;
  padding-bottom: 2px;
}

.group {
  border-top: 1px solid rgba(148, 163, 184, 0.3);
}

.group:first-of-type {
  border-top: none;
}

.group-head {
  display: flex;
  align-items: center;
  padding: 8px 0 4px;
  font-size: 13px;
  font-weight: 500;
}

.group-head + .row {
  border-top: none;
}

.row.entity {
  padding-left: 12px;
  font-size: 12px;
  color: #4b5563;
}

.row.empty {
  padding-left: 12px;
  padding-bottom: 8px;
}

.empty-text {
  font-size: 12px;
  color: #9ca3af;
}

.col-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-first {
  width: 92px;
  font-variant-numeric: tabular-nums;
  color: #6b7280;
}

.col-count {
  width: 72px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #6b7280;
}

.col-date {
  width: 92px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: #4b5563;
}

.col-state {
  width: 84px;
  text-align: right;
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

.note {
  margin: 10px 0 0;
  font-size: 11px;
  color: #9ca3af;
  line-height: 1.6;
}

.job {
  padding: 10px 0;
  border-top: 1px dashed rgba(148, 163, 184, 0.25);
}

.job:first-of-type {
  border-top: none;
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

.run-btn {
  padding: 3px 12px;
  border: 1px solid rgba(59, 109, 17, 0.4);
  border-radius: 6px;
  background: #eaf3de;
  color: #3b6d11;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.run-btn:hover:not(:disabled) {
  background: #d8ebc4;
}

.run-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.run-btn.running {
  background: #faeeda;
  border-color: rgba(133, 79, 11, 0.35);
  color: #854f0b;
}

.running-hint {
  color: #854f0b !important;
}

.job-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin-top: 5px;
  font-size: 11px;
  color: #6b7280;
}

.detail {
  margin-top: 6px;
}

.detail summary {
  font-size: 11px;
  color: #9ca3af;
  cursor: pointer;
}

.detail pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  background: #f8f9fb;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  color: #4b5563;
}

.bad-text {
  color: #a32d2d;
}

.job-error {
  margin: 6px 0 0;
  font-size: 11px;
}

/* 移动端: 收窄列宽, 隐藏起始日期列 */
@media (max-width: 767px) {
  .col-first {
    display: none;
  }

  .col-count {
    width: 58px;
    font-size: 11px;
  }

  .col-date {
    width: 82px;
    font-size: 11px;
  }

  .col-state {
    width: 72px;
  }
}
</style>
