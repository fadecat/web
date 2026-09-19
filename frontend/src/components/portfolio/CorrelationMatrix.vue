<script setup>
// 区域⑥ 相关性矩阵(page-spec §二-⑥)。
//
// 三个容易搞错的点, 都在这里显式处理:
// 1. **计算区间必须显式标注**——它与回测区间的对齐方向不同(相关性向后顺延),
//    同一天回推 10 年两个模块给出不同答案, 所以区间由后端单独给, 前端原样显示;
// 2. **不随再平衡方式变化**(三种模式共用同一矩阵);
// 3. 对角线固定为 1 且底色最深。
import { computed } from 'vue';

import { buildCorrelationView } from '../../utils/backtestView.mjs';

const props = defineProps({
  correlation: { type: Object, default: null },
  assets: { type: Array, default: () => [] },
});

const view = computed(() => buildCorrelationView(props.correlation, props.assets));
</script>

<template>
  <div v-if="view" class="corr">
    <div class="corr-head">
      <span class="title">相关性</span>
      <!-- 「?」解释这个概念本身 —— 相关性最容易被误读成"涨跌比例"(他涨 1%, 我涨 0.43%),
           所以文案的重点不是定义, 而是**体感**(0.4 是什么感觉)和**边界**(不能预测单日)。 -->
      <el-tooltip placement="right" :show-after="120" popper-class="corr-help-popper">
        <template #content>
          <div class="corr-help">
            <p class="lead">
              相关性 = 两只基金<b>「步调是否一致」</b>。越高越同涨同跌，越低越各走各的。
            </p>
            <div class="rows">
              <span class="num">1.00</span><span>完全同步（对角线那格：自己和自己）</span>
              <span class="num">0.9 以上</span><span>形影不离 —— 买它俩约等于买同一只</span>
              <span class="num">0.4 左右</span><span>有点默契 —— 一只涨的日子，另一只约八成也在涨</span>
              <span class="num">0.1 以下</span><span>基本没关系 —— 一只涨时另一只涨跌各半，跟抛硬币差不多</span>
              <span class="num">负值</span><span>反向 —— 一个涨，另一个往往在跌</span>
            </div>
            <p class="use">
              <b>为什么关心它：</b>相关性低 = 不会一起亏。一只在回撤时另一只可能还在涨，
              组合整体就被摊平了 —— 这就是「分散风险」的全部意义。
            </p>
            <p class="warn">
              ⚠️ 只说明<b>长期步调</b>，<b>不能用来猜某一天谁涨谁跌</b>。
              （相关系数 0.43 的那一对，在其中一只上涨的日子里，另一只仍有约两成是跌的。）
            </p>
          </div>
        </template>
        <span class="hint-icon">?</span>
      </el-tooltip>
      <span class="range">计算区间：{{ view.start }} ~ {{ view.end }}</span>
    </div>

    <div class="corr-scroll">
      <table class="corr-table">
        <thead>
          <tr>
            <th class="corner" />
            <th v-for="col in view.columns" :key="col.symbol" class="col-head">{{ col.index }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in view.rows" :key="row.symbol">
            <th class="row-head">
              <span class="idx">{{ row.index }}</span>
              <span class="names">
                <span class="name">{{ row.name }}</span>
                <span class="code">{{ row.symbol }}</span>
              </span>
            </th>
            <td
              v-for="(cell, i) in row.cells"
              :key="i"
              class="value"
              :class="{ 'is-strong': cell.strong, 'is-diagonal': cell.diagonal }"
              :style="{ background: cell.background }"
            >
              <span class="corr-value">{{ cell.text }}</span>
              <!-- 样本数 n(ambiguity-audit D8): 标的不全同期时只看系数会误判可信度 -->
              <span v-if="cell.sampleText" class="corr-n">{{ cell.sampleText }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="corr-legend">
      <span class="label">正相关</span>
      <span class="band" />
      <span class="label">负相关</span>
    </div>
  </div>
</template>

<style scoped>
.corr {
  padding: 4px 0;
}

.corr-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}

.title {
  font-size: 14px;
  font-weight: 600;
}

/* 「?」帮助入口: 与页面其它 hint-icon 同一视觉语言 */
.hint-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid var(--el-border-color);
  font-size: 10px;
  line-height: 1;
  color: var(--el-text-color-secondary);
  cursor: help;
  user-select: none;
}

.corr-head .hint-icon {
  align-self: center;
  margin-left: -4px;
}

.range {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.corr-scroll {
  overflow-x: auto;
}

.corr-table {
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}

.corr-table th,
.corr-table td {
  border: 1px solid var(--el-border-color-lighter);
}

.corner {
  min-width: 120px;
}

.col-head {
  width: 56px;
  font-weight: 500;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 4px 0;
}

.row-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  text-align: left;
  font-weight: 400;
}

.idx {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  min-width: 12px;
}

.names {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.name {
  font-size: 13px;
}

.code {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}

.value {
  text-align: center;
  font-size: 13px;
  padding: 4px 0;
  /* 底色由 correlationCellStyle 按相关系数叠加(透明→深红/深蓝) */
}

.corr-value {
  display: block;
  line-height: 1.25;
}

/* 样本数 n: 小字灰, 跟随单元格文字颜色(强相关时白字) */
.corr-n {
  display: block;
  font-size: 10px;
  line-height: 1.1;
  opacity: 0.7;
}

/* 底色够深时转白字, 保证可读 */
.value.is-strong {
  color: #fff;
}

.value.is-diagonal {
  font-weight: 600;
}

.corr-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.corr-legend .label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.corr-legend .band {
  flex: 1 1 auto;
  max-width: 320px;
  height: 8px;
  border-radius: 4px;
  background: linear-gradient(to right, rgba(220, 38, 38, 0.85), transparent, rgba(37, 99, 235, 0.85));
}
</style>

<style>
/* 帮助内容挂载在 body 上, scoped 样式够不着 → 用 popper-class 单独定 */
.corr-help-popper {
  max-width: 390px;
}

.corr-help-popper .corr-help {
  font-size: 12px;
  line-height: 1.7;
}

.corr-help-popper .corr-help p {
  margin: 0;
}

.corr-help-popper .lead {
  margin-bottom: 8px;
}

.corr-help-popper .rows {
  display: grid;
  grid-template-columns: 66px 1fr;
  gap: 3px 10px;
}

.corr-help-popper .num {
  text-align: right;
  color: var(--el-text-color-secondary);
}

.corr-help-popper .use {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.corr-help-popper .warn {
  margin-top: 8px;
  color: var(--el-color-warning);
}
</style>
