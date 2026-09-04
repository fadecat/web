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

const isMobile = () => window.innerWidth < 768;

// ── 移动端原生双滑杆(方案B) ─────────────────────────────
// 手机端隐藏 ECharts slider(触控目标过小/三种手势挤一条窄条, 详见 buildOption 注释),
// 改用图表下方两根原生 <input type="range">: 左杆=左边界, 右杆=右边界。
// 原生控件由浏览器调优过触控: 44px 命中、按下可微调、无跳变, 且两杆职责天然分离。
// 双杆驱动图表用 dispatchAction(dataZoom), 图表窗口变化同步回滑杆用 'dataZoom' 事件。
const rangeMin = ref(0);       // 滑杆值域下界(数据点序号)
const rangeMax = ref(100);     // 滑杆值域上界(数据点总数, 渲染时设置)
const leftPct = ref(0);        // 左边界(数据点序号)
const rightPct = ref(100);     // 右边界(数据点序号)
let syncingFromChart = false;  // 防循环: 图表事件回写滑杆时不再 dispatch

// 滑杆值域是"点序号", dataZoom 的 start/end 是"百分比", 两套量纲在此换算
const toPercent = (v) => (rangeMax.value ? (v / rangeMax.value) * 100 : 0);
const toPoint = (pct) => Math.round((pct / 100) * rangeMax.value);

const applyZoomFromSliders = () => {
  if (!chart) return;
  let lo = leftPct.value;
  let hi = rightPct.value;
  if (hi - lo < 1) {
    // 最小窗口保护: 至少 1 个数据点跨度, 拉到一起时顶开另一端
    if (lo === leftPct.value) lo = hi - 1;
    else hi = lo + 1;
    leftPct.value = Math.max(rangeMin.value, lo);
    rightPct.value = Math.min(rangeMax.value, hi);
  }
  syncingFromChart = true;
  chart.dispatchAction({
    type: 'dataZoom',
    start: toPercent(leftPct.value),
    end: toPercent(rightPct.value),
  });
  nextTick(() => { syncingFromChart = false; });
};

const onLeftSliderInput = () => {
  if (leftPct.value > rightPct.value - 1) leftPct.value = rightPct.value - 1;
  applyZoomFromSliders();
};

const onRightSliderInput = () => {
  if (rightPct.value < leftPct.value + 1) rightPct.value = leftPct.value + 1;
  applyZoomFromSliders();
};

const syncSlidersFromChart = (start, end) => {
  leftPct.value = Math.min(Math.max(toPoint(start), rangeMin.value), rangeMax.value);
  rightPct.value = Math.min(Math.max(toPoint(end), rangeMin.value), rangeMax.value);
};

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
  // 查询窗口 = 显示窗口: 选了 2021-2026 就铺满 2021-2026,
  // 不再默认只显示最后 250 个交易日(那需要用户手动拖滑块)。
  // 需要看局部细节时用户可自行用滑块/滚轮缩放。
  return { start: 0, end: 100 };
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

  // 移动端: 缩小字号/边距, 图例收到底部一行滚动, 图表加高便于触屏查看
  const mobile = isMobile();
  const gridLeft = mobile ? '4%' : '7%';
  const gridRight = mobile ? '3%' : '5%';
  const legendTop = mobile ? 30 : 16;

  return {
    animation: false,
    title: [
      {
        text: '收益差值(spread) %',
        top: '4%',
        left: gridLeft,
        textStyle: { fontSize: mobile ? 12 : 13, fontWeight: 700, color: '#475467' },
      },
      {
        text: mobile
          ? `spread ${summary.latest_spread}  MA ${summary.latest_ma}`
          : `左 ${leftLabel}  spread ${summary.latest_spread}  MA ${summary.latest_ma}`,
        top: '11%',
        left: gridLeft,
        textStyle: { fontSize: mobile ? 11 : 12, fontWeight: 600, color: '#475467' },
      },
    ],
    legend: {
      top: legendTop,
      left: 'center',
      itemWidth: mobile ? 10 : 12,
      itemHeight: mobile ? 10 : 12,
      textStyle: { color: '#475467', fontSize: mobile ? 10 : 12, fontWeight: 600 },
      type: mobile ? 'scroll' : 'plain',
      pageIconSize: 10,
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
        // 手机端隐藏 ECharts 自绘滑块——它按桌面鼠标设计: 触控目标小于 44px 人体工学标准、
        // 手柄/窗口/刷选三种手势挤一条窄条、按下手柄会跳变。
        // 替代方案: 组件模板里的原生双滑杆(见 rangeMin/leftPct 注释)
        show: !mobile,
        brushSelect: false,
      },
      {
        type: 'inside',
        xAxisIndex: [0],
        start: zoomRange.start,
        end: zoomRange.end,
        zoomOnMouseWheel: true,
        // 手机端: 单指拖动只查值(tooltip/十字光标跟手), 不平移窗口——
        // 否则用滑块框好区间后手指一碰画布窗口就滑走; 平移用底部滑块, 缩放用双指捏合。
        // 桌面端保留鼠标拖拽平移的习惯用法
        moveOnMouseMove: !mobile,
      },
    ],
    grid: [{ top: mobile ? '26%' : '20%', height: mobile ? '58%' : '65%', left: gridLeft, right: gridRight }],
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
          fontSize: mobile ? 9 : 11,
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
        nameGap: mobile ? 28 : 38,
        nameFontSize: mobile ? 10 : 12,
        scale: true,
        axisLabel: { color: '#667085', fontSize: mobile ? 9 : 11 },
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
  if (!chart) {
    chart = init(chartRef.value);
    // 图表侧窗口变化(双指捏合/inside 缩放/桌面滑块) → 回写原生滑杆
    chart.on('datazoom', (params) => {
      if (syncingFromChart) return;
      const z = chart.getOption().dataZoom[0];
      if (z && z.start != null && z.end != null) syncSlidersFromChart(z.start, z.end);
    });
  }
  chart.setOption(buildOption(), true);
  // 重渲染后滑杆回归全区间(与 buildDefaultZoomRange 的 start:0/end:100 一致)
  const n = props.data?.series?.dates?.length || 0;
  rangeMin.value = 0;
  rangeMax.value = Math.max(n, 2); // 步进=1 个数据点, 拖一格前进一天
  leftPct.value = 0;
  rightPct.value = rangeMax.value; // 右边界初始=数据点总数(点序号语义, 不是百分比!)
};

// 手机旋转屏幕/窗口尺寸跨越断点时重渲染(布局参数随断点变化)
let lastMobile = isMobile();
const onResize = () => {
  if (!chart) return;
  const nowMobile = isMobile();
  if (nowMobile !== lastMobile) {
    lastMobile = nowMobile;
    render();
  } else {
    chart.resize();
  }
};

watch(() => props.data, () => nextTick(render), { deep: true });

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
  <div>
    <div ref="chartRef" style="width: 100%; height: 420px" class="rotation-chart" />
    <!-- 移动端原生双滑杆: 左杆=区间左边界, 右杆=区间右边界, 职责分离无歧义 -->
    <div v-if="isMobile()" class="range-sliders">
      <div class="slider-row">
        <span class="slider-label">起点</span>
        <input
          v-model.number="leftPct"
          type="range"
          :min="rangeMin"
          :max="rangeMax"
          step="1"
          class="range-input"
          @input="onLeftSliderInput"
        />
      </div>
      <div class="slider-row">
        <span class="slider-label">终点</span>
        <input
          v-model.number="rightPct"
          type="range"
          :min="rangeMin"
          :max="rangeMax"
          step="1"
          class="range-input"
          @input="onRightSliderInput"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 触摸手势分配: 横向拖动交给 ECharts(tooltip/十字光标/滑块平移),
   纵向仍留给页面滚动(避免手指被困在占半屏的图表上滑不走页面)。
   桌面端鼠标事件不受 touch-action 影响,全局生效无副作用 */
.rotation-chart {
  touch-action: pan-y;
}

@media (max-width: 767px) {
  .rotation-chart {
    height: 380px;
  }
}

/* ── 移动端原生双滑杆 ─────────────────────────────── */
.range-sliders {
  display: none;
}

@media (max-width: 767px) {
  .range-sliders {
    display: block;
    padding: 10px 14px 2px;
    border-top: 1px dashed rgba(148, 163, 184, 0.4);
    margin-top: -6px;
  }
}

.slider-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
}

.slider-label {
  flex: 0 0 34px;
  font-size: 12px;
  color: #475467;
  font-weight: 600;
}

.range-input {
  flex: 1;
  height: 28px;          /* 触控目标高度: 含轨道总命中区 ≥44px(行高) */
  margin: 0;
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  touch-action: pan-y;   /* 拖杆时横向归滑杆, 纵向仍可滚页面 */
}

/* 轨道 */
.range-input::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: rgba(39, 76, 119, 0.18);
}

.range-input::-moz-range-track {
  height: 6px;
  border-radius: 3px;
  background: rgba(39, 76, 119, 0.18);
}

/* 手柄: 22px 圆钮, 深蓝底白边, 与图表配色一致 */
.range-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 22px;
  height: 22px;
  margin-top: -8px;      /* (轨道6 - 手柄22)/2, 垂直居中 */
  border-radius: 50%;
  background: #274c77;
  border: 2px solid #ffffff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.35);
}

.range-input::-moz-range-thumb {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #274c77;
  border: 2px solid #ffffff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.35);
}
</style>