import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
});

// 可转债全量快照
export const getCbListLatest = (params = {}) =>
  api.get('/cb-list/latest', { params }).then((r) => r.data);

export const getCbListHistory = (bondId) =>
  api.get('/cb-list/history', { params: { bond_id: bondId } }).then((r) => r.data);

// 因子目录与模板配置
export const getFactorCatalog = () =>
  api.get('/cb-list/factors/catalog').then((r) => r.data);

export const getFactors = () =>
  api.get('/cb-list/factors').then((r) => r.data);

export const saveFactors = (data) =>
  api.post('/cb-list/factors', data).then((r) => r.data);

// 筛选打分
export const screenBonds = (template) =>
  api.post('/cb-list/screen', template, { timeout: 30000 }).then((r) => r.data);

export const screenBondsActive = () =>
  api.get('/cb-list/screen/active', { timeout: 30000 }).then((r) => r.data);

// 盘中选债: 实时拉集思录纯条件过滤(不读快照不落库不打分), 约 1~2s
export const screenBondsIntraday = (filters = {}) =>
  api.get('/cb-list/screen/intraday', { params: filters, timeout: 30000 }).then((r) => r.data);

// 转债黑名单
export const getBlacklist = () =>
  api.get('/cb-list/blacklist').then((r) => r.data);

export const addBlacklist = (data) =>
  api.post('/cb-list/blacklist', data).then((r) => r.data);

export const removeBlacklist = (bondId) =>
  api.delete(`/cb-list/blacklist/${bondId}`).then((r) => r.data);

// 风格轮动主图
export const getRotationAnalysis = (params = {}) =>
  api.get('/style-rotation/analysis', { params, timeout: 30000 }).then((r) => r.data);

// 市场估值: 估值快照(PE/PB/PS 当前值 + 各 9 个周期分位)
// latest=true 时每只指数只返回最新一条(列表页用: 4KB 而非 13MB 全量历史)
export const getValuationSnapshot = (params = {}) =>
  api.get('/valuation/snapshot', { params, timeout: 60000 }).then((r) => r.data);

// 股息率(当前值 + 1Y/3Y/5Y/10Y 分位 + 5Y 均值)
export const getDividendYield = (indexCode) =>
  api.get('/valuation/dividend-yield', { params: { index_code: indexCode } }).then((r) => r.data);

// 国债收益率(2Y/5Y/10Y/30Y + 10Y-2Y 期限利差)
export const getBondYield = () =>
  api.get('/valuation/bond-yield', { timeout: 30000 }).then((r) => r.data);

// 股债收益差/比(EP-10Y 与 EP/10Y, 当前值 + 分位; 传 indexCode 附带全历史序列)
export const getEquityBond = (indexCode) =>
  api
    .get('/valuation/equity-bond', {
      params: { index_code: indexCode },
      timeout: 60000,
    })
    .then((r) => r.data);

export default api;
