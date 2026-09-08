<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { use, init } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { LineChart } from 'echarts/charts';
import { GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import { emitHover, onHover, findClosestIndex } from '../utils/chartLink';

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent]);

const props = defineProps({
  // 两侧 PE 历史(全量, 组件内按日期范围裁剪): { left: [...], right: [...] }
  history: { type: Object, default: () => ({ left: [], right: [] }) },
  // 两侧指数日线收盘价: { left: [...], right: [...] }, 行结构 { trade_date, close }
  quotes: { type: Object, default: () => ({ left: [], right: [] }) },
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

// PE 行与收盘价行按 trade_date 对齐: 以 PE 日期为准(估值与行情同源交易日, 缺则置 null 断线)
function alignClose(quoteRows, peRows) {
  const closeMap = new Map((quoteRows || []).map((r) => [r.trade_date, r.close]));
  return peRows.map((r) => ({
    date: r.trade_date,
    close: closeMap.get(r.trade_date) ?? null,
  }));
}

function buildOption(rows, closeRows, name, { showX }) {
  if (!rows.length) {
    return {
      title: { text: `${name}（无数据）`, left: '8%', top: 'middle', textStyle: { color: '#9ca3af', fontSize: 13 } },
      xAxis: [{ show: false }],
      yAxis: [{ show: false }],
      series: [],
    };
  }

  // 配色简化(用户定版): 两图统一 指数=蓝 / PE=橘黄, 不再用红绿区分左右
  const closeColor = '#185fa5';
  const peColor = '#ea580c';
  const mobile = window.innerWidth < 768;
  const last = rows[rows.length - 1];
  const lastClose = closeRows.find((c) => c.date === last.trade_date)?.close;
  const titleText = `${name}  最新 ${last.trade_date} · PE ${fmt(last.pe)} · 收盘 ${fmt(lastClose)} · 5年分位 ${pct5y(last)}`;
  const axisMin = props.startDate || undefined;
  const axisMax = props.endDate || undefined;
  // 刻度格式对齐参考站: 桌面统一 YYYY-MM-DD(信息完整无跨年突兀), 手机保持 MM-DD 防挤压
  const fullDate = !mobile;
  const timeLabel = (val) => {
    const d = new Date(val);
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    if (fullDate) return `${d.getFullYear()}-${m}-${day}`;
    return `${m}-${day}`;
  };

  const peData = rows.map((r) => ({
    value: [new Date(r.trade_date + 'T00:00:00').getTime(), r.pe],
    pct5y: pct5y(r),
    date: r.trade_date,
  }));
  const closeData = closeRows.map((c) => [
    new Date(c.date + 'T00:00:00').getTime(),
    c.close,
  ]);

  return {
    animation: false,
    grid: { left: '8%', right: '8%', top: mobile ? '20%' : '18%', height: mobile ? '70%' : '72%' },
    legend: {
      top: mobile ? '4%' : '2%',
      left: '8%',
      itemWidth: 14,
      itemHeight: 2,
      icon: 'rect',
      textStyle: { fontSize: mobile ? 10 : 11, color: '#475467' },
      // 两图各持 legend, 互不影响
    },
    title: {
      text: titleText,
      left: '8%',
      top: '10%',
      textStyle: { fontSize: mobile ? 11 : 12, fontWeight: 600, color: '#475467' },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255, 251, 245, 0.94)',
      borderColor: 'rgba(148, 163, 184, 0.35)',
      textStyle: { color: '#111827', fontSize: 11 },
      formatter(params) {
        const list = Array.isArray(params) ? params : [params];
        const date = list[0]?.data?.date || list[0]?.axisValueLabel || '';
        const peVal = list.find((p) => p.seriesName === 'PE')?.data?.value?.[1];
        const closeVal = list.find((p) => p.seriesName === '指数')?.data?.value?.[1];
        const pct = list.find((p) => p.seriesName === 'PE')?.data?.pct5y;
        return `${date}<br/>收盘 ${fmt(closeVal)}<br/>PE ${fmt(peVal)}<br/>5年分位 ${pct ?? '—'}`;
      },
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      min: axisMin,
      max: axisMax,
      show: showX, // 上图隐藏 x 轴(与下图共享时间范围), 下图显示
      axisLabel: { fontSize: 10, color: '#6b7280', hideOverlap: true, showMinLabel: true, showMaxLabel: true, formatter: timeLabel },
    },
    yAxis: [
      {
        // 左轴: 指数点位(主参照), 用户定版; 轴名省略(legend 已标注系列名, 避免左上角重叠)
        type: 'value',
        scale: true,
        axisLabel: { fontSize: 10, color: closeColor },
        splitLine: { show: false },
      },
      {
        // 右轴: PE(估值辅助)
        type: 'value',
        name: 'PE',
        nameTextStyle: { fontSize: 10, color: '#6b7280' },
        scale: true,
        axisLabel: { fontSize: 10, color: peColor },
        splitLine: { show: true, lineStyle: { color: 'rgba(148, 163, 184, 0.2)' } },
      },
    ],
    series: [
      {
        name: '指数',
        type: 'line',
        yAxisIndex: 0,
        showSymbol: false,
        lineStyle: { width: 1.5, color: closeColor },
        itemStyle: { color: closeColor },
        // [时间戳, close] 数据对: time 轴按时间对齐, 联动不因序列长度不同而错位
        data: closeData,
      },
      {
        name: 'PE',
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        lineStyle: { width: 1.5, color: peColor },
        itemStyle: { color: peColor },
        data: peData,
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
  const leftQuotes = alignClose(crop(props.quotes.left, props.startDate, props.endDate), left);
  const rightQuotes = alignClose(crop(props.quotes.right, props.startDate, props.endDate), right);
  topTs = left.map((r) => new Date(r.trade_date + 'T00:00:00').getTime());
  bottomTs = right.map((r) => new Date(r.trade_date + 'T00:00:00').getTime());

  topChart.setOption(
    buildOption(left, leftQuotes, props.leftName, { showX: false }),
    true,
  );
  bottomChart.setOption(
    buildOption(right, rightQuotes, props.rightName, { showX: true }),
    true,
  );
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
        chart.dispatchAction({ type: 'updateAxisPointer', currTrigger: 'leave' });
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
  () => [props.history, props.quotes, props.startDate, props.endDate, props.leftName, props.rightName],
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
  height: 250px;
}
@media (max-width: 767px) {
  .pe-half {
    height: 200px;
  }
}
</style>
