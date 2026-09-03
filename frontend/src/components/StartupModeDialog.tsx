import { useEffect, useRef } from 'react'
import { AlertTriangle, ArrowRight, Code2, LoaderCircle, ShieldCheck, X } from 'lucide-react'
import type { StartupMode, StartupModeSwitchBlocker } from '../types'
import { Button } from './ui'

const modePresentation = {
  develop: { label: '开发模式', icon: Code2 },
  product: { label: '生产模式', icon: ShieldCheck },
} satisfies Record<StartupMode, { label: string; icon: typeof Code2 }>

const taskStatusLabels: Record<string, string> = {
  pending: '待运行',
  running: '运行中',
  canceling: '取消中',
  failed: '已失败',
  succeeded: '已完成',
  canceled: '已取消',
  timeout: '已超时',
}

const cleanupStatusLabels: Record<string, string> = {
  pending: '等待清理',
  required: '需要清理',
  canceling: '正在清理',
  requires_attention: '清理需人工处理',
}

export function StartupModeDialog({
  currentMode,
  pending,
  blockers,
  errorMessage,
  onClose,
  onConfirm,
}: {
  currentMode: StartupMode
  pending: boolean
  blockers: StartupModeSwitchBlocker[]
  errorMessage?: string
  onClose: () => void
  onConfirm: () => void
}) {
  const dialogRef = useRef<HTMLElement>(null)
  const pendingRef = useRef(pending)
  pendingRef.current = pending
  const targetMode: StartupMode = currentMode === 'develop' ? 'product' : 'develop'
  const CurrentIcon = modePresentation[currentMode].icon
  const TargetIcon = modePresentation[targetMode].icon

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !pendingRef.current) onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !pending) onClose()
    }}>
      <section
        ref={dialogRef}
        className="task-dialog startup-mode-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="startup-mode-dialog-title"
        aria-describedby="startup-mode-dialog-description"
        tabIndex={-1}
      >
        <form onSubmit={(event) => { event.preventDefault(); onConfirm() }}>
          <header>
            <div>
              <span>RUNTIME MODE</span>
              <h2 id="startup-mode-dialog-title">切换至{modePresentation[targetMode].label}</h2>
              <p id="startup-mode-dialog-description">切换前将由 Edge 权威检查活动 Task 与资源清理状态。</p>
            </div>
            <button type="button" aria-label="关闭模式切换弹窗" disabled={pending} onClick={onClose}><X size={18} /></button>
          </header>
          <div className="dialog-content startup-mode-dialog-content">
            <div className="startup-mode-transition" aria-label="模式变化">
              <span><CurrentIcon size={17} />{modePresentation[currentMode].label}</span>
              <ArrowRight size={17} />
              <span><TargetIcon size={17} />{modePresentation[targetMode].label}</span>
            </div>
            <p className="startup-mode-session-note">
              仅影响当前 Runtime 会话，不修改部署配置；Runtime 重启后仍按启动配置进入默认模式。
            </p>
            {errorMessage ? (
              <div className="startup-mode-error" role="alert">
                <AlertTriangle size={17} />
                <div><strong>暂时无法切换</strong><p>{errorMessage}</p></div>
              </div>
            ) : null}
            {blockers.length ? (
              <div className="startup-mode-blockers">
                <strong>阻塞任务</strong>
                {blockers.map((blocker) => (
                  <article key={blocker.taskUuid}>
                    <code>{blocker.taskUuid}</code>
                    <span>{taskStatusLabels[blocker.status] || blocker.status}</span>
                    {cleanupStatusLabels[blocker.cleanupStatus]
                      ? <em>{cleanupStatusLabels[blocker.cleanupStatus]}</em>
                      : null}
                  </article>
                ))}
              </div>
            ) : null}
          </div>
          <footer>
            <Button type="button" disabled={pending} onClick={onClose}>取消</Button>
            <Button type="submit" tone="primary" disabled={pending} icon={pending ? <LoaderCircle className="spin" size={16} /> : <TargetIcon size={16} />}>
              {pending ? '正在检查…' : blockers.length ? '重新检查并切换' : '确认切换'}
            </Button>
          </footer>
        </form>
      </section>
    </div>
  )
}
