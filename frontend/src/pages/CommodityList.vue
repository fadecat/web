<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { getCommodities, getCommodityOverview } from '../api/commodity';
import {
  COMMODITY_WINDOWS, WINDOW_LABELS, filtersFromQuery, formatDateTime, formatNumber,
  formatPercentile, isTriggered, queryFromFilters, statusColor, statusLabel, statusTone,
} from '../utils/commodity.mjs';

const route = useRoute();
const router = useRouter();
const filters = ref(filtersFromQuery(route.query));
const rows = ref([]);
const overview = ref(null);
const loading = ref(true);
const error = ref('');
const categories = computed(() => [...new Set(rows.value.map((row) => row.category).filter(Boolean))]);
const visibleRows = computed(() => filters.value.triggered
  ? rows.value.filter((row) => isTriggered(row, filters.value.window))
  : rows.value);

const fetchData = async () => {
  loading.value = true;
  error.value = '';
  const params = {
    keyword: filters.value.keyword || undefined,
    category: filters.value.category || undefined,
    signal: filters.value.status || undefined,
    window: filters.value.window || undefined,
  };
  try {
    const [summary, items] = await Promise.all([getCommodityOverview(), getCommodities(params)]);
    overview.value = summary;
    rows.value = Array.isArray(items) ? items : [];
  } catch (err) {
    error.value = err?.response?.status === 404 ? '接口未初始化' : '商品监控接口暂时不可用';
  } finally {
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
const openDetail = (row) => router.push(`/commodities/${encodeURIComponent(row.code)}`);
const cell = (row, window) => row[window] || row.windows?.[window] || {};
const cellText = (row, window) => formatPercentile(cell(row, window).percentile);
const cellSignal = (row, window) => cell(row, window).signal;
const rowStatus = (row) => row.current_status || row.signal || 'never';
const rowStatusLabel = (row) => row.status_label || statusLabel(rowStatus(row));

watch(() => route.query, (query) => {
  filters.value = filtersFromQuery(query);
  fetchData();
}, { deep: true });
onMounted(fetchData);
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
    <div v-else-if="!rows.length" class="commodity-state">
      <strong>商品监控尚未初始化</strong><p>数据库中暂时没有可展示的商品数据。</p>
    </div>
    <div v-else-if="!visibleRows.length" class="commodity-state">没有符合当前筛选条件的品种。</div>
    <div v-else class="commodity-table-wrap page-card">
      <table class="commodity-table">
        <thead>
          <tr>
            <th class="fixed-col instrument-col">品种</th><th>分类</th><th>最新价</th><th>数据日期</th>
            <th v-for="window in COMMODITY_WINDOWS" :key="window">{{ WINDOW_LABELS[window] }}</th>
            <th class="fixed-status">当前状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleRows" :key="row.code" @click="openDetail(row)">
            <td class="fixed-col instrument-col"><strong>{{ row.name }}</strong><small>{{ row.code }}</small></td>
            <td>{{ row.category || '—' }}</td><td class="num">{{ formatNumber(row.latest_price) }}</td>
            <td>{{ row.data_date || '—' }}</td>
            <td v-for="window in COMMODITY_WINDOWS" :key="window" class="num" :class="`tone-${statusTone(cellSignal(row, window))}`">
              {{ cellText(row, window) }}
            </td>
            <td class="fixed-status"><span class="status-pill" :class="`tone-${statusTone(rowStatus(row))}`" :style="{ color: statusColor(rowStatus(row)) }">{{ rowStatusLabel(row) }}</span></td>
          </tr>
        </tbody>
      </table>
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
.summary-high strong { color: #ef4444; }.summary-low strong { color: #2563eb; }.summary-alert strong { color: #909399; }
.commodity-alert { padding: 9px 12px; margin-bottom: 14px; border: 1px solid #e5e7eb; border-radius: 6px; background: #fafafa; color: #6b7280; font-size: 12px; }
.commodity-filters { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; padding: 12px; }
.commodity-filters .el-input { width: 190px; }.commodity-filters .el-select { width: 130px; }
.commodity-table-wrap { overflow-x: auto; padding: 0; }
.commodity-table { border-collapse: separate; border-spacing: 0; min-width: 1050px; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
.commodity-table th, .commodity-table td { height: 36px; padding: 0 10px; border-bottom: 1px solid var(--el-border-color-lighter); white-space: nowrap; text-align: right; }
.commodity-table th { position: sticky; top: 0; background: var(--el-bg-color); color: var(--el-text-color-secondary); font-weight: 600; z-index: 2; }
.commodity-table tbody tr { cursor: pointer; }.commodity-table tbody tr:hover td { background: var(--el-fill-color-light); }
.commodity-table .instrument-col { text-align: left; width: 150px; }.commodity-table .instrument-col strong { display: block; font-weight: 600; }.commodity-table .instrument-col small { display: block; color: var(--el-text-color-secondary); font-size: 11px; }
.fixed-col, .fixed-status { position: sticky; background: var(--el-bg-color); z-index: 1; }.fixed-col { left: 0; }.fixed-status { right: 0; text-align: left !important; min-width: 92px; }.commodity-table tbody tr:hover .fixed-col, .commodity-table tbody tr:hover .fixed-status { background: var(--el-fill-color-light); }
.num { text-align: right; }.tone-high { color: #ef4444; }.tone-low { color: #2563eb; }.tone-divergent { color: #f97316; }.tone-muted { color: #909399; }.tone-neutral { color: var(--el-text-color-regular); }
.status-pill { font-weight: 600; }.commodity-state { text-align: center; padding: 56px 20px; color: var(--el-text-color-secondary); }.commodity-state p { margin: 8px 0 14px; }.error-state { color: #ef4444; }
@media (max-width: 767px) { .commodity-heading h1 { font-size: 18px; }.commodity-filters .el-input { width: 100%; }.commodity-filters .el-select { flex: 1; min-width: 110px; }.commodity-summary { gap: 16px; } }
</style>
