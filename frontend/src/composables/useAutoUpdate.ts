import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { loadBuildId } from '@/api'
import { useItineraryStore } from '@/stores/itinerary'

/**
 * 前端新版本自动更新。
 *
 * 解决的问题：前端是**构建产物**，已经打开的标签页里跑的是旧 JS。
 * 代码改动并重新构建后，旧页面不会自己变新 —— 用户必须手动刷新才能看到新内容
 * （例如新加的景点百科超链接曾经「要刷新一下才显示」）。
 *
 * 这里定期（以及切回标签页、切换路由时）向 `/api/build` 问一次构建标识，
 * 发现变化就自动重载页面。
 *
 * 保守之处：
 * - 只在标签页可见时检查（后台标签不折腾）
 * - **行程生成中不重载**（否则会打断正在等待的请求），改为生成结束后再重载
 * - 接口异常一律忽略，静默重试，不影响正常使用
 */

/** 轮询间隔：15 秒。请求只 stat 一个文件，开销可忽略 */
const POLL_MS = 15_000

/**
 * 用户是否正在输入。
 *
 * 自动重载最可能惹人烦的场景：刚在驿路谋士里敲完问题还没发出去就被刷新掉。
 * 所以「焦点在输入框且已有内容」时推迟重载，等空闲了再补上。
 */
function isTyping(): boolean {
  const el = document.activeElement as HTMLInputElement | HTMLTextAreaElement | null
  if (!el) return false
  const tag = el.tagName
  if (tag !== 'INPUT' && tag !== 'TEXTAREA') return false
  return (el.value || '').trim().length > 0
}

export function useAutoUpdate() {
  const store = useItineraryStore()
  const route = useRoute()
  /** 页面加载时的构建标识；null 表示还没取到 */
  let known: string | null = null
  /** 检测到新版本但因「生成中」被推迟 */
  let deferred = false
  let timer: number | undefined
  let stopped = false

  function reload() {
    stopped = true
    if (timer !== undefined) window.clearInterval(timer)
    known = null
    window.location.reload()
  }

  async function check() {
    if (stopped) return
    // 后台标签页不检查，避免无谓请求
    if (document.visibilityState !== 'visible') return

    let build = ''
    try {
      const res = await loadBuildId()
      build = res.build || ''
    } catch {
      return // 网络抖动忽略，下一轮再试
    }
    if (!build) return // 后端没托管前端（如 npm run dev 模式），无需处理

    if (known === null) {
      known = build // 首次只记录基线
      return
    }
    if (build === known) {
      // 版本没变；若之前有被推迟的重载，此时若已空闲则补上
      if (deferred && !store.isGenerating && !isTyping()) reload()
      return
    }

    // 有新版本：正在生成行程或正在输入时先推迟，不要打断用户
    if (store.isGenerating || isTyping()) {
      deferred = true
      return
    }
    reload()
  }

  /** 切回标签页时立即检查一次，体验上更「即时」 */
  function onVisible() {
    if (document.visibilityState === 'visible') void check()
  }

  onMounted(() => {
    void check()
    timer = window.setInterval(check, POLL_MS)
    document.addEventListener('visibilitychange', onVisible)
  })

  // 切换路由时也检查一次 —— 用户点进「三晋方志」时就能立刻拿到新版本
  watch(
    () => route.fullPath,
    () => void check()
  )

  onUnmounted(() => {
    stopped = true
    if (timer !== undefined) window.clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisible)
  })

  return { check }
}
