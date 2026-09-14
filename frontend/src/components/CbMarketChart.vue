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
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  TitleComponent,
} from 'echarts/components';

// 借鉴 ValuationChart 的 ECharts 注册与生命周期写法;
// 30/70 分位参考线同样参考其 buildMarkLine 口径(窗口内计算, 线性插值)。
use([
  CanvasRenderer,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  TitleComponent,
  DataZoomComponent,
  AxisPointerComponent,
]);

const props = defineProps({
  // 已规范化、已裁剪的记录: [{ trade_date, median_price, avg_ytm, count }]
  rows: { type: Array, default: () => [] },
  // 当前查看日期(ISO), 父页面回传并高亮
  selectedDate: { type: String, default: '' },
});

const emit = defineEmits(['date-select']);

const chartRef = ref(null);
let chart = null;
let resizeObserver = null;
let zrClickHandler = null;
// 深浅色切换时重新 setOption(ECharts 无响应式主题, 见 utils/chartTheme.mjs)
const theme = useThemeStore();

const dates = computed(() => props.rows.map((r) => r.trade_date));
const medianData = computed(() => props.rows.map((r) => r.median_price));
const ytmData = computed(() => props.rows.map((r) => r.avg_ytm));

// 30/70 分位参考线: 在当前窗口内计算, 随 1y/3y/5y/all 切换重算
// (与读数行"本窗口分位"同口径; ValuationChart 同理——切时间范围参考线跟着变)
const priceRefLines = computed(() => ({
  p30: quantile(medianData.value, 30),
  p70: quantile(medianData.value, 70),
}));
const ytmRefLines = computed(() => ({
  p30: quantile(ytmData.value, 30),
  p70: quantile(ytmData.value, 70),
}));

// 用容器宽度判定移动端(jsdom 中 clientWidth 为 0, 回落到 window.innerWidth)。
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
      xAxis: [{ show: false }, { show: false }],
      yAxis: [{ show: false }, { show: false }],
      series: [],
    };
  }

  const mobile = isMobile();
  // 单点时显示点标记, 多点为细线(不遮挡)
  const single = props.rows.length === 1;
  const symbol = single ? 'circle' : 'none';
  const symbolSize = single ? 8 : 0;

  // 选中日期竖线(定位, 非参考线)与窗口内 30/70 分位横线合入同系列 markLine;
  // 两图各自绘制以保持可见。竖线颜色 #94a3b8 对浅/深两种表面对比度均 >=3:1
  // (dataviz 校验), 双模式共用不换档。
  // 分位线颜色语义同页面摘要/估值页: 价格中位数分位低=便宜(p30 绿/p70 红),
  // 平均到期收益率方向相反(p30 红/p70 绿); 标签贴绘图区左端内侧,
  // 避免与外侧 Y 轴刻度重叠(ValuationChart 同款布局)。
  const fmt = (v) => (v == null ? '—' : Number(v).toFixed(2));
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
  const pctLine = (yValue, name, color) => ({
    yAxis: yValue,
    name,
    lineStyle: { color, type: 'dashed', width: 1 },
    label: pctLabel(color),
  });
  const seriesMarkLine = (refLines, lowColor, highColor) => {
    const data = [];
    if (props.selectedDate) {
      data.push({
        xAxis: props.selectedDate,
        lineStyle: { color: '#94a3b8', type: 'dashed', width: 1 },
        label: { show: false },
      });
    }
    if (refLines.p30 != null) data.push(pctLine(refLines.p30, '30分位', lowColor));
    if (refLines.p70 != null) data.push(pctLine(refLines.p70, '70分位', highColor));
    return data.length ? { symbol: 'none', silent: true, data } : undefined;
  };

  return {
    animation: false,
    legend: {
      top: mobile ? 2 : 6,
      left: 'center',
      selectedMode: false, // 仅识别, 不提供隐藏序列
      itemWidth: 16,
      itemHeight: 9,
      itemGap: mobile ? 10 : 18,
      textStyle: { color: t.labelLegend, fontSize: mobile ? 10 : 12 },
      data: ['价格中位数（元）', '平均到期收益率（集思录口径，%）'],
    },
    // 顶部图标题(单位明确, 颜色不是唯一识别)
    title: [
      {
        text: '价格中位数（元）',
        left: mobile ? 48 : 60,
        top: mobile ? 22 : 28,
        textStyle: { color: t.blue, fontSize: mobile ? 10 : 12, fontWeight: 600 },
      },
      {
        text: '平均到期收益率（集思录口径，%）',
        left: mobile ? 48 : 60,
        top: mobile ? 202 : 238,
        textStyle: { color: t.peOrange, fontSize: mobile ? 10 : 12, fontWeight: 600 },
      },
    ],
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
        const axisValue = params[0].axisValue;
        const lines = params.map((p) => {
          const isYtm = p.seriesName.startsWith('平均到期收益率');
          const unit = isYtm ? '%' : '元';
          const base = `${p.marker}${p.seriesName} <b>${fmt(p.data)}${unit}</b>`;
          // 悬浮日值在当前窗口内的回看分位(与读数行同口径; 样本不足 20 条不显示)。
          // 颜色语义同页面摘要: 价格同 PE(分位低=便宜), YTM 同股息率(分位高=便宜),
          // 30~70 中性不着色(色即信号, 与图上分位线一致)。
          const pctl = percentileRank(isYtm ? ytmData.value : medianData.value, p.data);
          if (pctl == null) return base;
          const tone = percentileTone(pctl, isYtm ? 'dividend' : 'pe');
          const color = tone === 'green' ? t.green : tone === 'red' ? t.red : null;
          const text = `（${fmtPct(pctl)} 分位）`;
          return color ? `${base} <span style="color:${color}">${text}</span>` : `${base} ${text}`;
        });
        return `${axisValue}<br/>${lines.join('<br/>')}`;
      },
    },
    // 两图游标同步
    axisPointer: { link: [{ xAxisIndex: [0, 1] }] },
    grid: [
      { left: mobile ? 48 : 60, right: mobile ? 16 : 28, top: 40, height: mobile ? 135 : 165 },
      { left: mobile ? 48 : 60, right: mobile ? 16 : 28, top: mobile ? 230 : 270, height: mobile ? 145 : 165 },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates.value,
        gridIndex: 0,
        boundaryGap: false,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      {
        type: 'category',
        data: dates.value,
        gridIndex: 1,
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
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        scale: true, // 自适应, 不固定 95-145
        name: '元',
        nameTextStyle: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
        axisLabel: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: t.splitLine } },
      },
      {
        type: 'value',
        gridIndex: 1,
        scale: true, // 不强制为正
        name: '%',
        nameTextStyle: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
        axisLabel: { color: t.axisLabel, fontSize: mobile ? 9 : 11 },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: t.splitLine } },
      },
    ],
    // 底部共用缩放条, 作用于两个 xAxis
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], zoomOnMouseWheel: true },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
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
        name: '价格中位数（元）',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: medianData.value,
        symbol,
        symbolSize,
        connectNulls: false, // 不补 0、不插值
        smooth: false,
        lineStyle: { color: t.blue, width: 1.8 },
        itemStyle: { color: t.blue },
        markLine: seriesMarkLine(priceRefLines.value, t.green, t.red),
      },
      {
        name: '平均到期收益率（集思录口径，%）',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: ytmData.value,
        symbol,
        symbolSize,
        connectNulls: false,
        smooth: false,
        lineStyle: { color: t.peOrange, width: 1.8, type: 'dashed' },
        itemStyle: { color: t.peOrange },
        markLine: seriesMarkLine(ytmRefLines.value, t.red, t.green),
      },
    ],
  };
}

const render = (replace = true) => {
  if (!chartRef.value) return;
  if (!chart) chart = init(chartRef.value);
  chart.setOption(buildOption(), replace);
};

const updateSelection = () => {
  if (!chartRef.value || !chart) return;
  chart.setOption(buildOption(), false);
};

// 点击/触摸图表任意位置 → 回传最近记录的 ISO 日期(移动端不依赖 hover)
function handleZrClick(event) {
  if (!chart || !dates.value.length) return;
  const coord = [event.offsetX, event.offsetY];
  for (const xi of [0, 1]) {
    if (typeof chart.containPixel === 'function' && !chart.containPixel({ gridIndex: xi }, coord)) {
      continue;
    }
    let idx;
    try {
      idx = chart.convertFromPixel({ xAxisIndex: xi }, coord[0]);
    } catch (e) {
      continue;
    }
    if (idx == null) continue;
    const numericIdx = Array.isArray(idx) ? idx[0] : idx;
    const i = Math.round(numericIdx);
    if (i >= 0 && i < dates.value.length) {
      emit('date-select', dates.value[i]);
      return;
    }
  }
}

let lastMobile = false;
const onResize = () => {
  if (!chart) return;
  if (isMobile() !== lastMobile) {
    lastMobile = isMobile();
    render(); // 跨断点重建以更新字号/布局
    chart.resize();
  } else {
    chart.resize();
  }
};

watch(() => props.rows, () => nextTick(() => render(true)), { deep: true });
watch(() => props.selectedDate, () => nextTick(updateSelection));

// 深浅色切换 → 换 token 重绘(数据不变, 只有颜色变)
watch(() => theme.isDark, () => nextTick(() => render(true)));

onMounted(() => {
  nextTick(() => {
    if (!chartRef.value) return;
    render();
    lastMobile = isMobile();
    zrClickHandler = handleZrClick;
    chart.getZr().on('click', zrClickHandler);
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
    try {
      if (zrClickHandler) chart.getZr().off('click', zrClickHandler);
    } catch (e) {
      /* noop */
    }
    chart.dispose();
    chart = null;
    zrClickHandler = null;
  }
});
</script>

<template>
  <div ref="chartRef" class="cb-market-chart" />
</template>

<style scoped>
.cb-market-chart {
  width: 100%;
  height: 480px;
  touch-action: pan-y; /* 纵向留给页面滚动, 横向归图表(tooltip 跟手) */
}

@media (max-width: 767px) {
  .cb-market-chart {
    height: 420px;
  }
}
</style>
