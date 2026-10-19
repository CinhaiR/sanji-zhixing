<script setup lang="ts">
// 首页 —— Hero + 三晋山河舆图（高德地图）
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useItineraryStore } from '@/stores/itinerary'
import { initShanxiMap, SHANXI_CITIES, type MapHandle } from '@/utils/amap'

const router = useRouter()
const store = useItineraryStore()

// 快速导航（保留：行驿日程、三晋方志、典藏线路；删除驿路谋士、华夏定制，因 Hero 已有直接入口）
const quickLinks = [
  { name: 'schedule', label: '行驿日程', char: '行' },
  { name: 'knowledge', label: '三晋方志', char: '三' },
  { name: 'preset', label: '典藏线路', char: '典' }
]

// ---- 三晋山河舆图（高德地图）----
const mapRef = ref<HTMLDivElement | null>(null)
/** 地图是否初始化成功；失败时回退到示意舆图，保证页面可用 */
const mapReady = ref(false)
const mapError = ref('')
let handle: MapHandle | null = null

async function setupMap() {
  const container = mapRef.value
  if (!container) return
  try {
    handle = await initShanxiMap(container, store.currentPlan)
    mapReady.value = true
    mapError.value = ''
  } catch (e: any) {
    // 加载失败不阻断页面：展示示意舆图与失败原因
    mapReady.value = false
    mapError.value = e?.message ?? '地图加载失败'
    console.warn('[舆图] 高德地图初始化失败：', e)
  }
}

/** 行程变化后刷新高亮与动线 */
async function refreshMap() {
  if (!handle || !mapRef.value) return
  handle.destroy()
  handle = null
  await nextTick()
  await setupMap()
}

onMounted(setupMap)

// 行程变化（新生成 / 载入历史）后刷新高亮与动线
watch(
  () => (store.currentPlan.destinations ?? []).join(','),
  () => {
    if (mapReady.value) void refreshMap()
  }
)

onUnmounted(() => {
  // 销毁地图实例，释放资源
  handle?.destroy()
  handle = null
})

defineExpose({ refreshMap })
</script>

<template>
  <div class="space-y-12 pb-16">
    <!-- ===== 首页 Hero ===== -->
    <section class="relative overflow-hidden rounded-2xl ink-border-double bg-gradient-to-br from-paper-card to-paper-base p-6 sm:p-12 text-center">
      <div class="absolute inset-0 opacity-[0.04] pointer-events-none" style="background-image: radial-gradient(circle at 20% 30%, #A83232 0, transparent 40%), radial-gradient(circle at 80% 70%, #6B8E7F 0, transparent 40%)"></div>
      <div class="relative z-10">
        <div class="seal-stamp inline-block px-3 py-1 rounded text-sm mb-4">三晋智行</div>
        <!-- 手机端收小字号并收紧字距，避免「文旅定制」被折成孤行 -->
        <h1 class="text-3xl sm:text-5xl font-calligraphy text-ink-dark mb-3 leading-tight">
          表里山河 · 文旅定制
        </h1>
        <p class="text-sandalwood-dark font-serif text-[13px] sm:text-base max-w-2xl mx-auto leading-relaxed">
          以三晋方志为底，大模型为笔，为您编排古建、佛艺、晋商、非遗、山川、风味的通关卷宗。
        </p>
        <div class="flex flex-col sm:flex-row flex-wrap items-center justify-center gap-3 mt-6">
          <button
            @click="router.push({ name: 'planner' })"
            class="w-full sm:w-auto px-6 py-2.5 bg-cinnabar hover:bg-cinnabar-light text-white font-serif rounded-lg transition"
          >
            开始定制行程
          </button>
          <button
            @click="router.push({ name: 'agent' })"
            class="w-full sm:w-auto px-6 py-2.5 border border-cinnabar text-cinnabar hover:bg-cinnabar hover:text-white font-serif rounded-lg transition"
          >
            咨询驿路谋士
          </button>
        </div>
      </div>

      <!-- 快速导航卡片（仅保留行驿日程、三晋方志、典藏线路） -->
      <div class="relative z-10 grid grid-cols-3 gap-2 sm:gap-3 mt-8 sm:mt-10 max-w-3xl mx-auto">
        <button
          v-for="link in quickLinks"
          :key="link.name"
          @click="router.push({ name: link.name })"
          class="bg-paper-card ink-border-thin rounded-lg p-3 sm:p-4 hover:shadow-md hover:border-cinnabar/40 transition group"
        >
          <div class="seal-stamp w-8 h-8 rounded-full text-[10px] mx-auto mb-2">{{ link.char }}</div>
          <div class="text-xs sm:text-sm font-serif text-ink-dark group-hover:text-cinnabar transition leading-tight">
            {{ link.label }}
          </div>
        </button>
      </div>
    </section>

    <!-- ===== 三晋山河舆图（高德地图） ===== -->
    <section>
      <div class="flex items-center justify-between mb-3 sm:mb-4 gap-2">
        <div class="flex items-center gap-2 sm:gap-3 min-w-0">
          <span class="seal-stamp px-2 py-1 text-xs rounded shrink-0">舆图</span>
          <h2 class="text-xl sm:text-2xl font-calligraphy text-ink-dark truncate">三晋山河舆图</h2>
        </div>
        <span class="text-xs text-sandalwood-dark font-serif shrink-0">高德地图</span>
      </div>

      <!-- 手机端地图矮一些，避免一屏只看到地图；桌面端保持 420px -->
      <div
        ref="mapRef"
        class="bg-paper-card ink-border-thin rounded-lg shadow-scroll relative overflow-hidden h-[280px] sm:h-[420px]"
      >
        <!-- 地图未就绪时的示意舆图（加载中或加载失败） -->
        <div v-if="!mapReady" class="absolute inset-0 bg-gradient-to-br from-paper-base to-paper-card">
          <div class="absolute inset-0 opacity-10" style="background-image: repeating-linear-gradient(45deg, #A83232 0, #A83232 1px, transparent 1px, transparent 20px)"></div>
          <svg viewBox="0 0 600 400" class="w-full h-full">
            <path d="M 300 50 Q 380 80 400 150 Q 420 220 380 290 Q 360 350 300 360 Q 240 350 220 290 Q 180 220 200 150 Q 220 80 300 50 Z"
              fill="none" stroke="#A83232" stroke-width="2" stroke-dasharray="6 3" opacity="0.6"/>
            <path d="M 420 100 Q 430 200 410 320" fill="none" stroke="#6B8E7F" stroke-width="1.5" opacity="0.4"/>
            <text x="435" y="200" font-family="serif" font-size="10" fill="#6B8E7F" opacity="0.6">太行山脉</text>
            <path d="M 180 120 Q 170 220 190 310" fill="none" stroke="#6B8E7F" stroke-width="1.5" opacity="0.4"/>
            <text x="130" y="200" font-family="serif" font-size="10" fill="#6B8E7F" opacity="0.6">吕梁山脉</text>
            <g v-for="(c, i) in SHANXI_CITIES" :key="c.name" font-family="serif" font-size="12" fill="#6b4c3b">
              <circle :cx="120 + (i % 4) * 120" :cy="120 + Math.floor(i / 4) * 90" r="3" fill="#6B8E7F"/>
              <text :x="120 + (i % 4) * 120 + 8" :y="120 + Math.floor(i / 4) * 90 + 4">{{ c.name }}</text>
            </g>
          </svg>
          <div class="absolute bottom-3 left-3 text-xs font-serif bg-paper-card/85 px-2 py-1 rounded text-sandalwood-dark">
            <template v-if="mapError">
              ⚠ 高德地图加载失败：{{ mapError }}
            </template>
            <template v-else>正在加载高德地图……</template>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
