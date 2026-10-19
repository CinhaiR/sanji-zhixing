import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/home' },
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true }
  },
  {
    path: '/home',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '首页' }
  },
  {
    path: '/agent',
    name: 'agent',
    component: () => import('@/views/AgentView.vue'),
    meta: { title: '驿路谋士' }
  },
  {
    path: '/planner',
    name: 'planner',
    component: () => import('@/views/PlannerView.vue'),
    meta: { title: '行程祈定册' }
  },
  {
    path: '/schedule',
    name: 'schedule',
    component: () => import('@/views/ScheduleView.vue'),
    meta: { title: '行驿日程' }
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '三晋方志' }
  },
  {
    path: '/preset',
    name: 'preset',
    component: () => import('@/views/PresetView.vue'),
    meta: { title: '典藏线路' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 全局路由守卫：未登录只能访问登录页
router.beforeEach((to) => {
  const auth = useAuthStore()
  // 标记为 public 的路由（登录页）无需登录
  if (to.meta.public) return true
  // 未登录 → 跳转登录页，并记录来源地址
  if (!auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  return true
})

router.afterEach((to) => {
  document.title = `三晋智行 · ${to.meta.title ?? ''}`
})

export default router
