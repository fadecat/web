// 数据管理页纯逻辑单测: node --test frontend/src/utils/dataManagementView.test.mjs
// 仅覆盖纯函数(过滤/分组/状态映射), 不声称组件级已验证。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  indexNeedsAttention,
  filterIndexes,
  groupRunsByJob,
  capabilitySelectable,
  capabilityStatusLabel,
  STATE_CLASS,
  RUN_CLASS,
} from './dataManagementView.js';

test('indexNeedsAttention: 未启用不算问题', () => {
  assert.equal(indexNeedsAttention({ enabled: false, datasets: [] }), false);
  assert.equal(
    indexNeedsAttention({ enabled: false, datasets: [{ state: 'no_data' }] }),
    false
  );
});

test('indexNeedsAttention: 暂无数据/滞后算需关注, 已更新不算', () => {
  assert.equal(
    indexNeedsAttention({ enabled: true, datasets: [{ state: 'no_data' }] }),
    true
  );
  assert.equal(
    indexNeedsAttention({ enabled: true, datasets: [{ state: 'lagging' }] }),
    true
  );
  assert.equal(
    indexNeedsAttention({ enabled: true, datasets: [{ state: 'fresh' }] }),
    false
  );
});

test('indexNeedsAttention: 空数据集算需关注', () => {
  assert.equal(indexNeedsAttention({ enabled: true, datasets: [] }), true);
});

test('filterIndexes: 搜索匹配代码或名称', () => {
  const idx = [
    { code: '930955', name: '红利低波100' },
    { code: '399296', name: '创成长' },
  ];
  assert.equal(filterIndexes(idx, { search: '930' }).length, 1);
  assert.equal(filterIndexes(idx, { search: '成长' }).length, 1);
  assert.equal(filterIndexes(idx, { search: 'XYZ' }).length, 0);
});

test('filterIndexes: 只看问题排除未启用', () => {
  const idx = [
    { code: 'A', enabled: false, datasets: [{ state: 'no_data' }] },
    { code: 'B', enabled: true, datasets: [{ state: 'no_data' }] },
    { code: 'C', enabled: true, datasets: [{ state: 'fresh' }] },
  ];
  const out = filterIndexes(idx, { onlyProblems: true });
  assert.deepEqual(out.map((x) => x.code), ['B']);
});

test('groupRunsByJob: 按 job_id 分组并取最新一条', () => {
  const runs = [
    { job_id: 'x', started_at: '2026-09-08T10:00:00', status: 'success' },
    { job_id: 'x', started_at: '2026-09-09T10:00:00', status: 'failed' },
    { job_id: 'y', started_at: '2026-09-09T09:00:00', status: 'running' },
  ];
  const groups = groupRunsByJob(runs);
  assert.equal(groups.length, 2);
  const gx = groups.find((g) => g.job_id === 'x');
  assert.equal(gx.latest.status, 'failed'); // 最新一条
  assert.equal(gx.runs.length, 2);
});

test('groupRunsByJob: 缺字段防御不抛错', () => {
  const groups = groupRunsByJob([null, { started_at: '2026-09-09T09:00:00' }]);
  assert.equal(groups.length, 0); // 无 job_id 的被跳过
});

test('capabilitySelectable: 仅 available 可勾选', () => {
  assert.equal(capabilitySelectable({ status: 'available' }), true);
  assert.equal(capabilitySelectable({ status: 'unavailable' }), false);
  assert.equal(capabilitySelectable({ status: 'error' }), false);
  assert.equal(capabilitySelectable(null), false);
});

test('状态映射类存在且覆盖主要状态', () => {
  assert.equal(STATE_CLASS.fresh, 'ok');
  assert.equal(STATE_CLASS.disabled, 'none');
  assert.equal(RUN_CLASS.failed, 'bad');
  assert.equal(capabilityStatusLabel('error'), '检查失败');
});
