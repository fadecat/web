// 市场估值共用判断逻辑
//
// 分位数(percentile)语义: 当前估值指标在历史区间中的位置, 0=历史最低, 100=历史最高。
// 例: PE-5y 分位 = 88.6 表示"当前 PE 高于过去 5 年 88.6% 的交易日" → 估值偏贵。
// 因此: 分位越低越便宜。

// 判断阈值(与蛋卷一致的三档)
export const PE_LOW = 30; // 分位 < 30% → 偏低
export const PE_HIGH = 70; // 分位 > 70% → 偏高

/**
 * 按分位数给出估值判断
 * @param {number|null} p 分位数(0~100)
 * @returns {{key: string, label: string, color: string}}
 */
export function judgeByPercentile(p) {
  if (p == null || Number.isNaN(p)) {
    return { key: 'na', label: '无数据', color: '#9ca3af' };
  }
  if (p < PE_LOW) return { key: 'low', label: '估值偏低', color: '#16a34a' };
  if (p > PE_HIGH) return { key: 'high', label: '估值偏高', color: '#dc2626' };
  return { key: 'mid', label: '估值正常', color: '#6b7280' };
}

/**
 * 蛋卷文案用: 「比过去 X% 的时间低」
 * 分位 p 表示"当前高于历史 p% 的时间", 反过来就是"低于历史 (100-p)% 的时间"。
 * 例: p=14.7 → 比过去 85.3% 的时间低(很便宜); p=88.6 → 只比过去 11.4% 的时间低(很贵)。
 */
export function cheaperThanPct(p) {
  if (p == null || Number.isNaN(p)) return null;
  return Math.max(0, Math.min(100, 100 - p));
}

/** 数值格式化: 空值统一显示破折号, 避免页面出现 null/NaN */
export function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}

/** 百分比格式化: 分位值保留 1 位小数 */
export function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(digits)}%`;
}
