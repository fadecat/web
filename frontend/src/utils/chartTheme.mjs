// 图表主题 token: 纯函数, 传 isDark 返回该模式下的图表配色。
// ECharts 没有响应式主题, 各图表组件在 buildOption 里取 token,
// 并 watch 主题 store 的 isDark 重新 setOption。
//
// 浅色档是现状基线——每个值与改造前的硬编码一一对应(锁定"浅色不变"约束);
// 深色档是按 dataviz 校验器(validate_palette.js, 暗色表面 #1a1a19)选档的结果:
// 系列色落在 OKLCH L 0.48-0.67 且 C>=0.1, 红/绿拉开明度差保证色觉可分。
// 注意: spread 主线穿"墨色"(ink, 同文本主色), 深色档必须翻成浅墨,
// 否则 #1f2937 的近黑线在深色表面上隐形——这是本文件最关键的一行。

/** 浅色档(现状基线, 与历史硬编码逐值一致, 勿随手改) */
const LIGHT = {
  // ---- chrome: 坐标轴/网格/图例/提示框/缩放条 ----
  axisLabel: '#9ca3af', // 轴刻度文字(估值/转债)
  axisLabelAlt: '#667085', // 轴刻度文字(轮动)
  axisName: '#6b7280', // 轴名/占位说明文字
  axisLine: '#e4e7ed', // x 轴线(估值/转债)
  axisLineStrong: '#cbd5e1', // x 轴线(轮动, 略深)
  splitLine: 'rgba(148, 163, 184, 0.18)', // 横向网格线
  splitLineStrong: 'rgba(148, 163, 184, 0.2)', // 横向网格线(PE 图右轴)
  labelTitle: '#475467', // 图内标题/图例文字(轮动/PE)
  labelLegend: '#4b5563', // 图例文字(估值/转债)
  labelStrong: '#303133', // 分布图主标题
  emptyText: '#9ca3af', // 空数据占位
  tooltipBg: 'rgba(255, 255, 255, 0.96)', // 提示框底
  tooltipBgWarm: 'rgba(255, 251, 245, 0.94)', // 提示框底(轮动/PE 米白)
  tooltipText: '#111827',
  tooltipBorder: 'rgba(148, 163, 184, 0.35)',
  pointerLabelBg: '#d1d5db', // 十字光标轴标签底
  pointerLabelText: '#111827',
  zoomBorder: 'rgba(148, 163, 184, 0.32)', // 缩放条边框
  zoomFiller: 'rgba(39, 76, 119, 0.12)', // 缩放条选中区填充
  zoomText: '#9ca3af', // 缩放条刻度
  // ---- 系列与参考线(数据色) ----
  ink: '#1f2937', // spread 主线(墨色, 非分类槽位)
  navy: '#274c77', // 估值主线(兼移动端滑杆手柄色)
  navyArea: 'rgba(39, 76, 119, 0.08)', // 估值主线面积
  amber: '#b45309', // 估值对照线(十年期国债)
  maAmber: '#f59e0b', // 轮动 MA20
  red: '#dc2626', // 70 分位/全局 P90/阈值参考线
  green: '#16a34a', // 30 分位/全局 P10/均值参考线
  medianGray: '#6b7280', // 中位值参考线(注记灰)
  redArea: 'rgba(214, 67, 69, 0.22)', // spread>0 面积
  greenArea: 'rgba(29, 141, 87, 0.22)', // spread<0 面积
  blue: '#2563eb', // 转债价格中位数线
  indexBlue: '#185fa5', // 指数收盘线
  peOrange: '#ea580c', // PE 线/转债 ytm 线
  bar: '#409eff', // 分布图柱(EP 主蓝, 与全局强调色一致)
};

/** 深色档: 只覆盖需要变化的键; 未列出的键沿用浅色值 */
const DARK = {
  // chrome 对齐 Element Plus 暗色变量族
  axisLabel: '#a3a6ad',
  axisLabelAlt: '#a3a6ad',
  axisName: '#a3a6ad',
  axisLine: '#414243',
  axisLineStrong: '#4c4d4f',
  splitLine: 'rgba(148, 163, 184, 0.16)',
  splitLineStrong: 'rgba(148, 163, 184, 0.16)',
  labelTitle: '#cfd3dc',
  labelLegend: '#cfd3dc',
  labelStrong: '#cfd3dc',
  emptyText: '#a3a6ad',
  tooltipBg: 'rgba(29, 29, 31, 0.96)',
  tooltipBgWarm: 'rgba(29, 29, 31, 0.96)',
  tooltipText: '#e5eaf3',
  pointerLabelBg: '#4c4d4f',
  pointerLabelText: '#e5eaf3',
  zoomBorder: 'rgba(148, 163, 184, 0.3)',
  zoomFiller: 'rgba(148, 163, 184, 0.18)',
  zoomText: '#a3a6ad',
  // 系列色: 同色相选档提亮/压暗(dataviz 校验通过值), ink 翻转为浅墨
  ink: '#e5eaf3',
  navy: '#3d7ab8',
  navyArea: 'rgba(61, 122, 184, 0.12)',
  amber: '#d97706',
  maAmber: '#c98210',
  red: '#c93030',
  green: '#30ae6c',
  medianGray: '#7c8594',
  redArea: 'rgba(201, 48, 48, 0.25)',
  greenArea: 'rgba(48, 174, 108, 0.25)',
  blue: '#5c94e8',
  indexBlue: '#4390d6',
  peOrange: '#dd6b22',
  // bar 不覆盖: EP 主蓝在深色下仍是全局强调色, 对暗表面对比度已达标
};

/**
 * 取当前模式的图表配色 token。
 * @param {boolean} isDark 是否深色模式
 * @returns {object} token 表(浅色为基线快照, 深色在其上合并覆盖)
 */
export function chartTheme(isDark) {
  return isDark ? { ...LIGHT, ...DARK } : { ...LIGHT };
}
