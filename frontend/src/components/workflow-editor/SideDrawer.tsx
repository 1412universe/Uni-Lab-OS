/**
 * 可收起的侧栏抽屉。
 *
 * 交互与 ChemWorldModel 编辑器一致（`app_workflow.js` 的 `lib-collapsed`）：
 *
 * - **收起态**只留一条竖条，把画布让出来（这是默认状态）；
 * - **悬停**时浮层展开并**覆盖**画布，而不是挤压它 —— 挤压会让画布在每次
 *   悬停时重排，视觉上很跳；
 * - **📌 钉住**后常驻展开，适合长时间在节点库里翻找；
 * - 状态记在 localStorage，下次打开保持上次习惯。
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { PanelLeftClose, PanelLeftOpen, Pin, PinOff } from 'lucide-react'

export interface SideDrawerProps {
  /** 侧栏标题（收起时竖排显示）。 */
  title: string
  side: 'left' | 'right'
  /** 展开时的宽度。 */
  width?: number
  /** 持久化键前缀，避免左右两侧互相覆盖。 */
  storageKey: string
  children: ReactNode
}

interface DrawerState {
  pinned: boolean
}

function loadState(key: string): DrawerState {
  try {
    const raw = localStorage.getItem(key)
    // 首次进来先展开：收起态虽然画布最大，但新用户会以为「没有节点库」。
    // 之后完全尊重用户上次的选择（展开就记住展开，收起就记住收起）。
    if (!raw) return { pinned: true }
    const parsed = JSON.parse(raw) as Partial<DrawerState>
    return { pinned: Boolean(parsed.pinned) }
  } catch {
    return { pinned: true }
  }
}

export function SideDrawer({ title, side, width = 250, storageKey, children }: SideDrawerProps) {
  const key = `${storageKey}:${side}`
  const [state, setState] = useState<DrawerState>(() => loadState(key))
  const [hovered, setHovered] = useState(false)
  const closeTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(state))
    } catch {
      // 隐私模式不可写时只影响记忆，不影响本次使用。
    }
  }, [key, state])

  // 离开后延迟收起，给鼠标穿过缝隙的时间，避免闪烁。
  const scheduleClose = useCallback(() => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setHovered(false), 160)
  }, [])
  const cancelClose = useCallback(() => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current)
  }, [])
  useEffect(() => () => { if (closeTimer.current) window.clearTimeout(closeTimer.current) }, [])

  const expanded = state.pinned || hovered

  return (
    <aside
      className={`wf-drawer-side is-${side}${expanded ? ' is-open' : ''}${state.pinned ? ' is-pinned' : ''}`}
      style={{ ['--drawer-w' as string]: `${width}px` }}
      onMouseEnter={() => { cancelClose(); setHovered(true) }}
      onMouseLeave={scheduleClose}
      aria-label={title}
    >
      {/* 收起态：竖排标题 + 展开按钮 */}
      <div className="wf-drawer-rail">
        <button
          type="button"
          className="wf-drawer-icon"
          title={state.pinned ? '取消钉住' : '钉住展开'}
          aria-label={state.pinned ? '取消钉住侧栏' : '钉住展开侧栏'}
          onClick={() => setState((current) => ({ pinned: !current.pinned }))}
        >
          {state.pinned ? <Pin size={13} /> : <PinOff size={13} />}
        </button>
        <span className="wf-drawer-rail-title">{title}</span>
        <button
          type="button"
          className="wf-drawer-icon"
          title={expanded ? '收起' : '展开'}
          aria-label={expanded ? '收起侧栏' : '展开侧栏'}
          onClick={() => {
            // 点击切换「钉住」，这样用户不必一直悬停才能操作。
            setState((current) => ({ pinned: !current.pinned }))
            setHovered(true)
          }}
        >
          {expanded
            ? (side === 'left' ? <PanelLeftClose size={13} /> : <PanelLeftClose size={13} />)
            : (side === 'left' ? <PanelLeftOpen size={13} /> : <PanelLeftOpen size={13} />)}
        </button>
      </div>

      {/* 展开态内容：浮层覆盖画布，不挤压 */}
      <div className="wf-drawer-body">{children}</div>
    </aside>
  )
}
