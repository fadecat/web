<script setup>
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { getCbListLatest, screenBondsActive } from '../api';
import DistributionChart from '../components/DistributionChart.vue';

const loading = ref(false);
const data = ref([]);
const date = ref(null);
const keyword = ref('');
const activeResult = ref(null); // active 模板筛选结果
const dialogVisible = ref(false);

const fmtNum = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
const fmtPct = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);

const loadList = async () => {
  loading.value = true;
  try {
    const res = await getCbListLatest();
    data.value = res ?? [];
    // 快照日期从第一条数据取
    if (res && res.length) {
      date.value = res[0].trade_date;
    }
  } catch (e) {
    ElMessage.error('加载转债列表失败');
  } finally {
    loading.value = false;
  }
};

const runScreen = async () => {
  loading.value = true;
  try {
    const res = await screenBondsActive();
    activeResult.value = res;
    dialogVisible.value = true;
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '筛选失败');
  } finally {
    loading.value = false;
  }
};

const filtered = computed(() => {
  if (!keyword.value) return data.value;
  const kw = keyword.value.trim();
  return data.value.filter(
    (r) =>
      String(r.bond_id || '').includes(kw) ||
      String(r.bond_nm || '').includes(kw),
  );
});

// 表格列定义（Element Plus）
const columns = [
  { prop: 'bond_id', label: '代码', width: 100 },
  { prop: 'bond_nm', label: '名称', width: 120 },
  { prop: 'price', label: '价格', width: 90, align: 'right', fmt: (v) => fmtNum(v, 3) },
  { prop: 'increase_rt', label: '涨跌幅', width: 90, align: 'right', fmt: fmtPct, color: (v) => (v > 0 ? '#f56c6c' : v < 0 ? '#67c23a' : '') },
  { prop: 'dblow', label: '双低值', width: 90, align: 'right', fmt: (v) => fmtNum(v, 2), sortable: true },
  { prop: 'premium_rt', label: '溢价率', width: 90, align: 'right', fmt: fmtPct, sortable: true },
  { prop: 'curr_iss_amt', label: '规模(亿)', width: 100, align: 'right', fmt: (v) => fmtNum(v, 2), sortable: true },
  { prop: 'convert_value', label: '转股价值', width: 95, align: 'right', fmt: (v) => fmtNum(v, 2) },
  { prop: 'year_left', label: '剩余年限', width: 90, align: 'right', fmt: (v) => fmtNum(v, 2) },
  { prop: 'rating_cd', label: '评级', width: 75, align: 'center' },
];

onMounted(loadList);
</script>

<template>
  <div>
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索代码 / 名称"
        clearable
        style="width: 220px"
      />
      <span v-if="date" class="date-tip">
        数据日期：{{ date }}　共 {{ data.length }} 只
      </span>
      <el-button type="primary" :loading="loading" @click="runScreen">
        按当前策略筛选
      </el-button>
    </div>

    <!-- 全量列表 -->
    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="filtered"
        stripe
        height="calc(100vh - 240px)"
        size="small"
        :default-sort="{ prop: 'dblow', order: 'ascending' }"
      >
        <el-table-column
          v-for="col in columns"
          :key="col.prop"
          :prop="col.prop"
          :label="col.label"
          :width="col.width"
          :align="col.align || 'left'"
          :sortable="col.sortable || false"
        >
          <template #default="{ row }">
            <span
              v-if="col.color"
              :style="{ color: col.color(row[col.prop]) }"
            >
              {{ col.fmt ? col.fmt(row[col.prop]) : row[col.prop] }}
            </span>
            <span v-else>
              {{ col.fmt ? col.fmt(row[col.prop]) : row[col.prop] }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 筛选结果弹层 -->
    <el-dialog
      v-model="dialogVisible"
      title="筛选结果"
      width="80%"
      top="5vh"
    >
      <div v-if="activeResult">
        <p class="result-summary">
          全量 {{ activeResult.total_all }} 只
          → 通过排除 {{ activeResult.total_filtered }} 只
          → 入选 <b>{{ activeResult.top_n }}</b> 只
          <span v-if="activeResult.keep_n > activeResult.top_n">
            （容差保留 {{ activeResult.keep_n - activeResult.top_n }} 只）
          </span>
        </p>
        <!-- 入选转债分布图 -->
        <div class="charts-row">
          <DistributionChart
            :rows="activeResult.rows"
            field="dblow"
            title="入选转债双低值分布"
          />
          <DistributionChart
            :rows="activeResult.rows"
            field="premium_rt"
            title="入选转债溢价率分布"
          />
        </div>
        <el-table
          :data="activeResult.rows"
          stripe
          size="small"
          max-height="60vh"
          :row-class-name="({ row }) => (row.selected ? 'bond-row-selected' : '')"
        >
          <el-table-column prop="rank" label="排名" width="60" align="center" />
          <el-table-column prop="code" label="代码" width="100" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column prop="price" label="价格" width="80" align="right">
            <template #default="{ row }">{{ fmtNum(row.price, 2) }}</template>
          </el-table-column>
          <el-table-column prop="dblow" label="双低" width="80" align="right">
            <template #default="{ row }">{{ fmtNum(row.dblow, 1) }}</template>
          </el-table-column>
          <el-table-column prop="premium_rt" label="溢价率" width="90" align="right">
            <template #default="{ row }">{{ fmtPct(row.premium_rt) }}</template>
          </el-table-column>
          <el-table-column prop="curr_iss_amt" label="规模(亿)" width="90" align="right">
            <template #default="{ row }">{{ fmtNum(row.curr_iss_amt, 1) }}</template>
          </el-table-column>
          <el-table-column prop="convert_value" label="转股价值" width="90" align="right">
            <template #default="{ row }">{{ fmtNum(row.convert_value, 1) }}</template>
          </el-table-column>
          <el-table-column prop="year_left" label="剩余年限" width="85" align="right">
            <template #default="{ row }">{{ fmtNum(row.year_left, 1) }}</template>
          </el-table-column>
          <el-table-column prop="pb" label="市净率" width="80" align="right">
            <template #default="{ row }">{{ fmtNum(row.pb, 2) }}</template>
          </el-table-column>
          <el-table-column prop="rating" label="评级" width="70" align="center" />
          <el-table-column prop="redeem" label="强赎" width="110" />
          <el-table-column prop="total_score" label="得分" width="80" align="right">
            <template #default="{ row }">{{ fmtNum(row.total_score, 1) }}</template>
          </el-table-column>
          <el-table-column label="入选" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-tag v-if="row.selected" type="success" size="small">✓ 入选</el-tag>
              <el-tag v-else-if="row.holdable" type="info" size="small">容差保留</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.date-tip {
  color: #909399;
  font-size: 13px;
  flex: 1;
}

.result-summary {
  margin-bottom: 12px;
  color: #606266;
  font-size: 14px;
}

.result-summary b {
  color: #67c23a;
}

.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}

/* 移动端适配 */
@media (max-width: 767px) {
  .toolbar {
    flex-wrap: wrap;
    gap: 10px;
  }

  .toolbar :deep(.el-input) {
    width: 100% !important;
  }

  .date-tip {
    flex-basis: 100%;
  }

  .charts-row {
    grid-template-columns: 1fr;
  }

  :deep(.el-dialog) {
    width: 95% !important;
  }
}
</style>
