<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { useThemeStore } from '../stores/theme';
import { chartTheme } from '../utils/chartTheme';
import { quantile, percentileRank } from '../utils/cbMarket.mjs';
import { percentileTone, fmtPct } from '../utils/valuation';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components';

// 借鉴 CbMarketChart 的注册/生命周期/主题写法; 单图两序列:
// 利差主线(蓝实线) + 10Y 国债对照线(橙虚线), 零轴与 30/70 分位虚线。
use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  DataZoomComponent,
]);

const props = defineProps({
  // 已清洗、已裁剪的窗口内记录: [{ trade_date, avg_ytm, bond_yield, spread }]
  rows: { type: Array, default: () => [] },
});

const chartRef = ref(null);
let chart = null;
let resizeObserver = null;
// 深浅色切换时重新 setOption(ECharts 无响应式主题, 见 utils/chartTheme.mjs)
const theme = useThemeStore();

const dates = computed(() => props.rows.map((r) => r.trade_date));
const spreadData = computed(() => props.rows.map((r) => r.spread));
const bondData = computed(() => props.rows.map((r) => r.bond_yield));

// 30/70 分位参考线: 当前窗口内计算, 随 1y/3y/5y/all 切换重算。
// 利差为正向指标(高=债底便宜): p30 红 / p70 绿, 与 YTM 图同向。
const refLines = computed(() => ({
  p30: quantile(spreadData.value, 30),
  p70: quantile(spreadData.value, 70),
}));

const isMobile = () => {
  const w = chartRef.value?.clientWidth || window.innerWidth;
  return w < 768;
};

function buildOption() {
  const t = chartTheme(theme.isDark);
  if (!props.rows.length) {
    return {
      title: {
        text: '暂无数据',
        left: 'center',
        top: 'middle',
        textStyle: { color: t.emptyText, fontSize: 15, fontWeight: 600 },
      },
      xAxis: { show: false },
      yAxis: { show: false },
      series: [],
    };
  }

  const mobile = isMobile();
  const single = props.rows.length === 1;
  const symbol = single ? 'circle' : 'none';
  const symbolSize = single ? 8 : 0;
  const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));

  // 零轴(利差符号切换点: 负值=转债整体比国债贵)+ 30/70 分位虚线;
  // 标签贴绘图区左端内侧(与 CbMarketChart 分位线同款布局)。
  const pctLabel = (color) => ({
    position: 'start',
    align: 'left',
    offset: [6, 0],
    backgroundColor: t.tooltipBg,
    padding: [1, 3],
    borderRadius: 2,
    fontSize: 10,
    fontWeight: 600,
    color,
    formatter: (p) => `${p.name} ${fmt(p.value)}`,
  });
  const markLineData = [
    {
      yAxis: 0,
      name: '0',
      lineStyle: { color: t.medianGray, type: 'solid', width: 1 },
      label: { ...pctLabel(t.medianGray), formatter: () => '0' },
    },
  ];
  if (refLines.value.p30 != null) {
    markLineData.push({
      yAxis: refLines.value.p30,
      name: '30分位',
      lineStyle: { color: t.red, type: 'dashed', width: 1 },
      label: pctLabel(t.red),
    });
  }
  if (refLines.value.p70 != null) {
    markLineData.push({
      yAxis: refLines.value.p70,
      name: '70分位',
      lineStyle: { color: t.green, type: 'dashed', width: 1 },
      label: pctLabel(t.green),
    });
  }

  return {
    animation: false,
    legend: {
      top: mobile ? 2 : 6,
      left: 'center',
      selectedMode: false,
      itemWidth: 16,
      itemHeight: 9,
      itemGap: mobile ? 10 : 18,
      textStyle: { color: t.labelLegend, fontSize: mobile ? 10 : 12 },
      data: ['转债-国债利差（百分点）', '10Y国债收益率（%）'],
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', snap: true },
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      borderWidth: 1,
      textStyle: { color: t.tooltipText, fontSize: 12 },
      extraCssText: 'box-shadow: 0 8px 20px rgba(15,23,42,0.14); border-radius: 10px;',
      formatter: (params) => {
        if (!params || !params.length) return '';
        const lines = params.map((p) => {
          const base = `${p.marker}${p.seriesName} <b>${fmt(p.data)}%</b>`;
          if (!p.seriesName.startsWith('转债-国债利差')) return base;
          // 悬浮日利差在当前窗口内的回看分位(与主图 tooltip 同口径)
          const pctl = percentileRank(spreadData.value, p.data);
          if (pctl == null) return base;
          const tone = percentileTone(pctl, 'spread');
          const color = tone === 'green' ? t.green : tone === 'red' ? t.red : null;
          const text = `（${fmtPct(pctl)} 分位）`;
          return color ? `${base} <span style="color:${color}">${text}</span>` : `${base} ${text}`;
        });
        return `${params[0].axisValue}<br/>${lines.join('<br/>')}`;
      },
    },
    grid: { left: mobile ? 48 : 60, right: mobile ? 16 : 28, top: 34, bottom: mobile ? 34 : 40 },
    xAxis: {
      type: 'category',
      data: dates.value,
      boundaryGap: false,
      axisLabel: {
        color: t.axisLabel,
        fontSize: mobile ? 9 : 11,
        hideOverlap: true,
        showMinLabel: true,
        showMaxLabel: true,
      },
      axisLine: { lineStyle: { color: t.axisLine } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true, // 利差常为负, 不强制为正
      name: '百分点',
      nameTextStyle: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
      axisLabel: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: t.splitLine } },
    },
    dataZoom: [
      { type: 'inside' },
      {
        type: 'slider',
        bottom: mobile ? 4 : 8,
        height: 16,
        borderColor: t.zoomBorder,
        fillerColor: t.zoomFiller,
        handleSize: '120%',
        textStyle: { color: t.zoomText, fontSize: 10 },
      },
    ],
    series: [
      {
        name: '转债-国债利差（百分点）',
        type: 'line',
        data: spreadData.value,
        symbol,
        symbolSize,
        connectNulls: false,
        smooth: false,
        lineStyle: { color: t.blue, width: 1.8 },
        itemStyle: { color: t.blue },
        markLine: { symbol: 'none', silent: true, data: markLineData },
      },
      {
        name: '10Y国债收益率（%）',
        type: 'line',
        data: bondData.value,
        symbol: 'none',
        connectNulls: false,
        smooth: false,
        lineStyle: { color: t.peOrange, width: 1.2, type: 'dashed' },
        itemStyle: { color: t.peOrange },
      },
    ],
  };
}

const render = () => {
  if (!chartRef.value) return;
  if (!chart) chart = init(chartRef.value);
  chart.setOption(buildOption(), true);
};

let lastMobile = false;
const onResize = () => {
  if (!chart) return;
  if (isMobile() !== lastMobile) {
    lastMobile = isMobile();
    render();
    chart.resize();
  } else {
    chart.resize();
  }
};

watch(() => props.rows, () => nextTick(() => render()), { deep: true });
watch(() => theme.isDark, () => nextTick(() => render()));

onMounted(() => {
  nextTick(() => {
    if (!chartRef.value) return;
    render();
    lastMobile = isMobile();
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(onResize);
      resizeObserver.observe(chartRef.value);
    }
  });
});

onBeforeUnmount(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  if (chart) {
    chart.dispose();
    chart = null;
  }
});
</script>

<template>
  <div ref="chartRef" class="cb-spread-chart" />
</template>

<style scoped>
.cb-spread-chart {
  width: 100%;
  height: 300px;
  touch-action: pan-y; /* 纵向留给页面滚动, 横向归图表(tooltip 跟手) */
}

@media (max-width: 767px) {
  .cb-spread-chart {
    height: 260px;
  }
}
</style>
