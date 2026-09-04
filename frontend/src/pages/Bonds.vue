<script setup>
import { ref, computed, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { getCbListLatest, screenBondsActive, screenBondsIntraday } from '../api';
import DistributionChart from '../components/DistributionChart.vue';

const loading = ref(false);
const data = ref([]);
const date = ref(null);
const keyword = ref('');
const activeResult = ref(null); // active 模板筛选结果
const dialogVisible = ref(false);

// ── 盘中选债模式 ─────────────────────────────────────
// 模式开关: 'close' = 收盘快照(读库, 快, 走选债因子模板) | 'intraday' = 实时拉集思录(1~2s, 页面条件纯过滤)
// 盘中数据不落库, 每次查询都是当次请求的实时值; 不打分不排序, 顺序=集思录自然顺序(默认双低升序)
const intradayMode = ref(false);
const intradayMeta = ref(null); // { fetched_at, quote_time, total_live, redeem_loaded }

// 盘中筛选条件: 六字段区间 + 评级多选, 空值 = 不限制
const defaultIntradayFilters = () => ({
  price_min: null,
  price_max: null,
  convert_value_min: null,
  convert_value_max: null,
  premium_rt_min: null,
  premium_rt_max: null,
  year_left_min: null,
  year_left_max: null,
  curr_iss_amt_min: null,
  curr_iss_amt_max: null,
  ratings: [],
});
const intradayFilters = ref(defaultIntradayFilters());

const RATING_OPTIONS = ['AAA', 'AA+', 'AA', 'AA-', 'A+'];

const resetIntradayFilters = () => {
  intradayFilters.value = defaultIntradayFilters();
};

// 条件是否为空(全空时按钮文案提示"拉全量")
const filtersEmpty = computed(() => {
  const f = intradayFilters.value;
  return !f.ratings.length
    && ['price', 'convert_value', 'premium_rt', 'year_left', 'curr_iss_amt'].every(
      (k) => f[`${k}_min`] == null && f[`${k}_max`] == null,
    );
});

const fmtNum = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
const fmtPct = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);

// 盘中结果分页: 每页 10 条, 手动翻页(每次查询重置回第 1 页)
const PAGE_SIZE = 10;
const intradayPage = ref(1);
const intradayTotalPages = computed(() => {
  const n = activeResult.value?.total_filtered ?? 0;
  return Math.max(1, Math.ceil(n / PAGE_SIZE));
});
const pagedIntradayRows = computed(() => {
  const rows = activeResult.value?.rows ?? [];
  const start = (intradayPage.value - 1) * PAGE_SIZE;
  return rows.slice(start, start + PAGE_SIZE);
});

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
    const res = intradayMode.value
      ? await screenBondsIntraday(intradayFilters.value)
      : await screenBondsActive();
    activeResult.value = res;
    intradayMeta.value = res.intraday ?? null;
    intradayPage.value = 1; // 新查询重置分页
    dialogVisible.value = true;
  } catch (e) {
    ElMessage.error(
      e?.response?.data?.detail
        || (intradayMode.value ? '实时数据拉取失败,请重试' : '筛选失败'),
    );
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
      <div class="toolbar-actions">
        <el-switch
          v-model="intradayMode"
          active-text="盘中选债"
          inline-prompt
          style="--el-switch-on-color: #e6a23c"
        />
        <el-button
          :type="intradayMode ? 'warning' : 'primary'"
          :loading="loading"
          @click="runScreen"
        >
          {{ intradayMode ? (filtersEmpty ? '拉取全量' : '盘中实时筛选') : '按当前策略筛选' }}
        </el-button>
      </div>
    </div>

    <!-- 盘中筛选条件表单(仅盘中模式显示) -->
    <el-card v-if="intradayMode" shadow="never" class="intraday-form">
      <template #header>
        <div class="form-header">
          <span>盘中筛选条件</span>
          <el-button link type="primary" size="small" @click="resetIntradayFilters">
            重置
          </el-button>
        </div>
      </template>
      <div class="filter-grid">
        <div class="filter-item">
          <label>现价（元）</label>
          <div class="range-inputs">
            <el-input-number
              v-model="intradayFilters.price_min"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="intradayFilters.price_max"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最高" size="small"
            />
          </div>
        </div>
        <div class="filter-item">
          <label>转换价值</label>
          <div class="range-inputs">
            <el-input-number
              v-model="intradayFilters.convert_value_min"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="intradayFilters.convert_value_max"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最高" size="small"
            />
          </div>
        </div>
        <div class="filter-item">
          <label>溢价率（%）</label>
          <div class="range-inputs">
            <el-input-number
              v-model="intradayFilters.premium_rt_min"
              :min="-100" :max="1000" :precision="2" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="intradayFilters.premium_rt_max"
              :min="-100" :max="1000" :precision="2" :controls="false"
              placeholder="最高" size="small"
            />
          </div>
        </div>
        <div class="filter-item">
          <label>剩余年限（年）</label>
          <div class="range-inputs">
            <el-input-number
              v-model="intradayFilters.year_left_min"
              :min="0" :max="10" :precision="1" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="intradayFilters.year_left_max"
              :min="0" :max="10" :precision="1" :controls="false"
              placeholder="最高" size="small"
            />
          </div>
        </div>
        <div class="filter-item">
          <label>剩余规模（亿）</label>
          <div class="range-inputs">
            <el-input-number
              v-model="intradayFilters.curr_iss_amt_min"
              :min="0" :max="500" :precision="2" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">~</span>
            <el-input-number
              v-model="intradayFilters.curr_iss_amt_max"
              :min="0" :max="500" :precision="2" :controls="false"
              placeholder="最高" size="small"
            />
          </div>
        </div>
        <div class="filter-item">
          <label>评级</label>
          <el-select
            v-model="intradayFilters.ratings"
            multiple
            collapse-tags
            clearable
            placeholder="不限"
            size="small"
            style="width: 100%"
          >
            <el-option
              v-for="r in RATING_OPTIONS"
              :key="r"
              :label="r"
              :value="r"
            />
          </el-select>
        </div>
      </div>
      <div class="form-tip">
        纯条件过滤：返回全部通过筛选的转债（不打分不排序，按集思录默认双低升序展示）
      </div>
    </el-card>

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
      :title="intradayMode ? '盘中选债结果' : '筛选结果'"
      width="80%"
      top="5vh"
    >
      <div v-if="activeResult">
        <!-- 数据模式条: 盘中实时(橙色) / 收盘快照(默认) -->
        <div v-if="intradayMeta" class="mode-bar mode-intraday">
          <span class="dot" /> 盘中实时
          <span class="meta">行情时点 {{ intradayMeta.quote_time || '—' }} · 拉取于 {{ intradayMeta.fetched_at }} · 实时 {{ intradayMeta.total_live }} 只<template v-if="!intradayMeta.redeem_loaded"> · 强赎数据缺失</template></span>
        </div>
        <div v-else-if="date" class="mode-bar">
          收盘快照 · 数据日期 {{ date }}
        </div>

        <!-- ── 盘中模式结果 ── -->
        <template v-if="intradayMeta">
          <p class="result-summary">
            实时 {{ activeResult.total_all }} 只 → 符合条件 <b>{{ activeResult.total_filtered }}</b> 只
          </p>
          <el-table
            :data="pagedIntradayRows"
            stripe
            size="small"
            max-height="60vh"
          >
            <el-table-column prop="code" label="代码" width="100" />
            <el-table-column prop="name" label="名称" width="110" />
            <el-table-column prop="price" label="现价" width="80" align="right">
              <template #default="{ row }">{{ fmtNum(row.price, 2) }}</template>
            </el-table-column>
            <el-table-column prop="change_rt" label="涨跌幅" width="85" align="right" sortable>
              <template #default="{ row }">
                <span
                  v-if="row.change_rt != null"
                  :style="{ color: row.change_rt > 0 ? '#f56c6c' : row.change_rt < 0 ? '#67c23a' : '' }"
                >{{ fmtPct(row.change_rt) }}</span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column prop="convert_value" label="转换价值" width="90" align="right" sortable>
              <template #default="{ row }">{{ fmtNum(row.convert_value, 1) }}</template>
            </el-table-column>
            <el-table-column prop="premium_rt" label="溢价率" width="90" align="right" sortable>
              <template #default="{ row }">{{ fmtPct(row.premium_rt) }}</template>
            </el-table-column>
            <el-table-column prop="year_left" label="剩余年限" width="85" align="right" sortable>
              <template #default="{ row }">{{ fmtNum(row.year_left, 1) }}</template>
            </el-table-column>
            <el-table-column prop="curr_iss_amt" label="规模(亿)" width="90" align="right" sortable>
              <template #default="{ row }">{{ fmtNum(row.curr_iss_amt, 1) }}</template>
            </el-table-column>
            <el-table-column prop="rating" label="评级" width="70" align="center" />
            <el-table-column prop="redeem_price" label="赎回价" width="80" align="right">
              <template #default="{ row }">{{ fmtNum(row.redeem_price, 1) }}</template>
            </el-table-column>
            <el-table-column prop="redeem_gap" label="保本价差" width="90" align="right" sortable>
              <template #default="{ row }">
                <span
                  v-if="row.redeem_gap != null"
                  :style="{ color: row.redeem_gap >= 0 ? '#67c23a' : '#f56c6c' }"
                >{{ row.redeem_gap >= 0 ? '+' : '' }}{{ fmtNum(row.redeem_gap, 1) }}</span>
                <span v-else>—</span>
              </template>
            </el-table-column>
            <el-table-column prop="redeem" label="强赎" width="110" />
          </el-table>
          <div class="pager-bar">
            <el-button
              size="small"
              :disabled="intradayPage <= 1"
              @click="intradayPage -= 1"
            >
              上一页
            </el-button>
            <span class="pager-info">
              第 {{ intradayPage }} / {{ intradayTotalPages }} 页 · 共 {{ activeResult.total_filtered }} 只
            </span>
            <el-button
              size="small"
              :disabled="intradayPage >= intradayTotalPages"
              @click="intradayPage += 1"
            >
              下一页
            </el-button>
          </div>
        </template>

        <!-- ── 收盘模式结果(原打分视图) ── -->
        <template v-else>
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
            <el-table-column prop="redeem_price" label="赎回价" width="80" align="right">
              <template #default="{ row }">{{ fmtNum(row.redeem_price, 1) }}</template>
            </el-table-column>
            <el-table-column prop="redeem_gap" label="保本价差" width="90" align="right" sortable>
              <template #default="{ row }">
                <span
                  v-if="row.redeem_gap != null"
                  :style="{ color: row.redeem_gap >= 0 ? '#67c23a' : '#f56c6c' }"
                >{{ row.redeem_gap >= 0 ? '+' : '' }}{{ fmtNum(row.redeem_gap, 1) }}</span>
                <span v-else>—</span>
              </template>
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
        </template>
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

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
}

.mode-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 6px;
  background: rgba(144, 147, 153, 0.08);
  color: #606266;
  font-size: 13px;
  margin-bottom: 12px;
}

.mode-bar.mode-intraday {
  background: rgba(230, 162, 60, 0.1);
  color: #b88230;
  font-weight: 600;
}

.mode-bar .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e6a23c;
  animation: pulse 1.6s ease-in-out infinite;
}

.mode-bar .meta {
  font-weight: 400;
  font-size: 12px;
  opacity: 0.85;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.date-tip {
  color: #909399;
  font-size: 13px;
  flex: 1;
}

/* 盘中筛选条件表单 */
.intraday-form {
  margin-bottom: 16px;
}

.intraday-form :deep(.el-card__header) {
  padding: 10px 16px;
}

.intraday-form :deep(.el-card__body) {
  padding: 14px 16px;
}

.form-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  font-size: 14px;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px 20px;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.filter-item > label {
  font-size: 12px;
  color: #909399;
}

.range-inputs {
  display: flex;
  align-items: center;
  gap: 6px;
}

.range-inputs .el-input-number {
  flex: 1;
  width: 100%;
}

.range-inputs .sep {
  color: #c0c4cc;
  flex: 0 0 auto;
}

.form-tip {
  margin-top: 12px;
  font-size: 12px;
  color: #909399;
}

/* 盘中结果分页条 */
.pager-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 14px;
}

.pager-info {
  font-size: 13px;
  color: #606266;
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

  .toolbar-actions {
    flex: 1;
    justify-content: space-between;
  }

  .charts-row {
    grid-template-columns: 1fr;
  }

  /* 盘中筛选表单: 两列 → 拥挤时单列 */
  .filter-grid {
    grid-template-columns: 1fr 1fr;
    gap: 10px 12px;
  }

  @media (max-width: 374px) {
    .filter-grid {
      grid-template-columns: 1fr;
    }
  }

  :deep(.el-dialog) {
    width: 95% !important;
  }
}
</style>
