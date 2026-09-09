import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

// 组件测试配置: jsdom 环境供挂载 Vue 页面; 不访问真实网络
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: false,
    include: ['tests/unit/**/*.test.js'],
    setupFiles: ['tests/setup.js'],
  },
});
