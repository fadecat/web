// createAutoRefresh 计时回归测试(P2-R03): node --test, 假时钟+注入调度
// 覆盖: 立即首刷/间隔串行不重叠/GET 失败即停/墙钟截止/切换目标/卸载后失效
import test from 'node:test';
import assert from 'node:assert/strict';
import { createAutoRefresh } from './dataManagementPolling.js';

// 假时钟环境: 受控 now + 手动触发 schedule 的任务队列
function makeFakeClock() {
  let time = 1000000;
  let seq = 0;
  const tasks = new Map(); // id -> { fn, at }
  return {
    now: () => time,
    schedule: (fn, ms) => {
      const id = ++seq;
      tasks.set(id, { fn, at: time + ms });
      return id;
    },
    cancel: (id) => tasks.delete(id),
    advance: (ms) => {
      time += ms;
      // 触发所有到期的任务(按到期时间升序)
      const due = [...tasks.entries()]
        .filter(([, t]) => t.at <= time)
        .sort((a, b) => a[1].at - b[1].at);
      for (const [id, t] of due) {
        tasks.delete(id);
        t.fn();
      }
    },
    pendingCount: () => tasks.size,
  };
}

function makeLoad(script) {
  // script: 每次调用的返回值队列(函数或值); 记录调用时间与并发状态
  const calls = [];
  let inFlight = 0;
  let maxConcurrent = 0;
  const queue = [...script];
  const fn = async () => {
    calls.push({ at: null, overlapping: inFlight > 0 });
    inFlight += 1;
    maxConcurrent = Math.max(maxConcurrent, inFlight);
    const next = queue.length > 1 ? queue.shift() : queue[0];
    const result = typeof next === 'function' ? next() : next;
    inFlight -= 1;
    return result;
  };
  fn.calls = calls;
  fn.maxConcurrent = () => maxConcurrent;
  return fn;
}

test('start 立即执行第一次 load, 失败立即停止', async () => {
  const clock = makeFakeClock();
  const load = makeLoad([false]);
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 60000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start();
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 1);
  assert.equal(refresh.isRunning(), false);
  assert.equal(clock.pendingCount(), 0);
});

test('成功后按间隔续排, 到 deadline 停止(墙钟而非次数)', async () => {
  const clock = makeFakeClock();
  const load = makeLoad([true]);
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 10000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start();
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 1);
  clock.advance(3000);
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 2);
  clock.advance(3000);
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 3);
  clock.advance(3000); // 累计 9s < deadline, 正常第 4 次
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 4);
  clock.advance(3000); // 累计 12s >= deadline: tick 到点先查墙钟, 不再发起 load
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 4, 'deadline 后不应再发起 load');
  assert.equal(refresh.isRunning(), false);
  assert.equal(clock.pendingCount(), 0);
});

test('load 未返回时不启动下一次(串行不重叠)', async () => {
  const clock = makeFakeClock();
  let resolveFirst;
  const load = async () => {
    calls += 1;
    if (calls === 1) {
      await new Promise((r) => { resolveFirst = r; }); // 第一次挂起
    }
    return true;
  };
  let calls = 0;
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 60000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start();
  await new Promise((r) => setImmediate(r)); // 第一次 load 已发出但未返回
  assert.equal(calls, 1);
  clock.advance(3000); // 间隔到, 但 load 未返回: 串行设计下此刻没有任何定时器在途
  assert.equal(clock.pendingCount(), 0, '挂起期间不应有定时器(load 返回后才续排)');
  assert.equal(calls, 1, '挂起期间不应发起第二次 load');
  resolveFirst(); // 第一次返回 → 续排下一轮
  await new Promise((r) => setImmediate(r));
  clock.advance(3000); // 下一轮到点
  await new Promise((r) => setImmediate(r));
  assert.equal(calls, 2, '第一次返回后恢复正常续排');
  refresh.stop();
});

test('同一时刻最多一个活动会话: start 新目标使旧会话失效', async () => {
  const clock = makeFakeClock();
  const load = makeLoad([true]);
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 60000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start(); // 会话 A
  await new Promise((r) => setImmediate(r));
  refresh.start(); // 会话 B 取代 A
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 2); // A 首刷 + B 首刷
  clock.advance(3000);
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 3, '只有新会话在续排');
  assert.equal(refresh.isRunning(), true);
});

test('stop 后在途回调不会续排(卸载安全)', async () => {
  const clock = makeFakeClock();
  const load = makeLoad([true]);
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 60000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start();
  await new Promise((r) => setImmediate(r));
  refresh.stop();
  const pendingBefore = clock.pendingCount();
  clock.advance(30000);
  await new Promise((r) => setImmediate(r));
  assert.equal(load.calls.length, 1);
  assert.ok(clock.pendingCount() <= pendingBefore);
  assert.equal(refresh.isRunning(), false);
});

test('异常的 load 视为失败并停止', async () => {
  const clock = makeFakeClock();
  const load = async () => { throw new Error('network'); };
  const refresh = createAutoRefresh({ load, intervalMs: 3000, deadlineMs: 60000, now: clock.now, schedule: clock.schedule, cancel: clock.cancel });
  refresh.start();
  await new Promise((r) => setImmediate(r));
  assert.equal(refresh.isRunning(), false);
});

test('onStop 回调: 失败报 load_failed, 截止报 deadline, 手动 stop 不回调', async () => {
  const clock = makeFakeClock();

  // 失败路径
  const reasons1 = [];
  const loadFail = makeLoad([false]);
  createAutoRefresh({
    load: loadFail, intervalMs: 3000, deadlineMs: 60000,
    now: clock.now, schedule: clock.schedule, cancel: clock.cancel,
    onStop: (r) => reasons1.push(r),
  }).start();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(reasons1, ['load_failed']);

  // 截止路径
  const reasons2 = [];
  const loadOk = makeLoad([true]);
  createAutoRefresh({
    load: loadOk, intervalMs: 3000, deadlineMs: 5000,
    now: clock.now, schedule: clock.schedule, cancel: clock.cancel,
    onStop: (r) => reasons2.push(r),
  }).start();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(reasons2, []);
  clock.advance(5000); // tick 到点先查墙钟 → deadline
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(reasons2, ['deadline']);

  // 手动 stop 不回调
  const reasons3 = [];
  const refresh3 = createAutoRefresh({
    load: makeLoad([true]), intervalMs: 3000, deadlineMs: 60000,
    now: clock.now, schedule: clock.schedule, cancel: clock.cancel,
    onStop: (r) => reasons3.push(r),
  });
  refresh3.start();
  await new Promise((r) => setImmediate(r));
  refresh3.stop();
  assert.deepEqual(reasons3, []);
});

test('缺 load 参数直接抛错', () => {
  assert.throws(() => createAutoRefresh({}));
});
