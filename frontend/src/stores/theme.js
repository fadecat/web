// 主题切换 store: 单一信号 html.dark 同时驱动 Element Plus 暗色变量与自定义 token。
// 持久化键名与 index.html 的防闪屏内联脚本共用, 改名须两处同步。
import { computed, ref, watch } from 'vue';
import { defineStore } from 'pinia';

const STORAGE_KEY = 'app-theme';
// 'auto' 跟随系统偏好(默认), 其余两个为用户显式指定
const MODES = ['light', 'dark', 'auto'];

function readStoredMode() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return MODES.includes(saved) ? saved : 'auto';
  } catch {
    return 'auto'; // 隐私模式等场景读不到, 退回默认
  }
}

function systemPrefersDark() {
  // matchMedia 缺失的环境(个别测试运行器)按浅色处理, 避免 boot 报错
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  );
}

export const useThemeStore = defineStore('theme', () => {
  // 用户选择的模式: light | dark | auto
  const mode = ref('auto');
  // 系统当前偏好, auto 模式的数据源
  const systemDark = ref(false);
  // 生效状态: 是否给 <html> 挂 dark 类的唯一依据
  const isDark = computed(
    () => mode.value === 'dark' || (mode.value === 'auto' && systemDark.value)
  );

  // 生效状态变化即同步到 <html>; 覆盖切换/系统偏好变化两条路径
  watch(isDark, (dark) => {
    document.documentElement.classList.toggle('dark', dark);
  });

  /** 应用启动时调用一次: 恢复持久化选择 + 订阅系统偏好变化 */
  function init() {
    mode.value = readStoredMode();
    systemDark.value = systemPrefersDark();

    if (typeof window.matchMedia === 'function') {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      // auto 模式下系统切换深浅时实时跟随
      mq.addEventListener?.('change', (e) => {
        systemDark.value = e.matches;
      });
    }
    // 内联防闪屏脚本可能已先挂过类, 这里幂等同步一次(读到的偏好与脚本一致时无感)
    document.documentElement.classList.toggle('dark', isDark.value);
  }

  /** 设置模式并持久化(本次会话立即生效) */
  function setMode(next) {
    if (!MODES.includes(next)) return;
    mode.value = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // 写不进只影响下次启动恢复, 本次会话仍生效
    }
  }

  /** 顶栏一键切换: 按当前生效状态翻转到反色(显式模式, 会脱离 auto 跟随) */
  function toggle() {
    setMode(isDark.value ? 'light' : 'dark');
  }

  return { mode, systemDark, isDark, init, setMode, toggle };
});
