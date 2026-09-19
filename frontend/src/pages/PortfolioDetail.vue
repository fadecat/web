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

import AddAssetDialog from '../components/portfolio/AddAssetDialog.vue';
import CorrelationMatrix from '../components/portfolio/CorrelationMatrix.vue';
import NavChart from '../components/portfolio/NavChart.vue';
import ReturnBar from '../components/portfolio/ReturnBar.vue';
import {
  deletePortfolio, getPortfolio, listAssets, listBenchmarks, patchPortfolio, refreshAsset,
  runBacktest,
} from '../api/portfolio';
import {
  activeRangeKey, basisCompositionText, buildBasisNotes, buildChartData, buildMetricCards,
  buildRangePresetGroups, drawdownSummaryText, EXEC_PRICE_TIP, priceBasisLabel,
  QDII_FOOTNOTE, RANGE_SPAN_PRESETS, REBALANCE_OPTIONS, REBALANCE_TIP, rebalanceLabel,
  resolveRangeSelection,
} from '../utils/backtestView.mjs';
import {
  EMPTY, formatDate, formatReturnPct, formatWeight, readinessText, trendOf, weightSummaryText,
} from '../utils/portfolioList.mjs';
import { isDate, rowStatusOf } from '../utils/portfolioAssets.mjs';

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
const showAddDialog = ref(false);
// 「新增标的可能把组合起点往后推」的警示条(规格 add-asset-ux 第 5 步)
const startShift = ref(null);
// 指标卡是**我方增强**(韭圈儿无此区) → ambiguity-audit D5 定"折叠在曲线下方, 默认收起"
const showMetrics = ref(false);

// 控制条 = 表单 draft; 点「组合回测」才落到 run 上(表单/结果分离)
const form = reactive({
  rebalance: 'none',
  benchmark: DEFAULT_BENCHMARK,
  start: '',
  end: '',
});

const TYPE_LABEL = { STOCK: '股票', ETF: 'ETF', FUND: '场外基金' };
const typeLabel = (t) => TYPE_LABEL[t] ?? EMPTY;

// 单标的补抓的进行中标记(按 symbol, 避免整表转圈)
const retrying = ref(null);

// 成员行状态(add-asset-ux §二第 4 步 / §四): 同步中 / 就绪 / 失败(重试) / 无数据
const statusOf = (row) => rowStatusOf(row);
// 只读表的数据来自回测结果(不含同步字段) → 按 symbol 回落到组合详情的成员行;
// 找不到时按「就绪」处理 —— 能出现在回测结果里就说明它有数据, 不该给它标红。
const syncOf = (symbol) => rowStatusOf(
  (detail.value?.assets ?? []).find((a) => a.symbol === symbol)
    ?? { row_count: 1, last_sync_status: 'success' },
);

// ---------------------------------------------------------------------------
// 派生视图
// ---------------------------------------------------------------------------

const result = computed(() => run.value?.result ?? null);
const chartData = computed(() => buildChartData(run.value?.nav));
const metricCards = computed(() =>
  result.value ? buildMetricCards(result.value.metrics, result.value.drawdown) : [],
);
const basisNotes = computed(() => buildBasisNotes(result.value));
// 图例要标本组合的口径构成(page-spec §三-3) —— 取成员的真实口径, 不用曲线反推
const basisComposition = computed(() => basisCompositionText(detail.value?.assets ?? []));
const drawdownText = computed(() => drawdownSummaryText(result.value?.drawdown));

// 曲线头部「基准」那一格必须**跟着模式换指标**:
//   收益模式 → 基准区间收益;  回撤模式 → 基准自己的区间最大回撤(负值)。
// ⚠ 原来两处都写死 `benchmark.total_return`, 于是在回撤模式下变成
//   「组合 区间最大回撤 -16.50%」旁边并列「基准 +36.45%」(还按涨红显示) ——
//   同一格左右两个数一个回撤一个收益, 而且与图上蓝线最低点(-28.63%)完全对不上。
// 基准回撤由后端同口径同函数算出(`backtest.drawdown_detail`); 旧 Run 没有该字段 → 显示 —
const benchmarkHeadValue = computed(() => {
  const bench = result.value?.benchmark;
  if (!bench) return null;
  if (chartTab.value === 'drawdown') return bench.drawdown?.value ?? null;
  return bench.total_return ?? null;
});

const benchmarkHeadHint = computed(() =>
  chartTab.value === 'drawdown'
    ? '基准的区间最大回撤（峰从区间首值起算，与组合同口径）'
    : '基准的区间收益',
);

// 曲线头部第一格 = **回测区间起止**，不是只有截止日。
// 用户实测：选了「成立以来」这格仍只写 2026-09-18，看不出这段 335% 是从哪年起算的 ——
// 旁边摆着实数，标签就得回答"这是哪一段"。
// ⚠ 起点用 `effectiveStart`（**意图口径**，未做交易日对齐）而不是 `actual_start`（对齐之后），
//   这样与「自定义」输入框、「近10年」等候选**完全一致**；实际起算的交易日另有「已应用」交代。
//   终点：`end_date` 为空表示"到最新" → 取 `actual_end`。
const appliedRangeText = computed(() => {
  const res = result.value;
  if (!res) return EMPTY;
  const start = effectiveStart.value;
  const end = res.end_date || res.actual_end;
  if (!start || !end) return res.actual_end || EMPTY;
  return `${start} ~ ${end}`;
});

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

// ⚠ page-spec §四: 起点**晚于末端**要**阻止提交** ——
//   注意与上一行的区别: 起点太**早**只前移(不报错), 起点太**晚**则直接拦住。
const lastDataDate = computed(() => result.value?.data_range?.last ?? null);
const startOutOfRange = computed(
  () => Boolean(form.start && lastDataDate.value && form.start >= lastDataDate.value),
);
const rangeWarning = computed(() => (
  startOutOfRange.value
    ? `起始日 ${form.start} 不早于数据最新日 ${lastDataDate.value}，区间不足两个交易日，无法回测`
    : ''
));

// ---------------------------------------------------------------------------
// 区间快捷选择: ① 快捷条(今年以来 / 近1月 / 近3月 / 近6月 / 近1年 / 近3年 / 近5年 / 近10年)
//              ② 「请选择」下拉(成立以来 / 事件锚点 / 按年份) —— 与韭圈儿底部那一条同构
// ---------------------------------------------------------------------------

// T0 = 回测给出的建仓日(共同起点), 无 result 时用组合的数据就绪摘要
const t0Date = computed(
  () => result.value?.t0_date ?? detail.value?.data_readiness?.common_start ?? null,
);
const rangePresetGroups = computed(
  () => buildRangePresetGroups({ t0: t0Date.value, lastDataDate: lastDataDate.value }),
);
// 相对区间以"数据最新日"为末端回推 —— 还没跑成过一次回测时算不出来, 按钮置灰而非隐藏
const rangeSpans = computed(() => RANGE_SPAN_PRESETS.map((preset) => ({
  ...preset,
  disabled: !isDate(lastDataDate.value),
})));
// ⭐ 实际生效的**意图**起点 = 后端 `effective_start`
//   = `max(用户所选 或 默认的"末端整年回推10年", T0)`, **未做交易日对齐**。
// 为什么不能直接用 `start_date`: 不传区间时它是 null(那字段记的是"用户传了什么");
// 为什么不能直接用 `actual_start`: 它向前对齐到交易日, 会早 1~4 天, 于是"日期框填什么、
// 快捷条高不高亮"全对不上(用户实测: 打开任意组合, 头部已有起点日期, 日期选择器却是空的)。
// 回落链覆盖引擎 v6 之前生成的旧 Run。
const effectiveStart = computed(() => (
  result.value?.effective_start
  || result.value?.start_date
  || result.value?.actual_start
  || ''
));

// 选中项由表单**反推**(不另存状态): 用户手动改日期时会自动取消高亮, 不会撒谎。
// ⚠ 表单为空时**回落到结果**反推: 进页面会自动跑一次默认区间, 那一刻表单还是空的,
//   只看表单就会"底部一条都不高亮、用户看不出这一屏属于哪个区间"(用户实测提出)。
//   标签也遵循同一原则 —— 跟着**结果**走, 因为旁边是实数。
const rangePresetKey = computed(() => (
  activeRangeKey(form.start, form.end, { t0: t0Date.value, lastDataDate: lastDataDate.value })
  || activeRangeKey(effectiveStart.value, result.value?.end_date,
    { t0: t0Date.value, lastDataDate: lastDataDate.value })
));

// 「请选择」下拉只管**固定日期项**(成立以来 / 事件锚点 / 按年份), 相对区间已有左侧那排按钮。
// ⚠ 相对区间的 key(`span-*`)绝不能传给这个下拉: el-select 在自己的选项列表里找不到该值,
//   会把**裸 key 原样回显**(用户实测在底部看到过 `span-10y`)。
const fixedRangeKey = computed(() => (
  String(rangePresetKey.value || '').startsWith('span-') ? '' : (rangePresetKey.value || '')
));

const applyRangePreset = async (key) => {
  const preset = resolveRangeSelection(key, {
    t0: t0Date.value, lastDataDate: lastDataDate.value,
  });
  if (!preset) return; // 拿不到 T0 / 数据末端(还没跑成过一次回测) → 什么都不做, 不猜日期
  form.start = preset.start || '';
  form.end = preset.end || '';
  // 韭圈儿点选即刷新; 权重不全时只填表单(此时「组合回测」按钮本来也是灰的)
  if (weightsComplete.value) await runIt(false);
};

/** 「已应用」区间的名字: 由**结果**里的起止日反推(不是表单), 才能与旁边的收益数字对得上。 */
const appliedRangeLabel = computed(() => {
  const key = activeRangeKey(
    effectiveStart.value, result.value?.end_date,
    { t0: t0Date.value, lastDataDate: lastDataDate.value },
  );
  if (!key) return '自定义区间';
  for (const group of [...rangePresetGroups.value, { options: rangeSpans.value }]) {
    const hit = group.options.find((option) => option.key === key);
    if (hit) return hit.label;
  }
  return '自定义区间';
});

// ---------------------------------------------------------------------------
// 对照基准: 下拉(带名称) —— 列表来自后端"真能算出曲线"的那些
// ---------------------------------------------------------------------------

const benchmarks = ref([]);

const benchmarkGroups = computed(() => {
  const groups = [
    { label: '指数（价格指数，不含股息）', options: benchmarks.value.filter((b) => b.kind === 'index') },
    { label: '已注册标的（后复权 / 分红再投）', options: benchmarks.value.filter((b) => b.kind === 'asset') },
  ];
  return groups.filter((group) => group.options.length);
});

// 基准展示名: 优先用下拉列表里的名字(后端已做了 storage_code 映射 + 名单兜底)。
// ⚠ 为什么不能只信 Run 里的 `benchmark.name`: Run 是"跑那一次的快照", 早于名字修复
//   生成的旧 Run 里该字段是 null, 直接渲染就会在图上显示成一串代码。
//   列表里查不到(手输代码 / 列表读取失败)才退回代码, **不编名字**。
const benchmarkNameOf = (symbol) => {
  const code = String(symbol || '').trim();
  if (!code) return '';
  const hit = benchmarks.value.find((item) => item.symbol === code);
  return hit?.name || code;
};

const benchmarkLabel = computed(() => benchmarkNameOf(form.benchmark));

const loadBenchmarks = async () => {
  try {
    benchmarks.value = await listBenchmarks();
  } catch {
    benchmarks.value = []; // 基准列表读取失败不阻塞页面
  }
};

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
    // 跑完把生效起点回填到「起始日」框 —— 否则日期框空着、快捷条也不高亮, 用户看不出
    // 这一屏数字属于哪段区间(用户实测提出)。用后端的 `effective_start`(意图口径),
    // 不用 `actual_start`(向前对齐后早 1~4 天, 与候选区间对不上); 旧 Run 无该字段则回落。
    // `end` 保持空 = "到最新"。
    if (!form.start && !form.end && effectiveStart.value) {
      form.start = effectiveStart.value;
    }
  } catch (err) {
    // ⚠ page-spec §四: 回测失败**不清空已有结果**（避免页面闪烁成空白）
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
  // 连同步状态一起带过来, 否则状态列会显示成「无数据」(字段缺失 ≠ 真的没数据)
  draft.value.push({
    id: asset.id,
    symbol: asset.symbol,
    name: asset.name,
    security_type: asset.security_type,
    row_count: asset.row_count,
    first_date: asset.first_date,
    last_date: asset.last_date,
    last_sync_status: asset.last_sync_status,
    last_sync_error: asset.last_sync_error,
    target_weight: null,
  });
};

/**
 * 单标的补抓(add-asset-ux §四「抓取失败 → 该行标红 + 重试」)。
 *
 * 只刷新这一只标的状态, **不重跑回测**(重跑由用户点「组合回测」触发);
 * 但会重新拉一次组合详情, 好让状态列与本库区间立刻更新。
 */
const retryRow = async (row) => {
  if (!row?.id) {
    ElMessage.warning('该标的还没注册完成，稍后再试');
    return;
  }
  retrying.value = row.symbol;
  try {
    const outcome = await refreshAsset(row.id);
    if (outcome?.status === 'failed') {
      ElMessage.error(`重试仍失败：${outcome?.error || '未知原因'}`);
    } else {
      ElMessage.success(`${row.name || row.symbol} 已同步 ${outcome?.rows ?? 0} 行`);
    }
    detail.value = await getPortfolio(portfolioId);
    // 保留用户已改的权重, 只覆盖同步状态类字段
    draft.value = draft.value.map((item) => {
      const fresh = (detail.value.assets ?? []).find((a) => a.symbol === item.symbol);
      return fresh ? { ...item, ...fresh, target_weight: item.target_weight } : item;
    });
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || '重试失败');
  } finally {
    retrying.value = null;
  }
};

const removeRow = (symbol) => {
  draft.value = draft.value.filter((a) => a.symbol !== symbol);
};

// 起点被推后时给一条退路: 直接撤掉刚加的那只标的(它往往就是起点后移的原因)
const removeShiftAsset = () => {
  const symbol = startShift.value?.asset?.symbol;
  if (symbol) removeRow(symbol);
  startShift.value = null;
};

/**
 * 「+ 新标的」回填: 组合里要加一只**尚未注册过**的股票/ETF/场外基金时,
 * 走 AddAssetDialog 的"注册 + 后台抓取"链路(P0 就做好了, P3 忘接到这里)。
 *
 * ⚠ 只从"已注册标的"里挑是不够的 —— 标的库为空、或想要的标的没注册过时,
 *    用户就完全没法往组合里加东西(创建空组合后尤其明显)。
 */
const onAssetAdded = async (asset) => {
  await loadRegistered(); // 让新标的进入 registered(下次可从下拉直接复用)
  if (asset?.symbol && !draft.value.some((a) => a.symbol === asset.symbol)) {
    draft.value.push({
      id: asset.id,
      symbol: asset.symbol,
      name: asset.name || asset.symbol,
      security_type: asset.security_type,
      // 注册返回体带 last_sync_status=running(后台异步首抓) → 状态列显示「同步中」
      row_count: asset.row_count ?? null,
      first_date: asset.first_date ?? null,
      last_date: asset.last_date ?? null,
      last_sync_status: asset.last_sync_status ?? 'running',
      last_sync_error: asset.last_sync_error ?? null,
      target_weight: null, // 权重按 add-asset-ux 的裁决"添加后统一设"
    });
  }
  ElMessage.success(`${asset?.name || asset?.symbol} 已加入，设好权重后保存即可`);
};

const draftWeightSum = computed(() => {
  const assigned = draft.value
    .map((a) => a.target_weight)
    .filter((v) => v !== null && v !== undefined && v !== '');
  return assigned.reduce((acc, v) => acc + Number(v), 0);
});

// add-asset-ux §四: 权重合计 ≠ 100% → 「组合回测」按钮**置灰**(后端也会 422, 前端先拦住)。
// 编辑态按草稿算(所见即所得), 否则按服务端成员状态。
const weightsComplete = computed(() => {
  const rows = editing.value ? draft.value : (detail.value?.assets ?? []);
  if (!rows.length) return false; // 空组合不能回测
  if (rows.some((a) => a.target_weight === null || a.target_weight === undefined || a.target_weight === '')) {
    return false;
  }
  const sum = rows.reduce((acc, a) => acc + Number(a.target_weight), 0);
  return Math.abs(sum - 100) < 0.01;
});

const weightsWarning = computed(
  () => (weightsComplete.value ? '' : '权重合计须为 100%（且每个标的都已设权重）才能回测'),
);

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
// 基准下拉候选与组合详情并行加载(失败不阻塞页面)
onMounted(loadBenchmarks);
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
            <!-- ⚠ 对照基准已挪到**曲线头部**的下拉里(带名称, 与韭圈儿同位置);
                 这里只回显当前选择, 不让人手打代码。 -->
            <span class="control-bench">{{ benchmarkLabel || '无基准' }}</span>
            <el-button
              type="primary"
              size="small"
              :loading="running"
              :disabled="startOutOfRange || !weightsComplete"
              @click="runIt(true)"
            >
              组合回测
            </el-button>
            <span v-if="startNotice" class="notice">{{ startNotice }}</span>
            <span v-if="rangeWarning" class="notice warn">{{ rangeWarning }}</span>
            <!-- add-asset-ux §四: 权重合计 ≠ 100% → 按钮置灰(后端也会 422, 前端先拦住) -->
            <span v-else-if="!weightsComplete" class="notice warn">{{ weightsWarning }}</span>
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
          <!-- 曲线头部(照韭圈儿 §二-④): 截止日 · 选中区间名 + 区间收益 · 基准下拉 + 基准收益
               ⚠ **两格都要随模式切换**: 回撤模式下左边换成「区间最大回撤」、右边换成
                 「基准的区间最大回撤」。只换左边不换右边, 就成了回撤与收益并列。 -->
          <div class="chart-head">
            <span class="chart-asof" title="回测区间（起点为你所选口径；实际起算的交易日见下方「已应用」）">{{ appliedRangeText }}</span>
            <span v-if="chartTab === 'drawdown'" class="chart-headline">
              {{ appliedRangeLabel }} 区间最大回撤
              <b class="trend-down">{{ formatReturnPct(result?.selected_drawdown) }}</b>
            </span>
            <span v-else class="chart-headline">
              {{ appliedRangeLabel }}:
              <b :class="`trend-${trendOf(result?.selected_return)}`">
                {{ formatReturnPct(result?.selected_return) }}
              </b>
            </span>
            <!-- 基准: 带名称的下拉(韭圈儿在头部就有 ▼), 不是让人手打代码 -->
            <span class="chart-benchmark">
              <span class="bench-dot">●</span>
              <el-select
                v-model="form.benchmark"
                class="bench-select"
                size="small"
                placeholder="无基准"
                clearable
                filterable
                @change="runIt(false)"
              >
                <el-option label="无基准" :value="''" />
                <el-option-group
                  v-for="group in benchmarkGroups"
                  :key="group.label"
                  :label="group.label"
                >
                  <el-option
                    v-for="item in group.options"
                    :key="item.symbol"
                    :label="item.name"
                    :value="item.symbol"
                  >
                    <span>{{ item.name }}</span>
                    <span class="bench-option-code">{{ item.symbol }}</span>
                  </el-option>
                </el-option-group>
              </el-select>
              <b
                v-if="result?.benchmark"
                :class="`trend-${trendOf(benchmarkHeadValue)}`"
                :title="benchmarkHeadHint"
              >
                {{ formatReturnPct(benchmarkHeadValue) }}
              </b>
            </span>
            <el-tooltip
              content="该模式起点为「成立最久的标的」、未成立者按空仓 —— 与「共同起点」规则互斥，v1 不实现"
              placement="top"
            >
              <el-button size="small" disabled>查看完整曲线</el-button>
            </el-tooltip>
          </div>
          <NavChart :data="chartData" :mode="chartTab === 'drawdown' ? 'drawdown' : 'return'" />
          <div v-if="chartTab === 'drawdown' && result?.drawdown" class="drawdown-note">
            {{ drawdownText }}
          </div>
          <div v-else-if="chartData?.portfolio" class="benchmark-note">
            ■ 本组合（红）<template v-if="basisComposition">，口径：{{ basisComposition }}</template>
            <template v-if="chartData?.benchmark">
              &nbsp;&nbsp;vs ■ {{ benchmarkNameOf(chartData.benchmark.symbol) }}（蓝，{{ priceBasisLabel(chartData.benchmark.priceBasis) }}）
            </template>
          </div>

          <!-- 区间快捷条(照韭圈儿的位置: 曲线**下方**, 图例之后) -->
          <div class="range-strip">
            <el-button-group>
              <el-button
                v-for="preset in rangeSpans"
                :key="preset.key"
                size="small"
                :type="rangePresetKey === preset.key ? 'primary' : 'default'"
                :disabled="preset.disabled"
                @click="applyRangePreset(preset.key)"
              >
                {{ preset.label }}
              </el-button>
            </el-button-group>
            <!-- 固定日期项(成立以来 / 事件锚点 / 按年份) -->
            <!-- ⚠ 绑定的是 `fixedRangeKey` 而不是 `rangePresetKey`: 这个下拉的选项里**只有**
                 固定日期项, 相对区间已经由左侧那排按钮承担。若把 `span-*` 的 key 也传进来,
                 el-select 在自己的选项列表里找不到该值, 会把**裸 key 原样显示**(用户实测看到
                 过 `span-10y`) —— 这是 el-select 的通用陷阱: 值不在选项里就回显原始值。 -->
            <el-select
              :model-value="fixedRangeKey"
              class="range-preset"
              size="small"
              placeholder="请选择"
              style="width: 176px"
              @change="applyRangePreset"
            >
              <el-option-group
                v-for="group in rangePresetGroups"
                :key="group.label"
                :label="group.label"
              >
                <el-option
                  v-for="option in group.options"
                  :key="option.key"
                  :label="option.label"
                  :value="option.key"
                />
              </el-option-group>
            </el-select>
          </div>
        </div>
      </section>

      <!-- 区域⑤ 指标卡(我方增强; 默认收起 —— ambiguity-audit D5) -->
      <section v-if="result" class="card">
        <div class="section-title clickable" @click="showMetrics = !showMetrics">
          指标
          <span class="toggle">{{ showMetrics ? '收起 ▲' : '展开 ▼' }}</span>
        </div>
        <div v-show="showMetrics" class="metrics">
          <div v-for="card in metricCards" :key="card.key" class="metric">
            <div class="metric-value" :class="card.key === 'mdd' ? 'trend-down' : ''">
              {{ card.value }}
            </div>
            <div class="metric-label">{{ card.label }}</div>
            <div v-if="card.hint" class="metric-hint">{{ card.hint }}</div>
          </div>
        </div>
      </section>

      <!-- 区域⑥ 相关性矩阵(⚠ 单标的时隐藏: 1×1 矩阵没有信息量 —— page-spec §四) -->
      <section v-if="result && assetRows.length > 1" class="card">
        <CorrelationMatrix :correlation="result.correlation" :assets="assetRows" />
      </section>

      <!-- 区域⑦ 组合详情表 -->
      <section class="card">
        <div class="section-title">组合详情</div>

        <!-- 编辑态: 改权重 / 从标的库添加 / 移除 -->
        <template v-if="editing">
          <div class="edit-bar">
            <el-select
              placeholder="从已注册标的里挑"
              size="small"
              style="width: 240px"
              no-data-text="标的库里的标的都已在本组合中，点右侧「+ 新标的」"
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
            <el-button size="small" type="primary" plain @click="showAddDialog = true">
              + 新标的
            </el-button>
            <span class="edit-sum" :class="Math.abs(draftWeightSum - 100) > 0.01 ? 'trend-down' : ''">
              权重合计 {{ draftWeightSum.toFixed(2) }}%
            </span>
          </div>

          <!-- 起点前移警示(规格 add-asset-ux 第 5 步): 新标的可能把组合的可回测起点往后推 -->
          <el-alert
            v-if="startShift"
            class="gap"
            type="warning"
            show-icon
            :closable="true"
            @close="startShift = null"
          >
            <template #title>{{ startShift.title }}</template>
            <template #default>
              <div>{{ startShift.detail }}</div>
              <!-- add-asset-ux §二第 5 步给了两条出路: 知道就好, 或直接撤掉这只标的 -->
              <el-button
                v-if="startShift.asset?.symbol"
                class="shift-remove"
                link
                type="warning"
                size="small"
                @click="removeShiftAsset"
              >
                移除该标的
              </el-button>
            </template>
          </el-alert>
          <el-table
            :data="draft"
            size="small"
            stripe
            style="width: 100%"
            :row-class-name="({ row }) => (statusOf(row).blocked ? 'row-blocked' : '')"
          >
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
            <!-- 状态列(add-asset-ux §二第 4 步): 同步中 / 就绪 / 失败(可重试) / 无数据 -->
            <el-table-column label="状态" width="150">
              <template #default="{ row }">
                <div class="row-status" :class="`is-${statusOf(row).tone}`">
                  <span class="status-dot" />
                  {{ statusOf(row).label }}
                  <el-tooltip v-if="statusOf(row).detail" :content="statusOf(row).detail" placement="top">
                    <span class="status-help">?</span>
                  </el-tooltip>
                </div>
                <el-button
                  v-if="statusOf(row).retry"
                  class="status-retry"
                  text
                  type="primary"
                  size="small"
                  :loading="retrying === row.symbol"
                  @click="retryRow(row)"
                >
                  重试
                </el-button>
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
          <!-- 空组合必须有明确入口: 创建完就是空的, 用户不该被"两个下拉都点不动"卡住 -->
          <el-empty v-if="!assetRows.length" :image-size="70" description="这个组合还没有标的">
            <el-button type="primary" @click="startEdit">添加标的</el-button>
          </el-empty>
          <el-table
            v-else
            :data="assetRows"
            size="small"
            stripe
            style="width: 100%"
            :row-class-name="({ row }) => (syncOf(row.symbol).blocked ? 'row-blocked' : '')"
          >
            <el-table-column label="标的" min-width="200">
              <template #default="{ row }">
                <el-tag size="small" type="info" class="type-tag">
                  {{ typeLabel(row.security_type) }}
                </el-tag>
                <div class="asset-name">
                  {{ row.name || row.symbol }}
                  <!-- 无数据/抓取失败的行**标红**(page-spec §四): 它不是样式问题, 是真的用不了 -->
                  <span v-if="syncOf(row.symbol).blocked" class="row-status is-down is-inline">
                    <span class="status-dot" />{{ syncOf(row.symbol).label }}
                  </span>
                </div>
                <div class="asset-code">{{ row.symbol }}</div>
              </template>
            </el-table-column>
            <el-table-column label="复权口径" width="110">
              <template #default="{ row }">{{ priceBasisLabel(row.price_basis) }}</template>
            </el-table-column>
            <el-table-column label="日涨幅" width="130" align="right">
              <template #default="{ row }">
                <div :class="`trend-${trendOf(row.daily_return)}`">
                  {{ formatReturnPct(row.daily_return) }}
                </div>
                <div class="asset-code">{{ formatDate(row.daily_return_date) }}</div>
              </template>
            </el-table-column>
            <el-table-column label="添加后的收益" width="130" align="right">
              <template #default="{ row }">
                <span :class="`trend-${trendOf(row.since_added_return)}`">
                  {{ formatReturnPct(row.since_added_return) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="初始比例" width="100" align="right">
              <template #default="{ row }">{{ formatWeight(row.target_weight) }}</template>
            </el-table-column>
            <el-table-column label="当前比例" width="100" align="right">
              <template #default="{ row }">
                {{ formatWeight(row.current_weight) }}
              </template>
            </el-table-column>
            <el-table-column label="基金经理" width="110">
              <template #default="{ row }">
                <el-tooltip
                  v-if="!row.manager"
                  content="股票/ETF 无此概念；场外基金的经理名来自蛋卷详情"
                  placement="top"
                >
                  <span class="muted">{{ EMPTY }}</span>
                </el-tooltip>
                <span v-else>{{ row.manager }}</span>
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

      <!-- 「+ 新标的」: 走 P0 的注册 + 后台抓取链路, 成功后回填到编辑草稿(不落库) -->
      <AddAssetDialog
        v-model="showAddDialog"
        :current-start="detail.data_readiness?.common_start || null"
        :existing-symbols="draft.map((a) => a.symbol)"
        @added="onAssetAdded"
        @start-change="(payload) => (startShift = payload)"
      />
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

/* 区间快捷条: 照韭圈儿放在**曲线下方**、居中对齐; 相对区间按钮组 + 「请选择」下拉 */
.range-strip {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.range-strip .range-preset {
  margin-left: 4px;
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

.shift-remove {
  margin-top: 4px;
}

/* 起点越界是"拦住不让提交", 与"已自动前移"的黄字提示不同量级 → 用红色 */
.notice.warn {
  color: var(--el-color-danger);
}

/* page-spec §五: 表格行高 ≥48px(含两行名称)、数值列右对齐(由 align="right" 给) */
:deep(.el-table td.el-table__cell) {
  padding: 10px 0;
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

.chart-head {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 13px;
}

.chart-asof {
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-regular);
  /* 区间是「起 ~ 止」整体, 别在中间折行 */
  white-space: nowrap;
}

.chart-headline {
  color: var(--el-text-color-regular);
}

.chart-headline b,
.chart-benchmark b {
  font-variant-numeric: tabular-nums;
}

.chart-benchmark {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--el-text-color-regular);
}

.bench-dot {
  color: var(--el-color-primary);
  font-size: 10px;
}

.bench-select {
  width: 168px;
}

.bench-select :deep(.el-select__wrapper) {
  box-shadow: none; /* 视觉上贴近韭圈儿的"名字 + ▼"纯文本, 不要输入框边框 */
  padding-left: 0;
}

.bench-option-code {
  float: right;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 12px;
}

.control-bench {
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.chart-benchmark {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.chart-head .el-button {
  margin-left: auto;
}

.section-title.clickable {
  cursor: pointer;
  user-select: none;
}

.section-title .toggle {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
  margin-left: 6px;
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

/* 成员状态列(add-asset-ux §二第 4 步): 同步中 / 就绪 / 失败 / 无数据。
   ⚠ 红/绿是**真实可用性**, 不是装饰 —— blocked 行同时整行标红。 */
.row-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  white-space: nowrap;
}

.row-status.is-inline {
  margin-left: 6px;
}

.row-status.is-up {
  color: var(--el-color-success);
}

.row-status.is-down {
  color: var(--el-color-danger);
}

.row-status.is-wait {
  color: var(--el-color-warning);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.row-status.is-wait .status-dot {
  animation: status-pulse 1.2s ease-in-out infinite;
}

@keyframes status-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.25; }
}

.status-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border: 1px solid currentColor;
  border-radius: 50%;
  font-size: 10px;
  cursor: help;
}

.status-retry {
  margin-left: 4px;
}

/* 「无数据/抓取失败」的行标红(page-spec §四): 让用户一眼看出哪一行不能用。
   ⚠ 表格开了斑马纹(stripe), Element 的 `.el-table__row--striped td` 会盖掉行背景,
     所以这里必须直接压到单元格上。 */
:deep(.el-table .row-blocked) {
  --el-table-tr-bg-color: var(--el-color-danger-light-9);
}

:deep(.el-table .row-blocked > td.el-table__cell),
:deep(.el-table .row-blocked.el-table__row--striped > td.el-table__cell) {
  background-color: var(--el-color-danger-light-9) !important;
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

/* 响应式断点(page-spec §五: 1280 / 960)。
   指标卡本身用 auto-fit 自适应; 这里收卡片内边距与控制条间距, 表格由 el-table 横向滚动兜底。 */
@media (max-width: 1280px) {
  .card {
    padding: 12px;
  }
}

@media (max-width: 960px) {
  .card {
    padding: 10px;
    margin-bottom: 10px;
  }

  .control-line {
    gap: 6px;
  }

  .metric-value {
    font-size: 16px;
  }
}
</style>
