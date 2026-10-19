<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useItineraryStore } from '@/stores/itinerary'
import { useAuthStore } from '@/stores/auth'
import { useAutoUpdate } from '@/composables/useAutoUpdate'
import SealIcon from '@/components/SealIcon.vue'

const route = useRoute()
const router = useRouter()
const store = useItineraryStore()
const auth = useAuthStore()

// 检测到前端有新构建时自动更新页面，避免「要手动刷新才能看到新内容」
useAutoUpdate()

/** 导航项：char 为印章单字，用于紧凑展示 */
const navItems = [
  { name: 'home', label: '首页', char: '首' },
  { name: 'agent', label: '驿路谋士', char: '问' },
  { name: 'planner', label: '行程祈定册', char: '定' },
  { name: 'schedule', label: '行驿日程', char: '程' },
  { name: 'knowledge', label: '三晋方志', char: '志' },
  { name: 'preset', label: '典藏线路', char: '线' }
]

/**
 * 移动端主导航 = 底部标签栏，**六项全放**。
 *
 * 早先移动端同时有「汉堡抽屉」和「底部标签栏」，两者内容重复：
 * 抽屉列 首页/驿路谋士/…，底部栏也列同样几项，用户面对两套入口。
 * 现已移除汉堡与抽屉，只保留底部标签栏（拇指可达、常驻可见、能指示当前位置）。
 * 抽屉里原有的「我的通关文牒」与登录/登出，顶部栏本来就有，不会丢失。
 */
const bottomItems = navItems

const activeName = computed(() => route.name as string)
const showHistoryDrawer = ref(false)

/** 登录页为纯净页面，不展示导航栏/底部栏/页脚 */
const isLoginPage = computed(() => route.name === 'login')

function go(name: string) {
  router.push({ name })
}

function openHistoryDrawer() {
  store.loadHistory()
  showHistoryDrawer.value = true
}

function closeHistoryDrawer() {
  showHistoryDrawer.value = false
}

function removePlan(idx: number) {
  store.remove(idx)
}

function restorePlan(saved: typeof store.savedItineraries[number]) {
  store.restore(saved)
  showHistoryDrawer.value = false
  router.push({ name: 'schedule' })
}

function goLogin() {
  router.push({ name: 'login', query: { redirect: route.fullPath } })
}

function logout() {
  auth.logout()
  store.savedItineraries = []
  // 登出后跳转登录页
  router.push({ name: 'login' })
}

/**
 * 底部标签栏在**除登录页外的一切页面**都显示。
 *
 * 曾经对驿路谋士单独隐藏（理由：该页是满屏聊天布局，栏会挤占视口），
 * 但那等于把移动端唯一的主导航入口拿掉 —— 顶部导航是 `hidden lg:flex`（桌面专用），
 * 汉堡菜单又已移除，用户进了这一页就**没有任何办法去别的功能页**。
 * 正确做法是导航常驻，由页面扣掉 `--bottom-bar-h` 的高度（见 AgentView）。
 */
const showBottomBar = computed(() => !isLoginPage.value)

onMounted(() => {
  // 仅在已登录时加载历史行程（未登录会被路由守卫拦截到登录页）
  if (auth.isLoggedIn) {
    store.loadHistory()
  }
})
</script>

<template>
  <div class="min-h-screen bg-paper">
    <!-- ================= 顶部导航栏（登录页隐藏） ================= -->
    <header v-if="!isLoginPage" class="sticky top-0 z-40 bg-paper-card shadow-scroll border-b border-sandalwood/30">
      <div class="max-w-7xl mx-auto px-3 sm:px-6 py-2.5 sm:py-3 flex items-center justify-between gap-2">
        <!-- 左：品牌（可点击回首页 —— 任何页面都有一个兜底的退出入口） -->
        <button
          @click="go('home')"
          class="flex items-center gap-2 min-w-0 rounded hover:opacity-80 transition text-left"
          title="回到首页"
        >
          <SealIcon char="三晋" :size="34" />
          <span class="font-calligraphy text-lg sm:text-xl text-ink-dark truncate hidden min-[360px]:inline">
            三晋智行
          </span>
        </button>

        <!-- 中：桌面端横向导航（窄屏隐藏，导航统一走底部标签栏） -->
        <nav class="hidden lg:flex items-center gap-1 text-sm font-medium">
          <button
            v-for="item in navItems"
            :key="item.name"
            @click="go(item.name)"
            :class="
              activeName === item.name
                ? 'bg-cinnabar text-white shadow-sm'
                : 'text-ink hover:text-cinnabar hover:bg-paper-dark/40'
            "
            class="px-3.5 py-1.5 rounded transition-all duration-200 whitespace-nowrap"
          >
            {{ item.label }}
          </button>
        </nav>

        <!-- 右：登录状态 + 我的通关文牒 -->
        <div class="flex items-center gap-2">
          <button
            @click="openHistoryDrawer"
            class="shrink-0 px-2.5 sm:px-3 py-1.5 rounded border border-sandalwood/50 text-sandalwood-dark hover:bg-sandalwood/10 transition flex items-center gap-1 text-xs sm:text-sm"
          >
            <span class="hidden sm:inline">我的通关文牒</span>
            <span class="sm:hidden">文牒</span>
            <span
              v-if="store.savedItineraries.length"
              class="text-[10px] min-w-[16px] h-4 px-1 rounded-full bg-cinnabar text-white flex items-center justify-center"
            >
              {{ store.savedItineraries.length }}
            </span>
          </button>

          <!--
            已登录：显示用户名 + 登出。
            窄屏只显示用户名本身 —— 375px 下「admin · 管理员」放不下会折成两行，把顶栏撑高；
            「·管理员」在 sm 及以上再显示。
          -->
          <template v-if="auth.isLoggedIn">
            <div class="flex items-center gap-1.5 px-2 sm:px-2.5 py-1.5 rounded bg-paper-dark/40 max-w-[6rem] sm:max-w-none overflow-hidden">
              <span class="text-xs text-ink-light font-serif whitespace-nowrap truncate">
                {{ auth.user?.username }}
                <span v-if="auth.user?.is_admin" class="text-cinnabar hidden sm:inline">·管理员</span>
              </span>
            </div>
            <button
              @click="logout"
              class="shrink-0 px-2.5 sm:px-3 py-1.5 rounded text-xs sm:text-sm text-ink-light hover:text-cinnabar hover:bg-paper-dark/40 transition font-serif"
            >
              登出
            </button>
          </template>
          <!-- 未登录：显示登录按钮 -->
          <button
            v-else
            @click="goLogin"
            class="shrink-0 px-2.5 sm:px-3 py-1.5 rounded bg-cinnabar hover:bg-cinnabar-light text-white text-xs sm:text-sm transition font-serif"
          >
            登录
          </button>
        </div>
      </div>
    </header>

    <!-- ================= 页面内容 ================= -->
    <main :class="isLoginPage ? '' : 'max-w-7xl mx-auto px-3 sm:px-6 py-4 sm:py-6'">
      <router-view />
    </main>

    <!-- ================= 移动端底部标签栏（登录页隐藏） ================= -->
    <nav
      v-if="showBottomBar"
      class="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-paper-card border-t border-sandalwood/30 shadow-[0_-2px_12px_rgba(44,36,25,0.08)]"
      style="padding-bottom: env(safe-area-inset-bottom)"
      aria-label="主导航"
    >
      <div class="grid grid-cols-6">
        <button
          v-for="item in bottomItems"
          :key="item.name"
          @click="go(item.name)"
          class="flex flex-col items-center gap-0.5 py-1.5 transition"
          :class="activeName === item.name ? 'text-cinnabar' : 'text-ink-light'"
        >
          <SealIcon
            :char="item.char"
            :size="26"
            shape="full"
            :class="activeName === item.name ? '' : 'opacity-45'"
          />
          <span class="font-serif text-[10px] leading-none truncate max-w-full px-0.5">{{ item.label }}</span>
        </button>
      </div>
    </nav>

    <!-- ================= 历史文牒抽屉（登录页隐藏） ================= -->
    <div
      v-if="showHistoryDrawer && !isLoginPage"
      class="fixed inset-0 z-50 overflow-hidden bg-ink-dark/50 backdrop-blur-sm flex justify-end"
      @click.self="closeHistoryDrawer"
    >
      <div class="w-full sm:max-w-md bg-paper-base h-full shadow-2xl p-4 sm:p-6 flex flex-col justify-between overflow-y-auto">
        <div>
          <div class="flex items-center justify-between border-b border-sandalwood/30 pb-3 mb-4">
            <div class="flex items-center gap-2 min-w-0">
              <SealIcon char="簿录" :size="26" />
              <h3 class="font-bold font-serif text-base sm:text-lg text-ink-dark truncate">
                我的通关文牒（历史）
              </h3>
            </div>
            <button
              @click="closeHistoryDrawer"
              class="shrink-0 p-1 text-ink-light hover:text-cinnabar"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>

          <div v-if="store.savedItineraries.length === 0" class="text-center py-12 text-ink-light text-xs font-serif">
            暂无已存文牒。可在规划后点击「保存此案」。
          </div>

          <div v-else class="space-y-3">
            <div
              v-for="(saved, sIdx) in store.savedItineraries"
              :key="saved.id ?? sIdx"
              class="bg-paper-card p-3.5 rounded border border-sandalwood/30 hover:border-cinnabar/40 transition"
            >
              <div class="flex items-start justify-between gap-2">
                <h4 class="font-bold text-sm text-ink-dark font-serif">{{ saved.title }}</h4>
                <button
                  @click="removePlan(sIdx)"
                  class="shrink-0 text-xs text-sandalwood hover:text-cinnabar"
                >
                  删除
                </button>
              </div>
              <p class="text-xs text-sandalwood-dark mt-1">保存时刻：{{ saved.savedAt }}</p>
              <div class="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs pt-2 border-t border-dashed border-sandalwood/20">
                <span class="text-ink-light">{{ saved.days.length }} 日游 · {{ saved.destinations.join('、') }}</span>
                <button @click="restorePlan(saved)" class="text-cinnabar font-bold hover:underline">
                  载入此案 ➔
                </button>
              </div>
            </div>
          </div>
        </div>

        <div class="pt-4 border-t border-sandalwood/30 text-center">
          <p class="text-[11px] text-sandalwood-dark font-serif">本地存储安全保管 · 随时翻检</p>
        </div>
      </div>
    </div>

    <!-- ================= 页脚（登录页隐藏） ================= -->
    <footer
      v-if="!isLoginPage"
      class="border-t border-sandalwood/20 pt-4 text-center text-xs text-sandalwood-dark font-serif
             pb-[calc(4.5rem+env(safe-area-inset-bottom))] lg:pb-4"
    >
      © 三晋智行 · 表里山河 文旅定制
    </footer>
  </div>
</template>
