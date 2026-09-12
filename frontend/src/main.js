import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import 'element-plus/dist/index.css';
// 暗色变量需在基础样式之后引入, html.dark 挂类即整体切换
import 'element-plus/theme-chalk/dark/css-vars.css';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import App from './App.vue';
import router from './router';
import { useThemeStore } from './stores/theme';
import './style.css';

const app = createApp(App);
app.use(createPinia());
// 挂载前恢复主题, 避免首帧闪色(index.html 内联脚本已先行兜底)
useThemeStore().init();
app.use(router);
app.use(ElementPlus, { locale: zhCn });
app.mount('#app');
