/**
 * 模板插入：把另一份工作流的节点图整段克隆进当前草稿。
 *
 * 为什么不走后端 composite-invocations：那条路线要求子流程已发布成
 * `experiment_operation` 合同，而本机目录当前一份已发布合同都没有（接口返回空）。
 * 克隆式插入不依赖发布状态，跨工作流复用「离心三步」「清洗循环」这类片段最直接，
 * 也仍然遵守「本地草稿 + 一次整图保存」的编辑模型。
 */
import { useCallback, useEffect, useState } from 'react'
import { Copy, Layers, Loader2, X } from 'lucide-react'
import { Button } from '../ui'
import { loadWorkflowGraphRaw, type RawWorkflowEdge, type RawWorkflowNode } from '../../lib/edgeClient'
import { nodeDeviceId, nodePosition } from '../../lib/workflowEditorModel'
import type { WorkflowDefinition } from '../../types'

interface TemplateInsertPanelProps {
  workflows: WorkflowDefinition[]
  currentWorkflowUuid?: string
  onClose: () => void
  onNotify: (message: string) => void
  onInsert: (graph: {
    nodes: RawWorkflowNode[]
    edges: RawWorkflowEdge[]
    handleTemplates: Record<string, any>[]
  }, label: string) => void
}

interface Preview {
  uuid: string
  name: string
  nodes: RawWorkflowNode[]
  edges: RawWorkflowEdge[]
  handleTemplates: Record<string, any>[]
  devices: string[]
  span: { width: number; height: number }
}

export function TemplateInsertPanel({
  workflows,
  currentWorkflowUuid,
  onClose,
  onNotify,
  onInsert,
}: TemplateInsertPanelProps) {
  const candidates = workflows.filter((workflow) => workflow.uuid !== currentWorkflowUuid)
  const [activeUuid, setActiveUuid] = useState(candidates[0]?.uuid || '')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async (uuid: string) => {
    if (!uuid) {
      setPreview(null)
      return
    }
    setLoading(true)
    setError('')
    try {
      const graph = await loadWorkflowGraphRaw(uuid)
      const positions = graph.nodes.map((node) => nodePosition(node))
      const xs = positions.map((point) => point.x)
      const ys = positions.map((point) => point.y)
      const devices = [...new Set(graph.nodes.map((node) => nodeDeviceId(node)).filter(Boolean))]
      setPreview({
        uuid,
        name: String(graph.workflow.name || '未命名工作流'),
        nodes: graph.nodes,
        edges: graph.edges,
        handleTemplates: graph.handleTemplates,
        devices,
        span: {
          width: xs.length ? Math.max(...xs) - Math.min(...xs) : 0,
          height: ys.length ? Math.max(...ys) - Math.min(...ys) : 0,
        },
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取工作流图失败')
      setPreview(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(activeUuid)
  }, [activeUuid, load])

  const insert = () => {
    if (!preview) return
    if (!preview.nodes.length) {
      onNotify('该工作流没有节点可插入')
      return
    }
    onInsert({
      nodes: preview.nodes,
      edges: preview.edges,
      handleTemplates: preview.handleTemplates,
    }, preview.name)
  }

  return (
    <div className="wf-drawer is-wide" role="dialog" aria-label="插入模板">
      <header>
        <strong>插入模板（另一份工作流的节点图）</strong>
        <button type="button" aria-label="关闭" onClick={onClose}><X size={15} /></button>
      </header>
      <div className="wf-drawer-body">
        {candidates.length === 0 ? (
          <p className="wf-props-empty">
            当前只有这一个工作流，没有可复用的模板。可以先用「导入导出」导入一份 JSON，
            它就出现在这里的候选列表里。
          </p>
        ) : (
          <>
            <label className="wf-field">
              <span className="wf-field-label"><strong>来源工作流</strong></span>
              <select value={activeUuid} onChange={(event) => setActiveUuid(event.target.value)}>
                {candidates.map((workflow) => (
                  <option key={workflow.uuid} value={workflow.uuid}>
                    {workflow.name}（r{workflow.revision}／{workflow.status}）
                  </option>
                ))}
              </select>
            </label>

            {loading ? (
              <p className="wf-io-hint"><Loader2 size={13} className="wf-spin" /> 正在读取图…</p>
            ) : error ? (
              <p className="wf-io-hint is-error">{error}</p>
            ) : preview ? (
              <>
                <dl className="wf-insert-stats">
                  <div><dt>节点</dt><dd>{preview.nodes.length}</dd></div>
                  <div><dt>连线</dt><dd>{preview.edges.length}</dd></div>
                  <div><dt>涉及设备</dt><dd>{preview.devices.length}</dd></div>
                  <div><dt>原始跨度</dt><dd>{Math.round(preview.span.width)} × {Math.round(preview.span.height)}</dd></div>
                </dl>
                <ul className="wf-insert-devices">
                  {preview.devices.slice(0, 8).map((device) => (
                    <li key={device}><Layers size={11} />{device}</li>
                  ))}
                </ul>
                <p className="wf-io-hint">
                  插入会把节点与连线复制到当前画布右侧，节点/连线 UUID 全部换新，
                  参数与设备绑定原样保留；插入后仍是一次整图保存才落库。
                </p>
                <Button tone="primary" icon={<Copy size={13} />} onClick={insert}>
                  插入到当前画布
                </Button>
              </>
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
