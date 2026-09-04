<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
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

// 旧版 localStorage 存的是数字+可能混入 el-input-number 的 0 值, 版本号升位直接作废重建
const FILTERS_STORAGE_VERSION = 'v2';

function loadSavedFilters() {
  try {
    const saved = localStorage.getItem(FILTERS_STORAGE_KEY);
    if (!saved) return null;
    const parsed = JSON.parse(saved);
    if (parsed?._v !== FILTERS_STORAGE_VERSION) return null; // 旧格式作废
    return parsed;
  } catch (e) {
    return null;
  }
}

const RATING_OPTIONS = ['AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-'];

// 默认预置(对齐集思录截图): 价格≤120 / 溢价率≤30 / 评级全选
// 数值条件用字符串存(普通输入框所见即所得, 无 .00 强制格式化), 查询时才转数字
// premium_rt 为转股溢价率(现价/转换价值-1, 集思录字段实测吻合), 表单标签写全称防歧义
const defaultFilters = () => ({
  price_min: '',
  price_max: '120',
  premium_rt_max: '30',
  curr_iss_amt_max: '',
  ytm_min: '',
  year_left_min: '',
  year_left_max: '',
  ratings: [...RATING_OPTIONS],
});
const filters = ref(defaultFilters());

// 恢复上次条件(集思录同款体验: 记住筛选)
onMounted(() => {
  const saved = loadSavedFilters();
  if (saved) {
    filters.value = { ...defaultFilters(), ...saved };
  }
});

watch(filters, (f) => {
  try {
    localStorage.setItem(FILTERS_STORAGE_KEY, JSON.stringify({ ...f, _v: FILTERS_STORAGE_VERSION }));
  } catch (e) { /* 忽略持久化失败 */ }
}, { deep: true });

const resetFilters = () => {
  filters.value = defaultFilters();
};

// 查询前把字符串条件转数字: 空串/非法输入 → 剔除该条件
const normalizeFilters = (f) => {
  const numKeys = [
    'price_min', 'price_max', 'premium_rt_max', 'curr_iss_amt_max',
    'ytm_min', 'year_left_min', 'year_left_max',
  ];
  const out = { ratings: [...(f.ratings || [])] };
  for (const k of numKeys) {
    const v = parseFloat(String(f[k]).trim());
    if (!Number.isNaN(v)) out[k] = v;
  }
  return out;
};

const fmtNum = (v, d = 2) => (v == null ? '—' : Number(v).toFixed(d));
const fmtPct = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);

const runScreen = async () => {
  loading.value = true;
  try {
    result.value = await screenBondsIntraday(normalizeFilters(filters.value));
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '实时数据拉取失败,请重试');
  } finally {
    loading.value = false;
  }
};

// ── 结果排序 ─────────────────────────────────────────
// 不分页: 筛选后通常仅十几条, 一页全展示, 底部仅一行汇总
// 排序作用于全量结果, 默认到期收益率降序
const tableRef = ref(null);
const sortState = ref({ prop: 'ytm_simple', order: 'descending' }); // 与 default-sort 一致

const sortedRows = computed(() => {
  const rows = [...(result.value?.rows ?? [])];
  const { prop, order } = sortState.value;
  if (!prop || !order) return rows;
  const dir = order === 'ascending' ? 1 : -1;
  rows.sort((a, b) => {
    const va = a[prop];
    const vb = b[prop];
    if (va == null && vb == null) return 0;
    if (va == null) return 1; // 缺数据沉底
    if (vb == null) return -1;
    return va < vb ? -dir : va > vb ? dir : 0;
  });
  return rows;
});

const onSortChange = ({ prop, order }) => {
  sortState.value = { prop, order };
};

// ── 表格视口自适应高度 ────────────────────────────────
// 不分页后行数不定, max-height 动态取"视口高度-表格顶部偏移", 数据多时表头固定+内部滚动
const tableMaxHeight = ref(600);
const updateTableHeight = () => {
  const el = document.querySelector('.result-card .el-table');
  const top = el ? el.getBoundingClientRect().top : 400;
  tableMaxHeight.value = Math.max(320, window.innerHeight - top - 24);
};
onMounted(() => {
  window.addEventListener('resize', updateTableHeight);
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateTableHeight);
});
watch(result, () => {
  // 查询结果渲染后按实际位置重算一次
  requestAnimationFrame(updateTableHeight);
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
            <el-input
              v-model="filters.price_min"
              placeholder="最低" size="small" clearable
            />
            <span class="sep">-</span>
            <el-input
              v-model="filters.price_max"
              placeholder="最高" size="small" clearable
            />
            <span class="unit">元</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">转股溢价率≤</div>
          <div class="jfilter-control range-row">
            <el-input
              v-model="filters.premium_rt_max"
              placeholder="不限" size="small" clearable
            />
            <span class="unit">%</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">剩余规模≤</div>
          <div class="jfilter-control range-row">
            <el-input
              v-model="filters.curr_iss_amt_max"
              placeholder="不限" size="small" clearable
            />
            <span class="unit">亿元</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">到期收益率≥</div>
          <div class="jfilter-control range-row">
            <el-input
              v-model="filters.ytm_min"
              placeholder="不限" size="small" clearable
            />
            <span class="unit">%</span>
          </div>
        </div>
        <div class="jfilter-row">
          <div class="jfilter-label">剩余年限</div>
          <div class="jfilter-control range-row">
            <el-input
              v-model="filters.year_left_min"
              placeholder="最低" size="small" clearable
            />
            <span class="sep">-</span>
            <el-input
              v-model="filters.year_left_max"
              placeholder="最高" size="small" clearable
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
        实时数据来自集思录，每次查询现拉最新行情（约 1~2 秒）；到期收益率 = (到期赎回价 − 现价) ÷ 现价，即持有到期的总回报率（未年化、不计利息税）；转股溢价率 = 现价 ÷ 转换价值 − 1
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
        实时 {{ result.total_all }} 只 → 符合条件 <b>{{ result.total_filtered }}</b> 只 · 默认按到期收益率降序（点表头可换序）
      </p>
      <el-table
        ref="tableRef"
        :data="sortedRows"
        stripe
        size="small"
        :max-height="tableMaxHeight"
        :default-sort="{ prop: 'ytm_simple', order: 'descending' }"
        @sort-change="onSortChange"
      >
        <!-- 序/名称/代码均固定左侧: Element Plus 会把 fixed 列排到最前, 序号想排第一必须同为 fixed -->
        <el-table-column label="序" width="45" align="center" fixed="left">
          <template #default="{ $index }">{{ $index + 1 }}</template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="100" fixed="left" />
        <el-table-column prop="code" label="代码" width="84" fixed="left" />
        <el-table-column prop="price" label="现价" width="80" align="right">
          <template #default="{ row }">{{ fmtNum(row.price, 2) }}</template>
        </el-table-column>
        <el-table-column prop="change_rt" label="涨跌幅" width="84" align="right" sortable>
          <template #default="{ row }">
            <span
              v-if="row.change_rt != null"
              :style="{ color: row.change_rt > 0 ? '#f56c6c' : row.change_rt < 0 ? '#67c23a' : '' }"
            >{{ fmtPct(row.change_rt) }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="redeem_price" label="到期赎回价" width="95" align="right">
          <template #default="{ row }">{{ fmtNum(row.redeem_price, 1) }}</template>
        </el-table-column>
        <el-table-column prop="ytm_simple" label="到期收益率" width="96" align="right" sortable>
          <template #default="{ row }">
            <span
              v-if="row.ytm_simple != null"
              :style="{ color: row.ytm_simple >= 0 ? '#67c23a' : '#f56c6c' }"
            >{{ fmtPct(row.ytm_simple) }}</span>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="year_left" label="剩余年限" width="84" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.year_left, 1) }}</template>
        </el-table-column>
        <el-table-column prop="premium_rt" label="转股溢价率" width="96" align="right" sortable>
          <template #default="{ row }">{{ fmtPct(row.premium_rt) }}</template>
        </el-table-column>
        <el-table-column prop="curr_iss_amt" label="规模(亿)" width="84" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.curr_iss_amt, 1) }}</template>
        </el-table-column>
        <el-table-column prop="rating" label="评级" width="60" align="center" />
        <el-table-column prop="redeem" label="强赎" width="96" />
      </el-table>
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
  /* PC 端尽量用满宽度(侧栏 220px), 表格 12 列无需横向拖拽; 手机端回落 100% */
  max-width: 1400px;
}

/* ── 集思录风格竖排筛选表单 ── */
.filter-card {
  margin-bottom: 16px;
  /* 竖排表单限宽, 避免宽屏下被拉得过长; 结果表格仍用满页面宽度 */
  max-width: 640px;
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
