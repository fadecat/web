<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import * as echarts from 'echarts';
import { getCommodityDetail, getCommodityHistory } from '../api/commodity';
import {
  COMMODITY_WINDOWS, WINDOW_LABELS, formatDateTime, formatNumber, formatPercentile,
  statusColor, statusLabel, statusTone,
} from '../utils/commodity.mjs';

const route = useRoute();
const router = useRouter();
const code = computed(() => String(route.params.code || ''));
const detail = ref(null);
const history = ref(null);
const range = ref('1y');
const loading = ref(true);
const chartLoading = ref(false);
const error = ref('');
const chartRef = ref(null);
let chart;

const loadDetail = async () => {
  loading.value = true; error.value = '';
  try {
    detail.value = await getCommodityDetail(code.value);
    await loadHistory();
  } catch (err) {
    error.value = err?.response?.status === 404 ? '找不到该商品' : '商品详情暂时不可用';
  } finally { loading.value = false; }
};
const loadHistory = async () => {
  chartLoading.value = true;
  try { history.value = await getCommodityHistory(code.value, range.value); await nextTick(); renderChart(); }
  catch (err) { error.value = err?.response?.status === 404 ? '找不到该商品' : '历史数据暂时不可用'; }
  finally { chartLoading.value = false; }
};
const signalsByDate = computed(() => (history.value?.signals || []).reduce((result, signal) => {
  (result[signal.date] ||= []).push(signal); return result;
}, {}));
const renderChart = () => {
  if (!chartRef.value || !history.value) return;
  if (!chart) chart = echarts.init(chartRef.value);
  const prices = history.value.prices || [];
  chart.setOption({
    animation: false,
    grid: { left: 42, right: 18, top: 22, bottom: 30 },
    tooltip: { trigger: 'axis', formatter(params) {
      const point = params?.[0]; const date = point?.axisValue; const signals = signalsByDate.value[date] || [];
      const signalText = signals.length ? `<br/>信号：${signals.map((s) => `${WINDOW_LABELS[s.window] || s.window} ${statusLabel(s.signal)}${s.percentile == null ? '' : ` ${formatPercentile(s.percentile)}`}`).join('、')}` : '';
      return `${date}<br/>收盘价：${formatNumber(point?.value)}${signalText}`;
    } },
    xAxis: { type: 'category', data: prices.map((item) => item.date), boundaryGap: false, axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', scale: true },
    series: [{ name: '收盘价', type: 'line', showSymbol: false, connectNulls: false, data: prices.map((item) => item.close), lineStyle: { color: '#2563eb', width: 2 }, itemStyle: { color: '#2563eb' } }],
  }, true);
};
const onResize = () => chart?.resize();
const signals = computed(() => [...(history.value?.signals || [])].filter((s) => s.signal === 'high' || s.signal === 'low').reverse().slice(0, 30));
const windowData = (window) => detail.value?.windows?.[window] || detail.value?.[window] || {};
const currentStatus = computed(() => detail.value?.current_status || detail.value?.signal || 'never');
watch(code, loadDetail);
watch(range, loadHistory);
onMounted(() => { loadDetail(); window.addEventListener('resize', onResize); });
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); chart?.dispose(); chart = null; });
</script>

<template>
  <section class="commodity-detail">
    <div class="detail-top"><el-button text @click="router.push('/commodities')">‹ 返回商品监控</el-button></div>
    <div v-if="loading" class="detail-state">正在加载商品详情…</div>
    <div v-else-if="error" class="detail-state error-state"><p>{{ error }}</p><el-button type="primary" size="small" @click="loadDetail">重试</el-button></div>
    <template v-else-if="detail">
      <div class="detail-heading">
        <div><h1>{{ detail.name }} <small>{{ detail.code }}</small></h1><p>{{ detail.category || '—' }} · {{ detail.market || '—' }}</p></div>
        <div class="detail-status" :style="{ color: statusColor(currentStatus) }">{{ detail.status_label || statusLabel(currentStatus) }}</div>
      </div>
      <div class="detail-metrics page-card">
        <div><span>最新价</span><strong>{{ formatNumber(detail.latest_price) }}</strong></div>
        <div><span>数据日期</span><strong>{{ detail.data_date || '—' }}</strong></div>
        <div><span>数据状态</span><strong :class="`tone-${statusTone(currentStatus)}`">{{ detail.data_state || '—' }}</strong></div>
        <div><span>最后成功</span><strong>{{ formatDateTime(detail.last_success_at || detail.sync_success_at) }}</strong></div>
      </div>
      <div class="percentile-grid">
        <div v-for="window in COMMODITY_WINDOWS" :key="window" class="percentile-card page-card">
          <span>{{ WINDOW_LABELS[window] }}</span><strong :class="`tone-${statusTone(windowData(window).signal)}`">{{ formatPercentile(windowData(window).percentile) }}</strong>
          <em>{{ statusLabel(windowData(window).signal || 'insufficient') }}</em>
          <small v-if="windowData(window).sample_count != null">样本 {{ windowData(window).sample_count }} / {{ detail.metric_definition?.windows?.[window] || '—' }}</small>
          <small v-else>样本不足，暂无法计算</small>
        </div>
      </div>
      <div class="chart-panel page-card">
        <div class="panel-heading"><h2>收盘价走势</h2><div class="range-buttons"><el-button v-for="item in ['6m','1y','3y','5y','10y','all']" :key="item" size="small" :type="range === item ? 'primary' : ''" @click="range = item">{{ item }}</el-button></div></div>
        <div v-loading="chartLoading" ref="chartRef" class="price-chart"></div>
        <p class="chart-note">历史缺口按实际落库日期展示，不补齐交易日，也不以前值填充。</p>
      </div>
      <div class="detail-lower">
        <div class="page-card signal-panel"><h2>最近信号</h2><table><thead><tr><th>日期</th><th>窗口</th><th>分位</th><th>状态</th><th>样本</th></tr></thead><tbody><tr v-for="signal in signals" :key="`${signal.date}-${signal.window}`"><td>{{ signal.date }}</td><td>{{ WINDOW_LABELS[signal.window] || signal.window }}</td><td>{{ formatPercentile(signal.percentile) }}</td><td :style="{ color: statusColor(signal.signal) }">{{ statusLabel(signal.signal) }}</td><td>{{ signal.sample_count ?? '—' }}</td></tr><tr v-if="!signals.length"><td colspan="5">暂无已落库高低位信号</td></tr></tbody></table></div>
        <div class="page-card source-panel"><h2>数据说明</h2><p>来源：{{ detail.source || '—' }}</p><p>最后抓取：{{ formatDateTime(detail.last_attempt_at || detail.sync_attempt_at) }}</p><p>同步状态：{{ statusLabel(detail.sync_status || detail.sync?.status || 'never') }}<span v-if="detail.last_error">（{{ detail.last_error }}）</span></p><p class="definition">{{ detail.metric_definition?.percentile }}</p><p class="definition">{{ detail.metric_definition?.signal }}</p></div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.commodity-detail { min-width: 0; color: var(--el-text-color-primary); }.detail-top { margin-bottom: 4px; }.detail-heading { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }.detail-heading h1 { font-size: 22px; }.detail-heading h1 small { color: var(--el-text-color-secondary); font-size: 13px; font-weight: 400; }.detail-heading p { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }.detail-status { font-weight: 700; padding-top: 5px; }.detail-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 14px; }.detail-metrics div { display: flex; flex-direction: column; gap: 4px; }.detail-metrics span, .percentile-card span { color: var(--el-text-color-secondary); font-size: 12px; }.detail-metrics strong { font-size: 18px; font-variant-numeric: tabular-nums; }.percentile-grid { display: grid; grid-template-columns: repeat(6, minmax(110px, 1fr)); gap: 10px; margin-bottom: 14px; }.percentile-card { padding: 12px; display: flex; flex-direction: column; gap: 4px; }.percentile-card strong { font-size: 22px; font-variant-numeric: tabular-nums; }.percentile-card em { font-size: 12px; font-style: normal; font-weight: 600; }.percentile-card small { color: var(--el-text-color-secondary); font-size: 11px; }.chart-panel { padding-bottom: 9px; }.panel-heading { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.panel-heading h2, .signal-panel h2, .source-panel h2 { font-size: 15px; }.range-buttons { display: flex; gap: 5px; flex-wrap: wrap; }.price-chart { height: 330px; margin-top: 8px; }.chart-note, .source-panel p { color: var(--el-text-color-secondary); font-size: 11px; line-height: 1.7; }.detail-lower { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(260px, 1fr); gap: 14px; margin-top: 14px; }.signal-panel, .source-panel { min-width: 0; }.signal-panel table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 12px; }.signal-panel th, .signal-panel td { padding: 7px 5px; border-bottom: 1px solid var(--el-border-color-lighter); text-align: left; }.source-panel p { margin-top: 8px; }.source-panel .definition { border-top: 1px solid var(--el-border-color-lighter); padding-top: 8px; }.tone-high { color: #ef4444; }.tone-low { color: #2563eb; }.tone-divergent { color: #f97316; }.tone-muted { color: #909399; }.tone-neutral { color: var(--el-text-color-regular); }.detail-state { text-align: center; padding: 70px 20px; color: var(--el-text-color-secondary); }.error-state { color: #ef4444; }.error-state p { margin-bottom: 12px; }
@media (max-width: 900px) { .percentile-grid { grid-template-columns: repeat(3, 1fr); }.detail-lower { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .detail-metrics { grid-template-columns: repeat(2, 1fr); }.percentile-grid { grid-template-columns: repeat(2, 1fr); }.panel-heading { align-items: flex-start; flex-direction: column; }.price-chart { height: 260px; } }
</style>
