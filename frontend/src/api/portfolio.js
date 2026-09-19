import api from './index';

// 组合实验室标的库: 标的注册与按需抓取(股票 / ETF / 场外基金)。
// ⚠ P1 起场外基金链路已通(蛋卷净值), 注册不再返回 501; 前端仍保留 501 兜底,
//    以防后端回退到旧版本时给出 500 而不是可读文案。

// 代码解析: 返回候选数组(0/1/多条)。未解析到 → 404; 代码写法非法 → 422。
// typeHint 为 null 时由后端自动判定(stock | etf | fund)。
export const probeAsset = (code, typeHint = null) =>
  api
    .get('/portfolio/assets/probe', {
      params: { code, type: typeHint || undefined },
      timeout: 15000,
    })
    .then((response) => response.data);

// 注册标的: 后端立即返回(last_sync_status=running), 首次抓取在后台执行(不阻塞)。
export const createAsset = (payload) =>
  api.post('/portfolio/assets', payload, { timeout: 15000 }).then((response) => response.data);

// 已注册标的列表(含 row_count / first_date / last_date / last_sync_*)。
export const listAssets = () =>
  api.get('/portfolio/assets', { timeout: 15000 }).then((response) => response.data);

// 单标的补抓: 后端同步抓取(耗时较长), 失败也返回 200 结果体, 由 last_sync_status 体现。
export const refreshAsset = (assetId) =>
  api
    .post(`/portfolio/assets/${assetId}/refresh`, null, { timeout: 60000 })
    .then((response) => response.data);

// ---------------------------------------------------------------------------
// 组合(P2)
// 注意: 再平衡 / 基准 / 区间**不在这些读写字段里** —— 它们属于 backtest_run(P3)。
//       `default_rebalance` 只是新建 Run 的预填值。
// ---------------------------------------------------------------------------

// L1 组合列表: 每行含 成立时间(created_at) / 收益时间(cached_asof_date) / 三格收益(cached_*)
export const listPortfolios = (includeArchived = false) =>
  api
    .get('/portfolio/portfolios', { params: { include_archived: includeArchived }, timeout: 15000 })
    .then((response) => response.data);

// 新建组合; name 缺省时后端给 我的组合N。传 fromId 即复制现有组合(含成员与权重)。
export const createPortfolio = ({ name = null, note = null, fromId = null } = {}) =>
  api
    .post('/portfolio/portfolios', { name, note, from_id: fromId }, { timeout: 15000 })
    .then((response) => response.data);

// L2 详情: 成员(含「添加后的收益」) + 权重状态(weight_sum/ready) + 数据就绪(含共同起点)
export const getPortfolio = (portfolioId) =>
  api.get(`/portfolio/portfolios/${portfolioId}`, { timeout: 30000 }).then((r) => r.data);

// 部分更新: name / note / default_rebalance / assets(传了就全量替换成员与权重)
export const patchPortfolio = (portfolioId, payload) =>
  api.patch(`/portfolio/portfolios/${portfolioId}`, payload, { timeout: 30000 }).then((r) => r.data);

// 软删(归档, 可恢复; 不级联删标的与 Run)
export const deletePortfolio = (portfolioId) =>
  api.delete(`/portfolio/portfolios/${portfolioId}`, { timeout: 15000 }).then((r) => r.data);

// ---------------------------------------------------------------------------
// 回测 Run(P3)
// ⚠ 再平衡 / 基准 / 区间**只在这里出现**, 不写进组合的读写字段 —— 它们属于 Run 不属于组合
//    (见 docs/portfolio-lab-flow.md 第二节归属修正)。所以"看三种再平衡"不需要建三个组合。
// ---------------------------------------------------------------------------

// 运行回测: 后端同步执行并返回完整 Run(收益条/指标/回撤/相关性/详情表/曲线)。
// 相同输入默认复用已有 Run(reuse=true); 改过组合或想强制重算时传 reuse=false。
export const runBacktest = ({
  portfolioId,
  rebalance = 'none',
  benchmarkSymbol = null,
  start = null,
  end = null,
  reuse = true,
} = {}) =>
  api
    .post(
      '/portfolio/backtests',
      {
        portfolio_id: portfolioId,
        rebalance,
        benchmark_symbol: benchmarkSymbol,
        start,
        end,
        reuse,
      },
      { timeout: 60000 },
    )
    .then((r) => r.data);

// Run 列表(不含曲线, 只有对照用的摘要数字)
export const listBacktests = ({ portfolioId = null, limit = 50 } = {}) =>
  api
    .get('/portfolio/backtests', { params: { portfolio_id: portfolioId, limit }, timeout: 20000 })
    .then((r) => r.data);

// 单 Run 详情: 详情页(区域①~⑧)的唯一数据来源
export const getBacktest = (runId) =>
  api.get(`/portfolio/backtests/${runId}`, { timeout: 30000 }).then((r) => r.data);

// 多 Run 对照(同持仓 × 不同再平衡/区间/基准)
export const compareBacktests = (runIds) =>
  api
    .get('/portfolio/backtests/compare', {
      params: { ids: (runIds || []).join(',') },
      timeout: 30000,
    })
    .then((r) => r.data);

// 手动重算 L1 卡片三格(与详情页收益条共用同一条账本, 数字必然一致)
export const refreshCachedMetrics = (portfolioId) =>
  api
    .post(`/portfolio/portfolios/${portfolioId}/cached-metrics`, null, { timeout: 60000 })
    .then((r) => r.data);
