<script setup lang="ts">
// 驿路谋士 —— 智能体对话页（含历史会话）
import { ref, nextTick, computed, onMounted, onUnmounted } from 'vue'
import type { ChatMessage, ChatSession } from '@/types'
import { chatStream, loadAgentStatus } from '@/api'

const STORAGE_KEY = 'sanji-chat-sessions'
const WELCOME = '行者您好，吾乃驿路谋士。可问古建源流、方志典故、行程宜忌，亦或面食老饕之选，尽请发问。'

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

function nowStr() {
  return new Date().toLocaleString('zh-CN', { hour12: false })
}

function loadSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) return parsed
    }
  } catch {}
  return []
}

function saveSessions(list: ChatSession[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
  } catch {}
}

// ---- 会话列表 ----
const sessions = ref<ChatSession[]>(loadSessions())
const currentId = ref<string>('')

// 若无会话，自动创建一个
// 注意：此处只定义，不立即调用 —— 该调用会走到 newSession() → leaveCurrentStream()，
// 依赖下方声明的流式状态。调用统一放在 setup 末尾（见文件底部 ensureSession()），
// 避免 let/const 的暂时性死区。
function ensureSession() {
  if (sessions.value.length === 0) {
    newSession()
  } else if (!currentId.value) {
    currentId.value = sessions.value[0].id
  }
}

const currentSession = computed(() =>
  sessions.value.find((s) => s.id === currentId.value) ?? null
)
const messages = computed<ChatMessage[]>(() => currentSession.value?.messages ?? [])

const input = ref('')
const isThinking = ref(false)
const scrollRef = ref<HTMLDivElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const showSidebar = ref(false)

/** 服务状态：用于提示当前是否降级 */
const agentStatus = ref<{ configured: boolean; provider?: string; message: string } | null>(null)
/** 最近一次失败原因，展示为可重试提示 */
const lastError = ref('')
/** 本轮检索命中的府邑（来自流式 sources 事件） */
const sources = ref<string[]>([])

// ---------------------------------------------------------------- 流式状态
// 这些状态被下面的多个函数读写，其中 leaveCurrentStream() 会在组件初始化阶段
// （ensureSession → newSession）被调用，因此**必须声明在所有使用者之前**：
// let/const 存在暂时性死区，声明前访问会抛
// 「Cannot access 'x' before initialization」，导致整个组件挂载失败，
// 而 vue-tsc 不会报此类错误（已实测踩过两次，故集中放在此处）。
/** 流式上屏节流间隔（ms）：把增量按固定节奏刷进消息，渲染次数与片段数解耦 */
const FLUSH_INTERVAL = 40
/** 尚未上屏的增量缓冲 */
let streamBuf = ''
/** 上屏定时器句柄 */
let flushTimer: number | null = null
/** 按帧节流滚动用的标志 */
let scrollPending = false
/** 进行中的流，切换/新建会话或组件卸载时中断 */
let activeStream: AbortController | null = null

/** 中断当前流并停掉上屏定时器 */
function abortStream() {
  if (activeStream) {
    activeStream.abort()
    activeStream = null
  }
  stopFlushTimer()
}

onMounted(async () => {
  try {
    agentStatus.value = await loadAgentStatus()
  } catch {
    agentStatus.value = null
  }
  // 挂载后按占位文字重算一次高度（窄屏下占位文字可能换行，rows=1 会裁掉第二行）
  nextTick(autoGrow)
})

// 组件卸载时中断进行中的流，避免离开页面后仍在写数据
onUnmounted(abortStream)

function persist() {
  saveSessions(sessions.value)
}

/** 切走会话前先中断生成，避免增量写进新切换到的会话 */
function leaveCurrentStream() {
  if (activeStream || flushTimer !== null) {
    abortStream()
    isThinking.value = false
    streamBuf = ''
  }
}

function newSession() {
  leaveCurrentStream()
  const s: ChatSession = {
    id: uid(),
    title: '新对话',
    messages: [{ role: 'agent', content: WELCOME }],
    createdAt: nowStr(),
    updatedAt: nowStr()
  }
  sessions.value.unshift(s)
  currentId.value = s.id
  persist()
  showSidebar.value = false
}

function selectSession(id: string) {
  if (id !== currentId.value) leaveCurrentStream()
  currentId.value = id
  showSidebar.value = false
  scroll()
}

function deleteSession(id: string) {
  if (id === currentId.value) leaveCurrentStream()
  const idx = sessions.value.findIndex((s) => s.id === id)
  if (idx === -1) return
  sessions.value.splice(idx, 1)
  if (currentId.value === id) {
    currentId.value = sessions.value[0]?.id ?? ''
    if (sessions.value.length === 0) newSession()
  }
  persist()
}

function sessionPreview(s: ChatSession): string {
  const firstUser = s.messages.find((m) => m.role === 'user')
  if (!firstUser) return s.title
  return firstUser.content.length > 16 ? `${firstUser.content.slice(0, 16)}…` : firstUser.content
}

/** 会话是否仍在列表中（用于中途删除时中止流式输出） */
function stillExists(sess: ChatSession): boolean {
  return sessions.value.some((s) => s.id === sess.id)
}

function send() {
  const text = input.value.trim()
  if (!text || isThinking.value || !currentSession.value) return

  const sess = currentSession.value
  sess.messages.push({ role: 'user', content: text })
  // 用首条用户消息作为会话标题
  if (sess.messages.filter((m) => m.role === 'user').length === 1) {
    sess.title = text.length > 16 ? `${text.slice(0, 16)}…` : text
  }
  sess.updatedAt = nowStr()
  persist()

  input.value = ''
  nextTick(autoGrow) // 清空后把输入框高度收回一行
  isThinking.value = true
  lastError.value = ''
  sources.value = []
  // 清掉上一轮可能残留的缓冲与定时器
  stopFlushTimer()
  streamBuf = ''

  // 关键：历史必须取自"用户原始文本"。若在流式追加期间取历史，
  // 最后一条 agent 消息会被截断成半截，污染后续轮次的上下文。
  const history = sess.messages.slice(-10).map((m) => ({ ...m }))

  // 不预先插入空气泡：否则会与下方「思考中」提示同时出现两个框。
  // 首个 delta 到达时才创建消息，插入位置固定在"思考中"提示之上。
  let msgRef: ChatMessage | null = null

  activeStream = chatStream(
    { message: text, history, session_id: sess.sessionId },
    {
      onSources: (cities) => {
        sources.value = cities
      },
      onDelta: (chunk) => {
        if (!stillExists(sess)) return
        if (!msgRef) {
          msgRef = { role: 'agent', content: '' }
          sess.messages.push(msgRef)
          isThinking.value = false
          streamBuf = ''
          ensureFlushTimer()
        }
        // 先攒进缓冲，由定时器按固定节奏上屏：渲染次数不再随 delta 数量膨胀
        streamBuf += chunk
      },
      onDone: (full) => {
        // 先把缓冲里剩余的增量上屏，再定稿
        stopFlushTimer()
        isThinking.value = false
        activeStream = null
        if (!stillExists(sess)) return
        const finalText = full || msgRef?.content || '谋士未能作答，请再问一次。'
        if (msgRef && sess.messages.includes(msgRef)) {
          // 用后端清洗后的完整文本收尾：流式期间可能露出 Markdown 标记
          msgRef.content = finalText
        } else {
          sess.messages.push({ role: 'agent', content: finalText })
        }
        sess.updatedAt = nowStr()
        persist()
        scroll()
      },
      onError: (message) => {
        stopFlushTimer()
        lastError.value = message
        if (!stillExists(sess)) return
        // 已有内容就保留它，只是补一条错误提示；否则才用降级话术占位
        if (msgRef && msgRef.content) {
          isThinking.value = false
          activeStream = null
          sess.updatedAt = nowStr()
          persist()
          return
        }
        isThinking.value = false
        activeStream = null
        sess.messages.push({
          role: 'agent',
          content: '谋士一时语塞，未能通达方志库。请稍后再问，或换个说法。'
        })
        sess.updatedAt = nowStr()
        persist()
        scroll()
      }
    }
  )
}

/**
 * 流式上屏节流：把增量先攒进缓冲，按固定节奏（约 40ms）刷进消息。
 *
 * 模型一次回答可能推送 300+ 个片段；若每个片段都改一次响应式文本，
 * 就等于每片段触发一轮渲染，密集时肉眼可见地顿。改为定频上屏后，
 * 渲染次数与片段数解耦，文字匀速推进、观感更顺。
 *
 * 相关状态声明在文件上方（见「流式状态」），因为它们在初始化阶段就会被读写。
 */
function flushStream() {
  const sess = currentSession.value
  if (!sess) return
  const last = sess.messages[sess.messages.length - 1]
  if (streamBuf && last && last.role === 'agent') {
    last.content += streamBuf
    streamBuf = ''
    // 按帧节流滚动，避免每片段一次强制布局
    scrollThrottled()
  }
}

function ensureFlushTimer() {
  if (flushTimer !== null) return
  flushTimer = window.setInterval(flushStream, FLUSH_INTERVAL)
}

function stopFlushTimer() {
  if (flushTimer !== null) {
    window.clearInterval(flushTimer)
    flushTimer = null
  }
  flushStream()
}

/** 中断当前生成（真流式下可直接切断连接） */
function stopGenerating() {
  abortStream()
  isThinking.value = false
  stopFlushTimer()
}

function scroll() {
  nextTick(() => {
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

/**
 * 输入框随内容自动增高（上限 120px，与模板里的 max-height 一致）。
 *
 * 模板里写了 `style="max-height: 120px"`，但**没有任何逻辑驱动它** ——
 * 于是 `rows="1"` 恒定只有一行高：
 * · 打字多行时只能在框内滚动，看不到上文；
 * · 手机端 16px 字号下占位文字「请问三晋古建、行程或风味之事……」会换成两行，
 *   第二行直接被裁掉（实测 scrollHeight 明显大于 clientHeight）。
 * 这里在输入、发送后、挂载时都重算一次高度。
 */
function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 120)}px`
}

/**
 * 流式期间的高频滚动：按动画帧节流。
 *
 * 每个 delta 都同步滚一次会导致每帧多次读取 scrollHeight + 写 scrollTop，
 * 强制反复布局，表现为文字一顿一顿。合并到每帧一次即可跟手。
 */
function scrollThrottled() {
  if (scrollPending) return
  scrollPending = true
  requestAnimationFrame(() => {
    scrollPending = false
    if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
  })
}

// ---------------------------------------------------------------- 初始化
// 放在最后：此时上面所有 state 与函数都已就绪，杜绝暂时性死区问题。
ensureSession()
</script>

<template>
  <section class="space-y-4 sm:space-y-6">
    <div class="text-center max-w-xl mx-auto mb-3 sm:mb-6">
      <div class="seal-stamp text-xs px-2 py-0.5 rounded mb-2 inline-block">顾问</div>
      <h2 class="text-2xl sm:text-3xl font-calligraphy text-ink-dark">驿路谋士</h2>
      <p class="text-xs text-ink-light mt-2 font-serif">三晋方志大模型驱动，可询古建源流、行程宜忌、风味老饕之选</p>
    </div>

    <div class="flex gap-4">
      <!-- 历史会话侧边栏（移动端为抽屉，桌面端常驻） -->
      <!--
        z-50 而非 z-30：顶部栏是 sticky + z-40，会把抽屉顶部压住 ——
        实测那正好盖住「✕ 关闭」按钮，导致抽屉关不掉（命中测试可复现）。
        抽屉抬到顶栏之上，关闭按钮才可点；遮罩保持较低层级，
        这样底部标签栏仍然可见可点，用户开着抽屉也能直接切页。
      -->
      <aside
        :class="showSidebar ? 'translate-x-0' : '-translate-x-full'"
        class="fixed lg:static lg:translate-x-0 inset-y-0 left-0 z-50 w-[78%] max-w-xs lg:w-64 bg-paper-card ink-border-thin rounded-none lg:rounded-lg shadow-scroll flex flex-col transition-transform duration-300 lg:h-auto"
        style="height: 100dvh; min-height: 0"
      >
        <div class="p-3 border-b border-sandalwood/20 flex items-center justify-between lg:block gap-2">
          <button
            @click="newSession"
            class="flex-grow lg:w-full px-3 py-2 bg-cinnabar hover:bg-cinnabar-light text-white text-sm font-serif rounded-lg transition"
          >
            + 新建对话
          </button>
          <button
            @click="showSidebar = false"
            class="lg:hidden shrink-0 p-2 text-ink-light hover:text-cinnabar"
            aria-label="关闭会话列表"
          >
            ✕
          </button>
        </div>
        <div class="flex-grow overflow-y-auto p-2 space-y-1">
          <div v-if="sessions.length === 0" class="text-center text-xs text-sandalwood-dark py-8 font-serif">
            暂无历史对话
          </div>
          <button
            v-for="s in sessions"
            :key="s.id"
            @click="selectSession(s.id)"
            :class="s.id === currentId ? 'bg-cinnabar/10 border-cinnabar/40' : 'bg-transparent hover:bg-paper-base border-transparent'"
            class="w-full text-left p-2.5 rounded border transition group"
          >
            <div class="flex items-center justify-between">
              <span class="text-sm font-serif text-ink-dark truncate">{{ sessionPreview(s) }}</span>
              <span
                @click.stop="deleteSession(s.id)"
                class="text-sandalwood hover:text-cinnabar text-xs lg:opacity-0 lg:group-hover:opacity-100 transition shrink-0 ml-1 p-1"
                aria-label="删除会话"
              >
                ✕
              </span>
            </div>
            <div class="text-[10px] text-sandalwood-dark mt-1">{{ s.updatedAt }}</div>
          </button>
        </div>
      </aside>

      <!-- 移动端遮罩 -->
      <div
        v-if="showSidebar"
        class="fixed inset-0 bg-ink-dark/40 z-20 lg:hidden"
        @click="showSidebar = false"
      ></div>

      <!-- 对话主区 -->
      <div class="flex-grow min-w-0">
        <!--
          移动端高度必须扣掉底部标签栏（--bottom-bar-h，桌面端该变量为 0），
          否则 fixed 的标签栏会盖住最下方的输入框。
          早先的做法是「在驿路谋士页隐藏标签栏」—— 那样输入框不被遮了，
          但移动端也再没有任何导航入口，进得来出不去。导航常驻 + 扣高度才是对的。
        -->
        <div
          class="bg-paper-card ink-border-double rounded-lg shadow-scroll flex flex-col
                 h-[calc(100dvh-19rem-var(--bottom-bar-h))] min-h-[360px] lg:h-[calc(100vh-280px)] lg:min-h-[460px]"
        >
          <!-- 工具栏 -->
          <div class="flex items-center justify-between border-b border-sandalwood/20 px-3 sm:px-4 py-2 gap-2">
            <button
              @click="showSidebar = !showSidebar"
              class="lg:hidden shrink-0 text-sandalwood-dark hover:text-cinnabar text-sm font-serif px-1"
            >
              ☰ 历史
            </button>
            <span class="text-xs text-sandalwood-dark font-serif truncate">
              {{ currentSession ? sessionPreview(currentSession) : '新对话' }}
            </span>
            <span class="w-12"></span>
          </div>

          <div ref="scrollRef" class="flex-grow overflow-y-auto p-3 sm:p-5 space-y-3 sm:space-y-4">
            <div
              v-for="(msg, idx) in messages"
              :key="idx"
              :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'"
            >
              <div :class="msg.role === 'user' ? 'max-w-[85%] sm:max-w-[75%]' : 'max-w-[92%] sm:max-w-[80%] flex items-start gap-2'">
                <template v-if="msg.role === 'agent'">
                  <span class="seal-stamp w-7 h-7 sm:w-8 sm:h-8 rounded-full text-[10px] shrink-0 mt-1">晋</span>
                  <!-- whitespace-pre-wrap：保留后端的换行与分段，否则整段挤成一块 -->
                  <div
                    class="bg-paper-base border border-sandalwood/30 rounded-lg p-3 text-[13px] sm:text-sm text-ink-dark leading-relaxed font-serif shadow-sm whitespace-pre-wrap break-words"
                  >
                    {{ msg.content }}
                  </div>
                </template>
                <template v-else>
                  <div
                    class="bg-cinnabar/10 border border-cinnabar/30 rounded-lg p-3 text-[13px] sm:text-sm text-ink-dark leading-relaxed font-serif whitespace-pre-wrap break-words"
                  >
                    {{ msg.content }}
                  </div>
                </template>
              </div>
            </div>

            <!-- 思考中 -->
            <div v-if="isThinking" class="flex justify-start">
              <div class="max-w-[92%] sm:max-w-[80%] flex items-start gap-2">
                <span class="seal-stamp w-7 h-7 sm:w-8 sm:h-8 rounded-full text-[10px] shrink-0 mt-1">晋</span>
                <div class="bg-paper-base border border-sandalwood/30 rounded-lg p-3 text-[13px] sm:text-sm text-ink-light font-serif">
                  谋士正在翻阅方志……
                </div>
              </div>
            </div>
          </div>

          <div class="border-t border-sandalwood/30 p-3 sm:p-4 bg-paper-base/50">
            <!-- 失败提示：展示真实原因，避免"只看到兜底话术不知为何" -->
            <div
              v-if="lastError"
              class="mb-2 text-[11px] font-serif text-cinnabar bg-cinnabar/5 border border-cinnabar/30 rounded px-3 py-2 flex items-start justify-between gap-2"
            >
              <span>⚠ 方志检索未成功：{{ lastError }}</span>
              <button
                @click="lastError = ''"
                class="shrink-0 text-cinnabar/70 hover:text-cinnabar p-1"
                title="关闭"
              >
                ✕
              </button>
            </div>

            <div class="flex items-end gap-2">
              <textarea
                ref="inputRef"
                v-model="input"
                @input="autoGrow"
                @keydown.enter.exact.prevent="send"
                rows="1"
                placeholder="可问古建、行程、风味"
                class="flex-grow min-w-0 bg-paper-card border border-sandalwood/40 rounded-lg px-3 py-2 text-[16px] sm:text-sm text-ink placeholder:text-sandalwood-dark/60 focus:outline-none focus:border-cinnabar resize-none font-serif"
                style="max-height: 120px"
              ></textarea>
              <button
                @click="send"
                :disabled="!input.trim() || isThinking"
                class="shrink-0 px-4 py-2 bg-cinnabar hover:bg-cinnabar-light text-white text-sm font-serif rounded-lg transition disabled:opacity-50"
              >
                发问 ➤
              </button>
            </div>
            <div class="flex items-center justify-center gap-3 mt-2">
              <!-- 流式生成中可中断，避免长回答等待 -->
              <button
                v-if="isThinking"
                @click="stopGenerating"
                class="shrink-0 text-[11px] text-celadon-dark hover:text-cinnabar font-serif underline decoration-dotted"
              >
                停止生成
              </button>
              <p class="text-[10px] sm:text-[11px] text-sandalwood-dark font-serif text-center leading-relaxed">
                <span v-if="sources.length" class="text-celadon-dark">已检索：{{ sources.join('、') }}</span>
                <span v-if="agentStatus && !agentStatus.configured" class="text-cinnabar">
                  <template v-if="sources.length"> · </template>当前为本地降级应答
                </span>
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
