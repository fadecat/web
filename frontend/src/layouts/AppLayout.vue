<script setup>
import { ref, computed } from 'vue';
import { useRoute } from 'vue-router';

const route = useRoute();
const collapsed = ref(false);

const menus = [
  {
    group: '可转债',
    items: [
      { path: '/cb-list', title: '转债筛选', icon: '💰' },
      { path: '/factors', title: '选债因子', icon: '⚙️' },
    ],
  },
  {
    group: '市场分析',
    items: [
      { path: '/valuation', title: '市场估值', icon: '📊', disabled: true },
      { path: '/style-rotation', title: '风格轮动', icon: '🔄', disabled: true },
    ],
  },
];

const currentTitle = computed(() => route.meta.title || '');
</script>

<template>
  <div class="app-layout">
    <!-- 侧边栏 -->
    <aside class="sidebar" :class="{ collapsed }">
      <div class="logo">
        <span class="logo-icon">📈</span>
        <span v-if="!collapsed" class="logo-text">市场数据平台</span>
      </div>
      <nav class="menu">
        <div v-for="group in menus" :key="group.group" class="menu-group">
          <div v-if="!collapsed" class="menu-group-title">{{ group.group }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.disabled ? '' : item.path"
            class="menu-item"
            :class="{ active: route.path === item.path, disabled: item.disabled }"
            @click.prevent="item.disabled && undefined"
          >
            <span class="menu-icon">{{ item.icon }}</span>
            <span v-if="!collapsed" class="menu-text">{{ item.title }}</span>
          </router-link>
        </div>
      </nav>
      <div class="collapse-btn" @click="collapsed = !collapsed">
        <span v-if="!collapsed">⟨ 收起</span>
        <span v-else>⟩</span>
      </div>
    </aside>

    <!-- 主内容 -->
    <div class="main">
      <header class="header">
        <h2 class="page-title">{{ currentTitle }}</h2>
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
}

.page-title {
  font-size: 17px;
  font-weight: 600;
  color: #303133;
}

.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}
</style>
