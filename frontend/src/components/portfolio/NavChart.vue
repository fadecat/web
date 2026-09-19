<script setup>
// 区域④ 净值曲线 / 区域② 组合回撤 —— 同一张图，两种纵轴语义：
// - `return`  : 收益率 %，**以区间起点归一为 0%**(规格 §二-④, 写死不随再平衡变)
// - `drawdown`: 相对历史峰值的回撤 %(组合回撤 Tab)
//
// 配色遵循 A 股习惯: 本组合**红**、对比基准**蓝**; 涨跌不在此图体现(曲线本身连续)。
// ⚠ 对比基准若是指数, 它是价格指数(不含股息), 与含分红的组合口径不同 → 图例上带提示。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';

import { useThemeStore } from '../../stores/theme';
import { chartTheme } from '../../utils/chartTheme';

const props = defineProps({
  // buildChartData() 的结果: { dates, portfolio, benchmark }
  data: { type: Object, default: null },
  mode: { type: String, default: 'return' }, // return | drawdown
  height: { type: Number, default: 320 },
});

const chartRef = ref(null);
let chart = null;
const theme = useThemeStore();

const toDrawdown = (returns) => {
  let peak = -Infinity;
  return (returns ?? []).map((r) => {
    if (r === null || r === undefined) return null;
    const nav = 1 + r / 100;
    peak = Math.max(peak, nav);
    return Number(((nav / peak - 1) * 100).toFixed(4));
  });
};

const seriesOf = (values) => (props.mode === 'drawdown' ? toDrawdown(values) : values);

const buildOption = () => {
  const t = chartTheme(theme.isDark);
  if (!props.data?.dates?.length) return null;

  const primary = seriesOf(props.data.portfolio);
  const series = [
    {
      name: '本组合',
      type: 'line',
      data: primary,
      showSymbol: false,
      lineStyle: { width: 2, color: t.red },
      itemStyle: { color: t.red },
      connectNulls: true,
    },
  ];
  if (props.data.benchmark) {
    series.push({
      name: props.data.benchmark.name,
      type: 'line',
      data: seriesOf(props.data.benchmark.values),
      showSymbol: false,
      lineStyle: { width: 1.5, color: t.blue },
      itemStyle: { color: t.blue },
      connectNulls: true,
    });
  }

  const axisText = { color: t.axisLabel, fontSize: 11 };
  return {
    tooltip: {
      trigger: 'axis',
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      textStyle: { color: t.tooltipText, fontSize: 12 },
      valueFormatter: (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(2)}%`),
    },
    legend: {
      bottom: 0,
      textStyle: { color: t.labelLegend, fontSize: 12 },
      data: series.map((s) => s.name),
    },
    grid: { left: 56, right: 20, top: 16, bottom: 44 },
    xAxis: {
      type: 'category',
      data: props.data.dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: t.axisLine } },
      axisLabel: { ...axisText, formatter: (value) => String(value).slice(0, 4) },
    },
    yAxis: {
      type: 'value',
      axisLabel: { ...axisText, formatter: '{value}%' },
      splitLine: { lineStyle: { color: t.splitLine } },
    },
    series,
  };
};

const render = () => {
  if (!chartRef.value) return;
  if (!chart) chart = echarts.init(chartRef.value);
  const option = buildOption();
  if (option) chart.setOption(option, true);
  else chart.clear();
};

watch(() => [props.data, props.mode], () => nextTick(render), { deep: true });
watch(() => theme.isDark, () => nextTick(render));

const resize = () => chart && chart.resize();

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

const empty = computed(() => !props.data?.dates?.length);
</script>

<template>
  <div class="nav-chart">
    <el-empty v-if="empty" description="暂无曲线数据" :image-size="60" />
    <div v-else ref="chartRef" :style="{ width: '100%', height: `${height}px` }" />
  </div>
</template>

<style scoped>
.nav-chart {
  width: 100%;
}
</style>
