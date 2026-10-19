<script setup lang="ts">
/**
 * 印章图标：把单个汉字渲染为朱砂印章。
 *
 * 复用全局 .seal-stamp 的配色（朱砂底、宣纸字），此处只负责尺寸与字体缩放，
 * 使「方章」在导航、抽屉等不同位置保持一致观感。
 */
withDefaults(
  defineProps<{
    /** 印章文字（1-2 字；2 字时上下排列） */
    char: string
    /** 边长（px） */
    size?: number
    /** 圆角：'rounded' 方形圆角 ／ 'full' 圆形 */
    shape?: 'rounded' | 'full'
  }>(),
  { size: 28, shape: 'rounded' }
)

/** 依尺寸推算字号，避免各处手调 */
function fontPx(size: number, len: number) {
  if (len >= 2) return Math.max(8, Math.round(size * 0.34))
  return Math.max(10, Math.round(size * 0.5))
}
</script>

<template>
  <span
    class="seal-stamp shrink-0"
    :class="shape === 'full' ? 'rounded-full' : 'rounded'"
    :style="{
      width: size + 'px',
      height: size + 'px',
      fontSize: fontPx(size, char.length) + 'px',
      lineHeight: 1.1
    }"
  >
    <span v-if="char.length <= 2" class="text-center leading-tight">{{ char }}</span>
    <span v-else class="text-center leading-tight">{{ char.slice(0, 2) }}</span>
  </span>
</template>
