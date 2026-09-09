// 筛选条件输入校验(转债筛选页共用)
//
// 规则:
//   - 空串 = 不限(该条件省略)
//   - 只接受纯数字(可带正负号/小数点), "120abc" / "abc" 属于非法输入
//   - 0 是合法值, 不当"空"处理
//   - 区间字段 min > max 视为非法

const NUM_RE = /^[+-]?(\d+(\.\d+)?|\.\d+)$/;

// 解析单个输入: '' → null(不限); 非法/非有限(如 400 个 9 转 Infinity) → NaN
export function parseFilterNumber(raw) {
  const s = String(raw ?? '').trim();
  if (!s) return null;
  if (!NUM_RE.test(s)) return NaN;
  const v = Number(s);
  return Number.isFinite(v) ? v : NaN;
}

// 校验整组条件
//   fields:  { key: { label, min } }, min 用于下限约束(如价格/规模/年限≥0)
//   ranges:  [ [minKey, maxKey, 区间中文名], ... ]
// 返回 { ok, values, errors }:
//   ok=true 时 values 里只含合法数字条件(空条件省略) + 原样透传的 ratings 数组
export function validateFilters(f, fields, ranges = []) {
  const errors = [];
  const values = { ratings: [...(f.ratings || [])] };
  for (const [key, def] of Object.entries(fields)) {
    const { label, min } = typeof def === 'object' ? def : { label: def, min: undefined };
    const v = parseFilterNumber(f[key]);
    if (Number.isNaN(v)) {
      errors.push(`${label}：请输入数字`);
      continue;
    }
    if (v === null) continue;
    if (min != null && v < min) {
      errors.push(`${label}：不能小于 ${min}`);
      continue;
    }
    values[key] = v;
  }
  for (const [minKey, maxKey, label] of ranges) {
    const lo = values[minKey];
    const hi = values[maxKey];
    if (lo != null && hi != null && lo > hi) {
      errors.push(`${label}：最低值不能大于最高值`);
    }
  }
  return { ok: errors.length === 0, values, errors };
}
