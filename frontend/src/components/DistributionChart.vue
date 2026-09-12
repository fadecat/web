<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import * as echarts from 'echarts';
import { useThemeStore } from '../stores/theme';
import { chartTheme } from '../utils/chartTheme';

const props = defineProps({
  rows: {
    type: Array,
    default: () => [],
  },
  field: {
    type: String,
    default: 'dblow',
  },
  title: {
    type: String,
    default: '',
  },
});

const chartRef = ref(null);
let chart = null;
// 深浅色切换时重新 setOption(ECharts 无响应式主题, 见 utils/chartTheme.mjs)
const theme = useThemeStore();

const buildOption = () => {
  const t = chartTheme(theme.isDark);
  const values = props.rows
    .map((r) => Number(r[props.field]))
    .filter((v) => !Number.isNaN(v));

  if (!values.length) return null;

  const min = Math.floor(Math.min(...values));
  const max = Math.ceil(Math.max(...values));
  const span = max - min;
  const bucketCount = Math.min(20, Math.max(8, Math.round(span / 5)));
  const width = span / bucketCount;

  const buckets = new Array(bucketCount).fill(0);
  values.forEach((v) => {
    let idx = Math.floor((v - min) / width);
    if (idx >= bucketCount) idx = bucketCount - 1;
    buckets[idx] += 1;
  });

  const categories = buckets.map((_, i) =>
    `${(min + i * width).toFixed(0)}-${(min + (i + 1) * width).toFixed(0)}`,
  );

  return {
    title: {
      text: props.title,
      left: 'center',
      textStyle: { fontSize: 14, color: t.labelStrong },
    },
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 50, bottom: 40 },
    xAxis: {
      type: 'category',
      data: categories,
      axisLabel: { rotate: 45, fontSize: 10 },
    },
    yAxis: { type: 'value', name: '数量' },
    series: [
      {
        type: 'bar',
        data: buckets,
        itemStyle: { color: t.bar },
        barMaxWidth: 30,
      },
    ],
  };
};

const render = () => {
  if (!chartRef.value) return;
  if (!chart) {
    chart = echarts.init(chartRef.value);
  }
  const option = buildOption();
  if (option) {
    chart.setOption(option);
  }
};

watch(
  () => [props.rows, props.field],
  () => nextTick(render),
  { deep: true },
);

// 深浅色切换 → 换 token 重绘(数据不变, 只有颜色变)
watch(() => theme.isDark, () => nextTick(render));

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
  <div ref="chartRef" style="width: 100%; height: 260px" />
</template>
