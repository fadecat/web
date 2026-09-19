// 曲线 X 轴刻度: 按区间跨度自适应"粒度 + 格式"。
//
// 背景(用户实测提出的 bug): 原实现是 `String(value).slice(0, 4)`, 写死只取前 4 位,
// 于是近 3 年的图上变成一排重复的 "2025 2025 … 2026 2026" —— 既不显示月份, 也分不清
// 哪个刻度落在哪一年。ECharts 的自动间隔在这种"标签全都一样"的情形下无从补救
// (它只能靠跳过标签来防重叠, 跳完还是重复的年份), 所以刻度必须由我们按跨度自己挑。
//
// 粒度阶梯(自上而下取第一个"预估刻度数 ≤ 上限"的档):
//   日  ≤ 100 天   等分取点,          格式 `9/18`
//   月  ≤ 1 年     每月边界,          格式 `3月`(1 月显示 `2026年`, 跨年不重复)
//   季  ≤ 3 年     1/4/7/10 月边界,   格式 `2025-10`
//   半年 ≤ 6 年    1/7 月边界,        格式 `2025-07`
//   年  > 6 年     1 月边界,          格式 `2025`
//
// 上限由调用方按画布宽度算(setOption 前用 chart.getWidth()) —— 刻度密度是**版面**
// 问题, 不是数据问题, 所以不在这里写死。

const DAY_MS = 86400000

const parseDay = (value) => {
  const text = String(value ?? '')
  return {
    // 兼容 'YYYY-MM-DD' 与 'YYYY/MM/DD'; 解析不出来时给出 NaN, 由调用方跳过
    y: Number(text.slice(0, 4)),
    m: Number(text.slice(5, 7)),
    d: Number(text.slice(8, 10)),
  }
}

const dayNumber = ({ y, m, d }) => Date.UTC(y, m - 1, d) / DAY_MS

const pad2 = (value) => String(value).padStart(2, '0')

const ALL_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

const CADENCES = [
  // 月: 刻度太密时会撞上"跨年 1 月与次年 1 月同标签", 所以 1 月改显年份
  {
    months: ALL_MONTHS,
    perYear: 12,
    label: (d) => (d.m === 1 ? `${d.y}年` : `${d.m}月`),
  },
  { months: [1, 4, 7, 10], perYear: 4, label: (d) => `${d.y}-${pad2(d.m)}` },
  { months: [1, 7], perYear: 2, label: (d) => `${d.y}-${pad2(d.m)}` },
  { months: [1], perYear: 1, label: (d) => `${d.y}` },
]

/**
 * 生成 category 轴的 `axisLabel.formatter` 与 `axisLabel.interval`。
 *
 * @param {string[]} dates  与 series 一一对应的 'YYYY-MM-DD' 列表
 * @param {number} maxTicks 刻度数上限(按画布可用宽度算, 建议 3~12)
 * @returns {{ format: (value: string, index?: number) => string, interval: (index: number) => boolean }}
 */
export function buildTimeAxisTicks(dates, maxTicks = 10) {
  const list = Array.isArray(dates) ? dates.map((item) => String(item)) : []
  const total = list.length
  const limit = Math.max(2, Math.min(12, Math.floor(Number(maxTicks) || 10) || 10))

  if (total === 0) {
    return { format: (value) => String(value ?? ''), interval: () => true }
  }

  const first = parseDay(list[0])
  const last = parseDay(list[total - 1])
  const spanDays = dayNumber(last) - dayNumber(first) + 1
  const spanYears = spanDays / 365.25

  // 短区间(≲ 3 个月)可标的"月边界"只有 1~3 个, 位置严重偏右 → 改为等分取点。
  // 这是唯一不按自然边界走的档, 因为此时"哪一天"比"哪个月"更有信息量。
  if (spanDays <= 100) {
    const stride = Math.max(1, Math.ceil(total / limit))
    const picked = new Set()
    for (let index = 0; index < total; index += stride) picked.add(index)
    return {
      format: (value) => {
        const d = parseDay(value)
        return `${d.m}/${d.d}`
      },
      interval: (index) => picked.has(index),
    }
  }

  const cadence =
    CADENCES.find((item) => item.perYear * spanYears <= limit) ??
    CADENCES[CADENCES.length - 1]

  // 每月(或每季/每年)只取**该月第一个出现的交易日**作刻度, 这样刻度天然落在
  // 自然边界上, 不会被"当月任意一天"带跑。
  const marks = []
  let lastKey = ''
  list.forEach((value, index) => {
    const d = parseDay(value)
    if (!Number.isFinite(d.y) || !cadence.months.includes(d.m)) return
    const key = `${d.y}-${d.m}`
    if (key === lastKey) return
    lastKey = key
    marks.push(index)
  })

  // 区间起点所在的那个月只是**残月**(从月中开始), 不是自然边界。若把它算进来,
  // 刻度数会虚高一级: 近 1 年实测 12 个真边界 + 1 个残月 = 13 → 触发抽稀 →
  // 刻度被抽成"隔月"(10/12/2/4/6 月全丢), 而且不规律。剔掉, 起点由下面
  // 的"补起点刻度"逻辑按实际距离决定要不要标。
  if (marks.length && marks[0] === 0) marks.shift()

  // 预估用的 perYear 是近似值(区间可能从半截季度/年开始), 实际仍可能超限 → 抽稀
  let picked = marks
  if (picked.length > limit) {
    const stride = Math.ceil(picked.length / limit)
    picked = picked.filter((_, index) => index % stride === 0)
  }

  const shown = new Set(picked)
  // 左边缘补一个起点刻度: 否则区间起点与第一个自然边界相距较远时, 图上最左边
  // 一大段没有参照。两组前提:
  //   ① 距离足够(≥ 平均刻度间距), 免得跟首个边界标签挤在一起;
  //   ② 标签不与已有刻度重名 —— 近 1 年最容易踩: 区间 2025-09-18~2026-09-11 的
  //      末刻度是「9月」, 起点也是「9月」, 直接补就出现两个一模一样的标签。
  //      重名时起点改用完整年月「2025-09」, 仍重名就干脆不补。
  let startPrecise = false
  if (shown.size) {
    const firstShown = Math.min(...shown)
    const averageGap = Math.ceil(total / (shown.size + 1))
    if (firstShown >= averageGap) {
      const taken = new Set(picked.map((index) => cadence.label(parseDay(list[index]))))
      const loose = cadence.label(first)
      const precise = `${first.y}-${pad2(first.m)}`
      if (!taken.has(loose)) {
        shown.add(0)
      } else if (!taken.has(precise)) {
        startPrecise = true
        shown.add(0)
      }
    }
  }

  return {
    format: (value, index) => {
      const d = parseDay(value)
      if (index === 0 && startPrecise) return `${d.y}-${pad2(d.m)}`
      return cadence.label(d)
    },
    interval: (index) => shown.has(index),
  }
}
