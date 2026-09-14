<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import { getCbIndexDaily, getCbIndexSpread } from '../api';
import {
  normalizeCbMarketRows,
  selectCbMarketWindow,
  summaryPercentile,
  windowPercentile,
  normalizeCbSpreadRows,
} from '../utils/cbMarket.mjs';
import { percentileTone, percentilePosition, fmtPct } from '../utils/valuation';
import CbMarketChart from '../components/CbMarketChart.vue';
import CbSpreadChart from '../components/CbSpreadChart.vue';

const router = useRouter();

// ---- 请求状态 ----
const loading = ref(false); // 首次加载(骨架)
const errorMsg = ref(''); // 首次失败
const refreshError = ref(false); // 刷新失败(已有数据时)
const allRows = ref([]); // 规范化升序全量
const invalidCounts = ref({ invalidDateCount: 0, duplicateDateCount: 0, invalidValueCount: 0 });
const range = ref('5y'); // 默认 5 年
const selectedDate = ref(''); // 历史游标(当前查看日期)

// 递增请求标识: 防止旧响应覆盖新响应
let reqToken = 0;
let disposed = false;

const RANGE_OPTIONS = [
  { key: '1y', label: '1年' },
  { key: '3y', label: '3年' },
  { key: '5y', label: '5年' },
  { key: 'all', label: '全部' },
];

async function loadData(isRefresh = false) {
  const token = ++reqToken;
  loading.value = true;
  loadSpread(); // 利差模块独立加载(自带 try/catch, 失败不阻塞主数据)
  if (!isRefresh) {
    errorMsg.value = '';
    refreshError.value = false;
  } else {
    refreshError.value = false; // 刷新中先清旧提示
  }
  try {
    const raw = await getCbIndexDaily();
    if (disposed || token !== reqToken) return; // 卸载或已被新请求取代
    const norm = normalizeCbMarketRows(raw);
    allRows.value = norm.rows;
    invalidCounts.value = {
      invalidDateCount: norm.invalidDateCount,
      duplicateDateCount: norm.duplicateDateCount,
      invalidValueCount: norm.invalidValueCount,
    };
    errorMsg.value = '';
    refreshError.value = false;
    loading.value = false;
    resetCursor(); // 加载后游标归最新窗口末条
  } catch (e) {
    if (disposed || token !== reqToken) return;
    if (isRefresh && allRows.value.length) {
      // 刷新失败: 保留上次成功结果与截至日期
      refreshError.value = true;
      loading.value = false;
    } else {
      errorMsg.value =
        (e?.response?.data && e.response.data.detail) || e?.message || '拉取失败,请检查后端日志';
      allRows.value = [];
      loading.value = false;
    }
  }
}

// ---- 窗口(本地过滤, 不请求网络) ----
const windowResult = computed(() => selectCbMarketWindow(allRows.value, range.value));
const windowRows = computed(() => windowResult.value.rows);
const insufficientHistory = computed(() => windowResult.value.insufficientHistory);

function rangeLabel(key) {
  return RANGE_OPTIONS.find((o) => o.key === key)?.label || key;
}

function setRange(key) {
  range.value = key;
  resetCursor(); // 缩放重置为该窗口完整区间, 读数归窗口末条
}

// ---- 最新摘要(全量最新合法日期, 不随游标/窗口变化) ----
const latest = computed(() =>
  allRows.value.length ? allRows.value[allRows.value.length - 1] : null,
);
const asOfDate = computed(() => latest.value?.trade_date || '');
const latestMedianDate = computed(() => [...allRows.value].reverse().find((r) => r.median_price != null)?.trade_date || '');
const latestYtmDate = computed(() => [...allRows.value].reverse().find((r) => r.avg_ytm != null)?.trade_date || '');

// ---- 摘要分位(固定 5 年口径, 不随游标/窗口变化; 历史不足 5 年按实际样本) ----
const pricePctl = computed(() => summaryPercentile(allRows.value, 'median_price'));
const ytmPctl = computed(() => summaryPercentile(allRows.value, 'avg_ytm'));

// 分位颜色: 语义同市场估值页(绿 = 相对吸引力较强, 红 = 偏弱, 30~70 中性)。
// median_price 同 PE(分位低=便宜); avg_ytm 同股息率(分位高=便宜)。
function percentileColor(value, metric) {
  const tone = percentileTone(value, metric);
  return tone === 'green'
    ? 'var(--el-color-success)'
    : tone === 'red'
      ? 'var(--el-color-danger)'
      : 'var(--el-text-color-regular)';
}

// 数据新鲜度: 截至日期较今天超过 7 个自然日 → 中性提示
const dataAgeDays = computed(() => {
  if (!asOfDate.value) return null;
  const t = Date.parse(`${asOfDate.value}T00:00:00`);
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
});
const isStale = computed(() => dataAgeDays.value != null && dataAgeDays.value > 7);

// ---- 历史游标(读取行) ----
const selectedRecord = computed(
  () => allRows.value.find((r) => r.trade_date === selectedDate.value) || null,
);

const cursorIndex = computed(() =>
  windowRows.value.findIndex((r) => r.trade_date === selectedDate.value),
);
const canPrev = computed(() => cursorIndex.value > 0);
const canNext = computed(
  () => cursorIndex.value >= 0 && cursorIndex.value < windowRows.value.length - 1,
);

function resetCursor() {
  const w = windowRows.value;
  selectedDate.value = w.length ? w[w.length - 1].trade_date : '';
}
function onChartSelect(date) {
  selectedDate.value = date;
}
function moveCursor(delta) {
  const i = cursorIndex.value;
  if (i < 0) return;
  const ni = i + delta;
  if (ni < 0 || ni >= windowRows.value.length) return;
  selectedDate.value = windowRows.value[ni].trade_date;
}

// ---- 读数分位(当前窗口内回看排名, 随 1y/3y/5y/all 窗口变化) ----
const priceWinPctl = computed(() =>
  windowPercentile(windowRows.value, selectedDate.value, 'median_price'),
);
const ytmWinPctl = computed(() =>
  windowPercentile(windowRows.value, selectedDate.value, 'avg_ytm'),
);

// 窗口内两项核心指标全空 → 提示(保留时间按钮)
const windowNoValid = computed(() => {
  const w = windowRows.value;
  if (!w.length) return false;
  return !w.some((r) => r.median_price != null || r.avg_ytm != null);
});

// ---- 转债-国债利差模块(独立加载, 失败/为空不阻塞主数据) ----
const spreadLoading = ref(false);
const spreadErrorMsg = ref('');
const spreadRaw = ref(null); // 后端响应(统计块 + 全历史序列); 样本不足为 null
const spreadRows = ref([]); // normalizeCbSpreadRows 输出的升序 rows
const spreadInvalidCounts = ref({ invalidDateCount: 0, duplicateDateCount: 0, invalidValueCount: 0 });

let spreadToken = 0;

async function loadSpread() {
  const token = ++spreadToken;
  spreadLoading.value = true;
  spreadErrorMsg.value = '';
  try {
    const raw = await getCbIndexSpread();
    if (disposed || token !== spreadToken) return;
    spreadRaw.value = raw || null; // 后端样本不足返回 null(空态)
    const norm = normalizeCbSpreadRows(raw);
    spreadRows.value = norm.rows;
    spreadInvalidCounts.value = {
      invalidDateCount: norm.invalidDateCount,
      duplicateDateCount: norm.duplicateDateCount,
      invalidValueCount: norm.invalidValueCount,
    };
  } catch (e) {
    if (disposed || token !== spreadToken) return;
    spreadErrorMsg.value = e?.message || '利差数据拉取失败';
    spreadRaw.value = null;
    spreadRows.value = [];
  } finally {
    if (!disposed && token === spreadToken) spreadLoading.value = false;
  }
}

// 统计块: 当前利差 / 5Y 均值(分位统一放在图表参考线与悬浮提示中)
const spreadSummary = computed(() => spreadRaw.value?.spread || null);
const spreadAsOf = computed(() => spreadRaw.value?.trade_date || '');

// 走势图窗口: 复用主图时间按钮(锚点=利差序列自身最新日期, 与主图可能差一天)
const spreadWindowRows = computed(() => selectCbMarketWindow(spreadRows.value, range.value).rows);

const spreadHasInvalid = computed(
  () =>
    spreadInvalidCounts.value.invalidDateCount > 0 ||
    spreadInvalidCounts.value.duplicateDateCount > 0 ||
    spreadInvalidCounts.value.invalidValueCount > 0,
);

// ---- 格式化(本页自用, 不依赖后端自定义口径) ----
function fmtPrice(v) {
  return v == null ? '—' : Number(v).toFixed(2);
}
function fmtYtm(v) {
  return v == null ? '—' : Number(v).toFixed(2); // 允许 0 与负数, 不再乘 100
}
function fmtCount(v) {
  return v == null ? '—' : String(Math.trunc(Number(v)));
}

const hasInvalid = computed(
  () =>
    invalidCounts.value.invalidDateCount > 0 ||
    invalidCounts.value.duplicateDateCount > 0 ||
    invalidCounts.value.invalidValueCount > 0,
);

const refreshing = computed(() => loading.value && allRows.value.length > 0);

onMounted(() => {
  loadData(false);
});

onBeforeUnmount(() => {
  disposed = true; // 卸载后不再更新页面
});
</script>

<template>
  <div class="cb-market-page">
    <!-- 1. 标题 / 副标题 / 链接 -->
    <div class="page-head">
      <div class="head-left">
        <h3 class="title">转债市场</h3>
        <span class="subtitle">日频市场统计</span>
      </div>
      <div class="head-right">
        <button
          class="refresh-btn"
          :disabled="loading"
          @click="loadData(true)"
        >
          {{ refreshing ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- 2. 数据说明 + 刷新失败提示 -->
    <div class="data-note">
      <template v-if="asOfDate">
        数据截至 {{ asOfDate }} · 来源：集思录转债指数历史
      </template>
      <template v-else-if="!loading && !errorMsg">数据来源：集思录转债指数历史</template>
    </div>
    <div v-if="refreshError" class="banner warn">
      刷新失败，已保留上次成功数据（截至 {{ asOfDate || '—' }}）。
    </div>

    <!-- 首次失败 -->
    <div v-if="errorMsg" class="banner error">
      <span>{{ errorMsg }}</span>
      <button class="retry-btn" :disabled="loading" @click="loadData(false)">重试</button>
    </div>

    <!-- 首次加载骨架 -->
    <div v-if="loading && allRows.length === 0" class="skeleton">
      <div class="sk-row">
        <div class="sk-card" v-for="n in 3" :key="n" />
      </div>
      <div class="sk-chart" />
    </div>

    <!-- 空数组 -->
    <div v-else-if="!errorMsg && allRows.length === 0" class="empty-state">
      <p>暂无转债市场历史数据</p>
      <router-link class="link-btn" to="/status">查看数据状态</router-link>
    </div>

    <!-- 正常内容 -->
    <template v-else-if="!errorMsg && allRows.length > 0">
      <!-- 3. 最新摘要(全量最新, 不随游标/窗口变化; 分位为固定 5 年口径) -->
      <div class="summary">
        <div class="sum-item">
          <div class="sum-label">价格中位数（元）</div>
          <div class="sum-value">{{ fmtPrice(latest.median_price) }}</div>
          <div v-if="pricePctl?.percentile != null" class="sum-pctl">
            5年分位
            <span :style="{ color: percentileColor(pricePctl.percentile, 'pe') }">
              {{ fmtPct(pricePctl.percentile) }} · {{ percentilePosition(pricePctl.percentile) }}
            </span>
            <span>（{{ pricePctl.since }} 起）</span>
          </div>
          <div class="sum-date">最后有效日期：{{ latestMedianDate || '—' }}</div>
        </div>
        <div class="sum-item">
          <div class="sum-label">平均到期收益率（集思录口径，%）</div>
          <div class="sum-value">{{ fmtYtm(latest.avg_ytm) }}</div>
          <div v-if="ytmPctl?.percentile != null" class="sum-pctl">
            5年分位
            <span :style="{ color: percentileColor(ytmPctl.percentile, 'dividend') }">
              {{ fmtPct(ytmPctl.percentile) }} · {{ percentilePosition(ytmPctl.percentile) }}
            </span>
            <span>（{{ ytmPctl.since }} 起）</span>
          </div>
          <div class="sum-date">最后有效日期：{{ latestYtmDate || '—' }}</div>
        </div>
        <div class="sum-item">
          <div class="sum-label">转债数量（只）</div>
          <div class="sum-value">{{ fmtCount(latest.count) }}</div>
        </div>
      </div>
      <p
        v-if="pricePctl?.percentile != null || ytmPctl?.percentile != null"
        class="pctl-legend"
      >
        分位颜色：红 = 相对吸引力偏弱，绿 = 相对吸引力较强；30～70 为中性。
        价格中位数分位越低越便宜，到期收益率分位越高越便宜。
      </p>

      <!-- 4. 时间按钮 -->
      <div class="seg-group">
        <button
          v-for="opt in RANGE_OPTIONS"
          :key="opt.key"
          class="seg-btn"
          :class="{ active: range === opt.key }"
          @click="setRange(opt.key)"
        >
          {{ opt.label }}
        </button>
      </div>
      <div class="window-info">
        区间 {{ windowResult.from || '—' }} ~ {{ windowResult.to || '—' }} · 共
        {{ windowRows.length }} 条
        <span v-if="insufficientHistory" class="hint">历史不足{{ rangeLabel(range) }}，展示已有记录</span>
      </div>

      <!-- 5. 双图 -->
      <CbMarketChart
        :rows="windowRows"
        :selected-date="selectedDate"
        @date-select="onChartSelect"
      />

      <!-- 6. 当前查看读数行 + 左右按钮 -->
      <div class="read-line">
        <button class="nav-btn" :disabled="!canPrev" @click="moveCursor(-1)" aria-label="上一天">‹</button>
        <div class="read-text">
          <template v-if="selectedRecord">
            当前查看 {{ selectedDate }}：价格中位数
            {{ fmtPrice(selectedRecord.median_price) }}元<template v-if="priceWinPctl != null">（本窗口 {{ fmtPct(priceWinPctl) }} 分位）</template>；平均到期收益率
            {{ fmtYtm(selectedRecord.avg_ytm) }}%<template v-if="ytmWinPctl != null">（本窗口 {{ fmtPct(ytmWinPctl) }} 分位）</template>
          </template>
          <template v-else>请选择日期查看历史读数</template>
        </div>
        <button class="nav-btn" :disabled="!canNext" @click="moveCursor(1)" aria-label="下一天">›</button>
      </div>

      <!-- 7. 转债-国债利差模块(独立加载) -->
      <div class="spread-module">
        <div class="spread-head">
          <h4 class="spread-title">转债-国债利差</h4>
          <span class="spread-sub">
            平均到期收益率 − 10Y 国债收益率（百分点）<template v-if="spreadAsOf"> · 截至 {{ spreadAsOf }}</template>
          </span>
        </div>

        <div v-if="spreadErrorMsg" class="banner warn">
          <span>利差数据加载失败：{{ spreadErrorMsg }}（不影响上方主数据）</span>
          <button class="retry-btn" :disabled="spreadLoading" @click="loadSpread()">重试</button>
        </div>

        <div v-else-if="spreadLoading" class="spread-empty">利差数据加载中…</div>

        <template v-else-if="spreadRaw">
          <div class="spread-stats">
            <div class="sp-item">
              <div class="sp-label">当前利差（百分点）</div>
              <div class="sp-value">{{ fmtYtm(spreadSummary?.current) }}</div>
            </div>
            <div class="sp-item">
              <div class="sp-label">5年均值（百分点）</div>
              <div class="sp-value">{{ fmtYtm(spreadSummary?.average_5y) }}</div>
            </div>
            <div class="sp-item">
              <div class="sp-label">平均到期收益率（%）</div>
              <div class="sp-value">{{ fmtYtm(spreadRaw.avg_ytm) }}</div>
            </div>
            <div class="sp-item">
              <div class="sp-label">10Y国债收益率（%）</div>
              <div class="sp-value">{{ fmtYtm(spreadRaw.bond_yield) }}</div>
            </div>
          </div>

          <p class="spread-note">
            分位统一在下方走势图中展示：30/50/70 分位线随时间窗口切换，悬浮时显示当前日期分位。
            利差越高代表转债债底相对国债越便宜，负值表示转债整体比国债贵。
          </p>

          <CbSpreadChart :rows="spreadWindowRows" />

          <div v-if="spreadHasInvalid" class="banner neutral">
            利差数据存在 {{ spreadInvalidCounts.invalidDateCount }} 条无效日期、
            {{ spreadInvalidCounts.duplicateDateCount }} 条重复日期、
            {{ spreadInvalidCounts.invalidValueCount }} 个异常值，已忽略。
          </div>
        </template>

        <div v-else class="spread-empty">
          利差样本不足 20 个交易日，暂不展示（需转债指数与 10Y 国债收益率有足够重叠日期）。
        </div>
      </div>

      <!-- 窗口无有效指标 -->
      <div v-if="windowNoValid" class="banner neutral">该区间暂无有效指标</div>

      <!-- 陈旧数据中性提示 -->
      <div v-if="isStale" class="banner neutral">
        已超过 7 天无新记录，节假日或同步延迟均可能导致。
      </div>

      <!-- 异常计数提示 -->
      <div v-if="hasInvalid" class="banner neutral">
        数据存在 {{ invalidCounts.invalidDateCount }} 条无效日期、
        {{ invalidCounts.duplicateDateCount }} 条重复日期、
        {{ invalidCounts.invalidValueCount }} 个异常值，已忽略。
      </div>

      <!-- 7. 口径说明 + 数据状态 -->
      <details class="caliber">
        <summary>数据口径</summary>
        <p>
          价格中位数为来源统计值（元）；平均到期收益率为来源
          <code>avg_ytm_rt</code>（%，集思录口径），允许 0 与负数，不乘 100；
          转债数量为来源只数。以上按来源统计值展示，<b>非本系统自定义选债收益率</b>，
          不承诺完整覆盖所有在市转债。缺失项显示 <code>—</code>，不从其他日期补值。
        </p>
        <p>
          分位为本页按全量历史现算：摘要卡为固定 5 年口径（锚点 = 最新日期回看 5 年，
          历史不足 5 年按实际样本，统计起点以卡片标注为准）；读数行、图上 30/70 分位虚线
          与图表悬浮分位均为当前所选窗口内的回看排名，随窗口切换变化，两种口径统计范围不同。
          分位 = 当前值高于窗口内 p% 的交易日，等值取中间秩，有效样本不足 20 条不显示；
          分位线颜色：价格中位数低于 30 分位线偏便宜（绿）、高于 70 分位线偏贵（红），
          平均到期收益率方向相反。
        </p>
        <p>
          转债-国债利差 = 平均到期收益率（集思录口径）− 10 年期国债收益率（百分点），
          国债期限与市场估值页股债收益差一致；转债为混合剩余期限的市场平均，不与 10 年精确匹配，
          定位为市场温度计而非可交易利差，负值表示转债整体比国债贵。
          利差分位由后端按最新交易日全历史回看计算（窗口 1/3/5/10 年，
          分位 = 窗口内利差严格小于当前值的天数占比，有效样本不足 20 天该窗口跳过；
          两序列重叠不足 20 天时整模块不展示）；利差走势图与图上 30/70 分位虚线、
          悬浮分位为当前所选窗口内的回看排名，随时间按钮变化，两种口径统计范围不同。
        </p>
        <router-link class="link-btn" to="/status">查看数据状态</router-link>
      </details>
    </template>
  </div>
</template>

<style scoped>
.cb-market-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.page-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.head-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}
.subtitle {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.link-btn {
  font-size: 13px;
  color: #2563eb;
  text-decoration: none;
  white-space: nowrap;
}
.link-btn:hover {
  text-decoration: underline;
}
.refresh-btn {
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  border-radius: 8px;
  padding: 5px 14px;
  font-size: 13px;
  color: var(--el-text-color-regular);
  cursor: pointer;
}
.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.data-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.banner {
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.banner.error {
  background: rgba(220, 38, 38, 0.06);
  color: #b91c1c;
}
.banner.warn {
  background: rgba(217, 119, 6, 0.08);
  color: #b45309;
}
.banner.neutral {
  background: rgba(100, 116, 139, 0.08);
  color: #475569;
}
.retry-btn {
  border: 1px solid #b91c1c;
  background: var(--el-bg-color);
  color: #b91c1c;
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.retry-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 骨架 */
.skeleton {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sk-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.sk-card {
  height: 64px;
  border-radius: 12px;
  background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}
.sk-chart {
  height: 480px;
  border-radius: 12px;
  background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.empty-state {
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 14px;
  padding: 40px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}

/* 摘要 */
.summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.sum-item {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.sum-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.sum-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}
.sum-date {
  margin-top: 3px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}
/* 摘要卡 5 年分位行: 数值与位置着色, 起点为次要色 */
.sum-pctl {
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}
.pctl-legend {
  margin: 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

/* 时间按钮 */
.seg-group {
  display: inline-flex;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
  flex-wrap: wrap;
}
.seg-btn {
  border: none;
  background: transparent;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  white-space: nowrap;
}
.seg-btn.active {
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}
.window-info {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.window-info .hint {
  margin-left: 8px;
  color: #b45309;
}

/* 读数行 */
.read-line {
  display: flex;
  align-items: center;
  gap: 10px;
}
.nav-btn {
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  border-radius: 8px;
  width: 32px;
  height: 32px;
  font-size: 18px;
  line-height: 1;
  color: var(--el-text-color-regular);
  cursor: pointer;
  flex-shrink: 0;
}
.nav-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.read-text {
  font-size: 13px;
  color: var(--el-text-color-regular);
  flex: 1;
  font-variant-numeric: tabular-nums;
}

/* 转债-国债利差模块 */
.spread-module {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--el-bg-color);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
}
.spread-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.spread-title {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}
.spread-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.spread-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.sp-item {
  background: var(--el-fill-color-light);
  border-radius: 10px;
  padding: 10px 12px;
}
.sp-label {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-bottom: 3px;
}
.sp-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  font-variant-numeric: tabular-nums;
}
.spread-note {
  margin: 0;
  font-size: 11px;
  line-height: 1.6;
  color: var(--el-text-color-secondary);
}
.spread-empty {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  padding: 8px 0;
}

/* 口径 */
.caliber {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
}
.caliber summary {
  cursor: pointer;
  font-weight: 600;
  color: var(--el-text-color-regular);
}
.caliber p {
  margin: 10px 0;
  line-height: 1.7;
}
.caliber code {
  background: var(--el-fill-color);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 11px;
}

/* 移动端 */
@media (max-width: 767px) {
  .title { font-size: 16px; }
  .summary { grid-template-columns: 1fr; gap: 10px; }
  .sum-value { font-size: 18px; }
  .sk-chart { height: 420px; }
  .sk-row { grid-template-columns: 1fr; }
  .spread-stats { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .sp-value { font-size: 16px; }
}

/* ---------- 深色模式微调(语义色只提亮不换色相, 骨架屏换暗色微光) ---------- */
html.dark .link-btn { color: #60a5fa; }
html.dark .banner.error { background: rgba(248, 113, 113, 0.1); color: #f87171; }
html.dark .banner.warn { background: rgba(251, 191, 36, 0.1); color: #fbbf24; }
html.dark .banner.neutral { background: rgba(148, 163, 184, 0.12); color: #94a3b8; }
html.dark .retry-btn { border-color: #f87171; color: #f87171; }
html.dark .window-info .hint { color: #fbbf24; }
html.dark .sk-card,
html.dark .sk-chart {
  background: linear-gradient(90deg, #1f2937, #374151, #1f2937);
  background-size: 200% 100%;
}
</style>
