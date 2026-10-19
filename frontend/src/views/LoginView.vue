<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useItineraryStore } from '@/stores/itinerary'
import { login, register, getCaptcha } from '@/api'
import SealIcon from '@/components/SealIcon.vue'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const itineraryStore = useItineraryStore()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const captchaId = ref('')
const captchaText = ref('')
const captchaImage = ref('')
const loading = ref(false)
const error = ref('')

// 密码强度校验
const passwordValid = () =>
  password.value.length >= 8 && /[A-Za-z]/.test(password.value)
const confirmValid = () =>
  confirmPassword.value === password.value

async function refreshCaptcha() {
  try {
    const cap = await getCaptcha()
    captchaId.value = cap.captcha_id
    captchaImage.value = `data:image/png;base64,${cap.image}`
    captchaText.value = ''
  } catch {
    error.value = '验证码加载失败，请刷新重试'
  }
}

onMounted(() => {
  if (mode.value === 'register') refreshCaptcha()
})

function switchMode(m: 'login' | 'register') {
  mode.value = m
  error.value = ''
  if (m === 'register' && !captchaId.value) refreshCaptcha()
}

function submit() {
  if (!username.value.trim() || !password.value) {
    error.value = '请填写用户名和密码'
    return
  }

  if (mode.value === 'register') {
    if (!passwordValid()) {
      error.value = '密码至少 8 位且必须包含英文字母'
      return
    }
    if (confirmPassword.value !== password.value) {
      error.value = '两次输入的密码不一致'
      return
    }
    if (!captchaText.value.trim()) {
      error.value = '请输入验证码'
      return
    }
    doRegister()
  } else {
    doLogin()
  }
}

async function doLogin() {
  loading.value = true
  error.value = ''
  try {
    const resp = await login(username.value.trim(), password.value)
    auth.setAuth(resp.access_token, {
      id: 0,
      username: resp.username,
      is_admin: resp.is_admin
    })
    // 登录后加载历史行程
    itineraryStore.loadHistory()
    const redirect = (route.query.redirect as string) || '/home'
    router.replace(redirect)
  } catch (e: any) {
    error.value = e?.response?.data?.detail || '用户名或密码错误'
  } finally {
    loading.value = false
  }
}

async function doRegister() {
  loading.value = true
  error.value = ''
  try {
    const resp = await register({
      username: username.value.trim(),
      password: password.value,
      confirm_password: confirmPassword.value,
      captcha_id: captchaId.value,
      captcha_text: captchaText.value.trim()
    })
    auth.setAuth(resp.access_token, {
      id: 0,
      username: resp.username,
      is_admin: resp.is_admin
    })
    // 注册后加载历史行程
    itineraryStore.loadHistory()
    const redirect = (route.query.redirect as string) || '/home'
    router.replace(redirect)
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    // Pydantic 校验错误格式
    if (Array.isArray(detail)) {
      error.value = detail[0]?.msg?.replace('Value error, ', '') || '注册失败'
    } else {
      error.value = detail || '注册失败'
    }
    // 注册失败后刷新验证码（一次性）
    refreshCaptcha()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-[100dvh] flex items-center justify-center bg-paper px-4 py-8 sm:py-12">
    <div class="w-full max-w-md">
      <!-- 品牌 -->
      <div class="text-center mb-8">
        <div class="flex justify-center mb-3">
          <SealIcon char="三晋" :size="52" />
        </div>
        <h1 class="font-calligraphy text-3xl text-ink-dark">三晋智行</h1>
        <p class="text-sandalwood-dark text-sm mt-1 font-serif">
          {{ mode === 'login' ? '行者登录' : '行者注册' }}
        </p>
      </div>

      <!-- 表单卡片 -->
      <div class="bg-paper-card border border-sandalwood/30 rounded-lg shadow-lg p-6 sm:p-8">
        <!-- 切换标签 -->
        <div class="flex mb-6 border-b border-sandalwood/20">
          <button
            @click="switchMode('login')"
            :class="mode === 'login' ? 'text-cinnabar border-cinnabar' : 'text-ink-light hover:text-ink'"
            class="flex-1 pb-2 font-serif text-base border-b-2 transition"
          >
            登录
          </button>
          <button
            @click="switchMode('register')"
            :class="mode === 'register' ? 'text-cinnabar border-cinnabar' : 'text-ink-light hover:text-ink'"
            class="flex-1 pb-2 font-serif text-base border-b-2 transition"
          >
            注册
          </button>
        </div>

        <!-- 错误提示 -->
        <div v-if="error" class="mb-4 px-3 py-2 bg-cinnabar/10 border border-cinnabar/30 rounded text-cinnabar text-sm">
          {{ error }}
        </div>

        <form @submit.prevent="submit" class="space-y-4">
          <div>
            <label class="block text-sm text-ink-light mb-1.5 font-serif">用户名</label>
            <input
              v-model="username"
              type="text"
              placeholder="2-64 个字符"
              class="w-full px-3.5 py-2.5 bg-paper border border-sandalwood/40 rounded text-ink-dark text-[16px] focus:outline-none focus:border-cinnabar transition"
              autocomplete="username"
            />
            <!-- 注册模式下显示用户名规则 -->
            <p v-if="mode === 'register'" class="mt-1.5 text-xs text-ink-light/50">
              ● 仅限中英文、数字、下划线，不可用 null/admin 等保留名
            </p>
          </div>

          <div>
            <label class="block text-sm text-ink-light mb-1.5 font-serif">密码</label>
            <input
              v-model="password"
              type="password"
              :placeholder="mode === 'login' ? '请输入密码' : '至少 8 位，须含字母'"
              class="w-full px-3.5 py-2.5 bg-paper border border-sandalwood/40 rounded text-ink-dark text-[16px] focus:outline-none focus:border-cinnabar transition"
              autocomplete="current-password"
            />
            <!-- 注册模式下显示密码规则与实时校验 -->
            <div v-if="mode === 'register'" class="mt-1.5 space-y-0.5">
              <p
                class="text-xs"
                :class="passwordValid() ? 'text-celadon' : 'text-ink-light/50'"
              >
                ● 至少 8 位，且包含英文字母
              </p>
            </div>
          </div>

          <!-- 确认密码（仅注册模式） -->
          <div v-if="mode === 'register'">
            <label class="block text-sm text-ink-light mb-1.5 font-serif">确认密码</label>
            <input
              v-model="confirmPassword"
              type="password"
              placeholder="再次输入密码"
              class="w-full px-3.5 py-2.5 bg-paper border border-sandalwood/40 rounded text-ink-dark text-[16px] focus:outline-none focus:border-cinnabar transition"
              autocomplete="new-password"
            />
            <p v-if="confirmPassword" class="mt-1 text-xs" :class="confirmValid() ? 'text-celadon' : 'text-cinnabar'">
              {{ confirmValid() ? '● 两次密码一致' : '● 两次密码不一致' }}
            </p>
          </div>

          <!-- 验证码（仅注册模式） -->
          <div v-if="mode === 'register'">
            <label class="block text-sm text-ink-light mb-1.5 font-serif">验证码</label>
            <!--
              小屏适配要点：
              · 输入框必须带 min-w-0 —— `flex-1` 只设 flex 简写，其 min-width:auto
                会取输入框的固有宽度（约 265px），导致它拒绝收缩、把验证码挤出卡片外，
                在手机上验证码被屏幕裁掉（只看得见一半）。
              · 允许 flex-wrap：320px 这类极窄屏放不下时，图片换到下一行，
                而不是越界被裁。
              · 图片用 h-10 w-auto：接近其 120×44 的自然尺寸，字符清晰；
                原来 h-8 会缩到 87px 宽，字太小。
            -->
            <div class="flex flex-wrap items-center gap-2">
              <input
                v-model="captchaText"
                type="text"
                placeholder="请输入图中字符"
                maxlength="6"
                class="flex-1 min-w-[9rem] px-3.5 py-2.5 bg-paper border border-sandalwood/40 rounded text-ink-dark text-[16px] focus:outline-none focus:border-cinnabar transition uppercase"
              />
              <button
                type="button"
                @click="refreshCaptcha"
                class="shrink-0 h-[46px] px-2 border border-sandalwood/40 rounded bg-paper hover:bg-paper-dark/40 transition flex items-center justify-center"
                title="点击刷新验证码"
              >
                <img
                  v-if="captchaImage"
                  :src="captchaImage"
                  alt="验证码"
                  class="h-10 w-auto max-w-full"
                />
                <span v-else class="text-xs text-ink-light px-3">加载中</span>
              </button>
            </div>
            <p class="mt-1 text-xs text-ink-light/50">点击图片可刷新验证码</p>
          </div>

          <button
            type="submit"
            :disabled="loading"
            class="w-full py-2.5 bg-cinnabar hover:bg-cinnabar-light disabled:opacity-50 text-white font-serif rounded transition shadow-sm"
          >
            {{ loading ? '处理中...' : (mode === 'login' ? '登 录' : '注 册') }}
          </button>
        </form>

        <p class="text-xs text-ink-light/60 text-center mt-5 font-serif">
          登录后可保存专属行程通关文牒
        </p>
      </div>
    </div>
  </div>
</template>
