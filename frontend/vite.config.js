import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    // echarts/element-plus 全量引入体积决定警告阈值, 拆分后业务代码 chunk 已很小;
    // chunkSizeWarningLimit 只为消除已知噪音, 真正的按需引入(tree-shake element-plus)留待后续
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        // 拆分大依赖 chunk, 改善缓存命中(业务代码与库分离, 库不变时用户无需重新下载)
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-echarts': ['echarts'],
          'vendor-element': ['element-plus'],
        },
      },
    },
  },
});
