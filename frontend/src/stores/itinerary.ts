import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Itinerary, ItineraryForm, SavedItinerary } from '@/types'
import {
  generateItinerary,
  saveItinerary,
  loadItineraries,
  deleteItinerary
} from '@/api'
import type { GenerateOptions } from '@/api'

/**
 * 行程全局状态
 * 对应原 code_artifact.html 中的 currentPlan / savedItineraries
 */
export const useItineraryStore = defineStore('itinerary', () => {
  const currentPlan = ref<Itinerary>({
    title: '待定制行程',
    destinations: [],
    highlights: [],
    days: []
  })

  const savedItineraries = ref<SavedItinerary[]>([])
  const isGenerating = ref(false)
  /** 最近一次生成错误提示 */
  const lastError = ref('')
  /** 最近一次错误码（如 plan_unreasonable），供页面决定是弹窗还是行内提示 */
  const lastErrorCode = ref('')
  /** 最近一次提交的表单，用于「重新生成」 */
  const lastForm = ref<ItineraryForm | null>(null)

  /**
   * 从典藏线路带来的待填表单。
   * 典藏线路页点击「用此线路定制」时写入，行程祈定册挂载时读取并清空。
   */
  const presetDraft = ref<(ItineraryForm & { presetName?: string }) | null>(null)

  /** 由典藏线路写入待填表单 */
  function applyPresetDraft(draft: ItineraryForm & { presetName?: string }) {
    presetDraft.value = {
      ...draft,
      destinations: [...draft.destinations],
      themes: [...draft.themes]
    }
  }

  /** 行程祈定册读取后清空，避免返回页面时被反复覆盖 */
  function consumePresetDraft() {
    const d = presetDraft.value
    presetDraft.value = null
    return d
  }

  /** 调用后端生成行程 */
  async function generate(form: ItineraryForm, options: GenerateOptions = {}) {
    isGenerating.value = true
    lastError.value = ''
    lastErrorCode.value = ''
    try {
      const plan = await generateItinerary(form, options)
      currentPlan.value = plan
      lastForm.value = {
        ...form,
        destinations: [...form.destinations],
        themes: [...form.themes]
      }
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        // 后端结构化错误：{ code, message, days, cities }
        lastError.value = detail.message ?? '行程编排失败，请调整后重试。'
        lastErrorCode.value = detail.code ?? ''
      } else {
        lastError.value = detail ?? '行程编排失败，请确认后端服务已启动后重试。'
        lastErrorCode.value = ''
      }
      throw e
    } finally {
      isGenerating.value = false
    }
  }

  /** 按上次表单重新生成（forceRefresh=true 时绕过缓存） */
  async function regenerate(forceRefresh = false) {
    if (!lastForm.value) return
    await generate(lastForm.value, { forceRefresh })
  }

  /** 仅在编辑后把当前方案写回缓存源（标记为手动修改） */
  function markEdited() {
    if (currentPlan.value) currentPlan.value.source = 'edited'
  }

  /** 保存当前行程 */
  async function save() {
    // 防止待定制（空）行程被保存
    if (!currentPlan.value || currentPlan.value.days.length === 0) return
    const saved = await saveItinerary(currentPlan.value)
    savedItineraries.value.unshift(saved)
  }

  /** 加载历史行程 */
  async function loadHistory() {
    savedItineraries.value = await loadItineraries()
  }

  /** 删除某条历史行程 */
  async function remove(idx: number) {
    const target = savedItineraries.value[idx]
    if (!target) return
    await deleteItinerary(target.id)
    savedItineraries.value.splice(idx, 1)
  }

  /** 载入历史行程到当前 */
  function restore(saved: SavedItinerary) {
    currentPlan.value = {
      title: saved.title,
      destinations: [...saved.destinations],
      highlights: [...saved.highlights],
      days: JSON.parse(JSON.stringify(saved.days))
    }
  }

  /** 清空当前行程（删除此案） */
  function clear() {
    currentPlan.value = {
      title: '待定制行程',
      destinations: [],
      highlights: [],
      days: []
    }
  }

  return {
    currentPlan,
    savedItineraries,
    isGenerating,
    lastError,
    lastErrorCode,
    lastForm,
    presetDraft,
    applyPresetDraft,
    consumePresetDraft,
    generate,
    regenerate,
    markEdited,
    save,
    loadHistory,
    remove,
    restore,
    clear
  }
})
