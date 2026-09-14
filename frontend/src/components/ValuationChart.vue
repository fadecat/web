<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { useThemeStore } from '../stores/theme';
import { chartTheme } from '../utils/chartTheme';
import { percentileDirection } from '../utils/valuation';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';

use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
]);

const props = defineProps({
  dates: { type: Array, default: () => [] },
  values: { type: Array, default: () => [] },
  metricLabel: { type: String, default: 'PE' }, // 图例/tooltip 用的指标名
  metricKey: { type: String, default: 'pe' },
  windowYears: { type: Number, default: 5 },
  // 可选对照序列(现用于指数收盘价): 不传时保持单轴单线行为
  comparisonValues: { type: Array, default: () => [] },
  comparisonLabel: { type: String, default: '' },
  primaryUnit: { type: String, default: '' }, // 主轴单位(如 百分点/倍)
  comparisonUnit: { type: String, default: '' }, // 对照轴单位(如收益率传 '%'; 指数点位留空裸显)
});

const chartRef = ref(null);
let chart = null;
// 深浅色切换时重新 setOption(ECharts 无响应式主题, 见 utils/chartTheme.mjs)
const theme = useThemeStore();

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
  const valid = props.values.filter((v) => v != null && Number.isFinite(Number(v))).map(Number);
  if (!valid.length) return { p30: null, p50: null, p70: null };
  const sorted = [...valid].sort((a, b) => a - b);
  return {
    p30: percentile(sorted, 30),
    p50: percentile(sorted, 50),
    p70: percentile(sorted, 70),
  };
});

const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));
const fmtUnit = (v, unit) => v == null || !Number.isFinite(Number(v)) ? '—' : `${fmt(v)}${unit || ''}`;

// 当前点在所选时间窗口中的历史分位。相同值取中间秩，最小/最大值分别为 0/100。
function valuePercentile(value, values, sortedValues = null) {
  if (value == null || !Number.isFinite(Number(value))) return null;
  const valid = sortedValues || values.filter((v) => v != null && Number.isFinite(Number(v))).map(Number).sort((a, b) => a - b);
  if (!valid.length) return null;
  const less = valid.filter((v) => v < Number(value)).length;
  const equal = valid.filter((v) => v === Number(value)).length;
  if (valid.length === 1) return 50;
  return ((less + (equal - 1) / 2) / (valid.length - 1)) * 100;
}

const sortedPrimaryValues = computed(() => props.values
  .filter((v) => v != null && Number.isFinite(Number(v)))
  .map(Number)
  .sort((a, b) => a - b));

// 对照序列(如十年期国债)是否有可用数据: 全部缺失时退回单轴单线
const hasComparison = computed(() =>
  props.comparisonValues.some((v) => v != null && Number.isFinite(Number(v))),
);

function buildMarkLine(t) {
  const { p30, p50, p70 } = refLines.value;
  const base = {
    symbol: 'none',
    // 标签贴绘图区左端内侧展开，避免与外侧 Y 轴文字重叠
    label: {
      position: 'start',
      align: 'left',
      offset: [6, 0],
      backgroundColor: t.tooltipBg,
      padding: [1, 3],
      borderRadius: 2,
      fontSize: 10,
      fontWeight: 600,
      formatter: (p) => `${p.name} ${fmt(p.value)}`,
    },
  };
  const inverse = percentileDirection(props.metricKey) === 'inverse';
  const lines = [
    { yAxis: p30, name: '30分位', lineStyle: { color: inverse ? t.green : t.red, type: 'dashed', width: 1 } },
    { yAxis: p50, name: '中位', lineStyle: { color: t.medianGray, type: 'dashed', width: 1 } },
    { yAxis: p70, name: '70分位', lineStyle: { color: inverse ? t.red : t.green, type: 'dashed', width: 1 } },
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
  const t = chartTheme(theme.isDark);
  if (!props.dates.length) {
    return {
      title: {
        text: '暂无数据',
        left: 'center',
        top: 'middle',
        textStyle: { color: t.emptyText, fontSize: 15, fontWeight: 600 },
      },
      xAxis: [{ show: false }],
      yAxis: [{ show: false }],
      series: [],
    };
  }

  const mobile = isMobile();
  const comparison = hasComparison.value;
  const yAxis = [
    {
      type: 'value',
      scale: true, // 不强制从 0 起, 否则 PE 波动被压平看不见
      name: comparison ? props.primaryUnit : '',
      nameTextStyle: { color: t.axisLabel, fontSize: mobile ? 9 : 11, align: 'right' },
      axisLabel: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: t.splitLine } },
    },
  ];
  if (comparison) {
    // 右轴: 对照序列(指数收盘价), 不画网格线避免与主轴混淆
    yAxis.push({
      type: 'value',
      scale: true,
      name: props.comparisonUnit, // 单位留空则不显示轴名(指数点位不带 %)
      nameTextStyle: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
      axisLabel: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
      axisLine: { show: false },
      splitLine: { show: false },
    });
  }
  // 配色对齐券商惯例(同 PeChart 用户定版): 估值指标=橘黄 / 指数收盘=蓝。
  // itemStyle 必须与线同色——图例图标与 tooltip 圆点取 itemStyle.color,
  // 不设时用调色板默认色, 会出现"圆点和线颜色不一样"的错位观感。
  const series = [
    {
      name: props.metricLabel,
      type: 'line',
      data: props.values,
      symbol: 'none',
      connectNulls: true, // 个别日期缺数据时不断线
      lineStyle: { width: 1.8, color: t.peOrange },
      itemStyle: { color: t.peOrange },
      areaStyle: { color: t.orangeArea },
      markLine: buildMarkLine(t),
    },
  ];
  if (comparison) {
    // 对照线: 指数收盘价=蓝色实线走右轴, 不带面积; 与主指标日期对不齐的个别缺口跨接连线, 保持视觉连续
    series.push({
      name: props.comparisonLabel,
      type: 'line',
      yAxisIndex: 1,
      data: props.comparisonValues,
      symbol: 'none',
      connectNulls: true, // 个别日期缺数据时不断线
      lineStyle: { width: 1.5, color: t.indexBlue },
      itemStyle: { color: t.indexBlue },
    });
  }
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: t.tooltipText, fontSize: 12 },
      extraCssText: 'box-shadow: 0 8px 20px rgba(15,23,42,0.14); border-radius: 10px;',
      formatter: (params) => {
        if (!params?.length) return '';
        const primary = params.find((p) => p.seriesName === props.metricLabel);
        const pointIndex = primary?.dataIndex ?? props.dates.indexOf(params[0].axisValue);
        const currentPct = pointIndex >= 0 ? valuePercentile(props.values[pointIndex], props.values, sortedPrimaryValues.value) : null;
        const rows = params.map((p) => {
          const isComp = p.seriesName === props.comparisonLabel;
          const unit = isComp ? props.comparisonUnit : props.primaryUnit;
          return `${p.marker}${p.seriesName} <b>${fmtUnit(p.data, unit)}</b>`;
        });
        const percentileLine = currentPct == null ? '' : `<br/>当日分位（近${props.windowYears}年窗口） <b>${currentPct.toFixed(1)}%</b>`;
        return `${params[0].axisValue}<br/>${rows.join('<br/>')}${percentileLine}`;
      },
    },
    legend:
      comparison && props.comparisonLabel
        ? {
            data: [props.metricLabel, props.comparisonLabel],
            top: mobile ? 2 : 6,
            left: 'center',
            // 图标用纯线段(同 PeChart), 不用默认"横线+圆点"——圆点易被误读成数据点
            icon: 'rect',
            itemWidth: 16,
            itemHeight: 3,
            itemGap: mobile ? 12 : 20,
            textStyle: { color: t.labelLegend, fontSize: mobile ? 10 : 12 },
          }
        : undefined,
    grid: {
      top: comparison ? (mobile ? 44 : 52) : mobile ? 24 : 32,
      left: mobile ? 48 : 62,
      right: comparison ? (mobile ? 44 : 60) : mobile ? 16 : 28,
      bottom: mobile ? 26 : 34,
    },
    xAxis: {
      type: 'category',
      data: props.dates,
      boundaryGap: false,
      axisLabel: {
        color: t.axisLabel,
        fontSize: mobile ? 9 : 11,
        hideOverlap: true,
        showMinLabel: true,
        showMaxLabel: true,
      },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: t.axisLine } },
    },
    yAxis,
    dataZoom: [
      {
        type: 'inside',
        // 手机端单指拖动只查值(十字光标跟手), 不平移窗口, 避免手指一碰画布窗口就滑走
        moveOnMouseMove: !mobile,
        zoomOnMouseWheel: true,
      },
    ],
    series,
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
  chart.resize();
  if (isMobile() !== lastMobile) {
    lastMobile = isMobile();
    render();
  }
};

watch(
  () => [
    props.dates,
    props.values,
    props.metricLabel,
    props.metricKey,
    props.windowYears,
    props.comparisonValues,
    props.comparisonLabel,
    props.primaryUnit,
    props.comparisonUnit,
  ],
  () => nextTick(render),
  { deep: true },
);

// 深浅色切换 → 换 token 重绘(数据不变, 只有颜色变)
watch(() => theme.isDark, () => nextTick(render));

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
