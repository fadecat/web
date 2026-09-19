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
