<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { ElMessage } from 'element-plus';
import { screenBondsIntraday } from '../api';

const loading = ref(false);
const result = ref(null); // 盘中筛选结果 { total_all, total_filtered, rows, intraday }

// ── 筛选条件(仿集思录手机端) ─────────────────────────
// 页面唯一筛选机制: 实时拉集思录 → 纯条件过滤, 不打分不排序
// 顺序=集思录自然顺序(默认双低升序); 结果页内直出, 不用弹窗
// 注: 抓取层已有评级白名单(AAA~A-, 剔除无评级/BB 及以下),
//     这里把白名单内 7 档的选择权完整交给用户, 默认全选
const FILTERS_STORAGE_KEY = 'cb-intraday-filters';

const RATING_OPTIONS = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-'];

// 默认预置(对齐集思录截图): 价格≤120 / 溢价率≤30 / 评级全选
const defaultFilters = () => ({
  price_min: null,
  price_max: 120,
  premium_rt_max: 30,
  curr_iss_amt_max: null,
  ytm_min: null,
  year_left_min: null,
  year_left_max: null,
  ratings: [...RATING_OPTIONS],
});
const filters = ref(defaultFilters());

// 恢复上次条件(集思录同款体验: 记住筛选)
onMounted(() => {
  try {
    const saved = localStorage.getItem(FILTERS_STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      filters.value = { ...defaultFilters(), ...parsed };
    }
  } catch (e) { /* 忽略恢复失败 */ }
});

watch(filters, (f) => {
  try {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify(f));
  } catch (e) { /* 忽略持久化失败 */ }
}, { deep: true });

const resetFilters = () => {
  filters.value = defaultFilters();
};

const fmtNum = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
const fmtPct = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);

const runScreen = async () => {
  loading.value = true;
  try {
    result.value = await screenBondsIntraday(filters.value);
    page.value = 1; // 新查询重置分页
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '实时数据拉取失败,请重试');
  } finally {
    loading.value = false;
  }
};

// ── 结果分页: 每页 10 条, 手动翻页 ────────────────────
const PAGE_SIZE = 10;
const page = ref(1);
const totalPages = computed(() => {
  const n = result.value?.total_filtered ?? 0;
  return Math.max(1, Math.ceil(n / PAGE_SIZE));
});
const pagedRows = computed(() => {
  const rows = result.value?.rows ?? [];
  const start = (page.value - 1) * PAGE_SIZE;
  return rows.slice(start, start + PAGE_SIZE);
});
</script>

<template>
  <div class="bonds-page">
    <!-- 筛选表单(集思录手机端风格: 竖排一行一项) -->
    <el-card shadow="never" class="filter-card">
      <div class="jfilter">
        <div class="jfilter-row">
          <div class="jfilter-label">评级</div>
          <div class="jfilter-control">
            <el-select
              v-model="filters.ratings"
              multiple
              collapse-tags
              clearable
              placeholder="不限"
              size="small"
              style="width: 100%"
            >
              <el-option v-for="r in RATING_OPTIONS" :key="r" :label="r" :value="r" />
            </el-select>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">转债价格</div>
          <div class="jfilter-control range-row">
            <el-input-number
              v-model="filters.price_min"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">-</span>
            <el-input-number
              v-model="filters.price_max"
              :min="0" :max="1000" :precision="2" :controls="false"
              placeholder="最高" size="small"
            />
            <span class="unit">元</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">溢价率≤</div>
          <div class="jfilter-control range-row">
            <el-input-number
              v-model="filters.premium_rt_max"
              :min="-100" :max="1000" :precision="2" :controls="false"
              placeholder="不限" size="small"
            />
            <span class="unit">%</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">剩余规模≤</div>
          <div class="jfilter-control range-row">
            <el-input-number
              v-model="filters.curr_iss_amt_max"
              :min="0" :max="500" :precision="2" :controls="false"
              placeholder="不限" size="small"
            />
            <span class="unit">亿元</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">到期收益率≥</div>
          <div class="jfilter-control range-row">
            <el-input-number
              v-model="filters.ytm_min"
              :min="-100" :max="100" :precision="2" :controls="false"
              placeholder="不限" size="small"
            />
            <span class="unit">%</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">剩余年限</div>
          <div class="jfilter-control range-row">
            <el-input-number
              v-model="filters.year_left_min"
              :min="0" :max="10" :precision="1" :controls="false"
              placeholder="最低" size="small"
            />
            <span class="sep">-</span>
            <el-input-number
              v-model="filters.year_left_max"
              :min="0" :max="10" :precision="1" :controls="false"
              placeholder="最高" size="small"
            />
            <span class="unit">年</span>
          </div>
        </div>
        <div class="jfilter-actions">
          <el-button
            type="primary"
            :loading="loading"
            class="btn-query"
            @click="runScreen"
          >
            查 询
          </el-button>
          <el-button @click="resetFilters">重置</el-button>
        </div>
      </div>
      <div class="form-tip">
        实时数据来自集思录，每次查询现拉最新行情（约 1~2 秒）；到期收益率 = (到期赎回价 − 现价) ÷ 现价，即持有到期的总回报率（未年化、不计利息税）
      </div>
    </el-card>

    <!-- 结果区: 页内直出 -->
    <el-card v-if="result" shadow="never" class="result-card">
      <div class="mode-bar mode-intraday">
        <span class="dot" /> 盘中实时
        <span class="meta">
          行情时点 {{ result.intraday?.quote_time || '—' }} · 拉取于 {{ result.intraday?.fetched_at }} · 实时 {{ result.intraday?.total_live }} 只<template v-if="!result.intraday?.redeem_loaded"> · 强赎数据缺失</template>
        </span>
      </div>
      <p class="result-summary">
        实时 {{ result.total_all }} 只 → 符合条件 <b>{{ result.total_filtered }}</b> 只
      </p>
      <el-table
        :data="pagedRows"
        stripe
        size="small"
        max-height="65vh"
      >
        <el-table-column prop="code" label="代码" width="90" fixed="left" />
        <el-table-column prop="name" label="名称" width="100" fixed="left" />
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
        <el-table-column prop="ytm_simple" label="到期收益率" width="100" align="right" sortable>
          <template #default="{ row }">
            <span
              v-if="row.ytm_simple != null"
              :style="{ color: row.ytm_simple >= 0 ? '#67c23a' : '#f56c6c' }"
            >{{ fmtPct(row.ytm_simple) }}</span>
            <span v-else>—</span>
          </template>
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
        <el-button size="small" :disabled="page <= 1" @click="page -= 1">
          上一页
        </el-button>
        <span class="pager-info">
          第 {{ page }} / {{ totalPages }} 页 · 共 {{ result.total_filtered }} 只
        </span>
        <el-button size="small" :disabled="page >= totalPages" @click="page += 1">
          下一页
        </el-button>
      </div>
    </el-card>
    <el-empty
      v-else-if="!loading"
      description="设置条件后点击「查询」"
      :image-size="88"
    />
  </div>
</template>

<style scoped>
.bonds-page {
  max-width: 960px;
}

/* ── 集思录风格竖排筛选表单 ── */
.filter-card {
  margin-bottom: 16px;
}

.jfilter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid #f2f3f5;
}

.jfilter-row:last-of-type {
  border-bottom: none;
}

.jfilter-label {
  flex: 0 0 96px;
  font-size: 14px;
  color: #303133;
}

.jfilter-control {
  flex: 1;
  min-width: 0;
}

.range-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.range-row .el-input-number {
  flex: 1;
  width: 100%;
}

.range-row .sep {
  color: #c0c4cc;
  flex: 0 0 auto;
}

.range-row .unit {
  flex: 0 0 auto;
  font-size: 13px;
  color: #909399;
  width: 28px;
}

.jfilter-actions {
  display: flex;
  gap: 12px;
  padding-top: 14px;
}

.jfilter-actions .btn-query {
  flex: 1;
}

.form-tip {
  margin-top: 12px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

/* ── 结果区 ── */
.result-card {
  margin-bottom: 16px;
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

.result-summary {
  margin-bottom: 12px;
  color: #606266;
  font-size: 14px;
}

.result-summary b {
  color: #67c23a;
}

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

/* ── 移动端适配 ── */
@media (max-width: 767px) {
  .jfilter-label {
    flex-basis: 88px;
    font-size: 13px;
  }

  .range-row .unit {
    width: 24px;
    font-size: 12px;
  }
}
</style>
