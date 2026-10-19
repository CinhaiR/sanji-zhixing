<script setup lang="ts">
// 典藏线路 —— 官方红色线路 + 经典主题线路策展
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { loadPresets } from '@/api'
import { useItineraryStore } from '@/stores/itinerary'
import type { PresetRoute } from '@/types'

const router = useRouter()
const store = useItineraryStore()

const routes = ref<PresetRoute[]>([])
const categories = ref<string[]>(['全部'])
const activeCategory = ref('全部')
const isLoading = ref(true)
const errorMsg = ref('')
/** 展开「为何选这条路线」的线路 id */
const expanded = ref<string | null>(null)

const filtered = computed(() =>
  activeCategory.value === '全部'
    ? routes.value
    : routes.value.filter((r) => r.category === activeCategory.value)
)

/** 分类角标配色 */
function badgeClass(cat: string) {
  switch (cat) {
    case '红色记忆':
      return 'bg-cinnabar/10 text-cinnabar border-cinnabar/40'
    case '古建营造':
      return 'bg-sandalwood/15 text-sandalwood-dark border-sandalwood/40'
    case '博物馆藏':
      return 'bg-celadon/15 text-celadon-dark border-celadon/40'
    case '晋商文化':
      return 'bg-sandalwood-dark/10 text-sandalwood-dark border-sandalwood-dark/30'
    case '佛教艺术':
      return 'bg-paper-base text-ink border-sandalwood/40'
    default:
      return 'bg-paper-base text-ink-light border-sandalwood/30'
  }
}

function toggleWhy(id: string) {
  expanded.value = expanded.value === id ? null : id
}

/** 套用线路：写入待填表单并跳转到行程祈定册 */
function useRoute(r: PresetRoute) {
  store.applyPresetDraft({
    destinations: [...r.cities],
    days: r.days,
    companion: r.companion,
    pace: r.pace,
    themes: [...r.themes],
    presetName: r.name
  })
  router.push({ name: 'planner' })
}

async function fetchRoutes() {
  isLoading.value = true
  errorMsg.value = ''
  try {
    const res = await loadPresets()
    routes.value = res.routes
    categories.value = res.categories
  } catch {
    errorMsg.value = '典藏线路加载失败，请确认后端服务已启动'
  } finally {
    isLoading.value = false
  }
}

onMounted(fetchRoutes)
</script>

<template>
  <section class="space-y-6">
    <div class="text-center max-w-2xl mx-auto mb-2">
      <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-2 inline-block">典藏</div>
      <h2 class="text-3xl font-calligraphy text-ink-dark">典藏线路</h2>
    </div>

    <!-- 分类筛选 -->
    <div
      v-if="!isLoading && !errorMsg"
      class="max-w-5xl mx-auto flex flex-wrap items-center justify-center gap-2"
    >
      <button
        v-for="cat in categories"
        :key="cat"
        @click="activeCategory = cat"
        :class="
          activeCategory === cat
            ? 'bg-cinnabar text-white border-cinnabar'
            : 'bg-paper-base text-ink border-sandalwood/40 hover:border-cinnabar/60'
        "
        class="px-3 py-1.5 rounded border text-xs font-serif transition"
      >
        {{ cat }}
      </button>
    </div>

    <!-- 加载中 -->
    <div v-if="isLoading" class="max-w-5xl mx-auto text-center py-16 text-ink-light font-serif">
      正在展卷……
    </div>

    <!-- 错误 -->
    <div
      v-else-if="errorMsg"
      class="max-w-5xl mx-auto text-xs font-serif text-cinnabar bg-cinnabar/5 border border-cinnabar/30 rounded px-3 py-2"
    >
      {{ errorMsg }}
    </div>

    <!-- 线路列表 -->
    <div v-else class="max-w-5xl mx-auto space-y-4">
      <article
        v-for="r in filtered"
        :key="r.id"
        class="bg-paper-card ink-border-thin rounded-lg p-4 sm:p-5 shadow-scroll"
      >
        <!-- 标题行 -->
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-lg sm:text-xl font-bold font-serif text-ink-dark">{{ r.name }}</h3>
              <span
                v-if="r.badge"
                class="text-[10px] font-serif border rounded px-1.5 py-0.5 bg-cinnabar text-white border-cinnabar"
              >
                {{ r.badge }}
              </span>
              <span
                class="text-[10px] font-serif border rounded px-1.5 py-0.5"
                :class="badgeClass(r.category)"
              >
                {{ r.category }}
              </span>
            </div>
            <p class="text-xs text-sandalwood-dark font-serif mt-1.5">{{ r.tagline }}</p>
          </div>
          <div class="text-right shrink-0">
            <div class="text-2xl font-calligraphy text-cinnabar leading-none">{{ r.days }}</div>
            <div class="text-[10px] text-sandalwood-dark font-serif mt-0.5">日行程</div>
          </div>
        </div>

        <!-- 动线 -->
        <p class="text-xs font-serif text-celadon-dark bg-celadon/10 border border-celadon/30 rounded px-2.5 py-1.5 mt-3">
          {{ r.route }}
        </p>

        <!-- 沿途看点 -->
        <div class="flex flex-wrap gap-1.5 mt-3">
          <span
            v-for="h in r.highlights"
            :key="h"
            class="text-[10px] font-serif bg-paper-base border border-sandalwood/25 rounded px-1.5 py-0.5 text-ink"
          >
            {{ h }}
          </span>
        </div>

        <!-- 适合人群 / 时节 -->
        <div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-sandalwood-dark font-serif mt-3">
          <span v-if="r.best_for">适合：{{ r.best_for }}</span>
          <span v-if="r.best_season">时节：{{ r.best_season }}</span>
        </div>

        <!-- 为何选这条路线 -->
        <div class="mt-3 border-t border-dashed border-sandalwood/30 pt-3">
          <button
            @click="toggleWhy(r.id)"
            class="flex items-center gap-1.5 text-xs font-serif text-cinnabar hover:text-cinnabar-light transition"
          >
            <span class="seal-stamp w-5 h-5 rounded-full text-[9px] shrink-0">缘</span>
            <span>为何选这条路线</span>
            <span class="text-[10px]">{{ expanded === r.id ? '▲ 收起' : '▼ 展开' }}</span>
          </button>
          <p
            v-if="expanded === r.id"
            class="text-xs sm:text-sm text-ink-dark font-serif leading-relaxed mt-2 bg-paper-base border-l-2 border-cinnabar/50 rounded-r px-3 py-2"
          >
            {{ r.why }}
          </p>
        </div>

        <!-- 操作 -->
        <div class="flex flex-wrap items-center justify-between gap-2 mt-4">
          <span class="text-[10px] text-ink-light font-serif">出处：{{ r.source }}</span>
          <button
            @click="useRoute(r)"
            class="px-4 py-2 bg-cinnabar hover:bg-cinnabar-light text-white text-xs font-serif rounded transition shadow"
          >
            用此线路定制 →
          </button>
        </div>
      </article>

      <p class="text-center text-[11px] text-ink-light font-serif pt-2">
        共 {{ filtered.length }} 条线路，均已按知识库校对府邑归属，可直接生成行程
      </p>
    </div>
  </section>
</template>
