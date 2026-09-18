import api from './index';

// 研究回放(次日 T 价位研究 V1): 信号回放/价位触达, 全量返回不分页。

export const getResearchSecurities = () =>
  api.get('/research/securities', { timeout: 15000 }).then((response) => response.data);

export const getResearchDataHealth = () =>
  api.get('/research/data-health', { timeout: 15000 }).then((response) => response.data);

export const getResearchReplays = (symbol = null) =>
  api
    .get('/research/replays', { params: symbol ? { symbol } : {}, timeout: 15000 })
    .then((response) => response.data);

// 创建回放: λ×窗口网格同步计算(约秒级), 无 USABLE 快照时后端返回 409
export const createResearchReplay = (payload) =>
  api.post('/research/replays', payload, { timeout: 120000 }).then((response) => response.data);

export const getReplaySummary = (runId) =>
  api.get(`/research/replays/${runId}/summary`, { timeout: 30000 }).then((response) => response.data);

export const getReplayDays = (runId) =>
  api.get(`/research/replays/${runId}/days`, { timeout: 60000 }).then((response) => response.data);

export const getReplayComparison = (symbol, startDate, endDate) =>
  api
    .get('/research/replay-comparison', {
      params: { symbol, start_date: startDate, end_date: endDate },
      timeout: 30000,
    })
    .then((response) => response.data);
