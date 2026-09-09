// API 层序列化测试(R4-06/T4): 验证 Axios 请求方法/URL/params/body 边界。
// 直接给默认 api 实例注入 mock adapter, 断言实际发出的 config。
import { describe, it, expect, vi, afterEach } from 'vitest';

import api from '../../src/api/index.js';
import {
  screenBondsIntraday,
  saveFactors,
  getRatingCatalog,
} from '../../src/api/index.js';
import { syncIndex } from '../../src/api/dataManagement.js';

const ORIGINAL_ADAPTER = api.defaults.adapter;

function capture() {
  const seen = [];
  api.defaults.adapter = async (config) => {
    seen.push(config);
    return { data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config };
  };
  return seen;
}

afterEach(() => {
  api.defaults.adapter = ORIGINAL_ADAPTER;
});

describe('API 层请求边界', () => {
  it('screenBondsIntraday 使用 GET 且 ratings 逗号序列化', async () => {
    const seen = capture();
    await screenBondsIntraday({ ratings: 'AA+,NONE', price_max: 120 });
    const cfg = seen[0];
    expect(cfg.method).toBe('get');
    expect(cfg.url).toBe('/cb-list/screen/intraday');
    expect(cfg.params).toEqual({ ratings: 'AA+,NONE', price_max: 120 });
  });

  it('saveFactors 使用 POST 且 body 为整包配置', async () => {
    const seen = capture();
    const payload = { active_id: 't1', templates: [{ id: 't1', ratings: [] }] };
    await saveFactors(payload);
    const cfg = seen[0];
    expect(cfg.method).toBe('post');
    expect(cfg.url).toBe('/cb-list/factors');
    expect(JSON.parse(cfg.data)).toEqual(payload); // axios 已把 body 序列化为 JSON 字符串
  });

  it('syncIndex 对指数代码做 URL 编码(含 /、空格、+ 必须转义)', async () => {
    const seen = capture();
    // 真实指数代码是纯数字, 但编码实现必须对任意字符串安全:
    // 用含斜杠/空格/加号的代码, 若去掉 encodeURIComponent 该用例立即失败
    await syncIndex('A/B + 1');
    const cfg = seen[0];
    expect(cfg.method).toBe('post');
    expect(cfg.url).toBe('/data-management/indexes/A%2FB%20%2B%201/sync');
  });

  it('getRatingCatalog 使用 GET 评级目录端点', async () => {
    const seen = capture();
    await getRatingCatalog();
    const cfg = seen[0];
    expect(cfg.method).toBe('get');
    expect(cfg.url).toBe('/cb-list/factors/ratings');
  });
});
