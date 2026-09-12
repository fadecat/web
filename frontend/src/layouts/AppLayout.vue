<script setup>
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';
import { useThemeStore } from '../stores/theme';

const route = useRoute();
const theme = useThemeStore();
const collapsed = ref(false);
const mobileOpen = ref(false);

// 简易移动端判定: <768px 视为手机, 侧边栏改为抽屉
const isMobile = () => window.innerWidth < 768;

const menus = [
  {
    group: '股票',
    items: [
      { path: '/stock-dividend', title: '高股息', icon: '💰' },
    ],
  },
  {
    group: '可转债',
    items: [
      { path: '/cb-market', title: '转债市场', icon: '📈' },
      { path: '/factors', title: '转债选债', icon: '⚙️' },
    ],
  },
  {
    group: '市场分析',
    items: [
      { path: '/valuation', title: '市场估值', icon: '📊' },
      { path: '/style-rotation', title: '风格轮动', icon: '🔄' },
    ],
  },
  {
    group: '系统',
    items: [
      { path: '/status', title: '数据管理', icon: '🩺' },
      { path: '/settings', title: '系统设置', icon: '⚙️' },
    ],
  },
];

const currentTitle = computed(() => route.meta.title || '');

// 详情页(/valuation/930955)也要让父级菜单(/valuation)保持高亮,
// 否则点进详情后侧栏选中态丢失, 看不出自己在哪个板块
const isMenuActive = (item) =>
  route.path === item.path || route.path.startsWith(`${item.path}/`);

function onMenuClick(item) {
  if (item.disabled) return;
  if (isMobile()) mobileOpen.value = false; // 手机点完菜单收起抽屉
}
</script>

<template>
  <div class="app-layout">
    <!-- 移动端遮罩 -->
    <div
      v-if="mobileOpen"
      class="mobile-mask"
      @click="mobileOpen = false"
    />

    <!-- 侧边栏: 桌面常驻 / 手机抽屉 -->
    <aside
      class="sidebar"
      :class="{ collapsed, 'mobile-open': mobileOpen }"
    >
      <div class="logo">
        <span class="logo-icon">📈</span>
        <span v-if="!collapsed || mobileOpen" class="logo-text">市场数据平台</span>
      </div>
      <nav class="menu">
        <div v-for="group in menus" :key="group.group" class="menu-group">
          <div v-if="!collapsed || mobileOpen" class="menu-group-title">{{ group.group }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.disabled ? '' : item.path"
            class="menu-item"
            :class="{ active: isMenuActive(item), disabled: item.disabled }"
            @click="onMenuClick(item)"
          >
            <span class="menu-icon">{{ item.icon }}</span>
            <span v-if="!collapsed || mobileOpen" class="menu-text">{{ item.title }}</span>
          </router-link>
        </div>
      </nav>
      <div v-if="!mobileOpen" class="collapse-btn" @click="collapsed = !collapsed">
        <span v-if="!collapsed">⟨ 收起</span>
        <span v-else>⟩</span>
      </div>
    </aside>

    <!-- 主内容 -->
    <div class="main">
      <header class="header">
        <!-- 手机汉堡按钮 -->
        <button class="hamburger" @click="mobileOpen = !mobileOpen">☰</button>
        <h2 class="page-title">{{ currentTitle }}</h2>
        <!-- 一键深浅切换: 图标示意点击后的目标态 -->
        <button
          class="theme-toggle"
          :title="theme.isDark ? '切换到浅色模式' : '切换到深色模式'"
          @click="theme.toggle()"
        >{{ theme.isDark ? '☀️' : '🌙' }}</button>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100%;
}

/* ---------- 移动端遮罩 ---------- */
.mobile-mask {
  display: none;
}

.sidebar {
  width: 220px;
  background: var(--sidebar-bg);
  color: var(--sidebar-text);
  display: flex;
  flex-direction: column;
  transition: width 0.2s;
  flex-shrink: 0;
}

.sidebar.collapsed {
  width: 60px;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid #374151;
  gap: 10px;
  overflow: hidden;
  white-space: nowrap;
}

.logo-icon {
  font-size: 20px;
}

.logo-text {
  font-size: 15px;
  font-weight: 600;
  color: #e5e7eb;
}

.menu {
  flex: 1;
  overflow-y: auto;
  padding: 12px 0;
}

.menu-group-title {
  padding: 8px 18px 4px;
  font-size: 12px;
  color: #6b7280;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  color: var(--sidebar-text);
  text-decoration: none;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s;
  white-space: nowrap;
  overflow: hidden;
}

.menu-item:hover {
  background: #374151;
}

.menu-item.active {
  background: var(--sidebar-active);
  color: #fff;
}

.menu-item.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.menu-icon {
  width: 20px;
  text-align: center;
  flex-shrink: 0;
}

.collapse-btn {
  height: 44px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-top: 1px solid #374151;
  color: #6b7280;
  font-size: 13px;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  overflow: hidden;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.header {
  height: 56px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  padding: 0 24px;
  flex-shrink: 0;
  gap: 10px;
}

.hamburger {
  display: none;
  border: none;
  background: none;
  font-size: 20px;
  cursor: pointer;
  padding: 4px 8px;
  color: #303133;
}

.page-title {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.theme-toggle {
  margin-left: auto;
  border: none;
  background: none;
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  padding: 4px 8px;
}

/* ---------- 深色模式(骨架期最小覆盖, token 化阶段统一整理) ---------- */
html.dark .header {
  background: var(--el-bg-color);
  border-bottom-color: var(--el-border-color);
}

html.dark .hamburger,
html.dark .page-title {
  color: var(--el-text-color-primary);
}

/* 顶部留白用 margin 而非 padding: sticky 元素相对滚动容器的内边距盒顶缘吸顶,
   padding-top 会把吸顶位置顶下 20px, 滚动的行从表头上方穿出(漏风);
   margin 在滚动容器之外, 行永远进不了这条带子, 表头与容器顶缘严丝合缝 */
.content {
  flex: 1;
  overflow-y: auto;
  margin-top: 20px;
  padding: 0 24px 20px;
}

/* ---------- 移动端适配 ---------- */
@media (max-width: 767px) {
  .mobile-mask {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.45);
    z-index: 99;
  }

  .sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 100;
    width: 220px !important;
    transform: translateX(-100%);
    transition: transform 0.25s;
  }

  .sidebar.mobile-open {
    transform: translateX(0);
  }

  .hamburger {
    display: block;
  }

  .header {
    padding: 0 12px;
  }

  .content {
    margin-top: 12px;
    padding: 0 12px 12px;
  }
}
</style>
