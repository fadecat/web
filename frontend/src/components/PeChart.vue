<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { emitHover, onHover, findClosestIndex } from '../utils/chartLink';

use([CanvasRenderer, LineChart, GridComponent, MarkLineComponent, TooltipComponent]);

const props = defineProps({
  // 两侧 PE 历史(全量, 组件内按日期范围裁剪): { left: [...], right: [...] }
  history: { type: Object, default: () => ({ left: [], right: [] }) },
  startDate: { type: String, default: '' },
  endDate: { type: String, default: '' },
  leftName: { type: String, default: '' },
  rightName: { type: String, default: '' },
});

// 上下两个独立 ECharts 实例: tooltip 是实例级的, 同实例两个 grid 的 showTip
// 会互相覆盖(联动时只有一张图有数值框); 拆开实例各持 tooltip, 联动时同时显示
const topRef = ref(null);
const bottomRef = ref(null);
let topChart = null;
let bottomChart = null;

// 三图联动: 悬停时按时间戳在 spread 与 PE 图间同步十字轴(详见 utils/chartLink.js)
let topTs = []; // 各图当前数据的时间戳缓存(与 series data 同序)
let bottomTs = [];
const syncing = new Set(); // 防循环: 正在接收联动 dispatch 的实例集合
let offHover = null; // 取消联动订阅

const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));
const pct5y = (row) => {
  const v = row?.pe_percentile?.['5y'];
  return v == null ? null : `${Number(v).toFixed(1)}%`;
};

// 裁剪 + 升序, 返回原始行列表
function crop(rows, start, end) {
  return (rows || [])
    .filter((r) => r?.trade_date && (!start || r.trade_date >= start) && (!end || r.trade_date <= end))
    .sort((a, b) => (a.trade_date < b.trade_date ? -1 : 1));
}

function buildOption(rows, color, name, { showX }) {
  if (!rows.length) {
    return {
      title: { text: `${name}（无数据）`, left: '8%', top: 'middle', textStyle: { color: '#9ca3af', fontSize: 13 } },
      xAxis: [{ show: false }],
      yAxis: [{ show: false }],
      series: [],
    };
  }

  const mobile = window.innerWidth < 768;
  const last = rows[rows.length - 1];
  const titleText = `${name}  最新 ${last.trade_date} · PE ${fmt(last.pe)} · 5年分位 ${pct5y(last)}`;
  const axisMin = props.startDate || undefined;
  const axisMax = props.endDate || undefined;
  const timeLabel = (val) => {
    const d = new Date(val);
    const m = d.getMonth() + 1;
    if (m === 1) return `${d.getFullYear()}`;
    return `${String(m).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  };

  return {
    animation: false,
    grid: { left: '8%', right: '5%', top: mobile ? '16%' : '15%', height: mobile ? '74%' : '76%' },
    title: {
      text: titleText,
      left: '8%',
      top: '2%',
      textStyle: { fontSize: mobile ? 11 : 12, fontWeight: 600, color: '#475467' },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255, 251, 245, 0.94)',
      borderColor: 'rgba(148, 163, 184, 0.35)',
      textStyle: { color: '#111827', fontSize: 11 },
      formatter(params) {
        const p = Array.isArray(params) ? params[0] : params;
        const d = p?.data;
        return `${d?.date || ''}<br/>PE ${fmt(d?.value?.[1] ?? d?.value)}<br/>5年分位 ${d?.pct5y || '—'}`;
      },
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      min: axisMin,
      max: axisMax,
      show: showX, // 上图隐藏 x 轴(与下图共享时间范围), 下图显示
      axisLabel: { fontSize: 10, color: '#6b7280', hideOverlap: true, formatter: timeLabel },
    },
    yAxis: {
      type: 'value',
      name: 'PE',
      nameTextStyle: { fontSize: 10, color: '#6b7280' },
      scale: true,
      axisLabel: { fontSize: 10 },
    },
    series: [
      {
        type: 'line',
        showSymbol: false,
        lineStyle: { width: 1.5, color },
        itemStyle: { color },
        // [时间戳, PE] 数据对: time 轴按时间对齐, 联动不因序列长度不同而错位
        data: rows.map((r) => ({
          value: [new Date(r.trade_date + 'T00:00:00').getTime(), r.pe],
          pct5y: pct5y(r),
          date: r.trade_date,
        })),
      },
    ],
  };
}

// 给实例绑定联动: 悬停上报时间戳, 移出上报 null
function bindLink(chart) {
  chart.on('updateAxisPointer', (e) => {
    if (syncing.has(chart)) return;
    const axis = (e?.axesInfo || []).find((a) => a.axisDim === 'x');
    if (axis && typeof axis.value === 'number') emitHover(axis.value);
  });
  chart.getZr().on('globalout', () => emitHover(null));
}

function render() {
  if (!topRef.value || !bottomRef.value) return;
  if (!topChart) {
    topChart = init(topRef.value);
    bindLink(topChart);
  }
  if (!bottomChart) {
    bottomChart = init(bottomRef.value);
    bindLink(bottomChart);
  }

  const left = crop(props.history.left, props.startDate, props.endDate);
  const right = crop(props.history.right, props.startDate, props.endDate);
  topTs = left.map((r) => new Date(r.trade_date + 'T00:00:00').getTime());
  bottomTs = right.map((r) => new Date(r.trade_date + 'T00:00:00').getTime());

  topChart.setOption(buildOption(left, '#dc2626', props.leftName, { showX: false }), true);
  bottomChart.setOption(buildOption(right, '#16a34a', props.rightName, { showX: true }), true);
}

// 收到外部联动时间戳(来自 spread 图或另一张 PE 图): 各自定位最近 index 弹 tooltip
function applyExternalHover(ts) {
  for (const [chart, tsArr] of [
    [topChart, topTs],
    [bottomChart, bottomTs],
  ]) {
    if (!chart) continue;
    syncing.add(chart);
    try {
      if (ts == null) {
        chart.dispatchAction({ type: 'hideTip' });
        continue;
      }
      const idx = findClosestIndex(tsArr, ts);
      if (idx >= 0) chart.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: idx });
    } finally {
      syncing.delete(chart);
    }
  }
}

function resize() {
  topChart?.resize();
  bottomChart?.resize();
}

onMounted(() => {
  render();
  offHover = onHover(applyExternalHover);
  window.addEventListener('resize', resize);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize);
  if (offHover) offHover();
  for (const c of [topChart, bottomChart]) {
    c?.dispose();
  }
  topChart = null;
  bottomChart = null;
});

watch(
  () => [props.history, props.startDate, props.endDate, props.leftName, props.rightName],
  () => render(),
  { deep: true },
);
</script>

<template>
  <div class="pe-chart">
    <div ref="topRef" class="pe-half"></div>
    <div ref="bottomRef" class="pe-half"></div>
  </div>
</template>

<style scoped>
.pe-half {
  width: 100%;
  height: 220px;
}
@media (max-width: 767px) {
  .pe-half {
    height: 180px;
  }
}
</style>
