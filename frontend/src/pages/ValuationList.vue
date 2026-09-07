<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import { getValuationSnapshot, getDividendYield, getEquityBond } from '../api';
import { judgeByPercentile, fmtNum, fmtPct } from '../utils/valuation';

const router = useRouter();

const loading = ref(false);
const errorMsg = ref('');
const updateDate = ref('');
const rows = ref([]);

// 移动端断点(与 AppLayout 一致: <768px 视为手机)
// 手机端不用表格(7 列挤不下), 改卡片列表: 一行一指数, 指标网格铺开
const isMobile = ref(false);
let mq = null;
const updateIsMobile = () => {
  isMobile.value = window.innerWidth < 768;
};

async function loadData() {
  loading.value = true;
  errorMsg.value = '';
  try {
    // 并行拉: latest=true 只取 8 只最新(4KB, 全量历史是 13MB) + 股息率 + 股债差
    const [snap, dy, eb] = await Promise.all([
      getValuationSnapshot({ latest: true }),
      getDividendYield(),
      getEquityBond(),
    ]);

    // 股息率按 index_code 建索引(与估值快照统一用 config code, 见 valuation_tasks 注释)
    const dyMap = {};
    for (const r of dy || []) dyMap[r.index_code] = r;

    // 股债收益差(EP-10Y, 越高股票越便宜)按 index_code 建索引
    const ebMap = {};
    for (const r of eb || []) ebMap[r.index_code] = r;

    rows.value = (snap || []).map((r) => {
      // 口径定版(2026-09-07): PE/PB 分位统一用 5 年窗口(数据源易方达原生带 5Y 分位)
      const pe5y = r.pe_percentile?.['5y'] ?? null;
      const pb5y = r.pb_percentile?.['5y'] ?? null;
      const d = dyMap[r.index_code];
      const ebRow = ebMap[r.index_code];
      return {
        code: r.index_code,
        name: r.index_name,
        date: r.trade_date,
        pe: r.pe,
        pb: r.pb,
        ps: r.ps,
        pePct5y: pe5y,
        pbPct5y: pb5y,
        dividend: d?.dividend_yield ?? null,
        dividendPct5y: d?.percentile?.['5y'] ?? null,
        ebSpread: ebRow?.spread?.current ?? null,
        ebSpreadPct5y: ebRow?.spread?.percentiles?.['5y'] ?? null,
        judgment: judgeByPercentile(pe5y),
      };
    });
    updateDate.value = rows.value[0]?.date || '';
  } catch (e) {
    errorMsg.value = e?.response?.data?.detail || e?.message || '拉取失败,请检查后端日志';
    rows.value = [];
  } finally {
    loading.value = false;
  }
}

// 移动端手动排序(没有 el-table 的内置排序): 默认 PE-5y 分位升序, 最便宜的在前
const sortedRows = computed(() => {
  const list = [...rows.value];
  list.sort((a, b) => {
    const va = a.pePct5y;
    const vb = b.pePct5y;
    if (va == null && vb == null) return 0;
    if (va == null) return 1; // 无数据沉底
    if (vb == null) return -1;
    return va - vb;
  });
  return list;
});

function gotoDetail(code) {
  router.push(`/valuation/${code}`);
}

onMounted(() => {
  loadData();
  updateIsMobile();
  mq = window.matchMedia('(max-width: 767px)');
  mq.addEventListener('change', updateIsMobile);
});

onBeforeUnmount(() => {
  mq?.removeEventListener('change', updateIsMobile);
});
</script>

<template>
  <div class="valuation-list-page">
    <div class="page-head">
      <div class="head-left">
        <h3 class="title">指数估值排行</h3>
        <span class="subtitle">分位越低越便宜 · 点击指数看历史走势</span>
      </div>
      <span v-if="updateDate" class="update-time">更新于 {{ updateDate }}</span>
    </div>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <!-- ===== PC 端: 表格(7 列约 740px, 1280 视口无需横向拖拽) ===== -->
    <el-card v-if="!isMobile" shadow="never" v-loading="loading">
      <el-table
        :data="rows"
        stripe
        size="small"
        :default-sort="{ prop: 'pePct5y', order: 'ascending' }"
        class="valuation-table"
        @row-click="(row) => gotoDetail(row.code)"
      >
        <el-table-column label="指数" min-width="190">
          <template #default="{ row }">
            <div class="index-cell">
              <span class="index-name">{{ row.name }}</span>
              <span class="index-code">{{ row.code }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="pe" label="PE" width="80" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.pe) }}</template>
        </el-table-column>
        <el-table-column prop="pePct5y" label="PE分位(5年)" width="120" align="right" sortable>
          <template #default="{ row }">
            <span :style="{ color: row.judgment.color, fontWeight: 600 }">
              {{ fmtPct(row.pePct5y) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="pb" label="PB" width="80" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.pb) }}</template>
        </el-table-column>
        <el-table-column prop="pbPct5y" label="PB分位(5年)" width="120" align="right" sortable>
          <template #default="{ row }">{{ fmtPct(row.pbPct5y) }}</template>
        </el-table-column>
        <el-table-column prop="dividend" label="股息率" width="90" align="right" sortable>
          <template #default="{ row }">{{ fmtNum(row.dividend) }}%</template>
        </el-table-column>
        <el-table-column prop="ebSpread" label="股债差" width="100" align="right" sortable>
          <template #default="{ row }">
            <div class="eb-cell">
              <span class="eb-value">{{ fmtNum(row.ebSpread) }}</span>
              <span class="eb-pct">5年分位 {{ fmtPct(row.ebSpreadPct5y) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="估值判断" width="90" align="center">
          <template #default="{ row }">
            <span class="judge-tag" :style="{ background: row.judgment.color }">
              {{ row.judgment.label }}
            </span>
          </template>
        </el-table-column>
        <template #empty>
          <span class="empty-hint">暂无估值数据</span>
        </template>
      </el-table>
    </el-card>

    <!-- ===== 移动端: 卡片列表(蛋卷风格, 一行一指数不用横滑) ===== -->
    <div v-else class="mobile-cards" v-loading="loading">
      <div
        v-for="row in sortedRows"
        :key="row.code"
        class="val-card"
        @click="gotoDetail(row.code)"
      >
        <div class="card-head">
          <div class="card-title">
            <span class="index-name">{{ row.name }}</span>
            <span class="index-code">{{ row.code }}</span>
          </div>
          <span class="judge-tag" :style="{ background: row.judgment.color }">
            {{ row.judgment.label }}
          </span>
        </div>
        <div class="card-metrics">
          <div class="metric">
            <div class="m-label">PE</div>
            <div class="m-value">{{ fmtNum(row.pe) }}</div>
          </div>
          <div class="metric">
            <div class="m-label">PE分位</div>
            <div class="m-value" :style="{ color: row.judgment.color }">
              {{ fmtPct(row.pePct5y) }}
            </div>
          </div>
          <div class="metric">
            <div class="m-label">PB</div>
            <div class="m-value">{{ fmtNum(row.pb) }}</div>
          </div>
          <div class="metric">
            <div class="m-label">股息率</div>
            <div class="m-value">{{ fmtNum(row.dividend) }}%</div>
          </div>
          <div class="metric">
            <div class="m-label">股债差</div>
            <div class="m-value">{{ fmtNum(row.ebSpread) }}</div>
          </div>
        </div>
      </div>
      <el-empty v-if="!loading && !sortedRows.length" description="暂无估值数据" :image-size="80" />
    </div>
  </div>
</template>

<style scoped>
.valuation-list-page {
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
  color: #111827;
}

.subtitle {
  font-size: 12px;
  color: #9ca3af;
}

.update-time {
  font-size: 12px;
  color: #9ca3af;
}

/* ---- 指数单元格: 名称在上, 代码灰色小字在下(对齐转债页"可转债"列风格) ---- */
.index-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}

.index-name {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
}

.index-code {
  font-size: 11px;
  color: #9ca3af;
}

/* 股债差单元格: 当前值在上, 5年分位灰色小字在下 */
.eb-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}

.eb-value {
  font-size: 13px;
  font-weight: 600;
  color: #111827;
  font-variant-numeric: tabular-nums;
}

.eb-pct {
  font-size: 11px;
  color: #9ca3af;
}

.judge-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  white-space: nowrap;
}

/* 行可点击提示 */
.valuation-table :deep(.el-table__row) {
  cursor: pointer;
}

.empty-hint {
  font-size: 13px;
  color: #9ca3af;
}

/* ---- 移动端卡片 ---- */
.mobile-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.val-card {
  background: #fff;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  cursor: pointer;
  /* 点击态: 手指按下有反馈 */
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}

.val-card:active {
  transform: scale(0.985);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
}

.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}

.card-title {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.card-title .index-name {
  font-size: 14px;
}

.card-metrics {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  padding-top: 10px;
  border-top: 1px dashed rgba(148, 163, 184, 0.35);
}

.metric .m-label {
  font-size: 11px;
  color: #9ca3af;
  margin-bottom: 2px;
  white-space: nowrap;
}

.metric .m-value {
  font-size: 13px;
  font-weight: 700;
  color: #111827;
  font-variant-numeric: tabular-nums;
}

/* 移动端: 表格卡片内边距收紧(与转债页一致) */
@media (max-width: 767px) {
  .title {
    font-size: 16px;
  }

  .subtitle {
    font-size: 11px;
  }
}
</style>
