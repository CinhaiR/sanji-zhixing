import axios from 'axios'
import type {
  ItineraryForm,
  Itinerary,
  SavedItinerary,
  ChatRequest,
  ChatResponse,
  AgentStatus,
  KnowledgeCity,
  KnowledgeItem,
  KnowledgeStats,
  KnowledgeSearchResult,
  CacheStats,
  PresetListResponse,
  AuthResponse,
  UserInfo
} from '@/types'
import { useAuthStore } from '@/stores/auth'

/**
 * 统一 API 请求实例
 * 前端通过 /api 前缀由 Vite 代理转发到后端 (http://localhost:8000)
 */
const request = axios.create({
  baseURL: '/api',
  // 行程生成为大模型调用，耗时较长（含审计约 15-60s）
  timeout: 180000
})

// 请求拦截器：自动注入 Authorization 头
request.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

// 响应拦截器：401 时自动登出
request.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const auth = useAuthStore()
      if (auth.isLoggedIn) {
        auth.logout()
      }
    }
    return Promise.reject(error)
  }
)

/** 生成行程的可选参数 */
export interface GenerateOptions {
  /** 是否允许读取缓存（默认 true） */
  useCache?: boolean
  /** 忽略缓存强制重新生成 */
  forceRefresh?: boolean
  /** 是否执行大模型地理审计 */
  audit?: boolean
}

/** 行程生成：POST /api/itinerary/generate */
export function generateItinerary(form: ItineraryForm, options: GenerateOptions = {}) {
  const payload = {
    ...form,
    options: {
      use_cache: options.useCache ?? true,
      force_refresh: options.forceRefresh ?? false,
      audit: options.audit ?? null
    }
  }
  return request.post<Itinerary>('/itinerary/generate', payload).then((r) => r.data)
}

/** 保存行程：POST /api/itinerary/save */
export function saveItinerary(plan: Itinerary) {
  return request.post<SavedItinerary>('/itinerary/save', plan).then((r) => r.data)
}

/** 加载历史行程列表：GET /api/itinerary/list */
export function loadItineraries() {
  return request.get<SavedItinerary[]>('/itinerary/list').then((r) => r.data)
}

/** 删除行程：DELETE /api/itinerary/:id */
export function deleteItinerary(id: number) {
  return request.delete(`/itinerary/${id}`).then((r) => r.data)
}

/** 生成缓存统计：GET /api/itinerary/cache/stats */
export function loadCacheStats() {
  return request.get<CacheStats>('/itinerary/cache/stats').then((r) => r.data)
}

/** 清空生成缓存：DELETE /api/itinerary/cache */
export function clearCache(expiredOnly = false) {
  return request
    .delete<{ success: boolean; removed: number }>('/itinerary/cache', {
      params: { expired_only: expiredOnly }
    })
    .then((r) => r.data)
}

/** 典藏线路列表：GET /api/presets */
export function loadPresets(category = '全部') {
  return request
    .get<PresetListResponse>('/presets', { params: { category } })
    .then((r) => r.data)
}

/** 前端构建标识：GET /api/build（用于检测「有新版本」并自动更新） */
export function loadBuildId() {
  return request
    .get<{ build: string; frontend_served: boolean }>('/build')
    .then((r) => r.data)
}

/** 智能体对话：POST /api/agent/chat */
export function chat(payload: ChatRequest) {
  return request.post<ChatResponse>('/agent/chat', payload).then((r) => r.data)
}

/** 流式对话事件 */
export interface ChatStreamEvents {
  onSources?: (cities: string[]) => void
  onDelta?: (chunk: string) => void
  onDone?: (full: string) => void
  onError?: (message: string) => void
}

/**
 * 流式对话：POST /api/agent/chat/stream（SSE）
 *
 * 用 fetch + ReadableStream 而非 EventSource —— EventSource 只支持 GET，
 * 而这里要发 JSON 请求体。返回 AbortController 供调用方中断。
 *
 * 注意：流式接口不走 axios 实例（axios 在浏览器端拿不到增量流），
 * 因此在此手动拼接 baseURL，保持与 axios 实例一致的 `/api` 前缀。
 */
export function chatStream(payload: ChatRequest, events: ChatStreamEvents): AbortController {
  const controller = new AbortController()
  const base = request.defaults.baseURL ?? '/api'

  ;(async () => {
    try {
      const auth = useAuthStore()
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      if (auth.token) headers.Authorization = `Bearer ${auth.token}`
      const resp = await fetch(`${base}/agent/chat/stream`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal: controller.signal
      })
      if (!resp.ok || !resp.body) {
        events.onError?.(`服务返回 HTTP ${resp.status}`)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      let event = ''
      let accumulated = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE 以空行分隔事件
        let idx: number
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const raw = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)

          let dataLine = ''
          for (const line of raw.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) dataLine += line.slice(5).trim()
          }
          if (!dataLine) continue

          let data: any
          try {
            data = JSON.parse(dataLine)
          } catch {
            continue
          }

          if (event === 'sources') {
            if (Array.isArray(data) && data.length) events.onSources?.(data)
          } else if (event === 'delta') {
            accumulated += String(data)
            events.onDelta?.(String(data))
          } else if (event === 'done') {
            events.onDone?.(String(data))
          } else if (event === 'error') {
            events.onError?.(String(data))
          }
        }
      }
      // 流意外结束且未收到 done：用已累积内容兜底
      if (accumulated && !controller.signal.aborted) events.onDone?.(accumulated)
    } catch (e: any) {
      if (e?.name === 'AbortError') return
      events.onError?.(e?.message ?? '流式连接失败')
    }
  })()

  return controller
}

/** 智能体服务状态：GET /api/agent/status */
export function loadAgentStatus() {
  return request.get<AgentStatus>('/agent/status').then((r) => r.data)
}

// ---------------------------------------------------------------- 知识库

/** 府邑列表：GET /api/knowledge/cities */
export function loadKnowledgeCities() {
  return request.get<KnowledgeCity[]>('/knowledge/cities').then((r) => r.data)
}

/** 知识库统计：GET /api/knowledge/stats */
export function loadKnowledgeStats() {
  return request.get<KnowledgeStats>('/knowledge/stats').then((r) => r.data)
}

/** 按府邑/分类列出条目：GET /api/knowledge/items */
export function loadKnowledgeItems(params: {
  kind?: string
  city?: string
  limit?: number
  offset?: number
}) {
  return request.get<KnowledgeItem[]>('/knowledge/items', { params }).then((r) => r.data)
}

/** 全文检索：GET /api/knowledge/search */
export function searchKnowledge(
  q: string,
  params: { kind?: string; city?: string; limit?: number } = {}
) {
  return request
    .get<KnowledgeSearchResult>('/knowledge/search', { params: { q, ...params } })
    .then((r) => r.data)
}

/** 校验文本的地理错配：GET /api/knowledge/validate */
export function validateText(text: string, city?: string) {
  return request
    .get<{ issues: Array<Record<string, unknown>>; detected_cities: string[] }>(
      '/knowledge/validate',
      { params: { text, city } }
    )
    .then((r) => r.data)
}

/** 重新导入知识库种子文件：POST /api/knowledge/reload */
export function reloadKnowledge() {
  return request
    .post<{ cities: number; attractions: number; foods: number; fingerprint: string }>(
      '/knowledge/reload'
    )
    .then((r) => r.data)
}

// ---------------------------------------------------------------- 认证

/** 登录：POST /api/auth/login */
export function login(username: string, password: string) {
  return request
    .post<AuthResponse>('/auth/login', { username, password })
    .then((r) => r.data)
}

/** 注册：POST /api/auth/register */
export function register(payload: {
  username: string
  password: string
  confirm_password: string
  captcha_id: string
  captcha_text: string
}) {
  return request
    .post<AuthResponse>('/auth/register', payload)
    .then((r) => r.data)
}

/** 获取验证码：GET /api/auth/captcha */
export function getCaptcha() {
  return request
    .get<{ captcha_id: string; image: string }>('/auth/captcha')
    .then((r) => r.data)
}

/** 获取当前用户：GET /api/auth/me */
export function fetchMe() {
  return request.get<UserInfo>('/auth/me').then((r) => r.data)
}
