import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { UserInfo } from '@/types'

/**
 * 认证状态 Store
 *
 * token 与用户信息持久化到 localStorage，刷新页面后自动恢复登录态。
 * 启动时会校验 JWT 的 exp 字段，过期则自动登出。
 */

/** 解析 JWT payload（不校验签名，仅取 exp 等字段用于本地过期判断） */
function parseJwtExp(token: string): number | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch {
    return null
  }
}

/** 检查 token 是否已过期（含 30 秒容差） */
function isTokenExpired(token: string): boolean {
  const exp = parseJwtExp(token)
  if (exp === null) return false // 无法解析时交给后端判断
  return Date.now() / 1000 >= exp - 30
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>('')
  const user = ref<UserInfo | null>(null)
  const isLoggedIn = ref(false)

  // 从 localStorage 恢复登录态，并校验 token 是否过期
  const savedToken = localStorage.getItem('sjx_token')
  if (savedToken && !isTokenExpired(savedToken)) {
    token.value = savedToken
    const savedUser = localStorage.getItem('sjx_user')
    if (savedUser) {
      try {
        const parsed = JSON.parse(savedUser)
        // 防御：确保 user 是有效对象而非 null/undefined
        if (parsed && typeof parsed === 'object' && parsed.username) {
          user.value = parsed
        }
      } catch {
        user.value = null
      }
    }
    isLoggedIn.value = true
  } else if (savedToken) {
    // token 已过期，清理本地存储
    localStorage.removeItem('sjx_token')
    localStorage.removeItem('sjx_user')
  }

  function setAuth(newToken: string, userInfo: UserInfo) {
    token.value = newToken
    user.value = userInfo
    isLoggedIn.value = true
    localStorage.setItem('sjx_token', newToken)
    localStorage.setItem('sjx_user', JSON.stringify(userInfo))
  }

  function logout() {
    token.value = ''
    user.value = null
    isLoggedIn.value = false
    localStorage.removeItem('sjx_token')
    localStorage.removeItem('sjx_user')
  }

  return { token, user, isLoggedIn, setAuth, logout }
})
