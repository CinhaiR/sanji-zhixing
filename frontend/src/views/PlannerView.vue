<script setup lang="ts">
// 行程祈定册（智能规划表单）
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useItineraryStore } from '@/stores/itinerary'

const store = useItineraryStore()
const router = useRouter()

const form = ref({
  destinations: [] as string[],
  days: 3,
  companion: '独自悠游',
  pace: '',
  themes: [] as string[]
})

const cityOptions = ['大同', '朔州', '忻州', '太原', '晋中', '吕梁', '阳泉', '长治', '晋城', '临汾', '运城']
const paceOptions = ['研学适中', '深度慢游', '紧凑打卡']
const themeOptions = ['古建营造', '佛教艺术', '晋商文化', '非遗民俗', '山川自然', '红色记忆', '博物馆藏']
const companionOptions = ['独自悠游', '双人同行', '亲子出游', '长者同行', '团队研学']

const errorMsg = ref('')
/** 安排不合理时的弹窗（区别于行内错误提示，强制用户重新选择） */
const showWarningModal = ref(false)
const warningMsg = ref('')
/** 从典藏线路套用时提示来源，让用户知道参数是被预填的 */
const presetNotice = ref('')

onMounted(() => {
  const draft = store.consumePresetDraft()
  if (!draft) return
  form.value.destinations = [...draft.destinations]
  form.value.days = draft.days
  form.value.companion = draft.companion
  form.value.pace = draft.pace
  form.value.themes = [...draft.themes]
  presetNotice.value = draft.presetName
    ? `已套用典藏线路「${draft.presetName}」，可按需调整后再生成。`
    : ''
})

function toggleCity(city: string) {
  const idx = form.value.destinations.indexOf(city)
  if (idx > -1) form.value.destinations.splice(idx, 1)
  else form.value.destinations.push(city)
}

function toggleTheme(theme: string) {
  const idx = form.value.themes.indexOf(theme)
  if (idx > -1) form.value.themes.splice(idx, 1)
  else form.value.themes.push(theme)
}

/**
 * 校验天数与府邑数是否相称。
 *
 * 每处府邑至少要摊到一日，否则必然有府邑被悄悄丢弃
 * （旧版就会「选 1 日游 4 城，最后只生成 1 城」）。
 */
function checkReasonable(): string {
  const cities = form.value.destinations
  if (cities.length === 0) return ''
  if (form.value.days < cities.length) {
    return (
      `所择 ${cities.length} 处府邑（${cities.join('、')}），行旅仅 ${form.value.days} 日，` +
      `每处府邑不足以从容驻足一日，恐难尽兴，亦失游赏之趣。` +
      `请或增其天数至 ${cities.length} 日以上，或减其府邑至 ${form.value.days} 处以内。`
    )
  }
  return ''
}

function openWarning(msg: string) {
  warningMsg.value = msg
  showWarningModal.value = true
}

async function generate() {
  errorMsg.value = ''
  if (form.value.destinations.length === 0) {
    errorMsg.value = '请至少择一处意向目的地府邑'
    return
  }
  if (!form.value.pace) {
    errorMsg.value = '请选择寻古节奏与意趣'
    return
  }
  // 本地先拦一道，避免白跑一次请求
  const warn = checkReasonable()
  if (warn) {
    openWarning(warn)
    return
  }
  try {
    await store.generate(form.value)
    router.push({ name: 'schedule' })
  } catch {
    // 后端同样会校验，作为第二道保险
    if (store.lastErrorCode === 'plan_unreasonable') {
      openWarning(store.lastError)
      return
    }
    errorMsg.value = store.lastError || '行程编排失败，请稍后重试'
  }
}
</script>

<template>
  <section class="space-y-6">
    <div class="text-center max-w-xl mx-auto mb-6">
      <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-2 inline-block">定制</div>
      <h2 class="text-3xl font-calligraphy text-ink-dark">行程祈定册</h2>
      <p class="text-xs text-ink-light mt-2 font-serif">填写意向，智旅 Agent 将依三晋方志为您编排通关卷宗</p>
    </div>

    <div class="max-w-3xl mx-auto bg-paper-card ink-border-double rounded-lg p-6 shadow-scroll space-y-6">
      <!-- 从典藏线路套用时提示来源 -->
      <div
        v-if="presetNotice"
        class="text-xs font-serif text-celadon-dark bg-celadon/10 border border-celadon/30 rounded px-3 py-2"
      >
        {{ presetNotice }}
      </div>

      <!-- 意向目的地 -->
      <div>
        <label class="block text-sm font-bold text-ink-dark mb-2 font-serif">意向目的地府邑</label>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="city in cityOptions"
            :key="city"
            @click="toggleCity(city)"
            :class="
              form.destinations.includes(city)
                ? 'bg-cinnabar text-white border-cinnabar'
                : 'bg-paper-base text-ink border-sandalwood/40'
            "
            class="px-3 py-1.5 rounded border text-sm transition"
          >
            {{ city }}
          </button>
        </div>
      </div>

      <!-- 天数 -->
      <div>
        <label class="block text-sm font-bold text-ink-dark mb-2 font-serif">行旅天数：{{ form.days }} 日</label>
        <input
          v-model.number="form.days"
          type="range"
          min="1"
          max="10"
          class="w-full accent-cinnabar"
        />
        <!-- 实时提示：每处府邑至少需一日，天数不足时提前告知 -->
        <p
          v-if="form.destinations.length > form.days"
          class="text-xs font-serif text-cinnabar mt-2"
        >
          所择 {{ form.destinations.length }} 处府邑，须至少 {{ form.destinations.length }} 日方得从容驻足。
        </p>
      </div>

      <!-- 同行人 -->
      <div>
        <label class="block text-sm font-bold text-ink-dark mb-2 font-serif">同行之人</label>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="c in companionOptions"
            :key="c"
            @click="form.companion = c"
            :class="
              form.companion === c
                ? 'bg-sandalwood-dark text-white border-sandalwood-dark'
                : 'bg-paper-base text-ink border-sandalwood/40'
            "
            class="px-3 py-1.5 rounded border text-sm transition"
          >
            {{ c }}
          </button>
        </div>
      </div>

      <!-- 节奏 -->
      <div>
        <label class="block text-sm font-bold text-ink-dark mb-2 font-serif">寻古节奏与意趣</label>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="pace in paceOptions"
            :key="pace"
            @click="form.pace = form.pace === pace ? '' : pace"
            :class="form.pace === pace ? 'bg-cinnabar text-white border-cinnabar' : 'bg-paper-base text-ink border-sandalwood/40'"
            class="px-3 py-1.5 rounded border text-sm transition"
          >
            {{ pace }}
          </button>
        </div>
      </div>

      <!-- 兴趣 -->
      <div>
        <label class="block text-sm font-bold text-ink-dark mb-2 font-serif">三晋意趣</label>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="theme in themeOptions"
            :key="theme"
            @click="toggleTheme(theme)"
            :class="
              form.themes.includes(theme)
                ? 'bg-celadon text-white border-celadon'
                : 'bg-paper-base text-ink border-sandalwood/40'
            "
            class="px-3 py-1.5 rounded border text-sm transition"
          >
            {{ theme }}
          </button>
        </div>
      </div>

      <!-- 错误提示 -->
      <div
        v-if="errorMsg"
        class="text-xs font-serif text-cinnabar bg-cinnabar/5 border border-cinnabar/30 rounded px-3 py-2"
      >
        {{ errorMsg }}
      </div>

      <!-- 提交 -->
      <button
        @click="generate"
        :disabled="store.isGenerating"
        class="w-full py-3 bg-cinnabar hover:bg-cinnabar-light text-white font-serif rounded-lg transition disabled:opacity-60"
      >
        {{ store.isGenerating ? '正在编排通关卷宗……' : '智能规划行程' }}
      </button>
    </div>

    <!-- 安排不合理：弹窗提醒，请用户重新选择 -->
    <div
      v-if="showWarningModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-ink-dark/50 px-4"
      @click.self="showWarningModal = false"
    >
      <div class="bg-paper-card ink-border-double rounded-lg shadow-scroll max-w-md w-full p-6 space-y-4">
        <div class="text-center">
          <div class="seal-stamp w-10 h-10 rounded-full text-sm mx-auto mb-3">酌</div>
          <h3 class="text-xl font-calligraphy text-ink-dark">行程未洽，请再酌之</h3>
        </div>
        <p class="text-sm text-ink-dark font-serif leading-relaxed">{{ warningMsg }}</p>
        <button
          @click="showWarningModal = false"
          class="w-full py-2.5 bg-cinnabar hover:bg-cinnabar-light text-white font-serif rounded-lg transition"
        >
          重新选择
        </button>
      </div>
    </div>
  </section>
</template>
