<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage } from 'element-plus';
import {
  createResearchReplay, getReplayComparison, getReplayDays, getReplaySummary,
  getResearchSecurities,
} from '../api/research';
import {
  WORDING, categoryLabel, reasonLabel, filtersFromQuery, formatNumber,
  buildSegmentRows, buildComparisonRows, pickComparisonRow, queryFromFilters,
} from '../utils/researchReplay';
import { createRequestGuard } from '../utils/requestGuard.js';

const route = useRoute();
const router = useRouter();
const requestGuard = createRequestGuard();

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const filters = ref(filtersFromQuery(route.query));
const securities = ref([]);
const loadingSecurities = ref(true);
const creating = ref(false);
const loadingDetail = ref(false);
const error = ref('');
const comparison = ref([]); // 参数比较网格(12 组合)
const summary = ref(null); // 当前选中 run 的汇总
const days = ref([]); // 当前选中 run 的逐日明细
const evidenceDay = ref(null); // evidence 对话框绑定的明细行
const evidenceVisible = ref(false);

// 默认区间 = 近 3 年(设计已定默认); 仅在 URL 无日期时预填
const defaultRange = () => {
  const end = new Date();
  const start = new Date(end);
  start.setFullYear(start.getFullYear() - 3);
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
};

const activeSymbol = computed(() => filters.value.symbol || securities.value[0]?.symbol || '');
const comparisonRows = computed(() => buildComparisonRows(comparison.value));
const selectedRow = computed(() => pickComparisonRow(comparisonRows.value, {
  lambda: filters.value.lambda, window: filters.value.window,
}));

// 汇总卡片: overall/train/validation 三段并列(选参只看训练段)
const segmentCards = computed(() => (summary.value ? [
  { key: 'train', label: '训练段（选参依据）', segment: summary.value.train },
  { key: 'validation', label: '验证段', segment: summary.value.validation },
  { key: 'overall', label: '全区间（仅供参考）', segment: summary.value.overall },
] : []));

// 类别计数 → 中文平铺(停用/排除分布 + PATH_AMBIGUOUS 单列)
const categoryCountRows = computed(() => {
  const counts = summary.value?.overall?.day_categories || {};
  const order = ['BUY_ONLY', 'SELL_ONLY', 'BOTH_HIT', 'NO_HIT', 'DISABLED',
    'EXCLUDED_DATA', 'EXCLUDED_CORP_ACTION', 'EXCLUDED_OPEN_INVALIDATION', 'CALENDAR_UNVERIFIED'];
  return order.filter((code) => counts[code]).map((code) => ({
    code, label: categoryLabel(code), count: counts[code],
  }));
});

// 逐日明细过滤: 只看有效评价日(剔除停用/排除)可切换
const daysFilter = ref('all');
const visibleDays = computed(() => {
  if (daysFilter.value === 'evaluated') {
    return days.value.filter((day) => !['DISABLED', 'EXCLUDED_DATA', 'EXCLUDED_CORP_ACTION',
      'EXCLUDED_OPEN_INVALIDATION', 'CALENDAR_UNVERIFIED'].includes(day.day_category));
  }
  return days.value;
});

const dataReadyBySymbol = computed(() => Object.fromEntries(
  securities.value.map((row) => [row.symbol, row.data_ready]),
));

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
const fetchSecurities = async () => {
  const version = requestGuard.next();
  try {
    const rows = await getResearchSecurities();
    if (!requestGuard.isLatest(version)) return;
    securities.value = Array.isArray(rows) ? rows : [];
  } catch (err) {
    if (!requestGuard.isLatest(version)) return;
    error.value = '研究标的接口暂时不可用';
  } finally {
    if (requestGuard.isLatest(version)) loadingSecurities.value = false;
  }
};

// 拉取某 symbol×区间的参数比较网格 + 当前选中 run 的汇总/明细
const fetchComparison = async () => {
  if (!activeSymbol.value) return;
  const version = requestGuard.next();
  loadingDetail.value = true;
  error.value = '';
  try {
    const rows = await getReplayComparison(
      activeSymbol.value, filters.value.startDate, filters.value.endDate,
    );
    if (!requestGuard.isLatest(version)) return;
    comparison.value = Array.isArray(rows) ? rows : [];
    await loadSelectedRun(version);
  } catch (err) {
    if (!requestGuard.isLatest(version)) return;
    comparison.value = [];
    summary.value = null;
    days.value = [];
    error.value = err?.response?.status === 404
      ? '该区间暂无回放结果，请先生成'
      : '回放比较接口暂时不可用';
  } finally {
    if (!requestGuard.isLatest(version)) return;
    loadingDetail.value = false;
  }
};

const loadSelectedRun = async (outerVersion) => {
  const row = selectedRow.value;
  if (!row) {
    summary.value = null;
    days.value = [];
    return;
  }
  loadingDetail.value = true;
  try {
    const [summaryData, daysData] = await Promise.all([
      getReplaySummary(row.runId),
      getReplayDays(row.runId),
    ]);
    if (!requestGuard.isLatest(outerVersion)) return;
    summary.value = summaryData;
    days.value = Array.isArray(daysData) ? daysData : [];
  } catch (err) {
    if (!requestGuard.isLatest(outerVersion)) return;
    summary.value = null;
    days.value = [];
  } finally {
    if (!requestGuard.isLatest(outerVersion)) return;
    loadingDetail.value = false;
  }
};

// 创建回放: 同步计算 12 组合(约秒级), 完成后刷新比较网格
const createReplay = async () => {
  if (!activeSymbol.value || !filters.value.startDate || !filters.value.endDate) {
    ElMessage.warning('请先选择标的与日期区间');
    return;
  }
  creating.value = true;
  error.value = '';
  try {
    await createResearchReplay({
      symbol: activeSymbol.value,
      start_date: filters.value.startDate,
      end_date: filters.value.endDate,
    });
    ElMessage.success('回放完成（12 组参数网格）');
    await fetchComparison();
  } catch (err) {
    const detail = err?.response?.data?.detail || '';
    if (err?.response?.status === 409) {
      error.value = `数据源未验收或未同步：${detail || '无可用（USABLE）数据快照'}`;
    } else if (err?.response?.status === 422) {
      error.value = `参数非法：${detail}`;
    } else {
      error.value = '生成回放失败，请稍后重试';
    }
  } finally {
    creating.value = false;
  }
};

// ---------------------------------------------------------------------------
// URL-query 往返
// ---------------------------------------------------------------------------
const syncQuery = () => {
  const query = queryFromFilters(filters.value);
  if (JSON.stringify(query) !== JSON.stringify(route.query)) router.replace({ query });
};

const onSymbolChange = () => {
  syncQuery();
  fetchComparison();
};

// λ/窗口筛选变化: 只换选中 run, 不重新拉比较网格
const onRunFilterChange = () => {
  syncQuery();
  loadSelectedRun(requestGuard.next());
};

const onRangeChange = () => {
  syncQuery();
  if (filters.value.startDate && filters.value.endDate) fetchComparison();
};

const resetFilters = () => {
  const defaults = defaultRange();
  filters.value = filtersFromQuery({ symbol: activeSymbol.value, start_date: defaults.start, end_date: defaults.end });
  syncQuery();
  fetchComparison();
};

const formatTierRate = (tiers, tier) => {
  const hit = (tiers || []).find((item) => item.tier === tier);
  return hit ? hit.rateText : '—';
};

const showEvidence = (day) => {
  evidenceDay.value = day;
  evidenceVisible.value = true;
};

watch(() => route.query, (query) => {
  filters.value = filtersFromQuery(query);
}, { deep: true });

onMounted(async () => {
  // URL 无日期时预填默认近 3 年(不立即写回 URL, 由用户操作触发)
  if (!filters.value.startDate || !filters.value.endDate) {
    const defaults = defaultRange();
    if (!filters.value.startDate) filters.value.startDate = defaults.start;
    if (!filters.value.endDate) filters.value.endDate = defaults.end;
  }
  await fetchSecurities();
  await fetchComparison();
});
onBeforeUnmount(() => requestGuard.invalidate());
</script>

<template>
  <section class="research-page">
    <div class="research-heading">
      <div>
        <h1>{{ WORDING.pageKind }}</h1>
        <p>
          次日 T 价位研究：以按 T 截断的输入生成六档模型价，统计次日价位触达情况 ·
          {{ WORDING.modelPriceNote }}
        </p>
      </div>
    </div>

    <!-- 筛选区 -->
    <div class="research-filters">
      <el-select
        v-model="filters.symbol"
        filterable
        placeholder="选择研究标的"
        class="filter-symbol"
        :loading="loadingSecurities"
        @change="onSymbolChange"
      >
        <el-option
          v-for="item in securities"
          :key="item.symbol"
          :value="item.symbol"
          :label="`${item.name}（${item.symbol}）`"
          :disabled="!item.data_ready"
        >
          <span>{{ item.name }}（{{ item.symbol }}）</span>
          <span class="option-meta">{{ item.data_ready ? '数据可用' : '数据源未验收' }}</span>
        </el-option>
      </el-select>
      <el-date-picker
        v-model="filters.startDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="评价日起"
        class="filter-date"
        :clearable="false"
        @change="onRangeChange"
      />
      <span class="range-sep">~</span>
      <el-date-picker
        v-model="filters.endDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="评价日止"
        class="filter-date"
        :clearable="false"
        @change="onRangeChange"
      />
      <el-button :loading="creating" type="primary" @click="createReplay">
        生成回放（λ×窗口网格）
      </el-button>
      <el-button @click="resetFilters">重置</el-button>
    </div>

    <el-alert
      v-if="error"
      :title="error"
      type="warning"
      show-icon
      :closable="false"
      class="research-error"
    />

    <template v-if="activeSymbol && dataReadyBySymbol[activeSymbol] === false">
      <el-alert
        title="该标的数据源未验收（无 USABLE 数据快照），生成回放将被拒绝"
        type="info"
        show-icon
        :closable="false"
        class="research-error"
      />
    </template>

    <div v-if="loadingDetail" class="research-loading" v-loading="true" element-loading-text="加载回放结果…" />

    <template v-if="!loadingDetail && comparisonRows.length">
      <!-- 参数比较网格: train/validation 并排, 选参只看训练段 -->
      <h3 class="section-title">
        参数比较（{{ WORDING.trainOnlyNote }}）
        <span class="section-note">训练/验证按区间开市日历前 60% 固定切分，不随参数漂移</span>
      </h3>
      <el-table :data="comparisonRows" size="small" class="grid-table" border>
        <el-table-column label="参数" align="center">
          <template #default="{ row }">
            <span class="param-link" :class="{ active: selectedRow && row.runId === selectedRow.runId }"
              @click="filters.lambda = row.lambda; filters.window = row.window; onRunFilterChange()">
              {{ row.lambdaText }} · {{ row.windowText }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="训练段买侧触达 P50/P70/P85" align="center">
          <template #default="{ row }">
            <span class="rate-cell">{{ formatTierRate(row.train_buy, 'P50') }} / {{ formatTierRate(row.train_buy, 'P70') }} / {{ formatTierRate(row.train_buy, 'P85') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="训练段卖侧触达 P50/P70/P85" align="center">
          <template #default="{ row }">
            <span class="rate-cell">{{ formatTierRate(row.train_sell, 'P50') }} / {{ formatTierRate(row.train_sell, 'P70') }} / {{ formatTierRate(row.train_sell, 'P85') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="验证段买侧触达 P50/P70/P85" align="center">
          <template #default="{ row }">
            <span class="rate-cell">{{ formatTierRate(row.validation_buy, 'P50') }} / {{ formatTierRate(row.validation_buy, 'P70') }} / {{ formatTierRate(row.validation_buy, 'P85') }}</span>
          </template>
        </el-table-column>
        <el-table-column label="验证段卖侧触达 P50/P70/P85" align="center">
          <template #default="{ row }">
            <span class="rate-cell">{{ formatTierRate(row.validation_sell, 'P50') }} / {{ formatTierRate(row.validation_sell, 'P70') }} / {{ formatTierRate(row.validation_sell, 'P85') }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="train_dayCount" label="训练天数" align="center" width="90" />
        <el-table-column prop="validation_dayCount" label="验证天数" align="center" width="90" />
      </el-table>

      <!-- 汇总卡: 选中 run 的分子/分母/比例三列明示 -->
      <template v-if="summary">
        <h3 class="section-title">
          汇总（{{ WORDING.hitTerm }}：分子 / 分母 / 比例）
          <span class="section-note">
            λ={{ summary.param_lambda }} · 窗口={{ summary.quantile_window }}日 ·
            切分日 {{ summary.train_end_date || '—' }} · 触达率＝触达日数 ÷ 有效分母日数
          </span>
        </h3>
        <div class="summary-cards">
          <div v-for="card in segmentCards" :key="card.key" class="summary-card">
            <div class="summary-card-title">{{ card.label }}（{{ card.segment.day_count }} 日）</div>
            <el-table :data="buildSegmentRows(card.segment)" size="small" class="summary-table">
              <el-table-column prop="sideLabel" label="侧别" width="56" align="center" />
              <el-table-column prop="tier" label="档位" width="60" align="center" />
              <el-table-column label="触达 / 分母" align="center">
                <template #default="{ row }">{{ row.numerator }} / {{ row.denominator }}</template>
              </el-table-column>
              <el-table-column prop="rateText" label="比例" width="72" align="center" />
            </el-table>
          </div>
        </div>

        <!-- 停用/排除分布 + 双侧触达单列 -->
        <div v-if="categoryCountRows.length" class="category-row">
          <span v-for="item in categoryCountRows" :key="item.code" class="category-chip">
            {{ item.label }} {{ item.count }}
          </span>
        </div>
        <p v-if="categoryCountRows.some((item) => item.code === 'BOTH_HIT')" class="path-note">
          ⚠ {{ WORDING.pathAmbiguousNote }}
        </p>

        <!-- 逐日明细宽表 -->
        <h3 class="section-title">
          逐日明细（{{ days.length }} 日 · 六档为{{ WORDING.modelPrice }}，未取整）
          <el-radio-group v-model="daysFilter" size="small" class="days-filter">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="evaluated">仅有效评价日</el-radio-button>
          </el-radio-group>
        </h3>
        <el-table :data="visibleDays" size="small" class="days-table" border max-height="520">
          <el-table-column prop="plan_date" label="T 日" width="100" fixed align="center" />
          <el-table-column prop="eval_date" label="T+1 日" width="100" fixed align="center">
            <template #default="{ row }">{{ row.eval_date || '—' }}</template>
          </el-table-column>
          <el-table-column label="类别" width="120" align="center">
            <template #default="{ row }">{{ categoryLabel(row.day_category) }}</template>
          </el-table-column>
          <el-table-column label="原因码" width="150" align="center">
            <template #default="{ row }">
              <template v-if="row.reason_codes && row.reason_codes.length">
                <span v-for="reason in row.reason_codes" :key="reason.code" class="reason-chip"
                  @click="showEvidence(row)">
                  {{ reasonLabel(reason.code) }}
                </span>
              </template>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="买档 P50/P70/P85" width="180" align="center">
            <template #default="{ row }">
              <span v-if="row.buy_levels_raw" class="price-cell">{{ row.buy_levels_raw.map((v) => formatNumber(v)).join(' / ') }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="买侧触达" width="100" align="center">
            <template #default="{ row }">
              <span v-if="row.buy_hits" :class="['hit-cell', row.buy_open_invalid && 'hit-invalid']">
                {{ row.buy_open_invalid ? '开盘失效' : row.buy_hits.map((h) => (h ? '✓' : '·')).join(' ') }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="卖档 P50/P70/P85" width="180" align="center">
            <template #default="{ row }">
              <span v-if="row.sell_levels_raw" class="price-cell">{{ row.sell_levels_raw.map((v) => formatNumber(v)).join(' / ') }}</span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="卖侧触达" width="100" align="center">
            <template #default="{ row }">
              <span v-if="row.sell_hits" :class="['hit-cell', row.sell_open_invalid && 'hit-invalid']">
                {{ row.sell_open_invalid ? '开盘失效' : row.sell_hits.map((h) => (h ? '✓' : '·')).join(' ') }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="next_open" label="T+1 开" width="80" align="center">
            <template #default="{ row }">{{ formatNumber(row.next_open, 2) }}</template>
          </el-table-column>
          <el-table-column prop="next_high" label="T+1 高" width="80" align="center">
            <template #default="{ row }">{{ formatNumber(row.next_high, 2) }}</template>
          </el-table-column>
          <el-table-column prop="next_low" label="T+1 低" width="80" align="center">
            <template #default="{ row }">{{ formatNumber(row.next_low, 2) }}</template>
          </el-table-column>
          <el-table-column prop="next_close" label="T+1 收" width="80" align="center">
            <template #default="{ row }">{{ formatNumber(row.next_close, 2) }}</template>
          </el-table-column>
          <el-table-column label="Z" width="70" align="center">
            <template #default="{ row }">{{ formatNumber(row.z, 2) }}</template>
          </el-table-column>
          <el-table-column label="ATR14" width="80" align="center">
            <template #default="{ row }">{{ formatNumber(row.atr14, 2) }}</template>
          </el-table-column>
          <el-table-column label="证据" width="60" align="center" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.evidence" link type="primary" size="small" @click="showEvidence(row)">查看</el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </template>

    <el-empty
      v-else-if="!loadingDetail && !error"
      :description="activeSymbol ? '该区间暂无回放结果，点击「生成回放」计算参数网格' : '暂无研究标的'"
    />

    <!-- evidence 对话框: PATH_AMBIGUOUS/开盘失效/权益事件的证据 -->
    <el-dialog v-model="evidenceVisible" :title="`评价证据 · ${evidenceDay?.plan_date || ''}`" width="560px">
      <div v-if="evidenceDay">
        <p><strong>类别：</strong>{{ categoryLabel(evidenceDay.day_category) }}</p>
        <p v-if="evidenceDay.reason_codes && evidenceDay.reason_codes.length">
          <strong>原因码：</strong>
          <span v-for="reason in evidenceDay.reason_codes" :key="reason.code">
            {{ reasonLabel(reason.code) }}{{ reason.measured !== undefined && reason.measured !== null ? `（测得 ${formatNumber(reason.measured, 4)}${reason.threshold !== undefined ? `，阈值 ${formatNumber(reason.threshold, 4)}` : ''}）` : '' }}
          </span>
        </p>
        <pre class="evidence-json">{{ JSON.stringify(evidenceDay.evidence, null, 2) }}</pre>
      </div>
    </el-dialog>

    <!-- 回顾性局限声明(页脚固定) -->    <footer class="research-footer">
      <p>{{ WORDING.retrospectiveNote }}</p>
      <p>六档价格为{{ WORDING.modelPrice }}；分位（P50/P70/P85）是历史距离分布，不是命中概率。</p>
    </footer>
  </section>
</template>

<style scoped>
.research-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-bottom: 8px;
}

.research-heading h1 {
  font-size: 20px;
  margin: 0 0 4px;
}

.research-heading p {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.research-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-symbol {
  width: 240px;
}

.filter-date {
  width: 150px;
}

.range-sep {
  color: var(--el-text-color-secondary);
}

.option-meta {
  float: right;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.research-error {
  margin-top: 0;
}

.research-loading {
  min-height: 200px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  font-size: 15px;
  margin: 6px 0 0;
}

.section-note {
  font-size: 12px;
  font-weight: normal;
  color: var(--el-text-color-secondary);
}

.days-filter {
  margin-left: auto;
}

.grid-table .param-link {
  color: #2563eb;
  cursor: pointer;
}

.grid-table .param-link:hover {
  text-decoration: underline;
}

.grid-table .param-link.active {
  font-weight: 700;
}

html.dark .grid-table .param-link {
  color: #60a5fa;
}

.rate-cell {
  font-variant-numeric: tabular-nums;
}

.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
}

.summary-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  padding: 10px;
}

.summary-card-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.category-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.category-chip {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 10px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-regular);
}

.path-note {
  margin: 0;
  font-size: 12px;
  color: #b45309;
}

.reason-chip {
  display: inline-block;
  margin: 1px 2px;
  padding: 0 6px;
  border-radius: 8px;
  font-size: 12px;
  background: var(--el-fill-color);
  color: var(--el-text-color-regular);
  cursor: pointer;
}

.reason-chip:hover {
  background: var(--el-fill-color-dark);
}

.muted {
  color: var(--el-text-color-placeholder);
}

.price-cell,
.hit-cell {
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}

.hit-invalid {
  color: #b45309;
}

.evidence-json {
  max-height: 320px;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 10px;
  border-radius: 6px;
  font-size: 12px;
}

.research-footer {
  margin-top: 18px;
  padding: 12px 0 4px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.research-footer p {
  margin: 4px 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

@media (max-width: 767px) {
  .filter-symbol,
  .filter-date {
    width: 100%;
  }

  .summary-cards {
    grid-template-columns: 1fr;
  }
}
</style>
