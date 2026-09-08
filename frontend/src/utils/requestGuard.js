// 请求版本守卫: 保证「仅最新一轮请求」允许更新 UI。
//
// 用途(轮动页快速切换条件时):
// - 乱序: A 慢 / B 快, B 先返回成为最新, 迟到返回的 A 被丢弃, 页面不会跳回 A。
// - 卸载: 组件卸载时 invalidate(), 在途响应全部失效, 不再写已卸载组件的 ref。
// - loading 收敛: 仅最新版本的 finally 才允许清除 loading/valuationLoading。
//
// 纯函数、无 Vue 依赖, 便于用 node:test 直接做单元测试(无需 vitest/jsdom)。
export function createRequestGuard() {
  let version = 0;
  return {
    // 发起新一轮请求, 返回该轮版本号
    next() {
      return ++version;
    },
    // 该轮是否仍为最新(迟到/卸载后的响应应被丢弃)
    isLatest(v) {
      return v === version;
    },
    // 当前最新版本号
    get current() {
      return version;
    },
    // 使所有在途响应失效(组件卸载时调用)
    invalidate() {
      version += 1;
    },
  };
}
