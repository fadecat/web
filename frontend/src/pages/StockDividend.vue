<script setup>
import { h, ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { ElMessage, ElMessageBox, ElTooltip } from 'element-plus';
import { getStockDividendSnapshot, getDividendPresets, saveDividendPresets } from '../api';
import { errorText } from '../composables/useSelectionWorkspace';
import {
  STOCK_DIVIDEND_COLUMNS,
  DEFAULT_SORT,
  PAGE_SIZES,
  emptyForm,
  sanitizeForm,
  buildIndustryTree,
  collectProvinceOptions,
  filterRows,
  sortRows,
  withPayoutRate,
  fmtNum,
  fmtVolume,
  tempText,
  tempClass,
  signedText,
  signedClass,
} from '../utils/stockDividend.mjs';

// 列配置切片: 代码/名称两列在 v2 渲染函数里有徽标/外链特例, 其余列共用 cellText/cellClass 分发
const dynamicColumns = STOCK_DIVIDEND_COLUMNS.slice(2);

// 主筛选区阈值(邮件漏斗口径 + 派生指标分红率)
const PRIMARY_THRESHOLDS = [
  { key: 'peMax', label: 'PE-TTM ≤' },
  { key: 'dividendMin', label: '股息率TTM ≥' },
  { key: 'payoutMin', label: '分红率 ≥' },
  { key: 'peTMax', label: 'PE温度 ≤' },
  { key: 'pbTMax', label: 'PB温度 ≤' },
  { key: 'roeAverageMin', label: '5年平均ROE ≥' },
];

// 高级筛选区阈值(默认收起)
const ADVANCED_THRESHOLDS = [
  { key: 'pbMax', label: 'PB ≤' },
  { key: 'intDebtMax', label: '有息负债率 ≤' },
  { key: 'roeMin', label: 'ROE ≥' },
  { key: 'revenueAvgMin', label: '5年营收复合 ≥' },
  { key: 'profitAvgMin', label: '5年利润复合 ≥' },
  { key: 'cashflowAvgMin', label: '5年现金流复合 ≥' },
  { key: 'epsGrowthTtmMin', label: '净利同比增长 ≥' },
];

// 高级区激活条件数( markets/行业/地域/7 阈值/流通市值区间 )
const ADVANCED_FORM_KEYS = [
  'pbMax', 'intDebtMax', 'roeMin', 'revenueAvgMin',
  'profitAvgMin', 'epsGrowthTtmMin', 'cashflowAvgMin',
];

const cascaderProps = { checkStrictly: true, emitPath: false, multiple: true };

// ---- 请求状态(复刻 CbMarket: reqToken 防竞态 + disposed) ----
const loading = ref(false); // 首次加载(骨架)
const errorMsg = ref(''); // 首次失败
const refreshError = ref(false); // 刷新失败(已有数据时)
const allRows = ref([]); // API 原始全量(最新交易日)

let reqToken = 0;
let disposed = false;

async function loadData(isRefresh = false) {
  const token = ++reqToken;
  loading.value = true;
  if (!isRefresh) errorMsg.value = '';
  refreshError.value = false;
  try {
    const raw = await getStockDividendSnapshot();
    if (disposed || token !== reqToken) return; // 卸载或已被新请求取代
    // 物化派生列 payout_rate(分红率=股息率TTM×PE), 列渲染/排序统一取该字段
    allRows.value = withPayoutRate(Array.isArray(raw) ? raw : []);
    page.value = 1; // 新数据回第一页
    errorMsg.value = '';
    loading.value = false;
  } catch (e) {
    if (disposed || token !== reqToken) return;
    if (isRefresh && allRows.value.length) {
      refreshError.value = true; // 保留上次成功数据
      loading.value = false;
    } else {
      errorMsg.value =
        (e?.response?.data && e.response.data.detail) || e?.message || '拉取失败,请检查后端日志';
      allRows.value = [];
      loading.value = false;
    }
  }
}

// ---- 筛选/排序/分页(全部本地, 零网络请求) ----
const form = ref(emptyForm());
const sort = ref({ ...DEFAULT_SORT });
const page = ref(1);
const size = ref(50);

const asOfDate = computed(() => allRows.value[0]?.trade_date || '');
const industryOptions = computed(() => buildIndustryTree(allRows.value));
const provinceOptions = computed(() => collectProvinceOptions(allRows.value));

// 一级行业码(全选写入这些即可: 一级前缀本身覆盖整棵子树)
const industryRootCodes = computed(() => industryOptions.value.map((o) => o.value));

function allRootsChecked(arr) {
  const roots = industryRootCodes.value;
  return roots.length > 0 && roots.every((c) => arr.includes(c));
}

const industriesAllSelected = computed(() => allRootsChecked(form.value.industries));
const excludeIndustriesAllSelected = computed(() => allRootsChecked(form.value.excludeIndustries));

// 全选 ⇄ 清空(填入全部一级码即语义完备; 再点一次清空)
function toggleAllIndustries() {
  form.value.industries = industriesAllSelected.value ? [] : [...industryRootCodes.value];
}

function toggleAllExcludeIndustries() {
  form.value.excludeIndustries = excludeIndustriesAllSelected.value ? [] : [...industryRootCodes.value];
}

// 地域一键选择: 常用沿海组合(江浙粤闽), 再点一次取消
const QUICK_PROVINCES = ['江苏', '浙江', '广东', '福建'];
const provincesQuick = computed(
  () => form.value.provinces.length > 0
    && form.value.provinces.every((p) => QUICK_PROVINCES.includes(p)),
);

function toggleQuickProvinces() {
  form.value.provinces = provincesQuick.value ? [] : [...QUICK_PROVINCES];
}

const filteredRows = computed(() => filterRows(allRows.value, form.value));
const sortedRows = computed(() => sortRows(filteredRows.value, sort.value));
const pagedRows = computed(() =>
  sortedRows.value.slice((page.value - 1) * size.value, page.value * size.value),
);

function resetForm() {
  // 重置 = 回到当前编辑预设的已保存值(而非清空; 预设未加载过则等同清空)
  form.value = sanitizeForm(savedForm.value);
}

// ---- 服务端预设(另存为/重命名/删除/设默认; 全量替换保存) ----
const presets = ref([]); // [{id, name, form}]
const activeId = ref(''); // 默认预设(下次进页即用它)
const editingId = ref(''); // 当前编辑中的预设
const savedForm = ref(emptyForm()); // 当前编辑预设的已保存表单(dirty 比较)
const presetsLoaded = ref(false);
const presetSaving = ref(false);
const advancedOpen = ref(false); // 高级筛选默认收起

const dirty = computed(
  () => JSON.stringify(form.value) !== JSON.stringify(savedForm.value),
);

const advancedCount = computed(() => {
  const f = form.value;
  let n = 0;
  if (f.markets.length) n += 1;
  if (f.industries.length) n += 1;
  if (f.excludeIndustries.length) n += 1;
  if (f.provinces.length) n += 1;
  for (const k of ADVANCED_FORM_KEYS) if (f[k] != null) n += 1;
  if (f.floatValueMin != null || f.floatValueMax != null) n += 1;
  return n;
});

function cloneForm(f) {
  return JSON.parse(JSON.stringify(f));
}

// 统一动作包装: ElMessageBox 的 cancel/close 静默, 其余报错走 ElMessage
async function action(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error(errorText(e));
  }
}

async function loadPresets() {
  try {
    const cfg = await getDividendPresets();
    presets.value = Array.isArray(cfg?.presets) ? cfg.presets : [];
    activeId.value = cfg?.active_id || presets.value[0]?.id || '';
    editingId.value = activeId.value;
    applyPresetForm(editingId.value);
    presetsLoaded.value = true;
  } catch (e) {
    ElMessage.error(`预设加载失败：${errorText(e)}`);
  }
}

function applyPresetForm(id) {
  const p = presets.value.find((x) => x.id === id);
  form.value = sanitizeForm(p?.form);
  savedForm.value = sanitizeForm(p?.form);
}

// el-select 用 :model-value(非 v-model): 取消切换时无需回滚状态
async function onSwitchPreset(id) {
  if (id === editingId.value) return;
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('切换预设将放弃当前未保存的修改，是否继续？', '切换预设');
    } catch {
      return;
    }
  }
  editingId.value = id;
  applyPresetForm(id);
}

async function persist() {
  presetSaving.value = true;
  try {
    const cfg = await saveDividendPresets({
      version: 1,
      active_id: activeId.value,
      presets: presets.value.map((p) => ({ id: p.id, name: p.name, form: sanitizeForm(p.form) })),
    });
    presets.value = Array.isArray(cfg?.presets) ? cfg.presets : presets.value;
    activeId.value = cfg?.active_id || activeId.value;
    // 以服务端返回为准刷新 dirty 基准
    savedForm.value = sanitizeForm(presets.value.find((p) => p.id === editingId.value)?.form);
  } finally {
    presetSaving.value = false;
  }
}

async function savePreset() {
  await action(async () => {
    const p = presets.value.find((x) => x.id === editingId.value);
    if (!p) return;
    p.form = cloneForm(form.value);
    await persist();
    ElMessage.success(`预设“${p.name}”已保存`);
  });
}

async function saveAs() {
  await action(async () => {
    const cur = presets.value.find((x) => x.id === editingId.value);
    const answer = await ElMessageBox.prompt('请输入新预设名称', '另存为预设', {
      inputValue: cur ? `${cur.name} 副本` : '',
      inputValidator: (v) =>
        (!!v?.trim() && [...v.trim()].length <= 40) || '名称需要 1～40 个字符',
    });
    const name = answer.value.trim();
    if (presets.value.some((p) => p.name === name)) throw new Error('预设名称重复');
    const preset = { id: `p${Date.now().toString(36)}`, name, form: cloneForm(form.value) };
    presets.value.push(preset);
    editingId.value = preset.id;
    savedForm.value = cloneForm(form.value);
    await persist();
    ElMessage.success(`预设“${name}”已保存`);
  });
}

async function setDefault() {
  await action(async () => {
    activeId.value = editingId.value;
    await persist();
    ElMessage.success('已设为默认预设，下次进入页面将直接使用');
  });
}

async function renamePreset() {
  await action(async () => {
    const p = presets.value.find((x) => x.id === editingId.value);
    if (!p) return;
    const answer = await ElMessageBox.prompt('请输入预设名称', '重命名预设', {
      inputValue: p.name,
      inputValidator: (v) =>
        (!!v?.trim() && [...v.trim()].length <= 40) || '名称需要 1～40 个字符',
    });
    const name = answer.value.trim();
    if (presets.value.some((x) => x.name === name && x.id !== p.id)) {
      throw new Error('预设名称重复');
    }
    p.name = name;
    await persist();
  });
}

async function removePreset() {
  await action(async () => {
    if (presets.value.length <= 1) {
      ElMessage.warning('至少保留一个预设');
      return;
    }
    const p = presets.value.find((x) => x.id === editingId.value);
    if (!p) return;
    await ElMessageBox.confirm(`删除预设“${p.name}”？`, '删除预设', { type: 'warning' });
    presets.value = presets.value.filter((x) => x.id !== p.id);
    if (activeId.value === p.id) activeId.value = presets.value[0].id;
    editingId.value = activeId.value;
    applyPresetForm(editingId.value);
    await persist();
  });
}

function onPresetCommand(cmd) {
  if (cmd === 'default') setDefault();
  else if (cmd === 'rename') renamePreset();
  else if (cmd === 'delete') removePreset();
}

function onSortChange({ prop, order }) {
  // 第三次点击清除排序时回默认(与 SelectionResults 惯例一致)
  sort.value = prop && order ? { prop, order } : { ...DEFAULT_SORT };
}

watch(form, () => { page.value = 1; }, { deep: true });
watch(size, () => { page.value = 1; });

// ---- 单元格分发(列配置驱动) ----
function withPct(text, v, col) {
  return col.pct && v != null && Number.isFinite(Number(v)) ? `${text}%` : text;
}

function cellText(row, col) {
  const v = row[col.field];
  if (col.fmt === 'volume') return fmtVolume(v);
  if (col.fmt === 'temp') return tempText(v);
  if (col.fmt === 'signed') return withPct(signedText(v), v, col);
  if (col.fmt === 'num2') return withPct(fmtNum(v), v, col);
  return v == null || v === '' ? '—' : v;
}

function cellClass(row, col) {
  if (col.fmt === 'temp') return tempClass(row[col.field]);
  if (col.fmt === 'signed') return signedClass(row[col.field]);
  return '';
}

function jisiluStockUrl(stockId) {
  return `https://www.jisilu.cn/data/stock/${stockId}`;
}

// ---- el-table-v2 表格层(固定表头 + 冻结列 + 虚拟滚动的原生形态) ----
// 列定义由 STOCK_DIVIDEND_COLUMNS 派生(列配置仍是单一事实源);
// 渲染函数产出的节点不带本组件 scoped 属性, 配色/徽标类放非 scoped 样式块。
const TOTAL_COLUMN_WIDTH = STOCK_DIVIDEND_COLUMNS.reduce((sum, c) => sum + c.width, 0);

function v2HeaderRenderer(col) {
  if (!col.headerTip) return undefined; // 缺省渲染 title
  return () => h(ElTooltip, { content: col.headerTip, placement: 'top' }, {
    default: () => h('span', { class: 'th-tip' }, [col.label, ' ⓘ']),
  });
}

function v2CellRenderer(col) {
  if (col.field === 'stock_id') {
    return ({ rowData }) => h('a', {
      class: 'code-link',
      href: jisiluStockUrl(rowData.stock_id),
      target: '_blank',
      rel: 'noopener',
    }, rowData.stock_id);
  }
  if (col.field === 'stock_nm') {
    // 名称 + R 徽标 + 审计警示(复刻原 el-table 名称列)
    return ({ rowData }) => [
      h('span', rowData.stock_nm),
      rowData.margin_flg === 'R' ? h('sup', { class: 'badge-r', title: '融资融券标的' }, 'R') : null,
      rowData.audit_info
        ? h(ElTooltip, { content: rowData.audit_info, placement: 'top' }, {
          default: () => h('span', { class: 'audit-warn' }, '⚠'),
        })
        : null,
    ];
  }
  if (col.field === 'industry_nm') {
    return ({ rowData }) => (rowData.industry_nm2
      ? h(ElTooltip, { content: rowData.industry_nm2, placement: 'top' }, {
        default: () => h('span', cellText(rowData, col)),
      })
      : h('span', cellText(rowData, col)));
  }
  if (col.field === 'pb') {
    return ({ rowData }) => (rowData.pb_flag === 'Y'
      ? h(ElTooltip, { content: '股东权益含优先股和永续债，PB值与其它平台计算会存在差异', placement: 'top' }, {
        default: () => h('span', { class: 'pb-gray' }, cellText(rowData, col)),
      })
      : h('span', { class: cellClass(rowData, col) }, cellText(rowData, col)));
  }
  return ({ rowData }) => h('span', { class: cellClass(rowData, col) }, cellText(rowData, col));
}

const v2Columns = STOCK_DIVIDEND_COLUMNS.map((col, idx) => ({
  key: col.field,
  dataKey: col.field,
  title: col.label,
  width: col.width,
  align: col.align,
  fixed: idx < 2 || undefined, // 代码/名称 左固定(true = left)
  sortable: true,
  flexGrow: col.field === 'province' ? 1 : undefined, // 末列吃满剩余宽, 容器更宽不出留白
  headerCellRenderer: v2HeaderRenderer(col),
  cellRenderer: v2CellRenderer(col),
}));

// 排序状态桥: table-v2 表头只有 asc/desc 两态循环(无第三次点击还原默认),
// 排序本体仍是 sortRows(null 沉底 + stock_id tie-break), 这里只翻译事件与指示态
const v2SortState = computed(() => ({
  [sort.value.prop]: sort.value.order === 'ascending' ? 'asc' : 'desc',
}));

function onV2ColumnSort({ key, order }) {
  onSortChange({ prop: String(key), order: order === 'asc' ? 'ascending' : 'descending' });
}

// 斑马纹(table-v2 无内建 stripe, 走行类)
function v2RowClass({ rowIndex }) {
  return rowIndex % 2 === 1 ? 'v2-row-alt' : '';
}

// width/height 必须传数字: 宽取容器实测(clientWidth 为 0 时回退列宽合计),
// 高随视口(表格上方筛选区/下方分页合计约 450px)
const tableWrap = ref(null);
const tableWidth = ref(0);
const tableHeight = ref(360);

function measureTable() {
  tableWidth.value = tableWrap.value?.clientWidth || 0;
  tableHeight.value = Math.max(320, window.innerHeight - 450);
}
const v2Width = computed(() => tableWidth.value || TOTAL_COLUMN_WIDTH);

// 数据新鲜度: 截至日期较今天超过 7 个自然日 → 中性提示
const dataAgeDays = computed(() => {
  if (!asOfDate.value) return null;
  const t = Date.parse(`${asOfDate.value}T00:00:00`);
  if (Number.isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 86400000);
});
const isStale = computed(() => dataAgeDays.value != null && dataAgeDays.value > 7);

const refreshing = computed(() => loading.value && allRows.value.length > 0);

onMounted(() => {
  measureTable(); // 表格容器尺寸(el-table-v2 需要数字宽高)
  window.addEventListener('resize', measureTable);
  loadData(false);
  loadPresets(); // 与数据加载并行; 完成后套用默认预设(套用即触发本地筛选)
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', measureTable);
  disposed = true; // 卸载后不再更新页面
});
</script>

<template>
  <div class="stock-dividend-page">
    <!-- 1. 数据说明 / 刷新(菜单已示页面名, 页内不再重复标题) -->
    <div class="page-head">
      <span class="data-note">
        <template v-if="asOfDate">
          数据截至 {{ asOfDate }} · 来源：集思录股息率排行 · 成分：总市值≥200亿
        </template>
        <template v-else-if="!loading && !errorMsg">来源：集思录股息率排行 · 成分：总市值≥200亿</template>
      </span>
      <button class="refresh-btn" :disabled="loading" @click="loadData(true)">
        {{ refreshing ? '刷新中…' : '刷新' }}
      </button>
    </div>
    <div v-if="refreshError" class="banner warn">
      刷新失败，已保留上次成功数据（截至 {{ asOfDate || '—' }}）。
    </div>

    <!-- 首次失败 -->
    <div v-if="errorMsg" class="banner error">
      <span>{{ errorMsg }}</span>
      <button class="retry-btn" :disabled="loading" @click="loadData(false)">重试</button>
    </div>

    <!-- 首次加载骨架 -->
    <div v-if="loading && allRows.length === 0" class="skeleton">
      <div class="sk-toolbar" />
      <div class="sk-row" v-for="n in 8" :key="n" />
    </div>

    <!-- 空数组 -->
    <div v-else-if="!errorMsg && allRows.length === 0" class="empty-state">
      <p>暂无高股息快照数据</p>
      <router-link class="link-btn" to="/status">查看数据状态</router-link>
    </div>

    <!-- 正常内容 -->
    <template v-else-if="!errorMsg && allRows.length > 0">
      <!-- 3. 预设工作台(服务端保存; 默认预设下次进页即套用) -->
      <div class="preset-bar">
        <span class="status" :class="{ dirty }">{{ dirty ? '未保存' : '已保存' }}</span>
        <el-select
          :model-value="editingId"
          filterable
          placeholder="筛选预设"
          size="small"
          class="preset-select"
          aria-label="筛选预设"
          @change="onSwitchPreset"
        >
          <el-option
            v-for="p in presets"
            :key="p.id"
            :value="p.id"
            :label="`${p.name}${activeId === p.id ? ' · 默认' : ''}`"
          />
        </el-select>
        <el-button size="small" :disabled="!dirty || presetSaving" :loading="presetSaving" @click="savePreset">保存</el-button>
        <el-button size="small" :disabled="presetSaving" @click="saveAs">另存为</el-button>
        <el-dropdown @command="onPresetCommand">
          <el-button size="small" :disabled="presetSaving">更多 ▾</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="default" :disabled="activeId === editingId">设为默认</el-dropdown-item>
              <el-dropdown-item command="rename">重命名</el-dropdown-item>
              <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <!-- 4. 筛选区: 邮件漏斗主条件常驻(4 列网格, 对齐集思录表单式排版),
           其余收进高级面板(本地过滤, 不发请求) -->
      <div class="filter-bar" :class="{ attached: advancedOpen }">
        <label v-for="f in PRIMARY_THRESHOLDS" :key="f.key" class="f-item">
          <span class="f-label">{{ f.label }}</span>
          <el-input-number
            v-model="form[f.key]"
            :controls="false"
            placeholder="不限"
            size="small"
            class="num-input"
          />
        </label>
        <label class="f-item">
          <span class="f-label">总市值(亿)</span>
          <el-input-number v-model="form.totalValueMin" :controls="false" placeholder="下限" size="small" class="num-input" />
          <span class="f-sep">~</span>
          <el-input-number v-model="form.totalValueMax" :controls="false" placeholder="上限" size="small" class="num-input" />
        </label>
        <label class="f-item">
          <span class="f-label">仅国资</span>
          <el-switch v-model="form.soeOnly" size="small" />
        </label>
        <div class="f-actions">
          <el-button size="small" @click="resetForm">重置</el-button>
          <span class="f-count">{{ sortedRows.length }} / {{ allRows.length }} 只</span>
          <el-button size="small" text class="adv-toggle" @click="advancedOpen = !advancedOpen">
            高级筛选{{ advancedCount ? `(${advancedCount})` : '' }}{{ advancedOpen ? ' ▴' : ' ▾' }}
          </el-button>
        </div>
      </div>

      <!-- 高级筛选面板(默认收起; 有激活条件时切换按钮带计数) -->
      <div v-if="advancedOpen" class="filter-bar advanced">
        <div class="f-item">
          <span class="f-label">市场</span>
          <el-checkbox-group v-model="form.markets" size="small">
            <el-checkbox value="sh">沪市</el-checkbox>
            <el-checkbox value="sz">深市</el-checkbox>
          </el-checkbox-group>
        </div>
        <label class="f-item">
          <span class="f-label">行业</span>
          <el-cascader
            v-model="form.industries"
            :options="industryOptions"
            :props="cascaderProps"
            placeholder="不限"
            clearable
            filterable
            collapse-tags
            collapse-tags-tooltip
            size="small"
            class="f-fill"
          />
          <button
            type="button"
            class="mini-link"
            :disabled="!industryRootCodes.length"
            @click="toggleAllIndustries"
          >
            {{ industriesAllSelected ? '清空' : '全选' }}
          </button>
        </label>
        <label class="f-item">
          <span class="f-label">排除行业</span>
          <el-cascader
            v-model="form.excludeIndustries"
            :options="industryOptions"
            :props="cascaderProps"
            placeholder="无"
            clearable
            filterable
            collapse-tags
            collapse-tags-tooltip
            size="small"
            class="f-fill"
          />
          <button
            type="button"
            class="mini-link"
            :disabled="!industryRootCodes.length"
            @click="toggleAllExcludeIndustries"
          >
            {{ excludeIndustriesAllSelected ? '清空' : '全选' }}
          </button>
        </label>
        <label class="f-item">
          <span class="f-label">地域</span>
          <el-select
            v-model="form.provinces"
            placeholder="不限"
            clearable
            filterable
            multiple
            collapse-tags
            collapse-tags-tooltip
            size="small"
            class="f-fill"
          >
            <el-option v-for="p in provinceOptions" :key="p" :label="p" :value="p" />
          </el-select>
          <button type="button" class="mini-link" @click="toggleQuickProvinces">
            {{ provincesQuick ? '清空' : '江浙粤闽' }}
          </button>
        </label>
        <label v-for="f in ADVANCED_THRESHOLDS" :key="f.key" class="f-item">
          <span class="f-label">{{ f.label }}</span>
          <el-input-number
            v-model="form[f.key]"
            :controls="false"
            placeholder="不限"
            size="small"
            class="num-input"
          />
        </label>
        <label class="f-item">
          <span class="f-label">流通市值(亿)</span>
          <el-input-number v-model="form.floatValueMin" :controls="false" placeholder="下限" size="small" class="num-input" />
          <span class="f-sep">~</span>
          <el-input-number v-model="form.floatValueMax" :controls="false" placeholder="上限" size="small" class="num-input" />
        </label>
      </div>

      <!-- 4. 宽表(el-table-v2: 固定表头 + 冻结列 + 虚拟滚动原生形态) -->
      <div ref="tableWrap" class="table-wrap">
        <el-table-v2
          class="stock-table"
          :columns="v2Columns"
          :data="pagedRows"
          :width="v2Width"
          :height="tableHeight"
          row-key="stock_id"
          :row-height="36"
          :header-height="40"
          :sort-state="v2SortState"
          :row-class="v2RowClass"
          @column-sort="onV2ColumnSort"
        >
          <template #empty>
            <span class="v2-empty">筛选无结果</span>
          </template>
        </el-table-v2>
      </div>

      <!-- 5. 分页 -->
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="size"
        :page-sizes="PAGE_SIZES"
        layout="prev,pager,next,sizes,total"
        :total="sortedRows.length"
      />

      <!-- 陈旧数据中性提示 -->
      <div v-if="isStale" class="banner neutral">
        已超过 7 天无新记录，节假日或同步延迟均可能导致。
      </div>

      <!-- 6. 口径说明 -->
      <details class="caliber">
        <summary>数据口径</summary>
        <p>
          快照来自集思录「股息率排行」页成分股（总市值≥200亿），交易日收盘后定时抓取；
          本页展示最新交易日全量，筛选/排序/分页在浏览器本地完成（与集思录筛选语义一致：
          数值条件启用时，缺失该字段的行会被过滤）。分红率为派生指标：
          分红率=股息率TTM×PE-TTM=每股分红÷TTM每股净利润，亏损（PE≤0）或缺数时无值
          （显示「—」，启用「分红率≥」筛选时该行被过滤）；股息率TTM=到前一交易日为止
          最近4个季报每股分红与当前股价的比值；静态股息率=上一自然年度收到的每股分红
          与当前股价的比值。
        </p>
        <p>
          PE/PB温度为当前估值在历史区间的分位色阶（&lt;25 青 / &lt;50 绿 / &lt;75 橙 / ≥75 红）；
          温度为负时仅显示「—」，但仍参与筛选与排序。PB 灰色值表示股东权益含优先股和永续债，
          与其它平台存在口径差异。缺失值一律显示「—」。
        </p>
        <router-link class="link-btn" to="/status">查看数据状态</router-link>
      </details>
    </template>
  </div>
</template>

<style scoped>
.stock-dividend-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.refresh-btn {
  border: 1px solid #d1d5db;
  background: #fff;
  border-radius: 8px;
  padding: 5px 14px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
}
.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.data-note {
  font-size: 12px;
  color: #9ca3af;
}

.banner {
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.banner.error {
  background: rgba(220, 38, 38, 0.06);
  color: #b91c1c;
}
.banner.warn {
  background: rgba(217, 119, 6, 0.08);
  color: #b45309;
}
.banner.neutral {
  background: rgba(100, 116, 139, 0.08);
  color: #475569;
}
.retry-btn {
  border: 1px solid #b91c1c;
  background: #fff;
  color: #b91c1c;
  border-radius: 6px;
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
}
.retry-btn:disabled {
  opacity: 0.5;
}

/* 骨架 */
.skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sk-toolbar {
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}
.sk-row {
  height: 30px;
  border-radius: 6px;
  background: linear-gradient(90deg, #f1f5f9, #e2e8f0, #f1f5f9);
  background-size: 200% 100%;
  animation: shimmer 1.3s infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

.empty-state {
  text-align: center;
  color: #9ca3af;
  font-size: 14px;
  padding: 40px 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: center;
}
.link-btn {
  font-size: 13px;
  color: #2563eb;
  text-decoration: none;
  white-space: nowrap;
}
.link-btn:hover {
  text-decoration: underline;
}

/* 预设工作台 */
.preset-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: #fafafa;
  border: 1px solid #eef2f7;
  border-radius: 10px;
  padding: 8px 12px;
}
.preset-select {
  width: 220px;
}
.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}
.status::before {
  content: '';
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #67c23a;
}
.status.dirty::before {
  background: #e6a23c;
}

/* 筛选区: 标签+控件成组, 4 列网格对齐(集思录表单式排版) */
.filter-bar {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px 12px; /* 紧凑: 行距 6 / 列距 12 */
  align-items: center;
  background: #fafafa;
  border: 1px solid #eef2f7;
  border-radius: 10px;
  padding: 10px 12px;
}
.filter-bar.attached {
  border-radius: 10px 10px 0 0;
  border-bottom: none; /* 与高级面板贴合, 由后者的虚线边作分隔 */
}
.filter-bar.advanced {
  border-radius: 0 0 10px 10px;
  border-top-style: dashed;
  margin-top: -12px; /* 抵消页面 gap, 与主筛选区贴合 */
  background: #f7f9fb;
}
.f-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #4b5563;
  white-space: nowrap;
  min-width: 0; /* 允许网格列内收缩 */
}
.f-label {
  color: #6b7280;
  flex-shrink: 0;
}
.num-input {
  width: 84px; /* 数字位数有限, 固定窄宽(不随网格列拉伸) */
  flex: none;
}
.num-input :deep(input) {
  text-align: right;
}
.f-fill {
  flex: 1;
  min-width: 0; /* 级联/下拉占满所在列剩余宽 */
}
.f-sep {
  color: #9ca3af;
}
.f-actions {
  grid-column: 1 / -1; /* 操作行独占整行 */
  display: flex;
  align-items: center;
  gap: 10px;
}
.adv-toggle {
  white-space: nowrap;
}
.mini-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  color: #2563eb;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.mini-link:disabled {
  color: #c0c4cc;
  cursor: not-allowed;
}
.mini-link:hover:not(:disabled) {
  text-decoration: underline;
}
.f-count {
  margin-left: auto;
  font-size: 12px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 表格容器(el-table-v2 需要数字宽高, 挂载后实测容器尺寸) */
.table-wrap {
  width: 100%;
}

/* 分页 */
.el-pagination {
  flex-wrap: wrap;
}

/* 口径 */
.caliber {
  border: 1px solid #eef2f7;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 12px;
  color: #6b7280;
  background: #fafafa;
}
.caliber summary {
  cursor: pointer;
  font-weight: 600;
  color: #4b5563;
}
.caliber p {
  margin: 10px 0;
  line-height: 1.7;
}

/* 移动端/窄屏: 网格降列(3 → 2), 表格横向滚动 */
@media (max-width: 1100px) and (min-width: 768px) {
  .filter-bar {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 767px) {
  .filter-bar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .f-count { margin-left: 0; }
  .num-input { width: 72px; }
}
</style>

<style>
/* el-table-v2 的单元格/表头由渲染函数产出, 不带本组件 scoped 属性,
   单元格配色/徽标类放非 scoped 块, 以页面根类限定作用域 */
.stock-dividend-page .stock-table .el-table-v2__row-cell {
  font-variant-numeric: tabular-nums;
}
.stock-dividend-page .stock-table .v2-row-alt {
  background: var(--el-fill-color-lighter);
}
.stock-dividend-page .stock-table .v2-empty {
  color: #9ca3af;
  font-size: 13px;
}
.stock-dividend-page .stock-table .code-link {
  color: #2563eb;
  text-decoration: none;
}
.stock-dividend-page .stock-table .code-link:hover {
  text-decoration: underline;
}
.stock-dividend-page .stock-table .badge-r {
  margin-left: 2px;
  font-size: 10px;
  color: #f59e0b;
}
.stock-dividend-page .stock-table .audit-warn {
  margin-left: 3px;
  color: #dc2626;
  cursor: help;
}
.stock-dividend-page .stock-table .pb-gray {
  color: #9ca3af;
}
.stock-dividend-page .stock-table .th-tip {
  cursor: help;
}

/* 温度色阶(对齐集思录 liquidColour 四档) */
.stock-dividend-page .stock-table .t-cyan { color: #0099cc; }
.stock-dividend-page .stock-table .t-green { color: #468847; }
.stock-dividend-page .stock-table .t-orange { color: #f89406; }
.stock-dividend-page .stock-table .t-red { color: #b94a48; }

/* 涨红跌绿 */
.stock-dividend-page .stock-table .up { color: #c0392b; }
.stock-dividend-page .stock-table .down { color: #1e8e4e; }
</style>
