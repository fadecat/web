// 轮动页三图悬停联动的共享事件总线
//
// 为什么不用 echarts.connect: connect 按 dataIndex(序号) 转发 tooltip/axisPointer,
// 但 spread 日线与 PE 估值的日期序列长度/起点不同, 序号对齐会错位。
// 这里改为按「时间戳」对齐: 各图在 hover 时上报当前横轴时间戳, 其他图按时间戳
// 二分查找自己序列里最近的 index 定位十字轴, 保证任意日期范围下精确同步。
//
// 防循环: emitHover 对相同时间戳去重——A 图 hover 上报 ts, B 图收到后 dispatch
// showTip 触发 B 的 updateAxisPointer, B 再上报相同 ts 时被去重直接返回, 环即断开。

let current = null;
const listeners = new Set();

// 上报悬停时间戳(ms); null 表示鼠标移出, 通知各图隐藏十字轴
export function emitHover(ts) {
  if (ts === current) return;
  current = ts;
  listeners.forEach((fn) => {
    try {
      fn(ts);
    } catch (e) {
      /* 单图异常不影响其他图 */
    }
  });
}

// 订阅悬停时间戳; 返回取消订阅函数
export function onHover(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// 升序数组中二分查找与目标时间戳最近的 index; 空数组返回 -1
export function findClosestIndex(arr, ts) {
  if (!arr || !arr.length || ts == null) return -1;
  let lo = 0;
  let hi = arr.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] < ts) lo = mid + 1;
    else hi = mid;
  }
  if (lo === 0) return 0;
  return Math.abs(arr[lo - 1] - ts) <= Math.abs(arr[lo] - ts) ? lo - 1 : lo;
}
