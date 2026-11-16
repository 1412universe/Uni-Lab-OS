import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, X } from 'lucide-react'

/**
 * 顶部提示条。
 *
 * 设计约束（来自实际使用中的痛点）：
 *
 * 1. **必须可复制**。报错常常带节点 UUID、字段路径这类需要拿去搜索或转述的
 *    内容，而全局 `.toast` 规则里有 `pointer-events: none`，鼠标直接穿透，
 *    连选中文本都做不到。这里把内容区显式恢复为可交互（`pointer-events: auto`
 *    且 `user-select: text`），外层仍保持穿透，不挡住页面其它点击。
 *
 * 2. **错误不应自动消失**。成功类提示几秒后消失没问题；但一串很长的报错在
 *    3.2 秒后消失，用户根本来不及读完，更别说复制。因此错误提示常驻，直到
 *    用户主动关闭或复制。
 *
 * 3. **复制入口只用一个图标**。文字按钮在中英混排的窄条里显得笨重，一个图标
 *    加悬浮说明足够，且视觉上不抢内容。
 */
export interface ToastBarProps {
  message: string
  onDismiss: () => void
}

/** 判断一条提示是否属于「需要用户处理」的错误，从而不自动消失。 */
function looksLikeError(message: string): boolean {
  return /失败|错误|异常|无法|不可用|未就绪|拒绝|超时|failed|error/i.test(message)
}

export function ToastBar({ message, onDismiss }: ToastBarProps) {
  const [copied, setCopied] = useState(false)
  const sticky = useMemo(() => looksLikeError(message), [message])

  // 复制反馈只存活 1.5 秒，避免图标长期停在「已复制」状态。
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 1500)
    return () => window.clearTimeout(timer)
  }, [copied])

  // 换新消息时重置复制态，否则会延续上一条的勾选图标。
  useEffect(() => setCopied(false), [message])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message)
      setCopied(true)
    } catch {
      // 剪贴板不可用（非安全上下文等）：退回到手动选中，不给用户假成功。
      const range = document.createRange()
      const node = document.querySelector('[data-toast-text]')
      if (node) {
        range.selectNodeContents(node)
        const selection = window.getSelection()
        selection?.removeAllRanges()
        selection?.addRange(range)
      }
    }
  }

  return (
    <div
      className={`toast ${message ? 'show' : ''} ${sticky ? 'toast-sticky' : ''}`}
      role={sticky ? 'alert' : 'status'}
      aria-live={sticky ? 'assertive' : 'polite'}
    >
      <div className="toast-inner">
        <span className="toast-text" data-toast-text>
          {message}
        </span>
        <span className="toast-actions">
          <button
            type="button"
            className={`toast-icon${copied ? ' is-copied' : ''}`}
            title={copied ? '已复制' : '复制这条提示'}
            aria-label={copied ? '已复制' : '复制这条提示'}
            onClick={() => void copy()}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
          <button
            type="button"
            className="toast-icon"
            title="关闭"
            aria-label="关闭提示"
            onClick={onDismiss}
          >
            <X size={14} />
          </button>
        </span>
      </div>
    </div>
  )
}
