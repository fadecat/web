<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { getValuationSnapshot, getDividendYield, getEquityBond } from '../api';
import {
  judgeByPercentile,
  cheaperThanPct,
  fmtNum,
  fmtPct,
} from '../utils/valuation';
import ValuationChart from '../components/ValuationChart.vue';

const route = useRoute();
const router = useRouter();

const code = computed(() => route.params.code || '');

const loading = ref(false);
const errorMsg = ref('');
const indexName = ref('');
const allRows = ref([]); // 全历史(按日期升序)
const dividend = ref(null); // 股息率最新一条
const dividendRows = ref([]); // 股息率全历史(按日期升序, 画折线图用)
const ebData = ref(null); // 股债收益差/比(含全历史序列)

// 图表控制: 指标 + 时间窗口
// metric 支持 URL ?tab=pe/pb/dividend 控制默认值, 方便分享链接/回归测试
const METRIC_OPTIONS = [
  { key: 'pe', label: 'PE走势' },
  { key: 'pb', label: 'PB走势' },
  { key: 'dividend', label: '股息率' },
  { key: 'eb', label: '股债差' },
];
const RANGE_OPTIONS = [
  { key: 3, label: '近3年' },
  { key: 5, label: '近5年' },
  { key: 10, label: '近10年' },
];
const metric = ref(['pe', 'pb', 'dividend', 'eb'].includes(route.query.tab) ? route.query.tab : 'pe');
const rangeYears = ref(3);

const isMobile = ref(false);
let mq = null;
const updateIsMobile = () => {
  isMobile.value = window.innerWidth < 768;
};

async function loadData() {
  if (!code.value) return;
  loading.value = true;
  errorMsg.value = '';
  try {
    const [raw, dy, eb] = await Promise.all([
      getValuationSnapshot({ index_code: code.value }),
      getDividendYield(code.value),
      getEquityBond(code.value),
    ]);
    // 后端按 trade_date 降序返回, 前端转升序便于按窗口切片和画走势
    allRows.value = (raw || []).slice().reverse();
    indexName.value = allRows.value[0]?.index_name || code.value;
    // 股息率: 后端按日期降序, 转升序; 最新一条单独存(判断卡/分位条用)
    const dyRows = (dy || []).slice().reverse();
    dividendRows.value = dyRows;
    dividend.value = dyRows[dyRows.length - 1] || null;
    ebData.value = (eb || [])[0] || null;
  } catch (e) {
    errorMsg.value = e?.response?.data?.detail || e?.message || '拉取失败,请检查后端日志';
    allRows.value = [];
  } finally {
    loading.value = false;
  }
}

// 最新一条(顶部判断卡/指标带用)
const latest = computed(() => allRows.value[allRows.value.length - 1] || null);

const latestDate = computed(() => latest.value?.trade_date || '');
const pe5y = computed(() => latest.value?.pe_percentile?.['5y'] ?? null);
const pb5y = computed(() => latest.value?.pb_percentile?.['5y'] ?? null);

const judgment = computed(() => judgeByPercentile(pe5y.value));

// 蛋卷文案: 分位 p 表示"当前高于历史 p% 的时间", 反过来即"低于历史 (100-p)% 的时间"
const cheaperPct = computed(() => {
  const v = cheaperThanPct(pe5y.value);
  return v == null ? '—' : v.toFixed(1);
});

// 按选定年数切窗口(从最新日期往前推 N 年)
const windowRows = computed(() => {
  if (!allRows.value.length) return [];
  const last = allRows.value[allRows.value.length - 1].trade_date;
  const d = new Date(last);
  d.setFullYear(d.getFullYear() - rangeYears.value);
  const cutoff = d.toISOString().slice(0, 10);
  return allRows.value.filter((r) => r.trade_date >= cutoff);
});

// 股债差 Tab 内二级切换: 看差值(spread)还是比值(ratio)的走势
const ebMetric = ref('spread');
const EB_METRIC_OPTIONS = [
  { key: 'spread', label: '股债差' },
  { key: 'ratio', label: '股债比' },
];

// 图表数据: PE/PB 走快照表, 股息率走股息率表, 股债差走现算序列, 都按窗口切片
const chartData = computed(() => {
  if (metric.value === 'dividend') {
    const rows = dividendRows.value;
    const last = rows.length ? rows[rows.length - 1].trade_date : '';
    let cutoff = '';
    if (last) {
      const d = new Date(last);
      d.setFullYear(d.getFullYear() - rangeYears.value);
      cutoff = d.toISOString().slice(0, 10);
    }
    const windowed = cutoff ? rows.filter((r) => r.trade_date >= cutoff) : rows;
    return {
      dates: windowed.map((r) => r.trade_date),
      values: windowed.map((r) => r.dividend_yield),
    };
  }
  if (metric.value === 'eb') {
    const series = ebData.value?.series || [];
    const last = series.length ? series[series.length - 1].date : '';
    let cutoff = '';
    if (last) {
      const d = new Date(last);
      d.setFullYear(d.getFullYear() - rangeYears.value);
      cutoff = d.toISOString().slice(0, 10);
    }
    const windowed = cutoff ? series.filter((p) => p.date >= cutoff) : series;
    return {
      dates: windowed.map((p) => p.date),
      values: windowed.map((p) => p[ebMetric.value]),
    };
  }
  const key = metric.value; // 'pe' | 'pb'
  return {
    dates: windowRows.value.map((r) => r.trade_date),
    values: windowRows.value.map((r) => r[key]),
  };
});

const chartLabel = computed(() => {
  if (metric.value === 'pe') return 'PE';
  if (metric.value === 'pb') return 'PB';
  if (metric.value === 'dividend') return '股息率';
  if (metric.value === 'eb') return ebMetric.value === 'ratio' ? '股债比' : '股债差';
  return 'PE';
});

// 股债差 Tab 的分位条: 随二级切换展示对应指标的 1/3/5/10Y 分位
const ebPercentiles = computed(() => {
  const block = ebMetric.value === 'ratio' ? ebData.value?.ratio : ebData.value?.spread;
  const p = block?.percentiles || {};
  return [
    { key: '1y', label: '近1年', value: p['1y'] },
    { key: '3y', label: '近3年', value: p['3y'] },
    { key: '5y', label: '近5年', value: p['5y'] },
    { key: '10y', label: '近10年', value: p['10y'] },
  ];
});

// 股息率分位(1Y/3Y/5Y/10Y) 用于进度条
const dyPercentiles = computed(() => {
  const p = dividend.value?.percentile || {};
  return [
    { key: '1y', label: '近1年', value: p['1y'] },
    { key: '3y', label: '近3年', value: p['3y'] },
    { key: '5y', label: '近5年', value: p['5y'] },
    { key: '10y', label: '近10年', value: p['10y'] },
  ];
});

function goBack() {
  router.push('/valuation');
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
  <div class="valuation-detail-page">
    <el-button link class="back-btn" @click="goBack">← 返回列表</el-button>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
    />

    <el-card v-loading="loading" shadow="never" class="head-card">
      <div v-if="latest">
        <div class="index-title">
          <h3 class="name">{{ indexName }}</h3>
          <span class="meta">{{ code }} · 更新于 {{ latestDate }}</span>
        </div>

        <!-- 判断区: 蛋卷同款「比过去 X% 的时间低」+ 三档结论 -->
        <div class="judge-block">
          <div class="cheaper-line">
            <span>
              比过去 <b>{{ cheaperPct }}%</b> 的时间低
            </span>
            <el-tooltip
              content="分位数: 当前 PE 在过去 5 年区间中的相对位置。0=五年最低, 100=五年最高。分位越低越便宜。"
              placement="top"
            >
              <span class="help-icon">?</span>
            </el-tooltip>
          </div>
          <div class="judge-label" :style="{ color: judgment.color }">
            {{ judgment.label }}
          </div>
        </div>

        <!-- 核心指标带 -->
        <div class="metric-band">
          <div class="mb-item">
            <div class="mb-label">PE</div>
            <div class="mb-value">{{ fmtNum(latest.pe) }}</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">PE分位(5年)</div>
            <div class="mb-value" :style="{ color: judgment.color }">
              {{ fmtPct(pe5y) }}
            </div>
          </div>
          <div class="mb-item">
            <div class="mb-label">PB</div>
            <div class="mb-value">{{ fmtNum(latest.pb) }}</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">PB分位(5年)</div>
            <div class="mb-value">{{ fmtPct(pb5y) }}</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">股息率</div>
            <div class="mb-value">{{ fmtNum(dividend?.dividend_yield) }}%</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">PS</div>
            <div class="mb-value">{{ fmtNum(latest.ps) }}</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">股债差</div>
            <div class="mb-value">{{ fmtNum(ebData?.spread?.current) }}</div>
          </div>
          <div class="mb-item">
            <div class="mb-label">股债比</div>
            <div class="mb-value">{{ fmtNum(ebData?.ratio?.current) }}</div>
          </div>
        </div>
      </div>
      <el-empty v-else-if="!loading" description="暂无该指数估值数据" :image-size="80" />
    </el-card>

    <el-card shadow="never" class="chart-card">
      <div class="chart-controls">
        <!-- 指标切换 -->
        <div class="seg-group">
          <button
            v-for="opt in METRIC_OPTIONS"
            :key="opt.key"
            class="seg-btn"
            :class="{ active: metric === opt.key }"
            @click="metric = opt.key"
          >
            {{ opt.label }}
          </button>
        </div>
        <!-- 时间窗口(四个指标都有历史) -->
        <div class="seg-group">
          <button
            v-for="opt in RANGE_OPTIONS"
            :key="opt.key"
            class="seg-btn"
            :class="{ active: rangeYears === opt.key }"
            @click="rangeYears = opt.key"
          >
            {{ opt.label }}
          </button>
        </div>
        <!-- 股债差 Tab 内的差值/比值切换 -->
        <div v-if="metric === 'eb'" class="seg-group">
          <button
            v-for="opt in EB_METRIC_OPTIONS"
            :key="opt.key"
            class="seg-btn"
            :class="{ active: ebMetric === opt.key }"
            @click="ebMetric = opt.key"
          >
            {{ opt.label }}
          </button>
        </div>
      </div>

      <!-- 股债差 Tab: 走势图(差值/比值可切) + 当前值/5Y均值 + 分位条 -->
      <template v-if="metric === 'eb'">
        <ValuationChart
          :dates="chartData.dates"
          :values="chartData.values"
          :metric-label="chartLabel"
        />
        <div v-if="ebData" class="dividend-panel eb-panel">
          <div class="dy-compare">
            <div class="dy-item">
              <div class="dy-label">{{ ebMetric === 'ratio' ? '当前股债比' : '当前股债差' }}</div>
              <div class="dy-value primary">
                {{ fmtNum(ebMetric === 'ratio' ? ebData.ratio?.current : ebData.spread?.current) }}
              </div>
            </div>
            <div class="dy-vs">·</div>
            <div class="dy-item">
              <div class="dy-label">{{ ebMetric === 'ratio' ? '股债差' : '股债比' }}</div>
              <div class="dy-value">
                {{ fmtNum(ebMetric === 'ratio' ? ebData.spread?.current : ebData.ratio?.current) }}
              </div>
            </div>
            <div class="dy-vs">·</div>
            <div class="dy-item">
              <div class="dy-label">5 年均值</div>
              <div class="dy-value">
                {{ fmtNum(ebMetric === 'ratio' ? ebData.ratio?.average_5y : ebData.spread?.average_5y) }}
              </div>
            </div>
          </div>

          <div class="dy-bars">
            <div v-for="p in ebPercentiles" :key="p.key" class="dy-bar-row">
              <span class="bar-label">{{ p.label }}</span>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: `${Math.min(100, Math.max(0, p.value ?? 0))}%` }" />
              </div>
              <span class="bar-value">{{ fmtPct(p.value) }}</span>
            </div>
          </div>

          <p class="dy-note">
            股债差 = 盈利收益率(1/PE) − 10年期国债收益率, 股债比 = 盈利收益率 ÷ 10年期国债收益率。<br />
            差值/比值越高代表股票相对债券越有吸引力, 分位越高越便宜。
          </p>
        </div>
      </template>

      <!-- PE / PB 走势 -->
      <ValuationChart
        v-else-if="metric !== 'dividend'"
        :dates="chartData.dates"
        :values="chartData.values"
        :metric-label="chartLabel"
      />

      <!-- 股息率 Tab: 走势图 + 当前 vs 5年均值 + 分位条 -->
      <template v-else-if="metric === 'dividend'">
        <ValuationChart
          :dates="chartData.dates"
          :values="chartData.values"
          metric-label="股息率"
        />
        <div v-if="dividend" class="dividend-panel eb-panel">
          <div class="dy-compare">
            <div class="dy-item">
              <div class="dy-label">当前股息率</div>
              <div class="dy-value primary">{{ fmtNum(dividend?.dividend_yield) }}%</div>
            </div>
            <div class="dy-vs">vs</div>
            <div class="dy-item">
              <div class="dy-label">5 年均值</div>
              <div class="dy-value">{{ fmtNum(dividend?.average_5y) }}%</div>
            </div>
          </div>

          <div class="dy-bars">
            <div v-for="p in dyPercentiles" :key="p.key" class="dy-bar-row">
              <span class="bar-label">{{ p.label }}</span>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: `${Math.min(100, Math.max(0, p.value ?? 0))}%` }" />
              </div>
              <span class="bar-value">{{ fmtPct(p.value) }}</span>
            </div>
          </div>

          <p class="dy-note">
            股息率越高越有吸引力, 分位越高代表当前股息率高于历史多数时间。
          </p>
        </div>
      </template>

      <!-- PE / PB 走势 -->
      <ValuationChart
        v-else
        :dates="chartData.dates"
        :values="chartData.values"
        :metric-label="chartLabel"
      />
    </el-card>
  </div>
</template>

<style scoped>
.valuation-detail-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.back-btn {
  align-self: flex-start;
  font-size: 13px;
  color: #6b7280;
  padding-left: 0;
}

.head-card,
.chart-card {
  margin-bottom: 0;
}

.index-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.index-title .name {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}

.index-title .meta {
  font-size: 12px;
  color: #9ca3af;
}

/* ---- 判断区 ---- */
.judge-block {
  padding: 14px 16px;
  border-radius: 10px;
  background: rgba(39, 76, 119, 0.04);
  margin-bottom: 14px;
}

/* 股债差统计块与走势图的间距 */
.eb-panel {
  margin-top: 12px;
}

.cheaper-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #6b7280;
  margin-bottom: 4px;
}

.cheaper-line b {
  color: #111827;
  font-size: 15px;
}

.help-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  border: 1px solid #cbd5e1;
  color: #9ca3af;
  font-size: 10px;
  cursor: help;
}

.judge-label {
  font-size: 24px;
  font-weight: 800;
  line-height: 1.2;
}

/* ---- 核心指标带 ---- */
.metric-band {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.mb-item .mb-label {
  font-size: 11px;
  color: #9ca3af;
  margin-bottom: 3px;
}

.mb-item .mb-value {
  font-size: 16px;
  font-weight: 700;
  color: #111827;
  font-variant-numeric: tabular-nums;
}

/* ---- 分段胶囊(指标/时间窗口) ---- */
.chart-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.seg-group {
  display: inline-flex;
  background: #f3f4f6;
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
}

/* 触控目标 ≥32px: 手机端胶囊按钮要够大 */
.seg-btn {
  border: none;
  background: transparent;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  color: #6b7280;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  white-space: nowrap;
}

.seg-btn.active {
  background: #fff;
  color: #111827;
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

/* ---- 股息率面板(降级展示) ---- */
.dividend-panel {
  padding: 8px 0 4px;
}

.dy-compare {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 24px;
  padding: 18px 0 20px;
}

.dy-item {
  text-align: center;
}

.dy-label {
  font-size: 12px;
  color: #9ca3af;
  margin-bottom: 4px;
}

.dy-value {
  font-size: 26px;
  font-weight: 800;
  color: #111827;
  font-variant-numeric: tabular-nums;
}

.dy-value.primary {
  color: #16a34a;
}

.dy-vs {
  font-size: 13px;
  color: #cbd5e1;
}

.dy-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 4px;
}

.dy-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bar-label {
  flex: 0 0 48px;
  font-size: 12px;
  color: #6b7280;
}

.bar-track {
  flex: 1;
  height: 8px;
  background: #f3f4f6;
  border-radius: 4px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #4ade80, #16a34a);
  border-radius: 4px;
}

.bar-value {
  flex: 0 0 52px;
  text-align: right;
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  font-variant-numeric: tabular-nums;
}

.dy-note {
  margin: 14px 0 0;
  font-size: 11px;
  line-height: 1.6;
  color: #9ca3af;
}

/* ---- 移动端 ---- */
@media (max-width: 767px) {
  .index-title .name {
    font-size: 16px;
  }

  .judge-label {
    font-size: 21px;
  }

  /* 6 列在手机上挤成 3 列两行 */
  .metric-band {
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }

  .mb-item .mb-value {
    font-size: 15px;
  }

  .chart-controls {
    gap: 8px;
  }

  .seg-btn {
    padding: 7px 12px;
    font-size: 12px;
  }

  .dy-compare {
    gap: 16px;
  }

  .dy-value {
    font-size: 22px;
  }
}
</style>
