<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { listAssets, refreshAsset } from '../api/portfolio';
import AddAssetDialog from '../components/portfolio/AddAssetDialog.vue';
import {
  computePortfolioStart, formatCount, formatRange, priceBasisLabel, rowStatusOf,
  securityTypeLabel, usedByText,
} from '../utils/portfolioAssets.mjs';

// P0 宿主页: 标的注册与按需抓取。组合构建/回测属后续阶段, 页脚常驻说明。

const rows = ref([]);
const loading = ref(true);
const error = ref('');
const dialogVisible = ref(false);
const startNotice = ref(null); // 起点被推后时的警示条
const refreshingId = ref(null);

// 状态口径只应有一份实现(与组合详情页共用 rowStatusOf):
// 「有行数但 last_sync_status 为空」是**就绪**, 不是「—」—— 那是种子/脚本写入的标的。
const statusOf = (row) => rowStatusOf(row);

// 组合共同起点 = 所有标的 first_date 里最晚的那个
const startInfo = computed(() => computePortfolioStart(rows.value));
const startText = computed(() => {
  if (!rows.value.length) return '—';
  return startInfo.value.pending ? '待抓取' : (startInfo.value.startDate || '待抓取');
});
const pendingCount = computed(
  () => rows.value.filter((row) => !row.first_date).length,
);
const hasRunning = computed(() => rows.value.some((row) => row.last_sync_status === 'running'));

const fetchAssets = async ({ silent = false } = {}) => {
  if (!silent) {
    loading.value = true;
    error.value = '';
  }
  try {
    const list = await listAssets();
    rows.value = Array.isArray(list) ? list : [];
  } catch (err) {
    if (!silent) error.value = '标的库接口暂时不可用';
  } finally {
    if (!silent) loading.value = false;
  }
};

// 注册返回后该行是「同步中」: 轮询到状态收敛为止(仅列表级刷新, 不影响其他行)
const POLL_INTERVAL_MS = 5000;
let pollTimer = null;
const syncPolling = () => {
  if (hasRunning.value && pollTimer === null) {
    pollTimer = setInterval(() => fetchAssets({ silent: true }), POLL_INTERVAL_MS);
  } else if (!hasRunning.value && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
};
watch(hasRunning, syncPolling);

const refreshOne = async (row) => {
  if (refreshingId.value !== null) return;
  refreshingId.value = row.id;
  try {
    await refreshAsset(row.id);
    await fetchAssets({ silent: true });
  } catch (err) {
    ElMessage.error(err?.response?.status === 404 ? '标的已不存在' : '补抓失败，请稍后重试');
  } finally {
    refreshingId.value = null;
  }
};

const onAdded = () => {
  dialogVisible.value = false;
  ElMessage.success('已添加，后台抓取中');
  fetchAssets();
};

// 新增标的把共同起点推后时展示(父页面职责: 只渲染, 不做计算)
const onStartChange = (payload) => {
  if (!payload?.newStart || payload.newStart === payload.oldStart) return;
  startNotice.value = payload;
};

const dismissNotice = () => {
  startNotice.value = null;
};

onMounted(fetchAssets);
onBeforeUnmount(() => {
  if (pollTimer !== null) clearInterval(pollTimer);
});
</script>

<template>
  <section class="assets-page">
    <div class="assets-heading">
      <div>
        <h1>标的库</h1>
        <p>组合实验室 · 标的注册与按需抓取（股票 / ETF）</p>
      </div>
      <el-button type="primary" size="small" @click="dialogVisible = true">+ 添加标的</el-button>
    </div>

    <div class="assets-start">
      <span>组合起点</span>
      <strong>{{ startText }}</strong>
      <span class="assets-start-note">
        共同起点 = 各标的数据可得区间的最晚起点
        <template v-if="pendingCount">（{{ pendingCount }} 个标的尚未抓取）</template>
      </span>
    </div>

    <el-alert
      v-if="startNotice"
      class="assets-notice"
      type="warning"
      show-icon
      :closable="true"
      @close="dismissNotice"
    >
      <template #title>
        <span>{{ startNotice.title }}</span>
        <span class="assets-notice-detail">{{ startNotice.detail }}</span>
      </template>
    </el-alert>

    <div v-if="error" class="assets-state error-state">
      <p>{{ error }}</p>
      <el-button size="small" type="primary" @click="fetchAssets">重试</el-button>
    </div>
    <div v-else-if="loading && !rows.length" class="assets-state">正在加载标的库…</div>
    <div v-else-if="!rows.length" class="assets-state">
      <strong>还没有标的</strong>
      <p>点击右上角「+ 添加标的」注册第一只股票或 ETF。</p>
    </div>
    <div v-else class="assets-table-wrap page-card">
      <el-table :data="rows" row-key="id" size="small" stripe table-layout="fixed">
        <el-table-column label="标的" min-width="160">
          <template #default="{ row }">
            <strong class="asset-name">{{ row.name || '—' }}</strong>
            <small class="asset-symbol">{{ row.symbol }}</small>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }">{{ securityTypeLabel(row.security_type) }}</template>
        </el-table-column>
        <el-table-column label="复权口径" width="120" align="center">
          <template #default="{ row }">{{ priceBasisLabel(row.price_basis) }}</template>
        </el-table-column>
        <el-table-column label="数据区间" min-width="200" align="center">
          <template #default="{ row }">
            {{ formatRange(row.first_date, row.last_date, row.row_count) }}
          </template>
        </el-table-column>
        <el-table-column label="行数" width="90" align="right">
          <template #default="{ row }">{{ formatCount(row.row_count) }}</template>
        </el-table-column>
        <el-table-column label="同步状态" width="120" align="center">
          <template #default="{ row }">
            <span v-if="statusOf(row).key === 'running'" class="status-running">
              <span class="status-spinner" />{{ statusOf(row).label }}
            </span>
            <span v-else-if="statusOf(row).key === 'ready'" class="status-success">
              {{ statusOf(row).label }}
            </span>
            <el-tooltip
              v-else-if="statusOf(row).tone === 'down'"
              :content="statusOf(row).detail || '同步失败'"
              placement="top"
              :show-after="120"
            >
              <span class="status-failed">{{ statusOf(row).label }}</span>
            </el-tooltip>
            <span v-else class="status-unknown">—</span>
          </template>
        </el-table-column>
        <!-- multi-portfolio §六-1: 标的是**全局注册表**, 停用/删除前要知道被谁用着 -->
        <el-table-column label="被组合使用" width="160" align="center">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.used_by_count"
              :content="usedByText(row)"
              placement="top"
              :show-after="120"
            >
              <span class="used-by">{{ row.used_by_count }} 个组合</span>
            </el-tooltip>
            <span v-else class="status-unknown">未被使用</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" align="center">
          <template #default="{ row }">
            <el-button
              link
              size="small"
              :type="statusOf(row).retry ? 'danger' : 'primary'"
              :loading="refreshingId === row.id"
              @click="refreshOne(row)"
            >
              {{ statusOf(row).retry ? '重试' : '刷新' }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>还没有标的</template>
      </el-table>
    </div>

    <footer class="assets-footer">
      <p>本期为「标的注册与按需抓取」：注册后在后台抓取历史行情，组合与回测在后续阶段开放。</p>
      <p>不同复权口径的曲线不可直接比较；组合共同起点受数据可得区间最晚的标的制约。</p>
    </footer>

    <AddAssetDialog
      v-model="dialogVisible"
      :current-start="startInfo.startDate"
      :existing-symbols="rows.map((row) => row.symbol)"
      @added="onAdded"
      @start-change="onStartChange"
    />
  </section>
</template>

<style scoped>
.assets-page {
  min-width: 0;
  color: var(--el-text-color-primary);
}

.assets-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 14px;
}

.assets-heading h1 {
  font-size: 21px;
  line-height: 1.3;
  margin-bottom: 4px;
}

.assets-heading p {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.assets-start {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 10px 12px;
  margin-bottom: 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.assets-start span {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.assets-start strong {
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.assets-start-note {
  margin-left: auto;
}

.assets-notice {
  margin-bottom: 12px;
}

.assets-notice-detail {
  display: block;
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.assets-state {
  text-align: center;
  padding: 56px 20px;
  color: var(--el-text-color-secondary);
}

.assets-state p {
  margin: 8px 0 14px;
}

.error-state {
  color: #ef4444;
}

/* 表头吸顶(对齐 CommodityList 约定): EP 默认 .el-table overflow:hidden 会自成滚动容器 */
.assets-table-wrap {
  padding: 0;
}

.assets-table-wrap :deep(.el-table) {
  overflow: clip;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.assets-table-wrap :deep(.el-table__header-wrapper) {
  position: sticky;
  top: 0;
  z-index: 3;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.asset-name {
  display: block;
  font-weight: 600;
}

.asset-symbol {
  display: block;
  color: var(--el-text-color-secondary);
  font-size: 11px;
}

/* 状态色: 成功=绿 / 失败=红(国内配色惯例) */
.status-success {
  color: #1aad19;
  font-weight: 600;
}

.status-failed {
  color: #d93026;
  font-weight: 600;
  cursor: help;
}

.status-running {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--el-text-color-secondary);
}

.status-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid var(--el-border-color);
  border-top-color: var(--el-color-primary);
  border-radius: 50%;
  animation: asset-spin 0.8s linear infinite;
}

.status-unknown {
  color: var(--el-text-color-placeholder);
}

/* 「被 N 个组合使用」(multi-portfolio §六-1) */
.used-by {
  color: var(--el-color-primary);
  cursor: help;
}

@keyframes asset-spin {
  to {
    transform: rotate(360deg);
  }
}

.assets-footer {
  margin-top: 18px;
  padding: 12px 0 4px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.assets-footer p {
  margin: 4px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 响应式断点(page-spec §五: 1280 / 960): 窄屏收内边距, 表格横向滚动兜底 */
@media (max-width: 1280px) {
  .assets-table-wrap :deep(.el-table) {
    font-size: 12px;
  }
}

@media (max-width: 960px) {
  .assets-page {
    padding: 12px;
  }
}
</style>
