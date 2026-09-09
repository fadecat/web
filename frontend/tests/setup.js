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

// 统一拦截 axios 适配器, 防止任何测试意外发出真实 HTTP 请求。
// 实现方式: 返回真实 axios 实例, 但把默认 adapter 换成"直接拒绝"。
// 好处: 默认禁止真实网络, 而需要验证请求序列化的测试(api.test.js)
// 可以覆盖 instance.defaults.adapter 观察实际发出的 config。
vi.mock('axios', async () => {
  const actual = await vi.importActual('axios');

  function forbidRealHttp() {
    throw new Error('测试中禁止真实 HTTP 请求');
  }

  function makeInstance() {
    const instance = actual.create();
    instance.defaults.adapter = async () => forbidRealHttp();
    return instance;
  }

  const defaultMock = makeInstance();
  defaultMock.create = vi.fn(() => makeInstance());
  return { ...actual, default: defaultMock };
});
