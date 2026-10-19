/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 高德地图 Web 端（JS API）Key，见 frontend/.env.example */
  readonly VITE_AMAP_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
