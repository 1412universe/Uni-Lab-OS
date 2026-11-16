/**
 * 工作流编辑 Agent 面板（右下角可拖动浮窗）。
 *
 * 与 ChemWorldModel 的 `wf_agent.js` 对齐的能力：
 * - 对话式批量生成 / 修改当前画布
 * - 支持外部接口（默认 GLM-4.5-Flash）与**本地接口**（Ollama / vLLM）切换
 * - 模型给出候选图时，显示「应用到画布」，由页面走既有整图保存流程
 *
 * 刻意不做的事：Agent **不直接写库**。它只返回候选图，应用动作交给页面，
 * 这样撤销栈、revision 冲突检测、源码回写都与手工编辑完全同一路径。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Bot, Copy, LoaderCircle, Send, Settings2, Sparkles, X } from 'lucide-react'
import {
  loadWorkflowAgentConfig,
  sendWorkflowAgentTurn,
  type RawWorkflowEdge,
  type RawWorkflowNode,
  type WorkflowAgentConfig,
} from '../../lib/edgeClient'

type ModelMode = 'ext' | 'local'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

const HISTORY_KEY = 'unilab_wf_agent_history_v1'
const MODEL_KEY = 'unilab_wf_agent_model_v1'
const POS_KEY = 'unilab_wf_agent_pos_v1'

interface StoredModel {
  mode: ModelMode
  extModel: string
  localUrl: string
  localModel: string
  localKey: string
}

function loadStored<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? { ...fallback, ...(JSON.parse(raw) as object) } as T : fallback
  } catch {
    return fallback
  }
}

function saveStored(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 隐私模式下 localStorage 可能不可写；Agent 仍可本次会话内使用。
  }
}

export interface WorkflowAgentPanelProps {
  /** 当前画布快照；作为 Agent 的上下文。 */
  graph: { nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[] }
  /** 应用候选图；由页面负责整图保存与撤销栈。 */
  onApplyCandidate: (graph: { nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[] }) => void
  onClose: () => void
  onNotify: (message: string) => void
}

export function WorkflowAgentPanel({
  graph,
  onApplyCandidate,
  onClose,
  onNotify,
}: WorkflowAgentPanelProps) {
  const [config, setConfig] = useState<WorkflowAgentConfig | null>(null)
  const [model, setModel] = useState<StoredModel>(() => loadStored<StoredModel>(MODEL_KEY, {
    mode: 'ext',
    extModel: 'GLM-4.5-Flash',
    localUrl: 'http://127.0.0.1:11434/v1',
    localModel: 'qwen2.5:7b',
    localKey: '',
  }))
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadStored<{ items: ChatMessage[] }>(
    HISTORY_KEY, { items: [] },
  ).items)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [candidate, setCandidate] = useState<{ nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[] } | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const [pos, setPos] = useState(() => loadStored(POS_KEY, { x: 0, y: 0 }))
  const dragging = useRef<{ dx: number; dy: number } | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    loadWorkflowAgentConfig(controller.signal)
      .then((value) => {
        setConfig(value)
        // 只在用户没保存过外部模型名时，采用服务端默认。
        setModel((current) => (
          current.extModel === 'GLM-4.5-Flash' ? { ...current, extModel: value.defaultModel } : current
        ))
      })
      .catch(() => setConfig(null))
    return () => controller.abort()
  }, [])

  useEffect(() => { saveStored(HISTORY_KEY, { items: messages.slice(-60) }) }, [messages])
  useEffect(() => { saveStored(MODEL_KEY, model) }, [model])
  useEffect(() => { saveStored(POS_KEY, pos) }, [pos])

  // 新消息到达后把列表滚到底，否则用户看不到刚生成的内容。
  useEffect(() => {
    const node = listRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [messages, busy])

  const endpoint = useMemo(() => (
    model.mode === 'local'
      ? { model: model.localModel, baseUrl: model.localUrl, apiKey: model.localKey }
      : { model: model.extModel }
  ), [model])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return
    const history = messages.slice(-4)
    setMessages((current) => [...current, { role: 'user', content: text }])
    setInput('')
    setBusy(true)
    setError('')
    setCandidate(null)
    try {
      const result = await sendWorkflowAgentTurn({
        message: text,
        graph: { nodes: graph.nodes, edges: graph.edges },
        history,
        ...endpoint,
      })
      if (!result.ok) {
        setError(result.error || 'Agent 调用失败')
        return
      }
      setMessages((current) => [...current, { role: 'assistant', content: result.reply || '(空回复)' }])
      if (result.candidate && result.candidate.nodes.length) {
        setCandidate(result.candidate)
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Agent 调用失败')
    } finally {
      setBusy(false)
    }
  }, [busy, endpoint, graph.edges, graph.nodes, input, messages])

  const applyCandidate = useCallback(() => {
    if (!candidate) return
    onApplyCandidate(candidate)
    setCandidate(null)
    onNotify('已把 Agent 的候选图应用到画布（记得保存）')
  }, [candidate, onApplyCandidate, onNotify])

  // 拖动：只改 transform 偏移，不触发布局；松手时落盘。
  const beginDrag = useCallback((event: React.PointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return
    dragging.current = { dx: event.clientX - pos.x, dy: event.clientY - pos.y }
    event.currentTarget.setPointerCapture(event.pointerId)
  }, [pos.x, pos.y])
  const moveDrag = useCallback((event: React.PointerEvent<HTMLElement>) => {
    const drag = dragging.current
    if (!drag) return
    setPos({
      x: Math.max(-window.innerWidth + 280, Math.min(0, event.clientX - drag.dx)),
      y: Math.max(-window.innerHeight + 200, Math.min(0, event.clientY - drag.dy)),
    })
  }, [])
  const endDrag = useCallback((event: React.PointerEvent<HTMLElement>) => {
    dragging.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }, [])

  return (
    <section
      className={`wf-agent${collapsed ? ' is-collapsed' : ''}`}
      style={{ transform: `translate(${pos.x}px, ${pos.y}px)` }}
      aria-label="工作流编辑 Agent"
    >
      <header
        className="wf-agent-head"
        onPointerDown={beginDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
      >
        <Bot size={14} />
        <strong>工作流 Agent</strong>
        <small title={endpoint.model}>{endpoint.model}</small>
        <span className="wf-agent-actions">
          <button
            type="button"
            title={settingsOpen ? '收起模型设置' : '模型与端点设置'}
            aria-label="模型与端点设置"
            onClick={() => setSettingsOpen((value) => !value)}
          ><Settings2 size={13} /></button>
          <button
            type="button"
            title={collapsed ? '展开' : '收起'}
            aria-label={collapsed ? '展开 Agent' : '收起 Agent'}
            onClick={() => setCollapsed((value) => !value)}
          ><Sparkles size={13} /></button>
          <button type="button" title="关闭" aria-label="关闭 Agent" onClick={onClose}><X size={13} /></button>
        </span>
      </header>

      {settingsOpen ? (
        <div className="wf-agent-settings">
          <label>
            <span>模型来源</span>
            <select
              value={model.mode}
              onChange={(event) => setModel({ ...model, mode: event.target.value as ModelMode })}
            >
              <option value="ext">外部接口（{config?.defaultModel || 'GLM-4.5-Flash'}）</option>
              <option value="local">本地接口（Ollama / vLLM）</option>
            </select>
          </label>
          {model.mode === 'ext' ? (
            <label>
              <span>模型名</span>
              <input
                value={model.extModel}
                onChange={(event) => setModel({ ...model, extModel: event.target.value })}
                placeholder="GLM-4.5-Flash"
              />
            </label>
          ) : (
            <>
              <label>
                <span>端点</span>
                <input
                  value={model.localUrl}
                  onChange={(event) => setModel({ ...model, localUrl: event.target.value })}
                  placeholder={config?.localHint || 'http://127.0.0.1:11434/v1'}
                />
              </label>
              <label>
                <span>模型名</span>
                <input
                  value={model.localModel}
                  onChange={(event) => setModel({ ...model, localModel: event.target.value })}
                  placeholder="qwen2.5:7b"
                />
              </label>
              <label>
                <span>密钥（可空）</span>
                <input
                  type="password"
                  value={model.localKey}
                  onChange={(event) => setModel({ ...model, localKey: event.target.value })}
                  placeholder="本地服务通常不需要"
                />
              </label>
            </>
          )}
          <p className="wf-agent-hint">
            {config?.hasServerKey
              ? '外部接口的服务端密钥已配置；切换本地接口可直接对接本机模型。'
              : '服务端未配置外部密钥：可改用本地接口，或由后端设置 UNILABOS_WF_AGENT_KEY。'}
          </p>
        </div>
      ) : null}

      {collapsed ? null : (
        <>
          <div className="wf-agent-msgs" ref={listRef}>
            {!messages.length ? (
              <p className="wf-agent-empty">
                让我看看当前画布，或直接说要改什么。例如：<br />
                「这两个节点没有依赖，应该并行」<br />
                「把所有节点按执行顺序重排」
              </p>
            ) : messages.map((item, index) => (
              <article className={`wf-agent-msg is-${item.role}`} key={`${item.role}-${index}`}>
                <pre>{item.content}</pre>
              </article>
            ))}
            {busy ? <p className="wf-agent-busy"><LoaderCircle size={13} className="spin" />思考中…</p> : null}
            {error ? <p className="wf-agent-err" role="alert">{error}</p> : null}
          </div>

          {candidate ? (
            <div className="wf-agent-candidate">
              <span>
                模型给出候选图：{candidate.nodes.length} 节点 / {candidate.edges.length} 连线
              </span>
              <button type="button" onClick={applyCandidate}>应用到画布</button>
              <button type="button" className="ghost" onClick={() => setCandidate(null)}>忽略</button>
            </div>
          ) : null}

          <div className="wf-agent-compose">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault()
                  void send()
                }
              }}
              placeholder="描述你想怎么改，⌘/Ctrl+Enter 发送"
              rows={2}
            />
            <button
              type="button"
              className="wf-agent-send"
              disabled={!input.trim() || busy}
              onClick={() => void send()}
              title="发送"
              aria-label="发送"
            >
              {busy ? <LoaderCircle size={14} className="spin" /> : <Send size={14} />}
            </button>
          </div>

          {messages.length ? (
            <button
              type="button"
              className="wf-agent-clear"
              onClick={() => { setMessages([]); setCandidate(null); setError('') }}
            >
              <Copy size={11} />清空对话
            </button>
          ) : null}
        </>
      )}
    </section>
  )
}
