<script setup>
import { ref, computed, onMounted } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getFactorCatalog, getFactors, saveFactors, screenBonds } from '../api';

// ── 状态 ──────────────────────────────────────────────
const catalog = ref([]);
const templates = ref([]);
const activeId = ref('');
const editingId = ref('');
const dirty = ref(false);
const loading = ref(true);
const saving = ref(false);
const previewing = ref(false);
const previewResult = ref(null);
const excludedBondInput = ref('');

const currentTmpl = computed(() => templates.value.find((t) => t.id === editingId.value));

// ── 加载 ──────────────────────────────────────────────
onMounted(async () => {
  try {
    const [cat, cfg] = await Promise.all([getFactorCatalog(), getFactors()]);
    catalog.value = cat;
    templates.value = cfg.templates || [];
    activeId.value = cfg.active_id;
    editingId.value = cfg.active_id;
  } catch {
    ElMessage.error('加载因子配置失败');
  } finally {
    loading.value = false;
  }
});

// ── 更新模板 ──────────────────────────────────────────
const updateTmpl = (patch) => {
  templates.value = templates.value.map((t) =>
    t.id === editingId.value ? { ...t, ...patch } : t,
  );
  dirty.value = true;
};

// ── 模板操作 ──────────────────────────────────────────
const newId = () => `tmpl_${Date.now()}`;

const handleNewTemplate = () => {
  const base = currentTmpl.value || {};
  templates.value.push({
    id: newId(),
    name: `新模板 ${templates.value.length + 1}`,
    description: '',
    target_count: base.target_count ?? 10,
    hold_tolerance: base.hold_tolerance ?? 0,
    exclusion_rules: JSON.parse(JSON.stringify(base.exclusion_rules ?? [])),
    strategy_factors: JSON.parse(JSON.stringify(base.strategy_factors ?? [])),
    excluded_redeem_icons: JSON.parse(JSON.stringify(base.excluded_redeem_icons ?? ['R', 'O', 'B'])),
    redeem_safe_days: base.redeem_safe_days ?? 2,
    excluded_bond_codes: JSON.parse(JSON.stringify(base.excluded_bond_codes ?? [])),
    min_listing_days: base.min_listing_days ?? 0,
  });
  editingId.value = templates.value[templates.value.length - 1].id;
  dirty.value = true;
};

const handleDuplicate = () => {
  const id = newId();
  templates.value.push({ ...JSON.parse(JSON.stringify(currentTmpl.value)), id, name: `${currentTmpl.value.name} 副本` });
  editingId.value = id;
  dirty.value = true;
};

const handleDelete = async (id) => {
  if (templates.value.length <= 1) {
    ElMessage.warning('至少保留一套模板');
    return;
  }
  await ElMessageBox.confirm('确定删除该模板？', '提示', { type: 'warning' });
  const remaining = templates.value.filter((t) => t.id !== id);
  templates.value = remaining;
  if (editingId.value === id) editingId.value = remaining[0].id;
  if (activeId.value === id) activeId.value = remaining[0].id;
  dirty.value = true;
};

const handleSetActive = () => {
  activeId.value = editingId.value;
  dirty.value = true;
  ElMessage.info(`已将"${currentTmpl.value.name}"设为当前策略（记得保存）`);
};

// ── 保存 ──────────────────────────────────────────────
const handleSave = async () => {
  saving.value = true;
  try {
    const result = await saveFactors({ active_id: activeId.value, templates: templates.value });
    if (result?.data) {
      activeId.value = result.data.active_id;
      templates.value = result.data.templates || [];
      if (!templates.value.some((t) => t.id === editingId.value)) {
        editingId.value = result.data.active_id;
      }
    }
    dirty.value = false;
    ElMessage.success('配置已保存');
  } catch {
    ElMessage.error('保存失败');
  } finally {
    saving.value = false;
  }
};

// ── 预览筛选 ──────────────────────────────────────────
const handlePreview = async () => {
  if (!currentTmpl.value) return;
  previewing.value = true;
  previewResult.value = null;
  try {
    previewResult.value = await screenBonds(currentTmpl.value);
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '筛选失败');
  } finally {
    previewing.value = false;
  }
};

// ── 排除代码 ──────────────────────────────────────────
const normalizeBondCode = (code) => {
  const n = String(code || '').trim().toUpperCase();
  if (!n) return '';
  if (n.endsWith('.SH') || n.endsWith('.SZ')) return n;
  if (/^\d{6}$/.test(n)) {
    if (n.startsWith('11')) return `${n}.SH`;
    if (n.startsWith('12')) return `${n}.SZ`;
  }
  return n;
};

const handleAddExcludedCode = () => {
  const normalized = normalizeBondCode(excludedBondInput.value);
  if (!normalized) {
    ElMessage.warning('请输入转债代码');
    return;
  }
  if (!/^\d{6}(\.(SH|SZ))?$/.test(normalized)) {
    ElMessage.warning('请输入 6 位转债代码，可选带 .SH/.SZ 后缀');
    return;
  }
  const existing = currentTmpl.value.excluded_bond_codes ?? [];
  if (existing.some((item) => (typeof item === 'string' ? item : item.code) === normalized)) {
    ElMessage.info('该转债代码已在排除列表中');
    return;
  }
  updateTmpl({ excluded_bond_codes: [...existing, { code: normalized, name: '' }] });
  excludedBondInput.value = '';
};

const handleRemoveExcludedCode = (code) => {
  updateTmpl({
    excluded_bond_codes: (currentTmpl.value.excluded_bond_codes ?? []).filter(
      (item) => (typeof item === 'string' ? item : item.code) !== code,
    ),
  });
};

// ── 排除因子/打分因子 ──────────────────────────────────
const addExclusionRule = () => {
  const rules = currentTmpl.value.exclusion_rules ?? [];
  const used = new Set(rules.map((r) => r.field));
  const next = catalog.value.find((f) => !used.has(f.field)) || catalog.value[0];
  if (!next) {
    ElMessage.warning('没有更多可选因子');
    return;
  }
  updateTmpl({
    exclusion_rules: [...rules, { field: next.field, label: next.label, op: 'lt', threshold: 0, unit: next.unit, enabled: true }],
  });
};

const removeExclusionRule = (idx) => {
  const rules = [...currentTmpl.value.exclusion_rules];
  rules.splice(idx, 1);
  updateTmpl({ exclusion_rules: rules });
};

// 排除因子字段变更：同步 label/unit
const onExclusionFieldChange = (rule) => {
  const meta = factorMeta(rule.field);
  rule.label = meta.label;
  rule.unit = meta.unit || '';
  dirty.value = true;
};

const addScoreFactor = () => {
  const factors = currentTmpl.value.strategy_factors ?? [];
  const used = new Set(factors.map((f) => f.field));
  const next = catalog.value.find((f) => !used.has(f.field)) || catalog.value[0];
  if (!next) {
    ElMessage.warning('没有更多可选因子');
    return;
  }
  updateTmpl({
    strategy_factors: [...factors, { field: next.field, label: next.label, ascending: true, weight: 1.0, enabled: true }],
  });
};

const removeScoreFactor = (idx) => {
  const factors = [...currentTmpl.value.strategy_factors];
  factors.splice(idx, 1);
  updateTmpl({ strategy_factors: factors });
};

// 打分因子字段变更：同步 label
const onScoreFieldChange = (factor) => {
  const meta = factorMeta(factor.field);
  factor.label = meta.label;
  dirty.value = true;
};

// 因子元数据（用于显示单位）
const factorMeta = (field) => catalog.value.find((f) => f.field === field) || {};
</script>

<template>
  <div v-loading="loading">
    <!-- 模板 Tab -->
    <div class="tmpl-tabs">
      <div
        v-for="t in templates"
        :key="t.id"
        class="tmpl-tab"
        :class="{ active: editingId === t.id }"
        @click="editingId = t.id"
      >
        {{ t.name }}
        <span v-if="activeId === t.id" class="active-dot" />
      </div>
      <el-button size="small" text @click="handleNewTemplate">＋ 新建模板</el-button>
    </div>

    <template v-if="currentTmpl">
      <!-- 全局过滤条件 -->
      <el-card shadow="never" class="block">
        <template #header><b>全局过滤条件</b></template>

        <div class="filter-row">
          <span class="filter-label">排除强赎状态：</span>
          <el-checkbox-group v-model="currentTmpl.excluded_redeem_icons">
            <el-checkbox value="R">已公告强赎</el-checkbox>
            <el-checkbox value="O">公告要强赎</el-checkbox>
            <el-checkbox value="B">已满足强赎条件</el-checkbox>
            <el-checkbox value="G">公告不强赎</el-checkbox>
          </el-checkbox-group>
        </div>

        <div class="filter-row">
          <span class="filter-label">距强赎触发安全天数：</span>
          <el-input-number v-model="currentTmpl.redeem_safe_days" :min="-1" :max="30" size="small" @change="dirty = true" />
          <span class="unit">天（-1 = 不限制）</span>
        </div>

        <div class="filter-row">
          <span class="filter-label">上市最少天数：</span>
          <el-input-number v-model="currentTmpl.min_listing_days" :min="0" :max="60" size="small" @change="dirty = true" />
          <span class="unit">天（0 = 不限制）</span>
        </div>

        <div class="filter-row">
          <span class="filter-label">全局排除代码：</span>
          <div class="excluded-codes">
            <div class="excluded-input">
              <el-input
                v-model="excludedBondInput"
                placeholder="例如 110081 或 110081.SH"
                size="small"
                style="width: 220px"
                @keyup.enter="handleAddExcludedCode"
              />
              <el-button size="small" @click="handleAddExcludedCode">添加</el-button>
            </div>
            <div class="excluded-tags">
              <span v-if="!currentTmpl.excluded_bond_codes?.length" class="empty-tip">未设置排除代码</span>
              <el-tag
                v-for="item in currentTmpl.excluded_bond_codes"
                :key="typeof item === 'string' ? item : item.code"
                closable
                type="danger"
                @close="handleRemoveExcludedCode(typeof item === 'string' ? item : item.code)"
              >
                {{ typeof item === 'string' ? item : (item.name ? `${item.code} ${item.name}` : item.code) }}
              </el-tag>
            </div>
          </div>
        </div>
      </el-card>

      <!-- 两列因子编辑 -->
      <div class="two-col">
        <!-- 排除因子 -->
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <b>排除因子</b>
              <el-button size="small" text @click="addExclusionRule">＋ 添加</el-button>
            </div>
          </template>
          <div v-if="!currentTmpl.exclusion_rules?.length" class="empty-tip">暂无排除规则</div>
          <div v-for="(rule, i) in currentTmpl.exclusion_rules" :key="i" class="rule-row">
            <el-switch v-model="rule.enabled" size="small" @change="dirty = true" />
            <el-select
              v-model="rule.field"
              style="width: 120px"
              size="small"
              @change="onExclusionFieldChange(rule)"
            >
              <el-option v-for="f in catalog" :key="f.field" :label="f.label" :value="f.field" />
            </el-select>
            <el-select v-model="rule.op" style="width: 100px" size="small" @change="dirty = true">
              <el-option label="小于 <" value="lt" />
              <el-option label="大于 >" value="gt" />
            </el-select>
            <el-input-number v-model="rule.threshold" :controls="false" style="width: 90px" size="small" @change="dirty = true" />
            <span class="unit">{{ factorMeta(rule.field).unit }}</span>
            <el-button text type="danger" size="small" @click="removeExclusionRule(i)">删除</el-button>
          </div>
        </el-card>

        <!-- 打分因子 -->
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <b>打分因子</b>
              <el-button size="small" text @click="addScoreFactor">＋ 添加</el-button>
            </div>
          </template>
          <div v-if="!currentTmpl.strategy_factors?.length" class="empty-tip">暂无打分因子</div>
          <div v-for="(factor, i) in currentTmpl.strategy_factors" :key="i" class="rule-row">
            <el-switch v-model="factor.enabled" size="small" @change="dirty = true" />
            <el-select
              v-model="factor.field"
              style="width: 120px"
              size="small"
              @change="onScoreFieldChange(factor)"
            >
              <el-option v-for="f in catalog" :key="f.field" :label="f.label" :value="f.field" />
            </el-select>
            <el-select v-model="factor.ascending" style="width: 100px" size="small" @change="dirty = true">
              <el-option label="越小越好" :value="true" />
              <el-option label="越大越好" :value="false" />
            </el-select>
            <span class="unit">权重</span>
            <el-input-number v-model="factor.weight" :step="0.5" :min="0.1" style="width: 80px" size="small" @change="dirty = true" />
            <el-button text type="danger" size="small" @click="removeScoreFactor(i)">删除</el-button>
          </div>
        </el-card>
      </div>

      <!-- 底部工具栏 -->
      <el-card shadow="never" class="block">
        <div class="bottom-toolbar">
          <div class="left">
            <span class="filter-label">持仓数量：</span>
            <el-input-number v-model="currentTmpl.target_count" :min="1" :max="50" size="small" @change="dirty = true" />
            <span class="filter-label">持仓容差：</span>
            <el-input-number v-model="currentTmpl.hold_tolerance" :min="0" :max="20" size="small" @change="dirty = true" />
            <span class="unit">名（0=严格按目标，5=跌出 target+5 才卖）</span>
          </div>
          <div class="right">
            <el-button size="small" @click="handleDuplicate">复制</el-button>
            <el-button size="small" type="danger" text @click="handleDelete(editingId)">删除</el-button>
            <el-button
              v-if="activeId !== editingId"
              size="small"
              @click="handleSetActive"
            >设为当前策略</el-button>
            <el-tag v-else type="success" size="small">当前策略</el-tag>
            <el-button
              size="small"
              type="success"
              :loading="previewing"
              @click="handlePreview"
            >执行筛选</el-button>
            <el-button
              type="primary"
              size="small"
              :loading="saving"
              :disabled="!dirty"
              @click="handleSave"
            >{{ dirty ? '保存配置 *' : '已保存' }}</el-button>
          </div>
        </div>
      </el-card>

      <!-- 预览结果 -->
      <el-card v-if="previewing || previewResult" shadow="never" class="block">
        <template #header>
          <b v-if="previewResult">
            筛选结果 — <span class="success-text">{{ previewResult.top_n }}</span> 只入选
            <span v-if="previewResult.keep_n > previewResult.top_n" class="info-text">
              / {{ previewResult.keep_n - previewResult.top_n }} 只在容差范围
            </span>
            / {{ previewResult.total_filtered }} 只通过排除 / {{ previewResult.total_all }} 只全量
          </b>
          <b v-else>正在筛选...</b>
        </template>
        <div v-if="previewing" v-loading="true" style="height: 100px" />
        <el-table
          v-if="previewResult"
          :data="previewResult.rows"
          stripe
          size="small"
          max-height="50vh"
          :row-class-name="({ row }) => (row.selected ? 'bond-row-selected' : '')"
        >
          <el-table-column prop="rank" label="排名" width="60" align="center" />
          <el-table-column prop="code" label="代码" width="100" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column prop="price" label="价格" width="80" align="right" />
          <el-table-column prop="dblow" label="双低" width="70" align="right" />
          <el-table-column prop="premium_rt" label="溢价率" width="85" align="right" />
          <el-table-column prop="curr_iss_amt" label="规模(亿)" width="85" align="right" />
          <el-table-column prop="convert_value" label="转股价值" width="85" align="right" />
          <el-table-column prop="year_left" label="剩余年限" width="80" align="right" />
          <el-table-column prop="pb" label="市净率" width="75" align="right" />
          <el-table-column prop="redeem" label="强赎" width="110" />
          <el-table-column prop="total_score" label="得分" width="75" align="right" />
          <el-table-column label="入选" width="90" align="center" fixed="right">
            <template #default="{ row }">
              <el-tag v-if="row.selected" type="success" size="small">✓ 入选</el-tag>
              <el-tag v-else-if="row.holdable" type="info" size="small">容差保留</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.tmpl-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.tmpl-tab {
  padding: 5px 14px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid #dcdfe6;
  background: #fff;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tmpl-tab.active {
  border-color: #409eff;
  color: #409eff;
  background: #ecf5ff;
}

.active-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #67c23a;
}

.block {
  margin-bottom: 16px;
}

.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.filter-label {
  color: #606266;
  font-size: 13px;
}

.unit {
  color: #909399;
  font-size: 12px;
}

.rule-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.rule-label {
  width: 80px;
  font-size: 13px;
  color: #303133;
  flex-shrink: 0;
}

.excluded-codes {
  flex: 1;
}

.excluded-input {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.excluded-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.empty-tip {
  color: #909399;
  font-size: 13px;
}

.bottom-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.bottom-toolbar .left,
.bottom-toolbar .right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.success-text {
  color: #67c23a;
}

.info-text {
  color: #409eff;
}
</style>
