import { createRouter, createWebHistory } from 'vue-router';
import AppLayout from '../layouts/AppLayout.vue';

const routes = [
  {
    path: '/',
    component: AppLayout,
    children: [
      {
        path: '',
        redirect: '/cb-list',
      },
      {
        path: 'cb-market',
        name: 'cb-market',
        component: () => import('../pages/CbMarket.vue'),
        meta: { title: '转债市场', group: '转债' },
      },
      {
        path: 'cb-list',
        name: 'cb-list',
        component: () => import('../pages/Bonds.vue'),
        meta: { title: '转债筛选', group: '转债' },
      },
      {
        path: 'factors',
        name: 'factors',
        component: () => import('../pages/Factors.vue'),
        meta: { title: '选债因子', group: '转债' },
      },
      {
        path: 'valuation',
        name: 'valuation',
        component: () => import('../pages/ValuationList.vue'),
        meta: { title: '市场估值', group: '市场' },
      },
      // 估值详情: 列表页点击某只指数进入, code 为指数代码(如 930955)
      {
        path: 'valuation/:code',
        name: 'valuation-detail',
        component: () => import('../pages/ValuationDetail.vue'),
        meta: { title: '估值详情', group: '市场' },
      },
      {
        path: 'style-rotation',
        name: 'style-rotation',
        component: () => import('../pages/Rotation.vue'),
        meta: { title: '风格轮动', group: '市场' },
      },
      {
        path: 'status',
        name: 'status',
        component: () => import('../pages/DataStatus.vue'),
        meta: { title: '数据状态', group: '系统' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('../pages/Settings.vue'),
        meta: { title: '系统设置', group: '系统' },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
