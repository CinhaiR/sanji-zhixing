// 三晋智行 全局类型定义

/** 行程中的单个活动 */
export interface Activity {
  time: string
  place: string
  type: string
  desc: string
  tips: string
  foodPairing: string
}

/** 单日行程 */
export interface DayPlan {
  theme: string
  title: string
  totalHours: string
  suggestedDeparture: string
  /** 当日府邑的本地特产：只在「主轴」标注一次 */
  localSpecialties?: string
  activities: Activity[]
}

/** 完整行程方案 */
export interface Itinerary {
  id?: number
  savedAt?: string
  title: string
  destinations: string[]
  highlights: string[]
  /** 「为何这样安排」——说明同行之人 / 寻古节奏与意趣 / 三晋意趣如何影响编排 */
  rationale?: string
  days: DayPlan[]
  /** 生成来源：llm（大模型）/ cache（缓存命中）/ mock（降级兜底） */
  source?: string | null
  /** 是否来自缓存 */
  cached?: boolean
  /** 生成时所用知识库内容指纹 */
  knowledge_fingerprint?: string | null
  /** 地理准确性问题自动修正次数 */
  geo_fixed_count?: number
}

/** 行程生成表单参数 */
export interface ItineraryForm {
  destinations: string[]
  days: number
  companion: string
  pace: string
  themes: string[]
}

// ---------------------------------------------------------------- 典藏线路

/** 一条典藏线路：字段可直接作为行程祈定册的表单参数 */
export interface PresetRoute {
  id: string
  name: string
  /** 动线，如「太原 → 阳泉 → 晋中 → 长治」 */
  route: string
  /** 一句话定位 */
  tagline: string
  /** 主题分类，如「红色记忆」 */
  category: string
  days: number
  cities: string[]
  themes: string[]
  pace: string
  companion: string
  /** 为何选这条线路 */
  why: string
  /** 沿途不可错过的点位（均取自知识库） */
  highlights: string[]
  best_for: string
  best_season: string
  source: string
  /** 角标，如「官方线路」 */
  badge?: string | null
}

/** 典藏线路列表响应 */
export interface PresetListResponse {
  total: number
  /** 可选主题分类，首项为「全部」 */
  categories: string[]
  routes: PresetRoute[]
}

/** 已保存的历史行程（带元数据） */
export interface SavedItinerary extends Itinerary {
  id: number
  savedAt: string
}

/** 智能体对话消息 */
export interface ChatMessage {
  role: 'user' | 'agent'
  content: string
}

/** 智能体对话会话（含多条消息历史） */
export interface ChatSession {
  id: string
  title: string
  messages: ChatMessage[]
  createdAt: string
  updatedAt: string
  /** 后端会话 id：多轮对话时回传，用于工作流侧上下文承接 */
  sessionId?: string
}

/** 智能体对话请求 */
export interface ChatRequest {
  message: string
  history: ChatMessage[]
  session_id?: string
}

/** 智能体对话响应 */
export interface ChatResponse {
  reply: string
  sources?: string[]
  session_id?: string | null
  /** xingchen 讯飞工作流 / fallback 本地降级 */
  source?: string | null
  /** 降级原因，供界面提示与排查 */
  error?: string | null
}

/** 智能体服务状态 */
export interface AgentStatus {
  configured: boolean
  provider: string
  flow_id_set: boolean
  input_key: string
  send_history: boolean
  timeout: number
  message: string
}

// ---------------------------------------------------------------- 知识库

/** 知识条目（景点 / 特产） */
export interface KnowledgeItem {
  id: number
  /** attraction 景点 / food 特产 */
  kind: string
  name: string
  aliases: string[]
  city: string
  city_name: string
  /** 特产的其它同源地区 city id */
  traditional_in: string[]
  traditional_in_names: string[]
  item_type: string
  level?: string | null
  tags: string[]
  desc: string
  tips: string
  taste?: string | null
  ingredients: string[]
  confidence: string
  source: string
}

/** 府邑信息 */
export interface KnowledgeCity {
  id: string
  name: string
  aliases: string[]
  theme_tags: string[]
  intro: string
  attraction_count: number
  food_count: number
}

/** 知识库统计 */
export interface KnowledgeStats {
  cities: number
  attractions: number
  foods: number
  total: number
  by_city: Array<{ city: string; city_name: string; attractions: number; foods: number }>
  knowledge_fingerprint: string
}

/** 知识库检索结果 */
export interface KnowledgeSearchResult {
  q: string
  total: number
  items: KnowledgeItem[]
}

/** 行程生成缓存统计 */
export interface CacheStats {
  enabled: boolean
  backend: string
  ttl_seconds: number
  memory_entries: number
  db_entries: number
  total_hits: number
  expired_entries: number
}

/** 用户信息 */
export interface UserInfo {
  id: number
  username: string
  is_admin: boolean
}

/** 登录/注册响应 */
export interface AuthResponse {
  access_token: string
  token_type: string
  username: string
  is_admin: boolean
}
