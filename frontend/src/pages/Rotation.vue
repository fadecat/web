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

const form = reactive({
  leftSymbol: '399376',
  rightSymbol: '399373',
  startDate: '2024-01-01',
  endDate: '',
  returnWindow: 20,
});

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
  fetchAnalysis();
});

watch(
  () => [form.leftSymbol, form.rightSymbol, form.startDate, form.endDate, form.returnWindow],
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
        <el-form-item label="起始日期">
          <el-date-picker
            v-model="form.startDate"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker
            v-model="form.endDate"
            type="date"
            value-format="YYYY-MM-DD"
            style="width: 160px"
          />
        </el-form-item>
        <el-form-item label="窗口">
          <el-input-number
            v-model="form.returnWindow"
            :min="5"
            :max="120"
            :step="1"
            controls-position="right"
            style="width: 110px"
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
          <div class="label">最新 MA{{ form.returnWindow }}</div>
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
</style>