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
import type { WorkflowTemplateRef } from '../../lib/workflowTemplateStore'
import type { WorkflowDefinition } from '../../types'

interface TemplateInsertPanelProps {
  workflows: WorkflowDefinition[]
  /** 本地模板库：引用式条目，插入时按 sourceWorkflowUuid 现读最新图。 */
  templates: WorkflowTemplateRef[]
  currentWorkflowUuid?: string
  onClose: () => void
  onNotify: (message: string) => void
  onRemoveTemplate: (template: WorkflowTemplateRef) => void
  onInsert: (graph: {
    nodes: RawWorkflowNode[]
    edges: RawWorkflowEdge[]
    handleTemplates: Record<string, any>[]
  }, label: string) => void
}

/** 插入来源：直接挑工作流，或挑一条本地模板引用。 */
type InsertSource = { kind: 'workflow'; uuid: string } | { kind: 'template'; id: string }

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
  templates,
  currentWorkflowUuid,
  onClose,
  onNotify,
  onRemoveTemplate,
  onInsert,
}: TemplateInsertPanelProps) {
  const candidates = workflows.filter((workflow) => workflow.uuid !== currentWorkflowUuid)
  // 模板引用可能指向已被删除的工作流：这里按现存工作流做一次存在性校验，
  // 失效条目仍展示但禁止插入，避免点了才报错。
  const liveWorkflowIds = new Set(workflows.map((workflow) => workflow.uuid))
  const [source, setSource] = useState<InsertSource>(() => (
    templates.length
      ? { kind: 'template', id: templates[0].id }
      : { kind: 'workflow', uuid: candidates[0]?.uuid || '' }
  ))
  const [preview, setPreview] = useState<Preview | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const activeTemplate = source.kind === 'template'
    ? templates.find((item) => item.id === source.id)
    : undefined
  // 引用式模板：uuid 从模板条目取，内容现读。
  const targetUuid = source.kind === 'template'
    ? (activeTemplate?.sourceWorkflowUuid || '')
    : source.uuid
  const targetMissing = Boolean(targetUuid) && !liveWorkflowIds.has(targetUuid)

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
    void load(targetUuid)
  }, [load, targetUuid])

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
        {templates.length ? (
          <div className="wf-template-section">
            <div className="wf-template-head">
              <strong>我的模板</strong>
              <small>{templates.length} 个（引用式：插入时读取源工作流最新图）</small>
            </div>
            <ul className="wf-template-list">
              {templates.map((template) => {
                const missing = !liveWorkflowIds.has(template.sourceWorkflowUuid)
                const active = source.kind === 'template' && source.id === template.id
                return (
                  <li key={template.id} className={active ? 'is-active' : ''}>
                    <button
                      type="button"
                      className="wf-template-pick"
                      onClick={() => setSource({ kind: 'template', id: template.id })}
                      aria-pressed={active}
                    >
                      <span>{template.name}</span>
                      <em>{missing ? '源工作流已删除' : `基于 r${template.sourceRevision}`}</em>
                    </button>
                    <button
                      type="button"
                      className="wf-template-remove"
                      aria-label={`移除模板 ${template.name}`}
                      title="从模板库移除（不影响源工作流）"
                      onClick={() => onRemoveTemplate(template)}
                    >
                      <X size={12} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        ) : null}

        {candidates.length === 0 && !templates.length ? (
          <p className="wf-props-empty">
            当前只有这一个工作流，也没有本地模板。可以在工作流列表里右键任意工作流
            选「存为模板」，或用「导入导出」导入一份 JSON 后再来复用。
          </p>
        ) : (
          <>
            <label className="wf-field">
              <span className="wf-field-label"><strong>或直接选来源工作流</strong></span>
              <select
                value={source.kind === 'workflow' ? source.uuid : ''}
                onChange={(event) => setSource({ kind: 'workflow', uuid: event.target.value })}
              >
                <option value="">（使用上方选中的模板）</option>
                {candidates.map((workflow) => (
                  <option key={workflow.uuid} value={workflow.uuid}>
                    {workflow.name}（r{workflow.revision}／{workflow.status}）
                  </option>
                ))}
              </select>
            </label>

            {targetMissing ? (
              <p className="wf-io-hint is-error">
                该模板的来源工作流已不存在，无法读取图内容。请改用其他来源，或移除这条模板。
              </p>
            ) : loading ? (
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
