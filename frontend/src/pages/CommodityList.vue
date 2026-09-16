<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { getCommodities, getCommodityOverview } from '../api/commodity';
import {
  COMMODITY_WINDOWS, WINDOW_LABELS, filtersFromQuery, formatDateTime,
  formatPercentile, formatPrice, isTriggered, isUninitialized, metricTone, queryFromFilters,
  statusLabel, statusTone,
} from '../utils/commodity.mjs';
import { createRequestGuard } from '../utils/requestGuard.js';

const route = useRoute();
const router = useRouter();
const filters = ref(filtersFromQuery(route.query));
const rows = ref([]);
const overview = ref(null);
const loading = ref(true);
const error = ref('');
const requestGuard = createRequestGuard();
const sortProp = computed(() => filters.value.sortBy === 'price' ? 'latest_price' : filters.value.sortBy === 'date' ? 'data_date' : filters.value.sortBy);
const defaultSort = computed(() => sortProp.value === 'signal' ? {} : ({ prop: sortProp.value, order: filters.value.sortOrder === 'asc' ? 'ascending' : 'descending' }));
const hasActiveFilters = computed(() => Boolean(filters.value.keyword || filters.value.category || filters.value.status || filters.value.window || filters.value.triggered));
const uninitialized = computed(() => isUninitialized(overview.value, { active: hasActiveFilters.value, rows: rows.value }));
const categories = computed(() => [...new Set(rows.value.map((row) => row.category).filter(Boolean))]);
const visibleRows = computed(() => filters.value.triggered
  ? rows.value.filter((row) => isTriggered(row, filters.value.window))
  : rows.value);

const fetchData = async () => {
  const version = requestGuard.next();
  loading.value = true;
  error.value = '';
  const params = {
    keyword: filters.value.keyword || undefined,
    category: filters.value.category || undefined,
    signal: filters.value.status || undefined,
    window: filters.value.window || undefined,
    sort_by: filters.value.sortBy || 'signal',
    sort_order: filters.value.sortOrder || 'desc',
  };
  try {
    const [summary, items] = await Promise.all([getCommodityOverview(), getCommodities(params)]);
    if (!requestGuard.isLatest(version)) return;
    overview.value = summary;
    rows.value = Array.isArray(items) ? items : [];
  } catch (err) {
    if (!requestGuard.isLatest(version)) return;
    error.value = err?.response?.status === 404 ? '接口未初始化' : '商品监控接口暂时不可用';
  } finally {
    if (!requestGuard.isLatest(version)) return;
    loading.value = false;
  }
};

const syncQuery = () => {
  const query = queryFromFilters(filters.value);
  if (JSON.stringify(query) !== JSON.stringify(route.query)) router.replace({ query });
};
const resetFilters = () => {
  filters.value = filtersFromQuery({});
  syncQuery();
};
const onSortChange = ({ prop, order }) => {
  const allowed = ['latest_price', 'data_date', ...COMMODITY_WINDOWS];
  if (!order || !allowed.includes(prop)) {
    filters.value.sortBy = 'signal'; filters.value.sortOrder = 'desc';
  } else {
    filters.value.sortBy = prop === 'latest_price' ? 'price' : prop === 'data_date' ? 'date' : prop;
    filters.value.sortOrder = order === 'ascending' ? 'asc' : 'desc';
  }
  syncQuery();
};
const openDetail = (row) => router.push(`/commodities/${encodeURIComponent(row.code)}`);
const cell = (row, window) => row[window] || row.windows?.[window] || {};
const cellText = (row, window) => formatPercentile(cell(row, window).percentile, 0);
const cellSignal = (row, window) => cell(row, window).signal;
const rowStatus = (row) => row.current_status || row.signal || 'never';
const rowStatusLabel = (row) => row.status_label || statusLabel(rowStatus(row));
// 对齐 market-daily 商品邮件配色: 高位/周期分化=红, 低位=绿, 中性=正文色, 缺数/滞后/失败=灰
const EMAIL_STATUS_COLORS = {
  high: '#D93026', divergent: '#D93026', low: '#1AAD19',
  neutral: 'var(--el-text-color-regular)',
};
const EMAIL_STATUS_EMOJI = { high: '🔴', divergent: '🔴', low: '🟢', neutral: '⚪' };
const emailStatusColor = (status) => EMAIL_STATUS_COLORS[status] || '#888888';
const emailStatusEmoji = (status) => EMAIL_STATUS_EMOJI[status] || '';
const statusCellText = (row) => [emailStatusEmoji(rowStatus(row)), rowStatusLabel(row)].filter(Boolean).join(' ');

watch(() => route.query, (query) => {
  filters.value = filtersFromQuery(query);
  fetchData();
}, { deep: true });
onMounted(fetchData);
onBeforeUnmount(() => requestGuard.invalidate());
</script>

<template>
  <section class="commodity-page">
    <div class="commodity-heading">
      <div>
        <h1>商品监控</h1>
        <p>基于本地已落库收盘价与分位快照 · 最后更新时间：{{ formatDateTime(overview?.last_run_at) }}</p>
      </div>
      <el-button size="small" @click="fetchData" :loading="loading">刷新</el-button>
    </div>

    <div v-if="overview" class="commodity-summary">
      <div><span>数据日期</span><strong>{{ overview.data_date || '—' }}</strong></div>
      <div><span>已更新</span><strong>{{ overview.fresh_count }} / {{ overview.instrument_total }}</strong></div>
      <div class="summary-high"><span>高位</span><strong>{{ overview.high_count }}</strong></div>
      <div class="summary-low"><span>低位</span><strong>{{ overview.low_count }}</strong></div>
      <div v-if="overview.stale_count || overview.failed_count" class="summary-alert">
        <span>异常</span><strong>{{ overview.stale_count + overview.failed_count }}</strong>
      </div>
    </div>
    <div v-if="overview && (overview.stale_count || overview.failed_count)" class="commodity-alert">
      数据异常：{{ overview.failed_count }} 个抓取失败，{{ overview.stale_count }} 个数据滞后。详情页会显示最近错误信息。
    </div>

    <div class="commodity-filters page-card">
      <el-input v-model="filters.keyword" clearable placeholder="搜索品种/代码" @change="syncQuery" @clear="syncQuery" />
      <el-select v-model="filters.category" clearable placeholder="分类" @change="syncQuery">
        <el-option v-for="category in categories" :key="category" :label="category" :value="category" />
      </el-select>
      <el-select v-model="filters.status" clearable placeholder="状态" @change="syncQuery">
        <el-option v-for="status in ['high', 'low', 'neutral', 'divergent', 'insufficient', 'stale', 'failed']" :key="status" :label="statusLabel(status)" :value="status" />
      </el-select>
      <el-select v-model="filters.window" clearable placeholder="窗口" @change="syncQuery">
        <el-option v-for="window in COMMODITY_WINDOWS" :key="window" :label="WINDOW_LABELS[window]" :value="window" />
      </el-select>
      <el-checkbox v-model="filters.triggered" @change="syncQuery">只看触发</el-checkbox>
      <el-button text @click="resetFilters">重置</el-button>
    </div>

    <div v-if="error" class="commodity-state error-state">
      <p>{{ error }}</p>
      <el-button size="small" type="primary" @click="fetchData">重试</el-button>
    </div>
    <div v-else-if="loading && !rows.length" class="commodity-state">正在加载商品监控…</div>
    <div v-else-if="uninitialized" class="commodity-state">
      <strong>商品监控尚未初始化</strong><p>数据库中暂时没有可展示的商品数据。</p>
    </div>
    <div v-else-if="!visibleRows.length" class="commodity-state">没有符合当前条件的品种。</div>
    <div v-else class="commodity-table-wrap page-card">
      <el-table :data="visibleRows" row-key="code" :default-sort="defaultSort" @row-click="openDetail" @sort-change="onSortChange" table-layout="fixed">
        <el-table-column prop="name" label="品种" fixed width="150" class-name="instrument-column">
          <template #default="{ row }"><strong>{{ row.name }}</strong><small>{{ row.code }}</small></template>
        </el-table-column>
        <el-table-column prop="category" label="分类" width="100"><template #default="{ row }">{{ row.category || '—' }}</template></el-table-column>
        <el-table-column prop="latest_price" label="最新价" width="110" sortable="custom" align="right"><template #default="{ row }">{{ formatPrice(row.latest_price) }}</template></el-table-column>
        <el-table-column prop="data_date" label="数据日期" width="120" sortable="custom"><template #default="{ row }">{{ row.data_date || '—' }}</template></el-table-column>
        <el-table-column v-for="window in COMMODITY_WINDOWS" :key="window" :prop="window" :label="WINDOW_LABELS[window]" width="90" sortable="custom" align="right">
          <template #default="{ row }"><span :class="`tone-${metricTone(rowStatus(row), cellSignal(row, window))}`">{{ cellText(row, window) }}</span></template>
        </el-table-column>
        <el-table-column prop="current_status" label="当前状态" fixed="right" width="110"><template #default="{ row }"><span class="status-pill" :style="{ color: emailStatusColor(rowStatus(row)) }">{{ statusCellText(row) }}</span></template></el-table-column>
      </el-table>
    </div>
  </section>
</template>

<style scoped>
.commodity-page { min-width: 0; color: var(--el-text-color-primary); }
.commodity-heading { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.commodity-heading h1 { font-size: 21px; line-height: 1.3; margin-bottom: 4px; }
.commodity-heading p { color: var(--el-text-color-secondary); font-size: 12px; }
.commodity-summary { display: flex; gap: 26px; flex-wrap: wrap; margin-bottom: 14px; }
.commodity-summary div { display: flex; flex-direction: column; gap: 3px; min-width: 72px; }
.commodity-summary span { color: var(--el-text-color-secondary); font-size: 12px; }
.commodity-summary strong { font-size: 18px; font-variant-numeric: tabular-nums; }
.summary-high strong { color: #D93026; }.summary-low strong { color: #1AAD19; }.summary-alert strong { color: #888888; }
.commodity-alert { padding: 9px 12px; margin-bottom: 14px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa; color: #6b7280; font-size: 12px; }
.commodity-filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; padding: 12px; }
.commodity-filters .el-input { width: 190px; }.commodity-filters .el-select { width: 130px; }
.commodity-table-wrap { overflow-x: auto; padding: 0; }.commodity-table-wrap :deep(.el-table) { min-width: 1050px; font-size: 13px; font-variant-numeric: tabular-nums; }.commodity-table-wrap :deep(.el-table th), .commodity-table-wrap :deep(.el-table td) { height: 36px; padding: 0; white-space: nowrap; }.commodity-table-wrap :deep(.el-table__row) { cursor: pointer; }.commodity-table-wrap :deep(.instrument-column .cell) { text-align: left; }.commodity-table-wrap :deep(.instrument-column strong) { display: block; font-weight: 600; }.commodity-table-wrap :deep(.instrument-column small) { display: block; color: var(--el-text-color-secondary); font-size: 11px; }
/* 对齐 market-daily 邮件: 触发分位加粗红/绿(分化按混合=红), 无数据灰 */
.tone-high { color: #D93026; font-weight: 700; }.tone-low { color: #1AAD19; font-weight: 700; }.tone-divergent { color: #D93026; font-weight: 700; }.tone-muted { color: #888888; }.tone-neutral { color: var(--el-text-color-regular); }
.status-pill { font-weight: 700; }.commodity-state { text-align: center; padding: 56px 20px; color: var(--el-text-color-secondary); }.commodity-state p { margin: 8px 0 14px; }.error-state { color: #ef4444; }
@media (max-width: 767px) { .commodity-heading h1 { font-size: 18px; }.commodity-filters .el-input { width: 100%; }.commodity-filters .el-select { flex: 1; min-width: 110px; }.commodity-summary { gap: 16px; } }
</style>
