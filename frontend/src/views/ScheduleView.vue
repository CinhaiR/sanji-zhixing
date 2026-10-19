<script setup lang="ts">
// 行驿日程 —— 逐日行程、编辑与通关文牒导出
// 编辑模式的输入框统一 text-[16px] sm:text-sm：手机端 <16px 会被 iOS 聚焦放大，撑破版面
import { ref, computed } from 'vue'
import { useItineraryStore } from '@/stores/itinerary'
import type { Activity, DayPlan } from '@/types'

const store = useItineraryStore()
const currentDayIndex = ref(0)
const isEditing = ref(false)

/**
 * 必须用 computed 跟踪 store，**不能**写成 `const plan = store.currentPlan`。
 *
 * store 里的 generate / restore / clear 都是 `currentPlan.value = {新对象}`（整体替换），
 * 而 `const plan = store.currentPlan` 只捕获了**当时那个对象的引用**。
 * 于是「在行驿日程页上点『载入此案』或『删除此案』」时，store 已换新对象，
 * 组件却还在读旧对象 —— 页面毫无反应（点了像没点）。
 * 用 computed 才能始终指向 store 的当前值。
 */
const plan = computed(() => store.currentPlan)
const currentDay = computed(() => plan.value.days[currentDayIndex.value] ?? null)

// 编辑用的深拷贝，避免直接改 store
const editDraft = ref<DayPlan | null>(null)

function removePlan() {
  if (confirm('确定要删除当前行程吗？此操作不可撤销。')) {
    store.clear()
    currentDayIndex.value = 0
  }
}

/** 进入编辑模式：深拷贝当日行程作为草稿 */
function startEdit() {
  if (!currentDay.value) return
  editDraft.value = JSON.parse(JSON.stringify(currentDay.value))
  isEditing.value = true
}

/** 取消编辑 */
function cancelEdit() {
  editDraft.value = null
  isEditing.value = false
}

/** 保存编辑：将草稿写回 store */
function saveEdit() {
  if (!editDraft.value) return
  plan.value.days[currentDayIndex.value] = JSON.parse(JSON.stringify(editDraft.value))
  isEditing.value = false
  editDraft.value = null
  // 标记为手工修改，避免仍显示「大模型生成」
  store.markEdited()
}

/** 新增一个活动到草稿 */
function addActivity() {
  if (!editDraft.value) return
  editDraft.value.activities.push({
    time: '09:00 - 12:00',
    place: '新点位',
    type: '自由活动',
    desc: '请填写活动简介',
    tips: '请填写参访锦囊',
    foodPairing: ''
  })
}

/** 删除草稿中的某个活动 */
function removeActivity(idx: number) {
  if (!editDraft.value) return
  editDraft.value.activities.splice(idx, 1)
}

// 通关文牒弹窗
const showPassportModal = ref(false)

/** 是否有行程可操作 */
const hasPlan = computed(() => plan.value.days.length > 0)

/** 生成来源标注 */
const sourceBadge = computed(() => {
  switch (plan.value.source) {
    case 'cache':
      return { text: '缓存命中', cls: 'text-celadon bg-celadon/10 border-celadon/30' }
    case 'mock':
      return { text: '兜底行程', cls: 'text-sandalwood-dark bg-sandalwood/15 border-sandalwood/40' }
    case 'edited':
      return { text: '已手工修改', cls: 'text-cinnabar bg-cinnabar/10 border-cinnabar/30' }
    default:
      return { text: '大模型生成', cls: 'text-cinnabar bg-cinnabar/10 border-cinnabar/30' }
  }
})

/** 重新生成（绕过缓存） */
async function regenerate() {
  if (!store.lastForm) {
    alert('缺少原始定制参数，请回到「行程祈定册」重新填写。')
    return
  }
  if (!confirm('将忽略缓存重新调用大模型生成，可能耗时 20-60 秒，确定继续？')) return
  try {
    await store.regenerate(true)
    currentDayIndex.value = 0
    isEditing.value = false
    editDraft.value = null
  } catch (e) {
    alert(store.lastError || '重新生成失败')
  }
}

/** 打开通关文牒弹窗 */
function exportPassport() {
  if (plan.value.days.length === 0) {
    alert('暂无行程可导出')
    return
  }
  showPassportModal.value = true
}

/** 保存为 PDF 卷轴 */
async function saveAsPdf() {
  const el = document.querySelector('.passport-scroll') as HTMLElement
  if (!el) return
  // 动态导入避免 SSR/构建问题
  const html2pdf = (await import('html2pdf.js')).default
  const opt = {
    // 用 as const 固定字面量，匹配 html2pdf 的联合类型（'jpeg' | 'png' | 'webp'）
    margin: [10, 10, 10, 10] as [number, number, number, number],
    filename: `三晋智行·通关文牒_${plan.value.title.replace(/[\/\\:*?"<>|]/g, '')}.pdf`,
    image: { type: 'jpeg' as const, quality: 0.98 },
    html2canvas: { scale: 2, useCORS: true, backgroundColor: '#F6EFE2' },
    jsPDF: { unit: 'mm' as const, format: 'a4' as const, orientation: 'portrait' as const }
  }
  html2pdf().set(opt).from(el).save()
}
</script>

<template>
  <section class="space-y-4 sm:space-y-6">
    <div class="text-center max-w-xl mx-auto mb-3 sm:mb-6">
      <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-2 inline-block">行驿</div>
      <h2 class="text-2xl sm:text-3xl font-calligraphy text-ink-dark">行驿日程</h2>
      <p class="text-xs text-ink-light mt-2 font-serif">按日铺展的通关行驿，含时辰、点位、餐食与避坑注记</p>
    </div>

    <!--
      摘要条（标题 + 操作按钮）仅在**确实有行程**时渲染。
      此前无行程时也会渲染，页面上摆着一个「💾 保存此案」按钮（虽被禁用），
      看起来像是能把「待定制行程」存进「我的通关文牒」。
      现在没有行程就只显示下方的空状态与「前往定制」，不存在保存入口。
    -->
    <div
      v-if="hasPlan"
      class="bg-paper-card ink-border-thin rounded-lg p-3.5 sm:p-5 shadow-scroll"
    >
      <div class="flex flex-col sm:flex-row sm:flex-wrap sm:items-center sm:justify-between gap-3 border-b border-sandalwood/20 pb-3 mb-4">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="seal-stamp px-1.5 py-0.5 text-xs rounded shrink-0">御览</span>
            <h3 class="text-base sm:text-xl font-bold font-serif text-ink-dark">{{ plan.title }}</h3>
          </div>
          <div class="flex flex-wrap items-center gap-2 mt-1.5">
            <p class="text-xs text-sandalwood-dark font-serif">
              总长 {{ plan.days.length }} 日 · 覆盖 {{ plan.destinations.join('、') }}
            </p>
            <span
              v-if="hasPlan"
              class="text-[10px] font-serif border rounded px-1.5 py-0.5"
              :class="sourceBadge.cls"
              :title="`知识库指纹 ${plan.knowledge_fingerprint ?? '-'}`"
            >
              {{ sourceBadge.text }}
            </span>
            <span
              v-if="plan.geo_fixed_count"
              class="text-[10px] font-serif border border-celadon/40 rounded px-1.5 py-0.5 text-celadon-dark bg-celadon/10"
              title="生成后由地理审计自动修正的餐食/地名错配数量"
            >
              ✓ 已自动校正 {{ plan.geo_fixed_count }} 处地理错配
            </span>
          </div>
        </div>
        <!-- 手机端按钮铺满一行，便于点按 -->
        <div class="flex flex-wrap items-center gap-2">
          <button
            v-if="store.lastForm"
            @click="regenerate"
            :disabled="store.isGenerating"
            class="flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-xs rounded border border-sandalwood-dark text-sandalwood-dark hover:bg-sandalwood-dark hover:text-white transition font-serif disabled:opacity-50 whitespace-nowrap"
            title="忽略缓存，重新调用大模型生成"
          >
            {{ store.isGenerating ? '重编中……' : '↻ 重新生成' }}
          </button>
          <button
            @click="exportPassport"
            class="flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-xs rounded border border-cinnabar text-cinnabar hover:bg-cinnabar hover:text-white transition font-serif whitespace-nowrap"
          >
            📜 导出通关文牒
          </button>
          <button
            @click="store.save"
            :disabled="!hasPlan"
            class="flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-xs rounded bg-celadon text-white hover:bg-celadon-dark transition font-serif whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-celadon"
          >
            💾 保存此案
          </button>
          <button
            v-if="plan.days.length > 0"
            @click="removePlan"
            class="flex-1 sm:flex-none px-3 py-2 sm:py-1.5 text-xs rounded bg-red-500/80 text-white hover:bg-red-600 transition font-serif whitespace-nowrap"
          >
            🗑 删除此案
          </button>
        </div>
      </div>
      <div class="flex flex-wrap gap-2 text-xs">
        <span class="text-sandalwood-dark font-medium">线路神韵：</span>
        <span
          v-for="(hl, idx) in plan.highlights"
          :key="idx"
          class="bg-paper-base px-2 py-0.5 rounded text-ink border border-sandalwood/20"
        >
          {{ hl }}
        </span>
      </div>

      <!-- 为何这样安排：说明同行之人 / 节奏意趣 / 三晋意趣如何影响编排 -->
      <div
        v-if="plan.rationale"
        class="mt-4 border-t border-dashed border-sandalwood/30 pt-4"
      >
        <div class="flex items-center gap-2 mb-2">
          <span class="seal-stamp w-6 h-6 rounded-full text-[10px] shrink-0">缘</span>
          <h4 class="text-sm font-bold font-serif text-ink-dark">为何这样安排</h4>
        </div>
        <p class="text-xs sm:text-sm text-sandalwood-dark font-serif leading-relaxed">
          {{ plan.rationale }}
        </p>
      </div>
    </div>

    <!-- 日期切换 + 详情 -->
    <div class="bg-paper-card ink-border-thin rounded-lg p-3.5 sm:p-5 shadow-scroll">
      <div v-if="plan.days.length === 0" class="text-center py-12 sm:py-16">
        <div class="seal-stamp w-12 h-12 rounded-full text-sm mx-auto mb-3">待</div>
        <p class="text-sandalwood-dark font-serif mb-3">暂无行程安排</p>
        <p class="text-xs text-ink-light font-serif mb-4">请前往「行程祈定册」填写意向，智能规划您的三晋行驿</p>
        <button
          @click="$router.push({ name: 'planner' })"
          class="px-5 py-2 bg-cinnabar hover:bg-cinnabar-light text-white font-serif rounded-lg transition text-sm"
        >
          前往定制 →
        </button>
      </div>

      <template v-else>
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
          <!-- 天数多时横向滚动，避免挤出容器 -->
          <div class="-mx-1 px-1 flex items-center gap-2 overflow-x-auto">
            <button
              v-for="(_, i) in plan.days"
              :key="i"
              @click="currentDayIndex = i; isEditing = false; editDraft = null"
              :class="currentDayIndex === i ? 'bg-cinnabar text-white' : 'bg-paper-base text-ink border border-sandalwood/40'"
              class="shrink-0 px-3 py-1.5 rounded text-sm font-serif whitespace-nowrap"
            >
              第 {{ i + 1 }} 日
            </button>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button
              v-if="!isEditing"
              @click="startEdit"
              class="px-3 py-1.5 text-xs rounded border border-celadon text-celadon hover:bg-celadon hover:text-white transition font-serif"
            >
              ✏️ 编辑本日
            </button>
            <template v-else>
              <button
                @click="saveEdit"
                class="px-3 py-1.5 text-xs rounded bg-celadon text-white hover:bg-celadon-dark transition font-serif"
              >
                ✅ 保存
              </button>
              <button
                @click="cancelEdit"
                class="px-3 py-1.5 text-xs rounded border border-sandalwood/40 text-ink-light hover:bg-paper-base transition font-serif"
              >
                取消
              </button>
            </template>
          </div>
        </div>

        <div v-if="currentDay" class="space-y-4">
          <!-- 编辑模式下可编辑日标题/主题 -->
          <div v-if="isEditing && editDraft" class="bg-paper-base rounded-lg p-4 space-y-3">
            <div class="flex items-center justify-between">
              <label class="text-xs text-sandalwood-dark font-serif w-16">第{{ currentDayIndex + 1 }}日标题</label>
              <input
                v-model="editDraft.title"
                class="flex-1 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white"
              />
            </div>
            <div class="flex items-center justify-between">
              <label class="text-xs text-sandalwood-dark font-serif w-16">当日主题</label>
              <input
                v-model="editDraft.theme"
                class="flex-1 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white"
              />
            </div>
            <div class="flex items-center gap-4">
              <div class="flex items-center gap-1">
                <label class="text-xs text-sandalwood-dark font-serif">起行</label>
                <input
                  v-model="editDraft.suggestedDeparture"
                  class="w-20 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white"
                />
              </div>
              <div class="flex items-center gap-1">
                <label class="text-xs text-sandalwood-dark font-serif">耗时(时)</label>
                <input
                  v-model="editDraft.totalHours"
                  class="w-16 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white"
                />
              </div>
            </div>
            <!-- 本地特产在主轴编辑（不再逐时段标注） -->
            <div class="flex items-center gap-2">
              <label class="text-xs text-sandalwood-dark font-serif shrink-0">当地特产</label>
              <input
                v-model="editDraft.localSpecialties"
                class="flex-1 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white text-celadon-dark"
                placeholder="如：右玉羊肉、应县紫皮蒜"
              />
            </div>
          </div>
          <div v-else class="bg-paper-base rounded-lg p-4 space-y-2">
            <div class="flex justify-between items-center gap-3">
              <h4 class="font-bold text-ink-dark font-serif">今日主轴：{{ currentDay.title }}</h4>
              <span class="text-xs text-sandalwood-dark shrink-0">预计耗时：{{ currentDay.totalHours }} 小时</span>
            </div>
            <!-- 本地特产：整日只在主轴标注一次 -->
            <p v-if="currentDay.localSpecialties" class="text-xs text-celadon-dark font-serif">
              🍜 当地特产：{{ currentDay.localSpecialties }}
            </p>
          </div>

          <!-- 活动列表（查看模式） -->
          <template v-if="!isEditing">
            <div
              v-for="(act, idx) in currentDay.activities"
              :key="idx"
              class="bg-paper-card border border-sandalwood/20 rounded-lg p-4 space-y-2"
            >
              <div class="flex items-center justify-between">
                <span class="text-xs bg-paper-base px-2 py-0.5 rounded text-ink-light">{{ act.time }}</span>
                <span class="text-xs text-cinnabar">{{ act.type }}</span>
              </div>
              <h5 class="font-bold text-ink-dark">{{ act.place }}</h5>
              <p class="text-sm text-ink-light">{{ act.desc }}</p>
              <p class="text-xs text-sandalwood-dark">📖 {{ act.tips }}</p>
            </div>
          </template>

          <!-- 活动列表（编辑模式） -->
          <template v-else-if="editDraft">
            <div
              v-for="(act, idx) in editDraft.activities"
              :key="idx"
              class="bg-paper-card border border-sandalwood/20 rounded-lg p-4 space-y-2"
            >
              <div class="flex items-center justify-between">
                <input
                  v-model="act.time"
                  class="w-32 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-xs bg-white"
                  placeholder="时段"
                />
                <input
                  v-model="act.type"
                  class="w-28 px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-xs bg-white text-cinnabar"
                  placeholder="活动类型"
                />
                <button
                  @click="removeActivity(idx)"
                  class="text-xs text-red-500 hover:text-red-700 font-serif"
                >
                  🗑 删除
                </button>
              </div>
              <input
                v-model="act.place"
                class="w-full px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white font-bold"
                placeholder="点位名称"
              />
              <textarea
                v-model="act.desc"
                rows="2"
                class="w-full px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-sm bg-white text-ink-light"
                placeholder="点位简介"
              ></textarea>
              <input
                v-model="act.tips"
                class="w-full px-2 py-1 border border-sandalwood/30 rounded text-[16px] sm:text-xs bg-white"
                placeholder="参访锦囊"
              />
            </div>
            <button
              @click="addActivity"
              class="w-full py-2 border border-dashed border-sandalwood/40 rounded text-xs text-sandalwood-dark hover:bg-paper-base transition font-serif"
            >
              ＋ 新增活动
            </button>
          </template>
        </div>
      </template>
    </div>
  </section>

  <!-- 通关文牒弹窗 -->
  <div
    v-if="showPassportModal"
    class="fixed inset-0 bg-ink-dark/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 overflow-y-auto"
  >
    <div class="passport-scroll bg-[#F6EFE2] ink-border-double rounded-lg max-w-2xl w-full p-5 sm:p-10 shadow-2xl relative my-4 sm:my-8 text-ink-dark">
      <!-- 关闭按钮 -->
      <button
        @click="showPassportModal = false"
        class="absolute top-4 right-4 text-ink-light hover:text-cinnabar text-xl font-bold"
      >
        ✕
      </button>

      <!-- 标题区 -->
      <div class="text-center border-b-2 border-cinnabar/40 pb-4 mb-6 relative">
        <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-1">三晋御览</div>
        <h2 class="text-2xl sm:text-3xl font-calligraphy tracking-widest text-cinnabar font-bold">三晋游赏通关文牒</h2>
        <p class="text-xs text-sandalwood-dark font-serif mt-1">天朝岁次 · 三晋智行通关文牒</p>
        <!-- 角印：晋阳巡按使 -->
        <div class="absolute right-0 top-0 hidden sm:flex seal-stamp w-16 h-16 rounded text-[10px] p-1 leading-tight">
          晋阳<br>巡按使
        </div>
      </div>

      <!-- 牒文奉示 -->
      <div class="bg-paper-card p-4 rounded border border-sandalwood/30 mb-6 font-serif text-xs text-ink-light leading-relaxed">
        <p>
          牒文奉示：兹有四方行者，心慕三晋厚土，涉太行之雄，探吕梁之秀，寻辽金木构遗珠，溯千载释道梵响。特核准行程如后，沿途关津驿馆、寺观名刹，验牒放行，一体周全关照！
        </p>
      </div>

      <!-- 逐驿行程 -->
      <div class="space-y-4 mb-8">
        <div
          v-for="(day, dIndex) in plan.days"
          :key="dIndex"
          class="border-l-2 border-cinnabar/60 pl-3"
        >
          <div class="flex items-center space-x-2 text-xs font-bold text-ink-dark">
            <span class="font-calligraphy text-sm text-cinnabar">第 {{ dIndex + 1 }} 驿</span>
            <span>· {{ day.theme }} ({{ day.title }})</span>
          </div>
          <div class="text-[11px] text-ink-light mt-1 flex flex-wrap gap-x-3 gap-y-1">
            <span v-for="(act, aIdx) in day.activities" :key="aIdx">
              ● {{ act.place }} ({{ act.time }})
            </span>
          </div>
        </div>
      </div>

      <!-- 底部印章与操作栏 -->
      <div class="pt-4 border-t border-sandalwood/30 flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-center space-x-3">
          <div class="seal-stamp w-14 h-14 rounded text-[10px] p-1 leading-tight">
            智行<br>钦印
          </div>
          <div class="seal-stamp w-14 h-14 rounded-full text-[10px] p-1 leading-tight">
            表里<br>山河
          </div>
        </div>

        <div class="flex items-center space-x-2">
          <button
            @click="saveAsPdf"
            class="px-4 py-2 bg-cinnabar hover:bg-cinnabar-light text-white text-xs font-serif rounded transition shadow"
          >
            📜 保存为 PDF 卷轴
          </button>
          <button
            @click="showPassportModal = false"
            class="px-4 py-2 bg-paper-dark text-ink text-xs font-serif rounded hover:bg-sandalwood/30 transition"
          >
            复返
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
