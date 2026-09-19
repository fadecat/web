<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { createAsset, probeAsset } from '../../api/portfolio';
import {
  TYPE_HINT_OPTIONS, buildStartShiftNotice, candidateKey, candidateRangeText,
  confirmSubtitle, exchangeLabel, formatRange, isDate, priceBasisLabel,
  securityTypeLabel, sourceLabel,
} from '../../utils/portfolioAssets.mjs';
import { createRequestGuard } from '../../utils/requestGuard.js';

// 交互(已对齐的产品设计, 勿自由发挥):
//   1 输入(类型 chip + 代码, 防抖 400ms 自动 probe)
//   2 解析结果(1 个直接确认 / 多个让用户挑 / 0 个给原因)
//   3 确认卡(把复权口径摆在用户面前)
//   4 添加(不阻塞: 返回即「同步中」)
//   5 emit 起点变化, 由父页面渲染警示条

const props = defineProps({
  // 对话框显隐(v-model)
  modelValue: { type: Boolean, default: false },
  // 当前组合共同起点(YYYY-MM-DD): 传入后新增标的若更晚, start-change 会带出前移幅度
  currentStart: { type: String, default: null },
  // 已在组合中的规范代码(可选): 后端 registered 之外的二次兜底(L2 编辑态用)
  existingSymbols: { type: Array, default: () => [] },
});

const emit = defineEmits(['update:modelValue', 'added', 'start-change']);

const PROBE_DEBOUNCE_MS = 400;
const MAX_CANDIDATES = 3;

const code = ref('');
const typeHint = ref('auto');
const candidates = ref([]);
const selectedKey = ref('');
const probeState = ref('idle'); // idle | probing | empty | invalid | unavailable
const probeDetail = ref('');
const submitting = ref(false);
const formError = ref('');

const probeGuard = createRequestGuard();
let debounceTimer = null;

const selected = computed(
  () => candidates.value.find((item) => candidateKey(item) === selectedKey.value) || null,
);

// 未解析到的标的不允许添加(口径/区间都无从展示); 场外基金例外 —— 蛋卷详情接口不可用时
// 只是"拿不到名称", 净值同步是独立链路, 仍应允许添加
const addBlocked = computed(
  () => !!selected.value && selected.value.resolved === false
    && selected.value.security_type !== 'FUND',
);

const clearResult = () => {
  candidates.value = [];
  selectedKey.value = '';
  probeState.value = 'idle';
  probeDetail.value = '';
  formError.value = '';
};

const runProbe = async (value) => {
  const version = probeGuard.next();
  probeState.value = 'probing';
  probeDetail.value = '';
  formError.value = '';
  try {
    const rows = await probeAsset(value, typeHint.value === 'auto' ? null : typeHint.value);
    if (!probeGuard.isLatest(version)) return;
    const list = Array.isArray(rows) ? rows.slice(0, MAX_CANDIDATES) : [];
    candidates.value = list;
    // 唯一候选直接进入确认卡, 多个候选等用户挑
    selectedKey.value = list.length === 1 ? candidateKey(list[0]) : '';
    probeState.value = list.length ? 'idle' : 'empty';
  } catch (err) {
    if (!probeGuard.isLatest(version)) return;
    candidates.value = [];
    selectedKey.value = '';
    const status = err?.response?.status;
    if (status === 404) {
      probeState.value = 'empty';
    } else if (status === 422) {
      probeState.value = 'invalid';
      probeDetail.value = err?.response?.data?.detail || '代码写法无法识别';
    } else {
      probeState.value = 'unavailable';
      probeDetail.value = '解析服务暂时不可用，请稍后重试';
    }
  }
};

watch([code, typeHint], () => {
  clearTimeout(debounceTimer);
  clearResult();
  const value = code.value.trim();
  if (!value) return;
  debounceTimer = setTimeout(() => runProbe(value), PROBE_DEBOUNCE_MS);
});

// 回车立即解析, 不等防抖
const probeNow = () => {
  clearTimeout(debounceTimer);
  const value = code.value.trim();
  if (!value) return;
  runProbe(value);
};

const submit = async () => {
  const candidate = selected.value;
  if (!candidate || addBlocked.value || submitting.value) return;
  if (candidate.registered || props.existingSymbols.includes(candidate.symbol)) {
    formError.value = '已在组合中';
    return;
  }
  submitting.value = true;
  formError.value = '';
  try {
    const asset = await createAsset({
      symbol: candidate.symbol,
      security_type: candidate.security_type,
      name: candidate.name || '',
    });
    emit('added', asset);
    emitStartChange(asset, candidate);
    close();
  } catch (err) {
    const status = err?.response?.status;
    if (status === 501) {
      // P1 起场外基金已可注册, 这里只是兜底(后端旧版本仍会拒绝 FUND)
      formError.value = err?.response?.data?.detail || '该类型标的暂不支持注册';
    } else if (status === 422) {
      formError.value = err?.response?.data?.detail || '参数非法，无法添加';
    } else {
      formError.value = '添加失败，请稍后重试';
    }
  } finally {
    submitting.value = false;
  }
};

// 第 5 步: 新标的可能把组合共同起点往后推, 由父页面渲染警示条
const emitStartChange = (asset, candidate) => {
  const firstDate = asset?.first_date || candidate?.first_date || null;
  const newStart = [props.currentStart, firstDate].filter(isDate).sort().pop() || null;
  if (!newStart) return;
  emit('start-change', {
    asset: asset || candidate,
    firstDate,
    oldStart: props.currentStart || null,
    newStart,
    ...buildStartShiftNotice({ oldStart: props.currentStart, newStart, asset: asset || candidate }),
  });
};

const close = () => emit('update:modelValue', false);

const resetAll = () => {
  code.value = '';
  typeHint.value = 'auto';
  clearResult();
};

const onVisibleChange = (visible) => {
  if (!visible) close();
};

onBeforeUnmount(() => {
  clearTimeout(debounceTimer);
  probeGuard.invalidate();
});

defineExpose({ open: () => emit('update:modelValue', true), close, reset: resetAll });
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="添加标的"
    width="580px"
    @update:model-value="onVisibleChange"
    @closed="resetAll"
  >
    <!-- 第 1 步: 类型 + 代码 -->
    <div class="add-asset-type">
      <el-radio-group v-model="typeHint" size="small">
        <el-radio-button
          v-for="option in TYPE_HINT_OPTIONS"
          :key="option.value"
          :value="option.value"
        >
          {{ option.label }}
        </el-radio-button>
      </el-radio-group>
    </div>
    <el-input
      v-model="code"
      class="add-asset-input"
      placeholder="支持 600900 / sh600900 / 600900.SH（场外基金 100018.OF）"
      clearable
      @keyup.enter="probeNow"
    />
    <p class="add-asset-hint">输入后自动解析；同一代码可能同时命中股票与场外基金，请看清候选再确认。</p>

    <p v-if="probeState === 'probing'" class="add-asset-probing">解析中…</p>

    <!-- 第 2 步: 0 候选 -->
    <div v-else-if="probeState === 'empty'" class="not-found">
      <p class="not-found-title">未找到该代码</p>
      <ul class="not-found-reasons">
        <li>代码有误（沪市股票 60/68、深市股票 00/30、ETF 51/15/56/58 开头）</li>
        <li>数据源不覆盖：股票 / ETF 走腾讯行情，场外基金走蛋卷净值</li>
        <li>场外基金用 6 位代码；场内 ETF 想用蛋卷净值口径，可写 513100.OF 这样带 .OF 的代码</li>
      </ul>
    </div>
    <div v-else-if="probeState === 'invalid'" class="not-found">
      <p class="not-found-title">代码写法无法识别</p>
      <p class="not-found-detail">{{ probeDetail }}</p>
    </div>
    <div v-else-if="probeState === 'unavailable'" class="not-found">
      <p class="not-found-title">解析服务暂时不可用</p>
      <p class="not-found-detail">{{ probeDetail }}</p>
    </div>

    <!-- 第 2 步: 多个候选 -->
    <div v-else-if="candidates.length > 1" class="candidate-list">
      <p class="candidate-hint">该代码有 {{ candidates.length }} 个候选，请选择：</p>
      <el-radio-group v-model="selectedKey" class="candidate-group">
        <el-radio
          v-for="item in candidates"
          :key="candidateKey(item)"
          :value="candidateKey(item)"
          class="candidate-item"
        >
          <span class="candidate-name">{{ item.name || '（未解析到名称）' }}</span>
          <span class="candidate-meta">
            {{ securityTypeLabel(item.security_type) }} · {{ exchangeLabel(item.symbol) }} · {{ sourceLabel(item.source) }}
          </span>
          <span class="candidate-range">{{ candidateRangeText(item) }}</span>
        </el-radio>
      </el-radio-group>
    </div>

    <!-- 第 3 步: 确认卡(口径摆在最前面) -->
    <div v-if="selected" class="confirm-card">
      <div class="confirm-name">{{ selected.name || '（未解析到名称）' }}</div>
      <!-- 副标题: 代码 · 类型描述 · 基金经理(add-asset-ux §二第 3 步的示例形态) -->
      <div class="confirm-subtitle">{{ confirmSubtitle(selected) }}</div>
      <div class="confirm-row">
        <span class="confirm-label">代码 · 类型</span>
        <span>{{ selected.symbol }} · {{ securityTypeLabel(selected.security_type) }}</span>
      </div>
      <div class="confirm-row">
        <span class="confirm-label">可用数据区间</span>
        <span>{{ formatRange(selected.first_date, selected.last_date, selected.row_count) }}</span>
      </div>
      <div class="confirm-row basis-row">
        <span class="confirm-label">复权口径</span>
        <span>{{ priceBasisLabel(selected.price_basis) }}</span>
      </div>
      <div class="confirm-row">
        <span class="confirm-label">数据源</span>
        <span>
          {{ sourceLabel(selected.source) }}
          <template v-if="selected.latest_date">（最新 {{ selected.latest_date }}）</template>
        </span>
      </div>
      <p v-if="selected.note" class="confirm-note">{{ selected.note }}</p>
      <p v-if="addBlocked" class="confirm-warn">数据源未解析到该标的，暂无可用数据，不可添加</p>
      <p v-if="formError" class="confirm-error">{{ formError }}</p>
      <div class="confirm-actions">
        <el-button @click="close">取消</el-button>
        <el-button type="primary" :loading="submitting" :disabled="addBlocked" @click="submit">
          添加
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.add-asset-type {
  margin-bottom: 10px;
}

.add-asset-input {
  width: 100%;
}

.add-asset-hint,
.add-asset-probing {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.not-found {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.not-found-title {
  font-size: 13px;
  font-weight: 600;
  color: #d93026;
}

.not-found-reasons {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.not-found-detail {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.candidate-list {
  margin-top: 12px;
}

.candidate-hint {
  margin: 0 0 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.candidate-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}

.candidate-item {
  width: 100%;
  margin-right: 0;
  padding: 6px 10px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 6px;
}

.candidate-name {
  font-weight: 600;
}

.candidate-meta {
  margin-left: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* 候选的数据区间(未抓取时显示「成立/最新 …（待抓取）」, 不编造区间) */
.candidate-range {
  display: block;
  margin: 2px 0 0 24px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}

.confirm-card {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

.confirm-name {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 2px;
}

/* 确认卡副标题: 代码 · 类型描述 · 基金经理 */
.confirm-subtitle {
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.confirm-row {
  display: flex;
  gap: 12px;
  font-size: 13px;
  line-height: 1.9;
}

.confirm-label {
  width: 88px;
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
}

.confirm-note {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.confirm-warn {
  margin: 6px 0 0;
  font-size: 12px;
  color: #b45309;
}

.confirm-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: #d93026;
}

.confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
