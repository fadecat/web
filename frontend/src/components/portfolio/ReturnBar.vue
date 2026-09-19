<script setup>
// 区域① 收益条(page-spec §二-①): 一行七格, 首格放大, 标签在下。
//
// ⚠ 七格由**同一条净值序列**(从建仓日 T0 等权建仓、不平衡持有至今)导出 —— 不给任何
//    单个区间重新建仓。这是本区最关键的口径约束, 数值全部由后端 `windows` 给出,
//    前端只负责显示与配色(涨红跌绿)。
import { computed } from 'vue';

import { buildReturnCells } from '../../utils/backtestView.mjs';

const props = defineProps({
  // 后端返回的 result.windows(七格字典); 不可回测时为 null
  windows: { type: Object, default: null },
});

const cells = computed(() => buildReturnCells(props.windows));
</script>

<template>
  <div class="return-bar">
    <el-tooltip
      v-for="cell in cells"
      :key="cell.key"
      :content="cell.hint || `${cell.label}：${cell.text}`"
      placement="top"
      :disabled="!cell.hint"
    >
      <div class="cell" :class="{ 'is-featured': cell.featured }">
        <div class="value" :class="cell.className">{{ cell.text }}</div>
        <div class="label">
          {{ cell.label }}
          <span v-if="cell.composite" class="mark">ⓘ</span>
        </div>
      </div>
    </el-tooltip>
  </div>
</template>

<style scoped>
.return-bar {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  overflow-x: auto;
}

.cell {
  flex: 1 1 0;
  min-width: 76px;
  text-align: center;
  padding: 2px 4px;
}

.value {
  font-size: 18px;   /* page-spec §五: 收益条其余格 ~18px */
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 首格放大(page-spec §五: ~34px, 约为其余 2 倍) */
.is-featured .value {
  font-size: 34px;
}

.label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

.mark {
  font-size: 11px;
  opacity: 0.7;
}
</style>
