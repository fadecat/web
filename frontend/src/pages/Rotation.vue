<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { getRotationAnalysis, getRotationValuation, getValuationSnapshot } from '../api';
import RotationChart from '../components/RotationChart.vue';
import PeChart from '../components/PeChart.vue';

// 预设对照组(用户定版: 不开放任意组合, 只有两档)
// 惯例: 左=进攻侧(小盘/成长), 右=防守侧(大盘/红利) → spread>0 恒为进攻侧强
// 大小盘走腾讯源, 红利组走易方达源(后端 index_eod 任务入库), 前端只管选组
const PAIR_PRESETS = [
  {
    key: 'size',
    label: '大小盘轮动',
    left: { code: '399376', name: '国证小盘成长' },
    right: { code: '399373', name: '国证大盘价值' },
  },
  {
    key: 'dividend',
    label: '成长 vs 红利',
    left: { code: '399296', name: '创成长' },
    right: { code: '930955', name: '红利低波100' },
  },
];

// 快捷日期范围预设(对齐源项目 DATE_RANGE_PRESETS)
const DATE_RANGE_PRESETS = [
  { key: 'custom', label: '自定义' },
  { key: '1m', label: '最近1个月' },
  { key: '3m', label: '最近3个月' },
  { key: '6m', label: '最近6个月' },
  { key: 'ytd', label: '年初至今' },
  { key: '1y', label: '最近1年' },
  { key: '3y', label: '最近3年' },
  { key: '5y', label: '最近5年' },
  { key: '10y', label: '最近10年' },
  { key: '20y', label: '最近20年' },
];

function formatDate(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function subtractMonths(d, months) {
  const copy = new Date(d);
  copy.setMonth(copy.getMonth() - months);
  return copy;
}

function subtractYears(d, years) {
  const copy = new Date(d);
  copy.setFullYear(copy.getFullYear() - years);
  return copy;
}

function getPresetStartDate(endDate, presetKey) {
  let startDate = null;
  if (presetKey === '1m') startDate = subtractMonths(endDate, 1);
  else if (presetKey === '3m') startDate = subtractMonths(endDate, 3);
  else if (presetKey === '6m') startDate = subtractMonths(endDate, 6);
  else if (presetKey === 'ytd') startDate = new Date(endDate.getFullYear(), 0, 1);
  else if (presetKey === '1y') startDate = subtractYears(endDate, 1);
  else if (presetKey === '3y') startDate = subtractYears(endDate, 3);
  else if (presetKey === '5y') startDate = subtractYears(endDate, 5);
  else if (presetKey === '10y') startDate = subtractYears(endDate, 10);
  else if (presetKey === '20y') startDate = subtractYears(endDate, 20);
  return startDate ? formatDate(startDate) : null;
}

function applyDateRangePreset() {
  if (dateRangePreset.value === 'custom') return;
  const end = form.endDate ? new Date(form.endDate) : new Date();
  if (!form.endDate) form.endDate = formatDate(end);
  const start = getPresetStartDate(end, dateRangePreset.value);
  if (start) form.startDate = start;
}

function markCustomRange() {
  dateRangePreset.value = 'custom';
}

const form = reactive({
  pairKey: 'dividend',   // 当前对照组(默认成长 vs 红利: 大小盘组无 PE 数据, 默认展示有 PE 图的红利组)
  leftSymbol: '399296',
  rightSymbol: '930955',
  startDate: '',
  endDate: '',
  returnWindow: 250, // 收益率计算窗口(交易日)
  maWindow: 20,      // spread 的 MA 趋势线窗口
});

// 切换对照组: 覆写左右标的(触发 watch 自动刷新)
function onPairChange(key) {
  const preset = PAIR_PRESETS.find((p) => p.key === key);
  if (!preset) return;
  form.leftSymbol = preset.left.code;
  form.rightSymbol = preset.right.code;
}

// 当前对照组的左右名称(摘要条/标题用)
const currentPair = computed(() => {
  const preset = PAIR_PRESETS.find((p) => p.key === form.pairKey) || PAIR_PRESETS[0];
  return preset;
});

const dateRangePreset = ref('3y'); // 默认「最近3年」

// ── 移动端交互: 筛选折叠 + 断点监听 ──────────────────────
// 手机端信息密度优先级: 图表 > 指标 > 筛选条件。
// 筛选表单默认折叠成一行摘要, 点开才展开——否则 7 项条件占满首屏,
// 图表被挤到第二三屏, 这不是手机端布局而是 PC 压缩版。
const isMobile = ref(false);
let mq = null;
const updateIsMobile = () => {
  isMobile.value = window.innerWidth < 768;
};
const filtersExpanded = ref(false);

// 折叠条摘要: 对照组 + 范围, 让用户不看表单也知道当前在查什么
const filterSummary = computed(() => {
  const left = currentPair.value.left.name;
  const right = currentPair.value.right.name;
  const preset = DATE_RANGE_PRESETS.find((p) => p.key === dateRangePreset.value);
  const rangeText = preset && preset.key !== 'custom' ? preset.label : `${form.startDate} ~ ${form.endDate}`;
  return `${left} vs ${right} · ${rangeText}`;
});

const toggleFilters = () => {
  filtersExpanded.value = !filtersExpanded.value;
};

const data = ref(null);
const valuation = ref(null);
const peHistory = ref({ left: [], right: [] });
const loading = ref(false);
const valuationLoading = ref(false);
const errorMsg = ref('');
const valuationError = ref('');

const todayIso = () => new Date().toISOString().slice(0, 10);

async function fetchAnalysis() {
  loading.value = true;
  errorMsg.value = '';
  const params = {
    left_symbol: form.leftSymbol,
    right_symbol: form.rightSymbol,
    start_date: form.startDate || undefined,
    end_date: form.endDate || undefined,
    return_window: form.returnWindow,
    ma_window: form.maWindow,
  };
  try {
    data.value = await getRotationAnalysis(params);
  } catch (e) {
    errorMsg.value =
      e?.response?.data?.detail || e?.message || '拉取失败,请检查后端日志';
    data.value = null;
  } finally {
    loading.value = false;
  }
  await fetchValuation(params);
}

async function fetchValuation(params = {}) {
  valuationLoading.value = true;
  valuationError.value = '';
  const left = params.left_symbol || form.leftSymbol;
  const right = params.right_symbol || form.rightSymbol;
  try {
    const [latest, leftHist, rightHist] = await Promise.all([
      getRotationValuation({ left_symbol: left, right_symbol: right }),
      getValuationSnapshot({ index_code: left }),
      getValuationSnapshot({ index_code: right }),
    ]);
    valuation.value = latest;
    peHistory.value = { left: leftHist || [], right: rightHist || [] };
  } catch (e) {
    valuationError.value =
      e?.response?.data?.detail || e?.message || '估值读取失败';
    valuation.value = null;
    peHistory.value = { left: [], right: [] };
  } finally {
    valuationLoading.value = false;
  }
}

onMounted(() => {
  form.endDate = todayIso();
  applyDateRangePreset();
  fetchAnalysis();
  updateIsMobile();
  mq = window.matchMedia('(max-width: 767px)');
  mq.addEventListener('change', updateIsMobile);
});

onBeforeUnmount(() => {
  mq?.removeEventListener('change', updateIsMobile);
});

watch(
  () => [form.leftSymbol, form.rightSymbol, form.startDate, form.endDate, form.returnWindow, form.maWindow],
  () => fetchAnalysis(),
);
</script>

<template>
  <div class="rotation-page">
    <el-card class="filter-card" shadow="never">
      <!-- 移动端: 折叠摘要条(点开才显示表单) -->
      <div v-if="isMobile" class="filter-collapse-bar" @click="toggleFilters">
        <span class="filter-summary">{{ filterSummary }}</span>
        <span class="filter-toggle" :class="{ expanded: filtersExpanded }">▾</span>
      </div>
      <el-form
        v-show="!isMobile || filtersExpanded"
        :inline="true"
        size="small"
        label-width="80px"
        :class="{ 'mobile-form': isMobile }"
      >
        <el-form-item label="对照组">
          <el-select v-model="form.pairKey" style="width: 200px" @change="onPairChange">
            <el-option
              v-for="p in PAIR_PRESETS"
              :key="p.key"
              :value="p.key"
              :label="`${p.label}（${p.left.name} vs ${p.right.name}）`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="快捷范围">
          <el-select
            v-model="dateRangePreset"
            style="width: 130px"
            @change="applyDateRangePreset"
          >
            <el-option
              v-for="preset in DATE_RANGE_PRESETS"
              :key="preset.key"
              :value="preset.key"
              :label="preset.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="起始日期">
          <el-date-picker
            v-model="form.startDate"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 150px"
            @change="markCustomRange"
          />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker
            v-model="form.endDate"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 150px"
            @change="markCustomRange"
          />
        </el-form-item>
        <el-form-item label="收益窗口">
          <el-input-number
            v-model="form.returnWindow"
            :min="5"
            :max="750"
            :step="5"
            controls-position="right"
            style="width: 110px"
          />
        </el-form-item>
        <el-form-item label="MA窗口">
          <el-input-number
            v-model="form.maWindow"
            :min="2"
            :max="120"
            :step="1"
            controls-position="right"
            style="width: 100px"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            @click="fetchAnalysis"
          >刷新</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      :closable="false"
      style="margin-bottom: 16px"
    />

    <el-alert
      v-if="data?.meta?.warmup_note"
      :title="`数据预热期不足: 显示范围早于可用数据`"
      :description="data.meta.warmup_note"
      type="warning"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    />

    <el-card v-if="data && data.summary" class="summary-card" shadow="never">
      <!-- 移动端: 横向滑动胶囊片(股票 App 风格, 一屏可见 + 左右滑看更多) -->
      <div v-if="isMobile" class="summary-strip">
        <div class="summary-chip">
          <div class="label">最新日期</div>
          <div class="value">{{ data.summary.latest_date }}</div>
        </div>
        <div class="summary-chip">
          <div class="label">最新 spread</div>
          <div class="value" :class="{ positive: data.summary.latest_spread > 0, negative: data.summary.latest_spread < 0 }">
            {{ data.summary.latest_spread }}%
          </div>
        </div>
        <div class="summary-chip">
          <div class="label">MA{{ form.maWindow }}</div>
          <div class="value" :class="{ positive: data.summary.latest_ma > 0, negative: data.summary.latest_ma < 0 }">
            {{ data.summary.latest_ma }}%
          </div>
        </div>
        <div class="summary-chip">
          <div class="label">P90 / P10</div>
          <div class="value small">
            <span style="color: #dc2626">{{ data.summary.global_p90 }}</span>
            /
            <span style="color: #16a34a">{{ data.summary.global_p10 }}</span>
          </div>
        </div>
      </div>
      <!-- 桌面端: 原四列网格 -->
      <div v-else class="summary-grid">
        <div class="summary-item">
          <div class="label">最新日期</div>
          <div class="value">{{ data.summary.latest_date }}</div>
        </div>
        <div class="summary-item">
          <div class="label">最新 spread</div>
          <div class="value" :class="{ positive: data.summary.latest_spread > 0, negative: data.summary.latest_spread < 0 }">
            {{ data.summary.latest_spread }} %
          </div>
        </div>
        <div class="summary-item">
          <div class="label">最新 MA{{ form.maWindow }}</div>
          <div class="value" :class="{ positive: data.summary.latest_ma > 0, negative: data.summary.latest_ma < 0 }">
            {{ data.summary.latest_ma }} %
          </div>
        </div>
        <div class="summary-item">
          <div class="label">全局 P90 / P10</div>
          <div class="value small">
            <span style="color: #dc2626">{{ data.summary.global_p90 }}</span>
            /
            <span style="color: #16a34a">{{ data.summary.global_p10 }}</span>
          </div>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" v-loading="loading">
      <RotationChart :data="data" />
    </el-card>

    <el-card class="valuation-card" shadow="never" v-loading="valuationLoading">
      <div class="valuation-head">
        <strong>指数 PE 走势</strong>
        <span>横轴跟随上方日期范围</span>
      </div>
      <el-alert v-if="valuationError" :title="valuationError" type="warning" :closable="false" />
      <PeChart
        v-else
        :history="peHistory"
        :start-date="form.startDate"
        :end-date="form.endDate"
        :left-name="currentPair.left.name"
        :right-name="currentPair.right.name"
      />
    </el-card>
  </div>
</template>

<style scoped>
.rotation-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.filter-card {
  margin-bottom: 0;
}
.summary-card {
  margin-bottom: 0;
}

.valuation-card {
  margin-bottom: 0;
}

.valuation-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 12px;
}

.valuation-head strong {
  font-size: 15px;
  color: #111827;
}

.valuation-head span {
  color: #6b7280;
  font-size: 12px;
}

/* ---------- 桌面端指标四列网格 ---------- */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.summary-item .label {
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.summary-item .value {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}
.summary-item .value.positive {
  color: #dc2626;
}
.summary-item .value.negative {
  color: #16a34a;
}
.summary-item .value.small {
  font-size: 14px;
  font-weight: 600;
}

/* ---------- 移动端 ---------- */
@media (max-width: 767px) {
  :deep(.el-card__body) {
    padding: 12px;
  }

  .valuation-head {
    display: block;
  }

  .valuation-head span {
    display: block;
    margin-top: 4px;
  }
}

/* 折叠摘要条: 仅移动端渲染(v-if=isMobile), 桌面端不参与 */
.filter-collapse-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 2px;
  cursor: pointer;
  user-select: none;
}

.filter-summary {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: #274c77;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.filter-toggle {
  flex: 0 0 auto;
  color: #98a2b3;
  font-size: 14px;
  transition: transform 0.2s ease;
}

.filter-toggle.expanded {
  transform: rotate(180deg);
}

.mobile-form {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed rgba(148, 163, 184, 0.45);
}

/* 指标胶囊滑动条: 一排横滑, 股票 App 风格 */
.summary-strip {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}

.summary-strip::-webkit-scrollbar {
  display: none;
}

.summary-chip {
  flex: 0 0 auto;
  min-width: 96px;
  padding: 8px 14px;
  border-radius: 12px;
  background: rgba(39, 76, 119, 0.06);
}

.summary-chip .label {
  font-size: 11px;
  color: #6b7280;
  margin-bottom: 3px;
  white-space: nowrap;
}

.summary-chip .value {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  white-space: nowrap;
}

.summary-chip .value.positive {
  color: #dc2626;
}

.summary-chip .value.negative {
  color: #16a34a;
}

.summary-chip .value.small {
  font-size: 13px;
  font-weight: 600;
}

/* 移动端表单两列布局(展开后) */
@media (max-width: 767px) {
  .mobile-form.el-form--inline .el-form-item {
    margin-right: 0;
    width: calc(50% - 8px);
  }
  .mobile-form :deep(.el-form-item__label) {
    width: auto !important;
    padding-right: 6px;
  }
  .mobile-form :deep(.el-select),
  .mobile-form :deep(.el-date-editor),
  .mobile-form :deep(.el-input-number) {
    width: 100% !important;
  }
}
</style>