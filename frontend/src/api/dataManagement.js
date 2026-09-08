// 数据管理 API 客户端(新增模块, 复用现有 axios 实例 /api 前缀)。
//
// 端点(后端已固定):
//   GET  /api/data-management           -> { indexes, non_index_groups, jobs, sources }
//   GET  /api/data-management/runs?limit=50 -> { runs:[TaskRunLog 最近记录] }
//   POST /api/data-management/probe     { code, source } -> { code,name,source,capabilities,probe_token }
//   POST /api/data-management/indexes   { code,name,source,datasets,probe_token } -> { code,status,sync_status,message }
//   POST /api/data-management/indexes/{code}/sync -> 同步状态
//   PATCH /api/data-management/indexes/{code} { enabled } -> 暂停/恢复
import api from './index.js';

export const getDataManagement = () => api.get('/data-management').then((r) => r.data);

export const getRuns = (limit = 50) =>
  api.get('/data-management/runs', { params: { limit } }).then((r) => r.data);

export const probeIndex = (code, source) =>
  api.post('/data-management/probe', { code, source }).then((r) => r.data);

export const addIndex = (payload) =>
  api.post('/data-management/indexes', payload).then((r) => r.data);

export const syncIndex = (code) =>
  api.post(`/data-management/indexes/${encodeURIComponent(code)}/sync`).then((r) => r.data);

export const setIndexEnabled = (code, enabled) =>
  api
    .patch(`/data-management/indexes/${encodeURIComponent(code)}`, { enabled })
    .then((r) => r.data);
