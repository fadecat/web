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
        path: 'style-rotation',
        name: 'style-rotation',
        component: () => import('../pages/Rotation.vue'),
        meta: { title: '风格轮动', group: '市场' },
      },
    ],
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
