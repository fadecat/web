// 数据管理自动刷新控制器(P2-R03)
//
// 语义定位: "自动刷新"而非"轮询任务终态"——数据新鲜度(fresh/stale 等)
// 只反映数据新旧, 不能证明本次抓取是否完成; 任务真实结果以抓取记录为准。
//
// 特性:
// - POST 只触发一次(由调用方完成), 控制器只负责 GET 刷新
// - 单次 setTimeout 串行: 前一次 load 未完成不启动下一次(不重叠)
// - 墙钟 deadline 截止; load 失败立即停止
// - 同一时刻最多一个活动会话; start 新目标会使旧会话失效(版本号)
// - 时钟/调度可注入, 供假时钟测试

/**
 * @param {Object} opts
 * @param {() => Promise<boolean>} opts.load  刷新动作, 返回 true=成功/false=失败
 * @param {number} [opts.intervalMs=3000]
 * @param {number} [opts.deadlineMs=300000]   墙钟截止(默认 5 分钟)
 * @param {() => number} [opts.now]           可注入时钟
 * @param {(fn: () => void, ms: number) => any} [opts.schedule]  可注入 setTimeout
 * @param {(id: any) => void} [opts.cancel]   可注入 clearTimeout
 * @param {(reason: 'load_failed'|'deadline') => void} [opts.onStop]
 *   会话因失败/截止而结束时回调; 手动 stop() 或被新 start() 取代不回调。
 */
export function createAutoRefresh({
  load,
  intervalMs = 3000,
  deadlineMs = 300000,
  now = () => Date.now(),
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (id) => clearTimeout(id),
  onStop,
}) {
  if (typeof load !== 'function') {
    throw new Error('createAutoRefresh: load 必须为函数');
  }
  let version = 0;      // 会话版本: start/stop 递增, 使在途回调失效
  let timerId = null;
  let running = false;
  let inFlight = null;  // 当前 load 的 flight 标识(对象), 无 load 在途时为 null
  let disposed = false; // 永久失效(R3-04): dispose 后 start 不再可用

  function _clearTimer() {
    if (timerId !== null) {
      cancel(timerId);
      timerId = null;
    }
  }

  function tick(versionAtStart, deadline) {
    if (versionAtStart !== version) return; // 已被新会话/停止取代
    if (now() >= deadline) {
      stop(); // 墙钟截止: 到点即停, 不再发起 load(即使定时器已在途)
      if (typeof onStop === 'function') onStop('deadline');
      return;
    }
    if (inFlight !== null) {
      // 理论上不可达(串行调度), 防御性兜底: 跳过本轮
      timerId = schedule(() => tick(versionAtStart, deadline), intervalMs);
      return;
    }
    const flight = { version: versionAtStart };
    inFlight = flight;
    Promise.resolve()
      .then(() => {
        // R4-02: 真正调用 load 前复核会话状态——start 后同一轮 stop/dispose
        // 会递增版本/置 disposed, 此时不得执行尚未开始的 load。
        if (disposed || versionAtStart !== version || inFlight !== flight) {
          return { skipped: true, ok: false };
        }
        return Promise.resolve(load())
          .then((r) => ({ skipped: false, ok: r !== false }))
          .catch(() => ({ skipped: false, ok: false }));
      })
      .then(({ skipped, ok }) => {
        if (inFlight === flight) inFlight = null;
        if (skipped || disposed || versionAtStart !== version) return;
        if (!ok) {
          stop();
          if (typeof onStop === 'function') onStop('load_failed');
          return;
        }
        if (now() >= deadline) {
          stop();
          if (typeof onStop === 'function') onStop('deadline');
          return;
        }
        timerId = schedule(() => tick(versionAtStart, deadline), intervalMs);
      });
  }

  /**
   * 启动一次自动刷新会话(取代已有会话)。
   * 立即执行第一次 load, 之后每 intervalMs 一次, 直到失败/截止/stop。
   * dispose 后调用: 直接忽略(不抛错), 保证卸载后迟到调用安全。
   */
  function start() {
    if (disposed) return;
    stop();
    version += 1;
    const versionAtStart = version;
    running = true;
    const deadline = now() + deadlineMs;
    tick(versionAtStart, deadline);
  }

  /** 停止当前会话(若在跑)。之后可再次 start; dispose 后则不可。 */
  function stop() {
    version += 1;
    _clearTimer();
    running = false;
  }

  /**
   * 永久失效(R3-04): 与 stop 的区别是之后任何 start 都被忽略,
   * 用于组件卸载——卸载后迟到的 start(如 await POST 完成后的续体)
   * 不得重启刷新。
   */
  function dispose() {
    disposed = true;
    stop();
  }

  function isRunning() {
    return running;
  }

  return { start, stop, dispose, isRunning };
}
