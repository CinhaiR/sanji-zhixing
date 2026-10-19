/**
 * 高德地图（AMap JSAPI 2.0）加载与配置
 *
 * 只使用高德，不接百度。SDK 由前端动态注入，便于在加载失败时回退到示意舆图 ——
 * 注意高德 Key 若开启了「域名白名单」，在非白名单域名下会加载失败，
 * 因此这里必须能感知失败而不能让整页报错。
 */
import type { Itinerary } from '@/types'

/**
 * 高德 Web端(JS API) Key —— 从环境变量 VITE_AMAP_KEY 读取。
 *
 * 不要写死在代码里：Key 随版本库公开即等于公开泄露（会被盗刷配额）。
 * 本地使用：复制 frontend/.env.example 为 frontend/.env，填入自己的 Key；
 * .env 已在 .gitignore 中，不会提交。未配置时地图功能给出明确提示，不影响页面其它部分。
 */
const AMAP_KEY: string =
  (import.meta.env.VITE_AMAP_KEY as string | undefined)?.trim() ?? ''

/** 山西 11 个地级市市中心坐标（经高德地理编码取得，[经度, 纬度]） */
export interface CityPoint {
  name: string
  lng: number
  lat: number
}

export const SHANXI_CITIES: CityPoint[] = [
  { name: '太原', lng: 112.549656, lat: 37.870451 },
  { name: '大同', lng: 113.366749, lat: 40.09711 },
  { name: '朔州', lng: 112.432906, lat: 39.331734 },
  { name: '忻州', lng: 112.734149, lat: 38.415958 },
  { name: '晋中', lng: 112.752633, lat: 37.688006 },
  { name: '吕梁', lng: 111.150325, lat: 37.517797 },
  { name: '阳泉', lng: 113.580426, lat: 37.857094 },
  { name: '长治', lng: 113.110364, lat: 36.198826 },
  { name: '晋城', lng: 112.852022, lat: 35.491315 },
  { name: '临汾', lng: 111.51931, lat: 36.088581 },
  { name: '运城', lng: 111.007051, lat: 35.02667 }
]

/** 山西省大致中心，作为默认视野中心 */
export const SHANXI_CENTER: [number, number] = [112.55, 37.87]

/** 名称 → 坐标，兼容「晋中市」这类带后缀写法 */
export function findCity(name: string): CityPoint | undefined {
  const n = (name || '').replace(/市$/, '').trim()
  return SHANXI_CITIES.find((c) => c.name === n)
}

/** 极简类型声明，避免依赖 @types/amap-js-api */
export interface AMapMap {
  add(overlay: unknown | unknown[]): void
  remove(overlay: unknown | unknown[]): void
  /**
   * 适配视野。第三参为留白（padding），可传数字或 [上,下,左,右]。
   * 若传空数组或不足两点的集合，可能把视野缩得过近，调用方需自行兜底。
   */
  setFitView(overlays?: unknown[], immediately?: boolean, padding?: number | number[]): void
  setZoomAndCenter(zoom: number, center: [number, number]): void
  getZoom(): number
  destroy(): void
}

export interface AMapMarker {
  getTitle?(): string
  setTitle?(title: string): void
}

export interface AMapNamespace {
  Map: new (container: HTMLElement, opts?: Record<string, unknown>) => AMapMap
  Marker: new (opts: Record<string, unknown>) => AMapMarker
  Polyline: new (opts: Record<string, unknown>) => unknown
  Text: new (opts: Record<string, unknown>) => unknown
  Pixel: new (x: number, y: number) => unknown
}

declare global {
  interface Window {
    AMap?: AMapNamespace
    _AMapSecurityConfig?: { securityJsCode?: string; serviceHost?: string }
  }
}

// 安全密钥（若在控制台配置了「安全密钥」，在此填入；未配置可留空）
// 说明：JS API 2.0 在未配置安全密钥时会提示告警，但仍可加载地图。
if (typeof window !== 'undefined') {
  window._AMapSecurityConfig = window._AMapSecurityConfig ?? {}
}

let loading: Promise<AMapNamespace> | null = null

/**
 * 动态加载高德 JSAPI。
 * 同一页面只注入一次；失败后允许重试（不缓存 rejected promise）。
 */
export function loadAMap(): Promise<AMapNamespace> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('非浏览器环境'))
  }
  if (!AMAP_KEY) {
    return Promise.reject(
      new Error('未配置高德地图 Key：请复制 frontend/.env.example 为 frontend/.env 并填入 VITE_AMAP_KEY')
    )
  }
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loading) return loading

  loading = new Promise<AMapNamespace>((resolve, reject) => {
    const script = document.createElement('script')
    script.type = 'text/javascript'
    script.async = true
    // 需要 Marker / Polyline / Text 插件
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${AMAP_KEY}&plugin=AMap.Marker,AMap.Polyline,AMap.Text`

    const timer = window.setTimeout(() => {
      cleanup()
      loading = null
      reject(new Error('高德地图加载超时'))
    }, 15000)

    function cleanup() {
      window.clearTimeout(timer)
      script.onload = null
      script.onerror = null
    }

    script.onload = () => {
      cleanup()
      if (window.AMap) {
        resolve(window.AMap)
      } else {
        loading = null
        reject(new Error('高德地图 SDK 未挂载到 window.AMap'))
      }
    }
    script.onerror = () => {
      cleanup()
      loading = null
      // 常见原因：Key 的域名白名单不含当前域名、Key 被删除、网络不可达
      reject(new Error('高德地图 SDK 加载失败（请检查 Key 的域名白名单与网络）'))
    }

    document.head.appendChild(script)
  })

  return loading
}

/** 生成一个城市标注的 DOM（带古典配色） */
function cityLabel(name: string, highlight: boolean): HTMLElement {
  const el = document.createElement('div')
  el.textContent = name
  el.style.cssText = [
    'padding:2px 8px',
    'border-radius:9999px',
    'font-size:12px',
    'font-family:"Noto Serif SC",serif',
    'white-space:nowrap',
    'box-shadow:0 1px 4px rgba(44,36,25,.18)',
    highlight
      ? 'background:#A83232;color:#FBF9F5;border:1px solid #8B2828'
      : 'background:#FBF9F5;color:#2C2419;border:1px solid rgba(201,169,126,.8)'
  ].join(';')
  return el
}

/** 生成一个圆点标记 */
function cityDot(highlight: boolean): HTMLElement {
  const el = document.createElement('div')
  const size = highlight ? 12 : 8
  el.style.cssText = [
    `width:${size}px`,
    `height:${size}px`,
    'border-radius:9999px',
    `background:${highlight ? '#A83232' : '#6B8E7F'}`,
    `border:2px solid ${highlight ? '#FBF9F5' : '#FBF9F5'}`,
    'box-shadow:0 1px 4px rgba(44,36,25,.35)'
  ].join(';')
  return el
}

export interface MapHandle {
  map: AMapMap
  destroy(): void
}

/**
 * 初始化三晋山河舆图。
 *
 * @param container 地图容器
 * @param plan      当前行程；其 destinations 会被高亮并连线，体现行程动线
 */
export async function initShanxiMap(
  container: HTMLElement,
  plan?: Itinerary | null
): Promise<MapHandle> {
  const AMap = await loadAMap()

  const highlighted = new Set(
    (plan?.destinations ?? []).map((d) => d.replace(/市$/, '').trim()).filter(Boolean)
  )

  const map = new AMap.Map(container, {
    // 缩放交给 setFitView 依容器尺寸自动计算：
    // 山西南北跨约 5.3 个纬度，固定 zoom 在矮容器里会把北部（大同/朔州）挤出视野
    zoom: 6,
    center: SHANXI_CENTER,
    mapStyle: 'amap://styles/whitesmoke',
    viewMode: '2D',
    resizeEnable: true
  })

  const overlays: unknown[] = []
  const markers: AMapMarker[] = []

  // 1. 城市圆点 + 名称
  for (const city of SHANXI_CITIES) {
    const isHl = highlighted.has(city.name)
    const position: [number, number] = [city.lng, city.lat]

    const dot = new AMap.Marker({
      position,
      content: cityDot(isHl),
      offset: new AMap.Pixel(isHl ? -6 : -4, isHl ? -6 : -4),
      zIndex: isHl ? 120 : 100,
      title: city.name
    })
    const label = new AMap.Marker({
      position,
      content: cityLabel(city.name, isHl),
      // 标签放在圆点右侧
      offset: new AMap.Pixel(isHl ? 10 : 8, -34),
      zIndex: isHl ? 121 : 101
    })
    overlays.push(dot, label)
    markers.push(dot, label)
  }

  // 2. 行程动线：按用户选择的府邑顺序连线
  const routePoints = (plan?.destinations ?? [])
    .map((d) => findCity(d))
    .filter((c): c is CityPoint => !!c)
    .map((c) => [c.lng, c.lat] as [number, number])

  if (routePoints.length >= 2) {
    overlays.push(
      new AMap.Polyline({
        path: routePoints,
        strokeColor: '#A83232',
        strokeWeight: 3,
        strokeOpacity: 0.8,
        strokeStyle: 'solid',
        lineJoin: 'round',
        showDir: true,
        zIndex: 90
      })
    )
  }

  map.add(overlays)

  // 视野：
  //  - 有行程 → 收拢到行程所涉府邑（且至少两个，否则 setFitView 会缩得过近）
  //  - 无行程 → 适配全省 11 市，保证南北都在视野内
  if (routePoints.length >= 2) {
    map.setFitView(
      markers.filter((m) => {
        const name = m.getTitle?.()
        return !!name && highlighted.has(name)
      }),
      false,
      [60, 60, 60, 60]
    )
    // 若过滤后不足两点，setFitView 可能缩得过近 —— 退回全省视野
    if (map.getZoom() > 9) {
      map.setFitView(markers, false, [40, 40, 40, 40])
    }
  } else {
    map.setFitView(markers, false, [40, 40, 40, 40])
  }

  return {
    map,
    destroy() {
      try {
        map.remove(overlays)
        map.destroy()
      } catch {
        /* 忽略销毁异常 */
      }
    }
  }
}
