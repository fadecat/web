<script setup>
import { ref, reactive, onMounted, watch } from 'vue';
import { getRotationAnalysis } from '../api';
import RotationChart from '../components/RotationChart.vue';

const LEFT_OPTIONS = [
  { code: '399376', name: '国证小盘成长' },
  { code: '399373', name: '国证大盘价值' },
];
const RIGHT_OPTIONS = [
  { code: '399373', name: '国证大盘价值' },
  { code: '399376', name: '国证小盘成长' },
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
  leftSymbol: '399376',
  rightSymbol: '399373',
  startDate: '',
  endDate: '',
  returnWindow: 250, // 收益率计算窗口(交易日)
  maWindow: 20,      // spread 的 MA 趋势线窗口
});

const dateRangePreset = ref('3y'); // 默认「最近3年」

const data = ref(null);
const loading = ref(false);
const errorMsg = ref('');

const todayIso = () => new Date().toISOString().slice(0, 10);

async function fetchAnalysis() {
  loading.value = true;
  errorMsg.value = '';
  try {
    const params = {
      left_symbol: form.leftSymbol,
      right_symbol: form.rightSymbol,
      start_date: form.startDate || undefined,
      end_date: form.endDate || undefined,
      return_window: form.returnWindow,
      ma_window: form.maWindow,
    };
    data.value = await getRotationAnalysis(params);
  } catch (e) {
    errorMsg.value =
      e?.response?.data?.detail || e?.message || '拉取失败,请检查后端日志';
    data.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  form.endDate = todayIso();
  applyDateRangePreset();
  fetchAnalysis();
});

watch(
  () => [form.leftSymbol, form.rightSymbol, form.startDate, form.endDate, form.returnWindow, form.maWindow],
  () => fetchAnalysis(),
);
</script>

<template>
  <div class="rotation-page">
    <el-card class="filter-card" shadow="never">
      <el-form :inline="true" size="small" label-width="80px">
        <el-form-item label="左侧标的">
          <el-select v-model="form.leftSymbol" style="width: 200px">
            <el-option
              v-for="opt in LEFT_OPTIONS"
              :key="opt.code"
              :value="opt.code"
              :label="`${opt.code} ${opt.name}`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="右侧标的">
          <el-select v-model="form.rightSymbol" style="width: 200px">
            <el-option
              v-for="opt in RIGHT_OPTIONS"
              :key="opt.code"
              :value="opt.code"
              :label="`${opt.code} ${opt.name}`"
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
      <div class="summary-grid">
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

/* ---------- 移动端适配 ---------- */
@media (max-width: 767px) {
  .summary-grid {
    grid-template-columns: repeat(2, 1fr); /* 4 列 → 2 列 */
    gap: 12px;
  }
  .summary-item .value {
    font-size: 16px;
  }
  :deep(.el-form--inline .el-form-item) {
    margin-right: 0;
    width: calc(50% - 8px); /* 每行两个控件 */
  }
  :deep(.el-form-item__label) {
    width: auto !important; /* 覆盖固定 label-width, 省横向空间 */
    padding-right: 6px;
  }
  :deep(.el-select),
  :deep(.el-date-editor),
  :deep(.el-input-number) {
    width: 100% !important; /* 控件撑满所在半行 */
  }
}
</style>