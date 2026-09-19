// X 轴刻度纯函数测试(node --test, 与 backtestView.test.mjs 同一套跑法)。
// 核心回归点: 原实现 `String(value).slice(0, 4)` 让近 3 年图上出现一排重复的
// "2025 2025 … 2026 2026"(用户实测截图), 所以第一条断言就是"标签不得重复"。
import test from 'node:test'
import assert from 'node:assert/strict'

import { buildTimeAxisTicks } from './chartTimeAxis.mjs'

// 造交易日序列(跳周末), 贴近真实数据: 每月第一个点可能是 1~3 号
const tradingSeries = (startISO, count) => {
  const out = []
  const base = Date.parse(`${startISO}T00:00:00Z`)
  for (let offset = 0; out.length < count; offset += 1) {
    const day = new Date(base + offset * 86400000)
    const weekday = day.getUTCDay()
    if (weekday === 0 || weekday === 6) continue
    out.push(day.toISOString().slice(0, 10))
  }
  return out
}

// 按刻度函数筛出真正会渲染的标签, 便于断言
// ⚠ formatter 必须带上 index: 起点刻度的格式会因"是否与已有刻度重名"而变
const labelsOf = (dates, maxTicks) => {
  const { format, interval } = buildTimeAxisTicks(dates, maxTicks)
  const out = []
  dates.forEach((value, index) => {
    if (interval(index, value)) out.push(format(value, index))
  })
  return out
}

test('空数据不抛错', () => {
  const ticks = buildTimeAxisTicks([], 10)
  assert.equal(typeof ticks.format, 'function')
  assert.equal(typeof ticks.interval, 'function')
  assert.equal(ticks.interval(0), true)
})

test('⭐ 近 1 年: 按月标注, 不再是一排重复年份', () => {
  const labels = labelsOf(tradingSeries('2025-09-18', 250), 12)
  // 没有任何重复标签 —— 这正是不含月份时必然出现的问题
  assert.equal(new Set(labels).size, labels.length)
  assert.ok(labels.length >= 10 && labels.length <= 12, `刻度数=${labels.length}`)
  // 跨年的 1 月显示年份, 避免与次年 1 月同标签
  assert.ok(labels.includes('2026年'), labels.join(' '))
  assert.ok(labels.some((text) => text.endsWith('月')))
})

test('近 3 年: 季度刻度, 格式 2025-10', () => {
  const labels = labelsOf(tradingSeries('2023-09-18', 730), 12)
  assert.equal(new Set(labels).size, labels.length)
  assert.ok(labels.length >= 8 && labels.length <= 12, `刻度数=${labels.length}`)
  labels.forEach((text) => assert.match(text, /^\d{4}-\d{2}$/))
  // 季度边界只能是 01/04/07/10
  labels.forEach((text) => assert.ok(['01', '04', '07', '10'].includes(text.slice(5))))
})

test('近 5 年: 半年刻度', () => {
  const labels = labelsOf(tradingSeries('2021-09-18', 1220), 12)
  assert.equal(new Set(labels).size, labels.length)
  assert.ok(labels.length >= 8 && labels.length <= 12, `刻度数=${labels.length}`)
  labels.forEach((text) => assert.match(text, /^\d{4}-(01|07)$/))
})

test('近 10 年: 年度刻度, 只留年份', () => {
  const labels = labelsOf(tradingSeries('2016-09-18', 2440), 12)
  assert.equal(new Set(labels).size, labels.length)
  assert.ok(labels.length >= 8 && labels.length <= 12, `刻度数=${labels.length}`)
  labels.forEach((text) => assert.match(text, /^\d{4}$/))
})

test('短区间(约 1 个月): 等分取点, 格式 月/日', () => {
  const dates = tradingSeries('2026-08-18', 22)
  const labels = labelsOf(dates, 10)
  assert.equal(new Set(labels).size, labels.length)
  assert.ok(labels.length >= 3 && labels.length <= 10, `刻度数=${labels.length}`)
  labels.forEach((text) => assert.match(text, /^\d{1,2}\/\d{1,2}$/))
})

test('刻度上限随画布收窄而下降', () => {
  const dates = tradingSeries('2023-09-18', 730)
  const wide = labelsOf(dates, 12)
  const narrow = labelsOf(dates, 4)
  assert.ok(narrow.length <= wide.length)
  assert.ok(narrow.length <= 4 + 1, `窄屏刻度数=${narrow.length}`)
})

test('上限被夹在 2~12, 异常入参不崩', () => {
  const dates = tradingSeries('2016-09-18', 2440)
  assert.ok(labelsOf(dates, 0).length <= 12)
  assert.ok(labelsOf(dates, 99).length <= 12)
  assert.ok(labelsOf(dates, Number.NaN).length <= 12)
})

test('刻度索引单调递增且落在序列范围内', () => {
  const dates = tradingSeries('2021-09-18', 1220)
  const { interval } = buildTimeAxisTicks(dates, 12)
  const hits = dates.map((_, index) => index).filter((index) => interval(index, dates[index]))
  assert.ok(hits.length > 0)
  hits.forEach((index, position) => {
    assert.ok(index >= 0 && index < dates.length)
    if (position > 0) assert.ok(index > hits[position - 1])
  })
})
