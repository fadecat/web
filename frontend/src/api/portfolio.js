import api from './index';

// 组合实验室 P0: 标的注册与按需抓取(股票 / ETF)。
// 场外基金(FUND)本期链路未通: 注册会返回 501, UI 需明示「场外基金链路 P1 实现」。

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
