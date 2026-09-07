<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';

use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  MarkLineComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
]);

const props = defineProps({
  dates: { type: Array, default: () => [] },
  values: { type: Array, default: () => [] },
  metricLabel: { type: String, default: 'PE' }, // 图例/tooltip 用的指标名
});

const chartRef = ref(null);
let chart = null;

const isMobile = () => window.innerWidth < 768;

/** 线性插值分位数: 输入已升序数组, p 取 0~100 */
function percentile(sorted, p) {
  if (!sorted.length) return null;
  const idx = (sorted.length - 1) * (p / 100);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

// 30/中位/70 三条参考线: 在"当前选定窗口内"计算, 所以切时间范围时参考线跟着变
// (蛋卷同理——3 年窗口看 3 年内的贵贱, 不是全历史)
const refLines = computed(() => {
  const valid = props.values.filter((v) => v != null && !Number.isNaN(v));
  if (!valid.length) return { p30: null, p50: null, p70: null };
  const sorted = [...valid].sort((a, b) => a - b);
  return {
    p30: percentile(sorted, 30),
    p50: percentile(sorted, 50),
    p70: percentile(sorted, 70),
  };
});

const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));

function buildMarkLine() {
  const { p30, p50, p70 } = refLines.value;
  const base = {
    symbol: 'none',
    // 标签贴右端显示具体数值, 与蛋卷"右侧标 30/中位/70 数值"一致
    label: {
      position: 'insideEndTop',
      fontSize: 10,
      fontWeight: 600,
      formatter: (p) => `${p.name} ${fmt(p.value)}`,
    },
  };
  const lines = [
    { yAxis: p30, name: '30分位', lineStyle: { color: '#16a34a', type: 'dashed', width: 1 } },
    { yAxis: p50, name: '中位值', lineStyle: { color: '#6b7280', type: 'dashed', width: 1 } },
    { yAxis: p70, name: '70分位', lineStyle: { color: '#dc2626', type: 'dashed', width: 1 } },
  ];
  return {
    ...base,
    data: lines.filter((l) => l.yAxis != null).map((l) => ({
      ...l,
      label: { ...base.label, color: l.lineStyle.color },
    })),
  };
}

function buildOption() {
  if (!props.dates.length) {
    return {
      title: {
        text: '暂无数据',
        left: 'center',
        top: 'middle',
        textStyle: { color: '#9ca3af', fontSize: 15, fontWeight: 600 },
      },
      xAxis: [{ show: false }],
      yAxis: [{ show: false }],
      series: [],
    };
  }

  const mobile = isMobile();
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: 'rgba(255, 255, 255, 0.96)',
      borderColor: 'rgba(148, 163, 184, 0.35)',
      borderWidth: 1,
      textStyle: { color: '#111827', fontSize: 12 },
      extraCssText: 'box-shadow: 0 8px 20px rgba(15,23,42,0.14); border-radius: 10px;',
      formatter: (params) => {
        if (!params?.length) return '';
        const p = params[0];
        return `${p.axisValue}<br/>${props.metricLabel} <b>${fmt(p.data)}</b>`;
      },
    },
    grid: {
      top: mobile ? 24 : 32,
      left: mobile ? 48 : 62,
      right: mobile ? 16 : 28,
      bottom: mobile ? 26 : 34,
    },
    xAxis: {
      type: 'category',
      data: props.dates,
      boundaryGap: false,
      axisLabel: {
        color: '#9ca3af',
        fontSize: mobile ? 9 : 11,
        hideOverlap: true,
        showMinLabel: true,
        showMaxLabel: true,
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e4e7ed' } },
    },
    yAxis: {
      type: 'value',
      scale: true, // 不强制从 0 起, 否则 PE 波动被压平看不见
      axisLabel: { color: '#9ca3af', fontSize: mobile ? 9 : 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.18)' } },
    },
    dataZoom: [
      {
        type: 'inside',
        // 手机端单指拖动只查值(十字光标跟手), 不平移窗口, 避免手指一碰画布窗口就滑走
        moveOnMouseMove: !mobile,
        zoomOnMouseWheel: true,
      },
    ],
    series: [
      {
        name: props.metricLabel,
        type: 'line',
        data: props.values,
        symbol: 'none',
        connectNulls: true, // 个别日期缺数据时不断线
        lineStyle: { width: 1.8, color: '#274c77' },
        areaStyle: { color: 'rgba(39, 76, 119, 0.08)' },
        markLine: buildMarkLine(),
      },
    ],
  };
}

const render = () => {
  if (!chartRef.value) return;
  if (!chart) chart = init(chartRef.value);
  chart.setOption(buildOption(), true);
};

// 跨断点(手机旋转/窗口缩放)时重建, 否则只 resize
let lastMobile = isMobile();
const onResize = () => {
  if (!chart) return;
  if (isMobile() !== lastMobile) {
    lastMobile = isMobile();
    render();
  } else {
    chart.resize();
  }
};

watch(() => [props.dates, props.values, props.metricLabel], () => nextTick(render), { deep: true });

onMounted(() => {
  nextTick(render);
  window.addEventListener('resize', onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});
</script>

<template>
  <div ref="chartRef" class="valuation-chart" />
</template>

<style scoped>
/* 纵向留给页面滚动, 横向归图表(tooltip 跟手), 避免手指被困在图表里滑不走页面 */
.valuation-chart {
  width: 100%;
  height: 380px;
  touch-action: pan-y;
}

@media (max-width: 767px) {
  .valuation-chart {
    height: 300px;
  }
}
</style>
