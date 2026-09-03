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

export default api;
