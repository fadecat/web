import api from './index';

export const getCommodityOverview = () =>
  api.get('/commodities/overview', { timeout: 15000 }).then((response) => response.data);

export const getCommodities = (params = {}) =>
  api.get('/commodities', { params, timeout: 20000 }).then((response) => response.data);

export const getCommodityDetail = (code) =>
  api.get(`/commodities/${encodeURIComponent(code)}`, { timeout: 15000 }).then((response) => response.data);

export const getCommodityHistory = (code, range = '1y') =>
  api
    .get(`/commodities/${encodeURIComponent(code)}/history`, { params: { range }, timeout: 30000 })
    .then((response) => response.data);
