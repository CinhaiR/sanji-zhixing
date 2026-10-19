<script setup lang="ts">
// 三晋方志 —— RAG 知识库浏览与检索
import { computed, onMounted, ref, watch } from 'vue'
import {
  loadKnowledgeCities,
  loadKnowledgeItems,
  loadKnowledgeStats,
  reloadKnowledge,
  searchKnowledge
} from '@/api'
import type { KnowledgeCity, KnowledgeItem, KnowledgeStats } from '@/types'
import { BAIKE_LINKS } from '@/data/baikeLinks'

const cities = ref<KnowledgeCity[]>([])
const stats = ref<KnowledgeStats | null>(null)
const items = ref<KnowledgeItem[]>([])
const isLoading = ref(false)
const isReloading = ref(false)
const errorMsg = ref('')
const noticeMsg = ref('')

const keyword = ref('')
const activeCity = ref<string>('') // '' = 全部
const activeKind = ref<string>('') // '' = 全部

const kindTabs = [
  { value: '', label: '全部' },
  { value: 'attraction', label: '景点' },
  { value: 'food', label: '特产' }
]

const activeCityName = computed(() => {
  if (!activeCity.value) return '三晋全域'
  return cities.value.find((c) => c.id === activeCity.value)?.name ?? '三晋全域'
})

const activeCityIntro = computed(() => {
  if (!activeCity.value) return '古建、佛艺、晋商、非遗、山川、风味，皆入方志。'
  return cities.value.find((c) => c.id === activeCity.value)?.intro ?? ''
})

/**
 * 生成该景点的百度百科链接。
 *
 * 1. **优先用人工核对的词条直链**（`BAIKE_LINKS`）：部分词条同名多义项
 *    （「雁门关」有多个义项、「南禅寺」无锡那座更知名），
 *    只按名称检索可能落到别的义项，直链才能保证跳对。
 * 2. 其余按景点名检索，并去掉括号注释
 *    （如「应县木塔（佛宫寺释迦塔）」→「应县木塔」）。
 * 3. 用「搜索式」URL 而非拼 `/item/<词条>`：即使百度没有完全同名词条，
 *    也会落到该词的搜索结果页，不会给用户死链。
 */
function baikeUrl(name: string): string {
  const exact = BAIKE_LINKS[name]
  if (exact) return exact
  const cleaned =
    (name || '')
      .replace(/[（(][^）)]*[）)]/g, '')
      .replace(/\s+/g, '')
      .trim() || name
  return `https://baike.baidu.com/search/word?word=${encodeURIComponent(cleaned)}`
}

/** 当前视图按「景点 / 特产」分区展示 */
const grouped = computed(() => {
  const attractions = items.value.filter((i) => i.kind === 'attraction')
  const foods = items.value.filter((i) => i.kind === 'food')
  return { attractions, foods }
})

async function fetchItems() {
  isLoading.value = true
  errorMsg.value = ''
  try {
    if (keyword.value.trim()) {
      const res = await searchKnowledge(keyword.value.trim(), {
        city: activeCity.value || undefined,
        kind: activeKind.value || undefined,
        limit: 60
      })
      items.value = res.items
    } else {
      items.value = await loadKnowledgeItems({
        city: activeCity.value || undefined,
        kind: activeKind.value || undefined,
        limit: 200
      })
    }
  } catch (e) {
    errorMsg.value = '方志检索失败，请确认后端服务已启动（http://localhost:8000）'
    items.value = []
  } finally {
    isLoading.value = false
  }
}

let searchTimer: number | undefined
watch([keyword, activeCity, activeKind], () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(fetchItems, 250)
})

async function init() {
  try {
    const [c, s] = await Promise.all([loadKnowledgeCities(), loadKnowledgeStats()])
    cities.value = c
    stats.value = s
  } catch (e) {
    errorMsg.value = '无法连接后端，请先启动 FastAPI 服务'
  }
  await fetchItems()
}

/**
 * 重新导入种子数据。
 *
 * 说明：统计条上的「↻ 重载种子数据」按钮已按需求移除（面向演示，不暴露管理操作），
 * 但函数保留 —— 需要时把按钮加回统计条即可；
 * 也可以直接调接口：`POST /api/knowledge/reload`，或跑
 * `python scripts/import_knowledge.py`。
 */
async function handleReload() {
  isReloading.value = true
  noticeMsg.value = ''
  try {
    const res = await reloadKnowledge()
    noticeMsg.value = `已重新导入：景点 ${res.attractions} 条、特产 ${res.foods} 条`
    await init()
  } catch (e) {
    errorMsg.value = '重新导入失败，请检查 data/knowledge_seed.json 格式'
  } finally {
    isReloading.value = false
  }
}

function confidenceLabel(level: string) {
  return level === 'high' ? '确凿' : '待校'
}

onMounted(init)
</script>

<template>
  <section class="space-y-6">
    <!-- 标题 -->
    <div class="text-center max-w-2xl mx-auto mb-2">
      <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-2 inline-block">方志</div>
      <h2 class="text-2xl sm:text-3xl font-calligraphy text-ink-dark">三晋文旅方志录</h2>
    </div>

    <!-- 统计条 -->
    <div
      v-if="stats"
      class="max-w-5xl mx-auto flex flex-wrap items-center justify-center gap-x-4 sm:gap-x-6 gap-y-2 text-xs text-ink-light font-serif bg-paper-card ink-border-thin rounded-lg px-3 sm:px-4 py-3 shadow-scroll"
    >
      <span>府邑 <b class="text-cinnabar text-sm">{{ stats.cities }}</b></span>
      <span>景点 <b class="text-cinnabar text-sm">{{ stats.attractions }}</b></span>
      <span>特产 <b class="text-cinnabar text-sm">{{ stats.foods }}</b></span>
    </div>

    <div
      v-if="noticeMsg"
      class="max-w-5xl mx-auto text-xs font-serif text-celadon-dark bg-celadon/10 border border-celadon/30 rounded px-3 py-2"
    >
      {{ noticeMsg }}
    </div>
    <div
      v-if="errorMsg"
      class="max-w-5xl mx-auto text-xs font-serif text-cinnabar bg-cinnabar/5 border border-cinnabar/30 rounded px-3 py-2"
    >
      {{ errorMsg }}
    </div>

    <!-- 检索栏 -->
    <div class="max-w-5xl mx-auto bg-paper-card ink-border-double rounded-lg p-3 sm:p-4 shadow-scroll space-y-3">
      <div class="flex flex-col sm:flex-row gap-2 sm:gap-3 sm:items-center">
        <input
          v-model="keyword"
          type="search"
          placeholder="检索景点、特产、风味……（如：木塔 / 牛肉 / 凉粉）"
          class="w-full sm:flex-1 min-w-0 px-3 py-2 text-[16px] sm:text-sm font-serif bg-paper-base border border-sandalwood/40 rounded focus:outline-none focus:border-cinnabar"
        />
        <div class="flex items-center gap-1 shrink-0">
          <button
            v-for="tab in kindTabs"
            :key="tab.value"
            @click="activeKind = tab.value"
            :class="
              activeKind === tab.value
                ? 'bg-cinnabar text-white border-cinnabar'
                : 'bg-paper-base text-ink border-sandalwood/40 hover:border-cinnabar/50'
            "
            class="flex-1 sm:flex-none px-3 py-1.5 rounded border text-xs font-serif transition"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <!-- 府邑筛选 -->
      <div class="flex flex-wrap gap-1.5 sm:gap-2">
        <button
          @click="activeCity = ''"
          :class="
            activeCity === ''
              ? 'bg-celadon text-white border-celadon'
              : 'bg-paper-base text-ink border-sandalwood/40 hover:border-celadon/60'
          "
          class="px-2.5 py-1 rounded border text-xs font-serif transition"
        >
          全部府邑
        </button>
        <button
          v-for="c in cities"
          :key="c.id"
          @click="activeCity = c.id"
          :class="
            activeCity === c.id
              ? 'bg-celadon text-white border-celadon'
              : 'bg-paper-base text-ink border-sandalwood/40 hover:border-celadon/60'
          "
          class="px-2.5 py-1 rounded border text-xs font-serif transition"
        >
          {{ c.name }}
          <span class="opacity-60">{{ c.attraction_count + c.food_count }}</span>
        </button>
      </div>
    </div>

    <!-- 当前范围说明 -->
    <div class="max-w-5xl mx-auto">
      <div class="flex items-baseline gap-3 border-b border-sandalwood/30 pb-2">
        <h3 class="text-2xl font-calligraphy text-cinnabar">{{ activeCityName }}</h3>
        <span class="text-xs text-ink-light font-serif">
          共 {{ items.length }} 条{{ keyword ? `（检索「${keyword}」）` : '' }}
        </span>
      </div>
      <p class="text-xs text-sandalwood-dark font-serif mt-2 leading-relaxed">{{ activeCityIntro }}</p>
    </div>

    <!-- 加载态 -->
    <div v-if="isLoading" class="max-w-5xl mx-auto text-center text-sm text-ink-light font-serif py-8">
      正在翻阅方志……
    </div>

    <!-- 空态 -->
    <div
      v-else-if="items.length === 0"
      class="max-w-5xl mx-auto bg-paper-card ink-border-thin rounded-lg p-8 text-center shadow-scroll"
    >
      <p class="text-ink-light font-serif text-sm">未找到相符的方志条目。</p>
      <p class="text-xs text-sandalwood-dark font-serif mt-2">
        可换个关键词，或切换上方府邑浏览他处方志。
      </p>
    </div>

    <template v-else>
      <!-- 景点 -->
      <div v-if="grouped.attractions.length" class="max-w-5xl mx-auto space-y-3">
        <div class="flex items-center gap-2">
          <span class="seal-stamp w-7 h-7 rounded-full text-[10px]">景</span>
          <h4 class="text-lg font-serif font-bold text-ink-dark">
            景点 · 古建佛艺
            <span class="text-xs text-ink-light">{{ grouped.attractions.length }}</span>
          </h4>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <article
            v-for="item in grouped.attractions"
            :key="item.id"
            class="bg-paper-card ink-border-thin rounded-lg p-4 shadow-scroll hover:border-cinnabar/40 transition"
          >
            <div class="flex items-start justify-between gap-2">
              <h5 class="font-bold text-ink-dark font-serif leading-snug">
                <a
                  :href="baikeUrl(item.name)"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="hover:text-cinnabar underline decoration-dotted decoration-sandalwood/50 underline-offset-2 transition"
                  :title="`在百度百科查看「${item.name}」`"
                >
                  {{ item.name }}<span class="text-[10px] text-sandalwood/70 ml-0.5">↗</span>
                </a>
              </h5>
              <span
                class="shrink-0 text-[10px] text-sandalwood-dark border border-sandalwood/40 rounded px-1.5 py-0.5"
              >
                {{ item.city_name }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 mt-2">
              <span
                v-if="item.item_type"
                class="text-[10px] text-cinnabar bg-cinnabar/10 rounded px-1.5 py-0.5"
              >
                {{ item.item_type }}
              </span>
              <span
                v-if="item.level"
                class="text-[10px] text-celadon bg-celadon/10 rounded px-1.5 py-0.5"
              >
                {{ item.level }}
              </span>
              <span
                v-if="item.confidence !== 'high'"
                class="text-[10px] text-sandalwood-dark bg-sandalwood/15 rounded px-1.5 py-0.5"
              >
                {{ confidenceLabel(item.confidence) }}
              </span>
            </div>
            <p class="text-xs text-ink-light leading-relaxed mt-2 font-serif">{{ item.desc }}</p>
            <p v-if="item.tips" class="text-[11px] text-sandalwood-dark mt-2 leading-relaxed">
              📖 {{ item.tips }}
            </p>
            <p v-if="item.aliases.length" class="text-[10px] text-ink-light/70 mt-2">
              别名：{{ item.aliases.join('、') }}
            </p>
          </article>
        </div>
      </div>

      <!-- 特产 -->
      <div v-if="grouped.foods.length" class="max-w-5xl mx-auto space-y-3 pt-4">
        <div class="flex items-center gap-2">
          <span class="seal-stamp w-7 h-7 rounded-full text-[10px]">味</span>
          <h4 class="text-lg font-serif font-bold text-ink-dark">
            特产 · 风味饮食
            <span class="text-xs text-ink-light">{{ grouped.foods.length }}</span>
          </h4>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <article
            v-for="item in grouped.foods"
            :key="item.id"
            class="bg-paper-card ink-border-thin rounded-lg p-4 shadow-scroll hover:border-celadon/50 transition"
          >
            <div class="flex items-start justify-between gap-2">
              <h5 class="font-bold text-ink-dark font-serif leading-snug">{{ item.name }}</h5>
              <span
                class="shrink-0 text-[10px] text-celadon-dark border border-celadon/40 rounded px-1.5 py-0.5"
              >
                产自 {{ item.city_name }}
              </span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 mt-2">
              <span
                v-if="item.item_type"
                class="text-[10px] text-celadon bg-celadon/10 rounded px-1.5 py-0.5"
              >
                {{ item.item_type }}
              </span>
              <span
                v-if="item.taste && item.taste !== '-'"
                class="text-[10px] text-sandalwood-dark bg-sandalwood/15 rounded px-1.5 py-0.5"
              >
                {{ item.taste }}
              </span>
              <span
                v-if="item.confidence !== 'high'"
                class="text-[10px] text-sandalwood-dark bg-sandalwood/15 rounded px-1.5 py-0.5"
              >
                {{ confidenceLabel(item.confidence) }}
              </span>
            </div>
            <p class="text-xs text-ink-light leading-relaxed mt-2 font-serif">{{ item.desc }}</p>
            <p v-if="item.tips" class="text-[11px] text-sandalwood-dark mt-2 leading-relaxed">
              🍜 {{ item.tips }}
            </p>
            <p
              v-if="item.traditional_in_names.length"
              class="text-[10px] text-celadon-dark mt-2"
              title="这些地区也有同源做法，行程中允许出现"
            >
              同源地区：{{ item.traditional_in_names.join('、') }}
            </p>
          </article>
        </div>
      </div>
    </template>
  </section>
</template>
