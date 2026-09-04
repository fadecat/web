<script setup>
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';

use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
]);

const props = defineProps({
  data: {
    type: Object,
    default: null,
  },
});

const chartRef = ref(null);
let chart = null;

function buildStrengthAreaData(series, predicate) {
  const result = [];
  for (let i = 0; i < series.length; i++) {
    const v = series[i];
    result.push(predicate(v) ? v : null);
  }
  return result;
}

function buildFlatReference(dates, value) {
  return dates.map(() => value);
}

function buildDefaultZoomRange(length) {
  if (length <= 250) return { start: 0, end: 100 };
  return { start: Math.max(0, 100 - Math.floor(250 / length * 100)), end: 100 };
}

function buildOption() {
  const data = props.data;
  if (!data) {
    return {
      title: {
        text: '暂无图表数据',
        left: 'center',
        top: 'middle',
        textStyle: { color: '#6b7280', fontSize: 18, fontWeight: 600 },
      },
      xAxis: [{ show: false }],
      yAxis: [{ show: false }],
      series: [],
    };
  }

  const { meta, series, summary } = data;
  const leftLabel = meta.left_symbol;
  const rightLabel = meta.right_symbol;
  const masterDates = series.dates;
  const positiveArea = buildStrengthAreaData(series.spread, (v) => v > 0);
  const negativeArea = buildStrengthAreaData(series.spread, (v) => v < 0);
  const globalP90 = buildFlatReference(masterDates, summary.global_p90);
  const globalP10 = buildFlatReference(masterDates, summary.global_p10);
  const zoomRange = buildDefaultZoomRange(masterDates.length);

  return {
    animation: false,
    title: [
      {
        text: '收益差值(spread) %',
        top: '4%',
        left: '7%',
        textStyle: { fontSize: 13, fontWeight: 700, color: '#475467' },
      },
      {
        text: `左 ${leftLabel}  spread ${summary.latest_spread}  MA ${summary.latest_ma}`,
        top: '11%',
        left: '7%',
        textStyle: { fontSize: 12, fontWeight: 600, color: '#475467' },
      },
    ],
    legend: {
      top: 16,
      left: 'center',
      itemWidth: 12,
      itemHeight: 12,
      textStyle: { color: '#475467', fontSize: 12, fontWeight: 600 },
      data: [
        'spread>0(左强)',
        'spread<0(右强)',
        '收益价差',
        'MA20',
        '全局P90',
        '全局P10',
      ],
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        snap: true,
        label: { backgroundColor: '#d1d5db', color: '#111827' },
      },
      backgroundColor: 'rgba(255, 251, 245, 0.94)',
      borderColor: 'rgba(148, 163, 184, 0.35)',
      borderWidth: 1,
      textStyle: { color: '#111827' },
      extraCssText:
        'box-shadow: 0 18px 36px rgba(15, 23, 42, 0.16); border-radius: 14px; padding: 10px 12px;',
    },
    axisPointer: { link: [{ xAxisIndex: [0] }] },
    dataZoom: [
      {
        type: 'slider',
        xAxisIndex: [0],
        bottom: 14,
        height: 18,
        start: zoomRange.start,
        end: zoomRange.end,
        borderColor: 'rgba(148, 163, 184, 0.32)',
        fillerColor: 'rgba(39, 76, 119, 0.12)',
      },
      {
        type: 'inside',
        xAxisIndex: [0],
        start: zoomRange.start,
        end: zoomRange.end,
      },
    ],
    grid: [{ top: '20%', height: '65%', left: '7%', right: '5%' }],
    xAxis: [
      {
        type: 'category',
        gridIndex: 0,
        data: masterDates,
        boundaryGap: false,
        axisLabel: {
          color: '#667085',
          hideOverlap: true,
          showMinLabel: true,
          showMaxLabel: true,
          fontSize: 11,
          margin: 10,
        },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#cbd5e1' } },
      },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        name: 'spread(%)',
        nameLocation: 'middle',
        nameGap: 38,
        scale: true,
        axisLabel: { color: '#667085' },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.18)' } },
      },
    ],
    series: [
      {
        name: 'spread>0(左强)',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: positiveArea,
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { color: 'rgba(214, 67, 69, 0.22)' },
        tooltip: { show: false },
        z: 1,
      },
      {
        name: 'spread<0(右强)',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: negativeArea,
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { color: 'rgba(29, 141, 87, 0.22)' },
        tooltip: { show: false },
        z: 1,
      },
      {
        name: '收益价差',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: series.spread,
        symbol: 'none',
        lineStyle: { width: 1.8, color: '#1f2937' },
        z: 4,
      },
      {
        name: 'MA20',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: series.ma,
        symbol: 'none',
        lineStyle: { width: 1.6, type: 'dashed', color: '#f59e0b' },
        z: 4,
      },
      {
        name: '全局P90',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: globalP90,
        symbol: 'none',
        lineStyle: { width: 1.2, type: 'dashed', color: '#dc2626' },
        z: 3,
      },
      {
        name: '全局P10',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: globalP10,
        symbol: 'none',
        lineStyle: { width: 1.2, type: 'dashed', color: '#16a34a' },
        z: 3,
      },
    ],
  };
}

const render = () => {
  if (!chartRef.value) return;
  if (!chart) chart = init(chartRef.value);
  chart.setOption(buildOption(), true);
};

watch(() => props.data, () => nextTick(render), { deep: true });

onMounted(() => {
  nextTick(render);
  window.addEventListener('resize', resize);
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize);
  if (chart) {
    chart.dispose();
    chart = null;
  }
});

const resize = () => chart && chart.resize();
</script>

<template>
  <div ref="chartRef" style="width: 100%; height: 420px" />
</template>