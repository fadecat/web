// 组件测试全局 setup: mock 网络, 保证测试不触真实后端
import { vi } from 'vitest';

// jsdom 不实现的 API 兜底
if (!window.matchMedia) {
  window.matchMedia = () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  });
}

// 统一拦截 axios 适配器, 防止任何测试意外发出真实 HTTP 请求
vi.mock('axios', async () => {
  const actual = await vi.importActual('axios');
  const mock = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
  mock.create = vi.fn(() => {
    const instance = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.get = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.post = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.put = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.patch = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.delete = vi.fn(() => Promise.reject(new Error('测试中禁止真实 HTTP 请求')));
    instance.interceptors = { request: { use: () => {} }, response: { use: () => {} } };
    instance.defaults = actual.default.defaults;
    return instance;
  });
  return { ...actual, default: mock };
});
