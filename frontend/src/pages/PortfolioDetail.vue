<script setup>
// 组合详情页(L2): 复刻韭圈儿 combination_details 的八个展示区 + 编辑能力。
//
// 区域编号与 docs/portfolio-lab-page-spec.md §二 一一对应:
//   ① 收益条  ② Tabs(收益详情/组合回撤)  ③ 控制条  ④ 净值曲线
//   ⑤ 指标卡(我方增强)  ⑥ 相关性矩阵  ⑦ 组合详情表  ⑧ 底部口径提示
//
// 三条归属/口径约束(写在代码里防止后来者"顺手改回去"):
// 1. **再平衡 / 基准 / 区间属于 Run 不属于组合** —— 所以它们只出现在回测请求体里,
//    PATCH 组合时绝不带上; 同一组合因此可以并存三种再平衡的 Run。
// 2. **收益条恒用不平衡账本**(由后端负责), 切换再平衡只影响曲线/指标/回撤 Tab。
// 3. 「近1日」是合成口径(Σ wᵢ×rᵢ 各自最新日), 与账本末两日之比不是一回事。
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';

import CorrelationMatrix from '../components/portfolio/CorrelationMatrix.vue';
import NavChart from '../components/portfolio/NavChart.vue';
import ReturnBar from '../components/portfolio/ReturnBar.vue';
import {
  deletePortfolio, getPortfolio, listAssets, patchPortfolio, runBacktest,
} from '../api/portfolio';
import {
  buildBasisNotes, buildChartData, buildMetricCards, drawdownSummaryText,
  EXEC_PRICE_TIP, priceBasisLabel, QDII_FOOTNOTE, REBALANCE_OPTIONS, REBALANCE_TIP,
  rebalanceLabel,
} from '../utils/backtestView.mjs';
import {
  EMPTY, formatDate, formatReturnPct, formatWeight, readinessText, trendOf, weightSummaryText,
} from '../utils/portfolioList.mjs';

const route = useRoute();
const router = useRouter();
const portfolioId = Number(route.params.id);

// 默认对照基准: 与韭圈儿一致取沪深300(价格指数, 口径提示见区域⑧)
const DEFAULT_BENCHMARK = '000300';

const loading = ref(false);
const running = ref(false);
const errorText = ref('');
const detail = ref(null);
const run = ref(null);
const registered = ref([]);
const editing = ref(false);
const saving = ref(false);
const draft = ref([]);
const chartTab = ref('return'); // return | drawdown

// 控制条 = 表单 draft; 点「组合回测」才落到 run 上(表单/结果分离)
const form = reactive({
  rebalance: 'none',
  benchmark: DEFAULT_BENCHMARK,
  start: '',
  end: '',
});

const TYPE_LABEL = { STOCK: '股票', ETF: 'ETF', FUND: '场外基金' };
const typeLabel = (t) => TYPE_LABEL[t] ?? EMPTY;

// ---------------------------------------------------------------------------
// 派生视图
// ---------------------------------------------------------------------------

const result = computed(() => run.value?.result ?? null);
const chartData = computed(() => buildChartData(run.value?.nav));
const metricCards = computed(() =>
  result.value ? buildMetricCards(result.value.metrics, result.value.drawdown) : [],
);
const basisNotes = computed(() => buildBasisNotes(result.value));
const drawdownText = computed(() => drawdownSummaryText(result.value?.drawdown));

const assetRows = computed(() => result.value?.assets ?? detail.value?.assets ?? []);

const weightState = computed(() => ({
  assets: detail.value?.assets ?? [],
  weight_sum: detail.value?.weight_sum,
}));
const tips = computed(() => ({
  weight: weightSummaryText(weightState.value),
  readiness: readinessText(detail.value?.data_readiness),
}));

// 实际起始与用户输入不同 → 提示"已自动前移"(规格 §二-③: 不报错、不静默改模式)
const startNotice = computed(() => {
  const actual = result.value?.actual_start;
  if (!actual) return '';
  if (form.start && form.start < actual) return `实际起始：${actual}（早于建仓日，已自动前移）`;
  if (form.end && form.end > (result.value?.actual_end ?? '')) {
    return `实际结束：${result.value.actual_end}（晚于最新数据日，已自动前移）`;
  }
  return '';
});

// ---------------------------------------------------------------------------
// 加载 / 回测 / 编辑
// ---------------------------------------------------------------------------

const load = async () => {
  loading.value = true;
  errorText.value = '';
  try {
    detail.value = await getPortfolio(portfolioId);
    draft.value = (detail.value.assets ?? []).map((a) => ({ ...a }));
    form.rebalance = detail.value.default_rebalance || 'none';
  } catch (err) {
    errorText.value = err?.response?.status === 404
      ? `组合不存在（id=${portfolioId}）`
      : err?.response?.data?.detail || '加载失败';
    return;
  } finally {
    loading.value = false;
  }
  // 进页面即跑一次默认回测(后端对相同输入做幂等复用, 不会重复计算)
  await runIt();
};

const runIt = async (reuse = true) => {
  if (!detail.value) return;
  running.value = true;
  try {
    run.value = await runBacktest({
      portfolioId,
      rebalance: form.rebalance,
      benchmarkSymbol: form.benchmark || null,
      start: form.start || null,
      end: form.end || null,
      reuse,
    });
  } catch (err) {
    run.value = null;
    ElMessage.error(err?.response?.data?.detail || '回测失败');
  } finally {
    running.value = false;
  }
};

const loadRegistered = async () => {
  try {
    registered.value = await listAssets();
  } catch {
    registered.value = []; // 标的库读取失败不阻塞详情页
  }
};

const startEdit = async () => {
  await loadRegistered();
  editing.value = true;
};

const cancelEdit = () => {
  editing.value = false;
  draft.value = (detail.value?.assets ?? []).map((a) => ({ ...a }));
};

// 只让用户从"已注册"里挑 —— 注册与抓取走标的库页(那里有同步状态与重试)
const selectable = computed(() => {
  const inDraft = new Set(draft.value.map((a) => a.symbol));
  return registered.value.filter((a) => !inDraft.has(a.symbol) && a.enabled !== false);
});

const addRow = (symbol) => {
  const asset = registered.value.find((a) => a.symbol === symbol);
  if (!asset) return;
  draft.value.push({
    symbol: asset.symbol, name: asset.name,
    security_type: asset.security_type, target_weight: null,
  });
};

const removeRow = (symbol) => {
  draft.value = draft.value.filter((a) => a.symbol !== symbol);
};

const draftWeightSum = computed(() => {
  const assigned = draft.value
    .map((a) => a.target_weight)
    .filter((v) => v !== null && v !== undefined && v !== '');
  return assigned.reduce((acc, v) => acc + Number(v), 0);
});

const save = async () => {
  saving.value = true;
  try {
    // PATCH 的 assets 是**全量替换**: 增/删/改权重都走这一条
    detail.value = await patchPortfolio(portfolioId, {
      assets: draft.value.map((a) => ({
        symbol: a.symbol,
        target_weight: a.target_weight === '' ? null : a.target_weight,
      })),
    });
    draft.value = (detail.value.assets ?? []).map((a) => ({ ...a }));
    editing.value = false;
    ElMessage.success('已保存，正在重新回测');
    await runIt(false); // 成员/权重变了 → 必须重算(input_hash 已变, 这里显式告意)
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '保存失败');
  } finally {
    saving.value = false;
  }
};

const confirmDelete = async () => {
  try {
    await ElMessageBox.confirm(
      `删除后组合会被归档（可恢复），已跑过的回测结果会保留。确定删除「${detail.value?.name}」？`,
      '删除组合',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    );
  } catch {
    return; // 用户取消
  }
  await deletePortfolio(portfolioId);
  ElMessage.success('已归档');
  router.push({ name: 'portfolio-list' });
};

onMounted(load);
</script>

<template>
  <div v-loading="loading" class="portfolio-detail-page">
    <div class="detail-head">
      <el-button text @click="router.push({ name: 'portfolio-list' })">← 返回组合列表</el-button>
    </div>

    <el-alert v-if="errorText" :title="errorText" type="error" show-icon :closable="false" />

    <template v-else-if="detail">
      <!-- 顶部: 组合名 + 操作 -->
      <div class="title-row">
        <h2 class="page-title">
          {{ detail.name }}
          <span class="meta">成立时间：{{ formatDate(detail.created_at) }}</span>
        </h2>
        <div class="actions">
          <el-button v-if="!editing" size="small" @click="startEdit">编辑</el-button>
          <el-button v-if="editing" size="small" type="primary" :loading="saving" @click="save">
            保存
          </el-button>
          <el-button v-if="editing" size="small" @click="cancelEdit">取消</el-button>
          <el-button size="small" type="danger" plain @click="confirmDelete">删除</el-button>
        </div>
      </div>

      <!-- 数据就绪(回测前检查): 不阻塞, 把问题摆出来 -->
      <el-alert
        v-if="!detail.data_readiness?.all_ready"
        class="gap"
        type="warning"
        :title="tips.readiness"
        :closable="false"
        show-icon
      />

      <!-- 区域① 收益条 -->
      <section class="card">
        <ReturnBar :windows="result?.windows" />
      </section>

      <!-- 区域② Tabs + 区域③ 控制条 + 区域④ 曲线 -->
      <section class="card">
        <el-tabs v-model="chartTab">
          <el-tab-pane label="收益详情" name="return" />
          <el-tab-pane label="组合回撤" name="drawdown" />
          <!-- v1 不做(需基金季报持仓穿透 / 与回测语义冲突) -->
          <el-tab-pane label="组合大爆炸" name="explode" disabled />
          <el-tab-pane label="组合实盘" name="live" disabled />
        </el-tabs>

        <!-- 区域③ 控制条 -->
        <div class="control-bar">
          <div class="control-line">
            <span class="control-label">
              再平衡
              <el-tooltip :content="REBALANCE_TIP" placement="top">
                <span class="hint-icon">ⓘ</span>
              </el-tooltip>
              ：
            </span>
            <el-radio-group v-model="form.rebalance" size="small" @change="runIt(false)">
              <el-radio-button
                v-for="option in REBALANCE_OPTIONS"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </el-radio-button>
            </el-radio-group>
          </div>

          <div class="control-line">
            <span class="control-label">自定义：</span>
            <el-date-picker
              v-model="form.start"
              type="date"
              size="small"
              value-format="YYYY-MM-DD"
              placeholder="起始日"
              style="width: 150px"
            />
            <span class="dash">-</span>
            <el-date-picker
              v-model="form.end"
              type="date"
              size="small"
              value-format="YYYY-MM-DD"
              placeholder="结束日"
              style="width: 150px"
            />
            <span class="control-label baseline">对照：</span>
            <el-input
              v-model="form.benchmark"
              size="small"
              placeholder="如 000300 / 510300.SH"
              style="width: 180px"
            />
            <el-button type="primary" size="small" :loading="running" @click="runIt(true)">
              组合回测
            </el-button>
            <span v-if="startNotice" class="notice">{{ startNotice }}</span>
          </div>

          <div class="control-tip">
            {{ EXEC_PRICE_TIP }}
            <span v-if="result" class="applied">
              已应用：{{ rebalanceLabel(result.rebalance) }} · {{ result.actual_start }} ~ {{ result.actual_end }}
            </span>
          </div>
        </div>

        <!-- 区域④ 曲线 / 回撤 -->
        <div v-loading="running" class="chart-area">
          <NavChart :data="chartData" :mode="chartTab === 'drawdown' ? 'drawdown' : 'return'" />
          <div v-if="chartTab === 'drawdown' && result?.drawdown" class="drawdown-note">
            {{ drawdownText }}
          </div>
          <div v-else-if="chartData?.benchmark" class="benchmark-note">
            ■ 本组合（红） vs ■ {{ chartData.benchmark.name }}（蓝，{{ priceBasisLabel(chartData.benchmark.priceBasis) }}）
          </div>
        </div>
      </section>

      <!-- 区域⑤ 指标卡(我方增强; 只列后端真的算出来的, 不放置灰占位) -->
      <section v-if="result" class="card">
        <div class="section-title">指标</div>
        <div class="metrics">
          <div v-for="card in metricCards" :key="card.key" class="metric">
            <div class="metric-value" :class="card.key === 'mdd' ? 'trend-down' : ''">
              {{ card.value }}
            </div>
            <div class="metric-label">{{ card.label }}</div>
            <div v-if="card.hint" class="metric-hint">{{ card.hint }}</div>
          </div>
        </div>
      </section>

      <!-- 区域⑥ 相关性矩阵 -->
      <section v-if="result" class="card">
        <CorrelationMatrix :correlation="result.correlation" :assets="assetRows" />
      </section>

      <!-- 区域⑦ 组合详情表 -->
      <section class="card">
        <div class="section-title">组合详情</div>

        <!-- 编辑态: 改权重 / 从标的库添加 / 移除 -->
        <template v-if="editing">
          <div class="edit-bar">
            <el-select
              placeholder="从标的库添加标的"
              size="small"
              style="width: 260px"
              :model-value="''"
              @change="addRow"
            >
              <el-option
                v-for="asset in selectable"
                :key="asset.symbol"
                :label="`${asset.name} ${asset.symbol}`"
                :value="asset.symbol"
              />
            </el-select>
            <span class="edit-sum" :class="Math.abs(draftWeightSum - 100) > 0.01 ? 'trend-down' : ''">
              权重合计 {{ draftWeightSum.toFixed(2) }}%
            </span>
          </div>
          <el-table :data="draft" size="small" style="width: 100%">
            <el-table-column label="标的" min-width="180">
              <template #default="{ row }">
                <div class="asset-name">{{ row.name || row.symbol }}</div>
                <div class="asset-code">{{ row.symbol }} · {{ typeLabel(row.security_type) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="初始比例" width="160">
              <template #default="{ row }">
                <el-input-number
                  v-model="row.target_weight"
                  :min="0"
                  :max="100"
                  :precision="2"
                  :step="5"
                  size="small"
                  controls-position="right"
                  style="width: 130px"
                />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="90">
              <template #default="{ row }">
                <el-button text type="danger" size="small" @click="removeRow(row.symbol)">
                  移除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>

        <!-- 只读态: 与韭圈儿同列 + 「添加后的收益」纯展示列 -->
        <template v-else>
          <el-table :data="assetRows" size="small" style="width: 100%">
            <el-table-column label="标的" min-width="200">
              <template #default="{ row }">
                <el-tag size="small" type="info" class="type-tag">
                  {{ typeLabel(row.security_type) }}
                </el-tag>
                <div class="asset-name">{{ row.name || row.symbol }}</div>
                <div class="asset-code">{{ row.symbol }}</div>
              </template>
            </el-table-column>
            <el-table-column label="复权口径" width="110">
              <template #default="{ row }">{{ priceBasisLabel(row.price_basis) }}</template>
            </el-table-column>
            <el-table-column label="日涨幅" width="130">
              <template #default="{ row }">
                <div :class="`trend-${trendOf(row.daily_return)}`">
                  {{ formatReturnPct(row.daily_return) }}
                </div>
                <div class="asset-code">{{ formatDate(row.daily_return_date) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="添加后的收益" width="130">
              <template #default="{ row }">
                <span :class="`trend-${trendOf(row.since_added_return)}`">
                  {{ formatReturnPct(row.since_added_return) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="初始比例" width="100">
              <template #default="{ row }">{{ formatWeight(row.target_weight) }}</template>
            </el-table-column>
            <el-table-column label="当前比例" width="100">
              <template #default="{ row }">
                {{ formatWeight(row.current_weight) }}
              </template>
            </el-table-column>
            <el-table-column label="基金经理" width="110">
              <template #default="{ row }">
                <el-tooltip
                  v-if="!row.fund_manager"
                  content="基金经理待接入（蛋卷基金详情）"
                  placement="top"
                >
                  <span class="muted">{{ EMPTY }}</span>
                </el-tooltip>
                <span v-else>{{ row.fund_manager }}</span>
              </template>
            </el-table-column>
          </el-table>

          <div class="table-foot">
            <span>{{ tips.weight }}</span>
            <span class="muted">
              ⚠「添加后的收益」是该标的自身自其添加日的收益，与权重无关，纯展示列，对回测零影响。
            </span>
          </div>
        </template>
      </section>

      <!-- 区域⑧ 底部口径提示 -->
      <section class="card footnote">
        <div>{{ QDII_FOOTNOTE }}</div>
        <div v-if="basisNotes.length" class="basis-notes">
          <div v-for="(note, i) in basisNotes" :key="i">· {{ note }}</div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.portfolio-detail-page {
  padding: 4px 0 40px;
}

.detail-head {
  margin-bottom: 4px;
}

.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.page-title {
  margin: 0 0 12px;
  font-size: 20px;
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.page-title .meta {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.gap {
  margin-bottom: 12px;
}

.card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}

.control-bar {
  padding: 4px 0 8px;
}

.control-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.control-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
  white-space: nowrap;
}

.control-label.baseline {
  margin-left: 8px;
}

.hint-icon {
  color: var(--el-text-color-secondary);
  cursor: help;
}

.dash {
  color: var(--el-text-color-secondary);
}

.notice {
  font-size: 12px;
  color: var(--el-color-warning);
}

.control-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.applied {
  color: var(--el-text-color-regular);
}

.chart-area {
  min-height: 240px;
}

.drawdown-note,
.benchmark-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 12px;
}

.metric {
  padding: 6px 8px;
  border-left: 2px solid var(--el-border-color-lighter);
}

.metric-value {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.metric-label {
  font-size: 12px;
  color: var(--el-text-color-regular);
  margin-top: 2px;
}

.metric-hint {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  margin-top: 2px;
}

.edit-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.edit-sum {
  font-size: 13px;
}

.asset-name {
  font-size: 13px;
}

.asset-code {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.type-tag {
  margin-right: 6px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.table-foot {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}

.footnote {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.8;
}

.basis-notes {
  margin-top: 6px;
}
</style>
