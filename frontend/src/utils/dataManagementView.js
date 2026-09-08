// 数据管理页纯展示逻辑(无 Vue / 无网络依赖, 便于 node:test 直接单测)。
//
// 注意术语: 后端 jobs / runs 都是 TaskRunLog「任务级」记录, 不是 RunItem。
// stale / lagging / no_data 只是「更新较晚 / 滞后 / 暂无数据」, 绝不等同于
// 「抓取失败」; failure 只能来自实际运行结果(RunItem status=failed/partial)。

// 状态 -> badge 样式类(沿用现有浅色配色: ok/warn/bad/none/run)
export const STATE_CLASS = {
  fresh: 'ok',
  stale: 'warn',
  lagging: 'bad',
  no_data: 'none',
  disabled: 'none',
  waiting: 'run',
};

export const STATE_LABEL = {
  fresh: '已更新',
  stale: '更新较晚',
  lagging: '滞后多日',
  no_data: '暂无数据',
  disabled: '未启用',
  waiting: '待更新',
};

// 任务运行状态 -> badge 样式类
export const RUN_CLASS = {
  success: 'ok',
  partial: 'warn',
  failed: 'bad',
  running: 'run',
  skipped: 'none',
  interrupted: 'warn',
  never: 'none',
};

export const RUN_LABEL = {
  success: '成功',
  partial: '部分成功',
  failed: '失败',
  running: '运行中',
  skipped: '跳过',
  interrupted: '中断',
  never: '暂无记录',
};

// 指数是否需要关注: 未启用(enabled=false)不算问题;
// 有任一数据集处于 stale/lagging/no_data, 或完全没有数据集, 算需关注。
export function indexNeedsAttention(index) {
  if (!index) return false;
  if (index.enabled === false) return false;
  const ds = index.datasets || [];
  if (!ds.length) return true;
  return ds.some((d) => ['stale', 'lagging', 'no_data'].includes(d.state));
}

// 按代码 / 名称搜索 + 只看问题过滤(未启用不算问题)。
export function filterIndexes(indexes, { search = '', onlyProblems = false } = {}) {
  const q = String(search || '').trim().toLowerCase();
  return (indexes || []).filter((idx) => {
    if (q) {
      const hay = `${idx.name || ''} ${idx.code || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    if (onlyProblems && !indexNeedsAttention(idx)) return false;
    return true;
  });
}

// 抓取记录: 按 job_id 分组, 每组按 started_at 倒序, latest 取最新一条。
// 防御: 缺字段不抛错。
export function groupRunsByJob(runs) {
  const map = new Map();
  for (const r of runs || []) {
    const id = r && r.job_id;
    if (id == null) continue;
    if (!map.has(id)) map.set(id, []);
    map.get(id).push(r);
  }
  const out = [];
  for (const [job_id, list] of map) {
    const sorted = [...list].sort((a, b) =>
      String(b.started_at || '').localeCompare(String(a.started_at || ''))
    );
    out.push({ job_id, latest: sorted[0] || null, runs: sorted });
  }
  // 组间按最新一条的 started_at 倒序
  out.sort((a, b) =>
    String((b.latest && b.latest.started_at) || '').localeCompare(
      String((a.latest && a.latest.started_at) || '')
    )
  );
  return out;
}

// 探测能力 -> 是否可勾选(仅 available 可勾选; unavailable=来源不支持, error=检查失败可重试)。
export function capabilitySelectable(cap) {
  return !!cap && cap.status === 'available';
}

// 探测能力 status -> 展示标签
export function capabilityStatusLabel(status) {
  if (status === 'available') return '支持';
  if (status === 'unavailable') return '不支持';
  if (status === 'error') return '检查失败';
  return status || '-';
}
