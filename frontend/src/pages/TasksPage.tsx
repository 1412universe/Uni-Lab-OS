import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertCircle,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  ExternalLink,
  FlaskConical,
  LoaderCircle,
  Plus,
  Pause,
  Play,
  RefreshCw,
  Send,
  ShieldAlert,
  Square,
  StepForward,
  X,
} from 'lucide-react'
import { commandWorkflowTask, createWorkflowTask, decideManualConfirmation, loadWorkflowTaskDetail, loadWorkflowTaskStepState } from '../lib/edgeClient'
import type { ContractField, MaterialRecord, TaskNode, WorkflowDefinition, WorkflowTarget, WorkflowTask } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/ui'

type TaskFilter = 'all' | 'running' | 'waiting' | 'failed' | 'succeeded'

const TASK_IDENTITY_COLUMN_WIDTH = 300
const TASK_NODE_COLUMN_WIDTH = 160
const TASK_PROGRESS_COLUMN_WIDTH = 86

const nodeStatusLabels: Record<TaskNode['status'], string> = {
  succeeded: '已完成',
  running: '正在运行',
  waiting: '等待资源',
  failed: '失败',
  pending: '待运行',
  skipped: '已跳过',
  canceling: '取消中',
  canceled: '已取消',
  attention: '需要人工确认',
}

const taskPriorityLabels = {
  urgent: '紧急优先级',
  high: '高优先级',
  normal: '普通优先级',
  low: '低优先级',
  unknown: '优先级未知',
} as const

function presentTaskPriority(priority: WorkflowTask['priority']) {
  if (typeof priority === 'number') return { label: `权重 ${priority}`, tone: 'custom' }
  return { label: taskPriorityLabels[priority], tone: priority }
}

function matchesFilter(task: WorkflowTask, filter: TaskFilter) {
  if (filter === 'all') return true
  if (filter === 'running') return task.status === 'running' || task.status === 'canceling'
  if (filter === 'waiting') return ['admission_blocked', 'paused'].includes(task.status)
  if (filter === 'failed') return ['failed', 'timeout', 'intervention_required', 'execution_unknown', 'unknown'].includes(task.status)
  return task.status === 'succeeded'
}

function NodeMarker({
  node,
  index,
  selected,
  ready,
  writable,
  onSelect,
  onNotify,
}: {
  node?: TaskNode
  index: number
  selected: boolean
  ready?: boolean
  writable: boolean
  onSelect: () => void
  onNotify: (message: string) => void
}) {
  const status = node?.status || 'pending'
  const waitReason = node?.waitReason
  const markerRef = useRef<HTMLButtonElement>(null)
  const tooltipId = useId()
  const [tooltipVisible, setTooltipVisible] = useState(false)
  const [tooltipPosition, setTooltipPosition] = useState({ left: 0, top: 0, above: false })
  const [now, setNow] = useState(() => Date.now())
  const queryClient = useQueryClient()
  const confirmation = node?.job?.manualConfirmation
  const awaitingConfirmation = confirmation?.status === 'pending'
  const decision = useMutation({
    mutationFn: (action: 'approve' | 'reject') => {
      if (!writable) throw new Error('Edge 未连接，写操作已暂停')
      return decideManualConfirmation(node?.job?.uuid || '', action)
    },
    onSuccess: (_result, action) => {
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      onNotify(action === 'approve' ? '人工确认已批准，设备动作将继续执行。' : '人工确认已拒绝，任务正在取消。')
    },
    onError: (error) => onNotify(error instanceof Error ? error.message : '人工确认提交失败'),
  })
  useEffect(() => {
    if (!awaitingConfirmation) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [awaitingConfirmation])
  const showTooltip = useCallback(() => {
    if (!markerRef.current) return
    const rect = markerRef.current.getBoundingClientRect()
    const above = rect.top > 190
    const halfWidth = 142
    setTooltipPosition({
      left: Math.min(Math.max(rect.left + rect.width / 2, halfWidth + 10), window.innerWidth - halfWidth - 10),
      top: above ? rect.top - 10 : rect.bottom + 10,
      above,
    })
    setTooltipVisible(true)
  }, [])

  useEffect(() => {
    if (!tooltipVisible) return undefined
    const hideTooltip = () => setTooltipVisible(false)
    document.addEventListener('scroll', hideTooltip, true)
    window.addEventListener('resize', hideTooltip)
    return () => {
      document.removeEventListener('scroll', hideTooltip, true)
      window.removeEventListener('resize', hideTooltip)
    }
  }, [tooltipVisible])

  const label = `${node?.name || `节点 ${index + 1}`}，${nodeStatusLabels[status]}`
  const remainingSeconds = awaitingConfirmation && confirmation.deadlineAt
    ? Math.max(0, Math.ceil((new Date(confirmation.deadlineAt).getTime() - now) / 1000))
    : 0
  return (
    <div
      className={`matrix-node matrix-node-${status} ${awaitingConfirmation ? 'matrix-node-manual-confirmation' : ''} ${selected ? 'matrix-node-selected' : ''} ${ready ? 'matrix-node-step-ready' : ''}`}
    >
      <button
        type="button"
        ref={markerRef}
        className="matrix-node-select"
        aria-label={label}
        aria-pressed={selected}
        aria-describedby={tooltipVisible ? tooltipId : undefined}
        onClick={(event) => {
          event.stopPropagation()
          onSelect()
        }}
        onMouseEnter={showTooltip}
        onMouseLeave={() => setTooltipVisible(false)}
        onFocus={showTooltip}
        onBlur={() => setTooltipVisible(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setTooltipVisible(false)
        }}
      >
        <span className="matrix-node-marker">
          {status === 'succeeded' || status === 'skipped'
            ? <Check size={13} />
            : status === 'running' || status === 'canceling'
              ? <LoaderCircle size={13} />
              : status === 'failed' || status === 'canceled'
                ? <X size={13} />
              : status === 'attention' && awaitingConfirmation
                  ? <ShieldAlert size={13} />
                  : status === 'attention'
                    ? <X size={13} />
                    : index + 1}
        </span>
        <small className="matrix-node-meta">{String(index + 1).padStart(2, '0')} · {nodeStatusLabels[status]}</small>
        <strong className="matrix-node-title">{node?.name || `节点 ${index + 1}`}</strong>
      </button>
      {awaitingConfirmation ? (
        <div className="manual-confirmation-actions" onClick={(event) => event.stopPropagation()}>
          <small>剩余 {remainingSeconds}s</small>
          <span>
            <button type="button" disabled={!writable || decision.isPending} onClick={() => decision.mutate('reject')}>拒绝</button>
            <button type="button" disabled={!writable || decision.isPending} onClick={() => decision.mutate('approve')}>批准</button>
          </span>
        </div>
      ) : null}
      {tooltipVisible && typeof document !== 'undefined' && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className={`node-wait-tooltip ${tooltipPosition.above ? 'node-wait-tooltip-above' : 'node-wait-tooltip-below'}`}
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{node?.name || `节点 ${index + 1}`}</strong>
          <p>{nodeStatusLabels[status]}</p>
          {waitReason ? (
            <>
              <em>{waitReason.title}</em>
              <p>{waitReason.message}</p>
              {waitReason.details.length > 0 && (
                <ul>{waitReason.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
              )}
              {waitReason.waitingSince && <small>等待开始：{waitReason.waitingSince}</small>}
            </>
          ) : null}
        </div>,
        document.body,
      )}
    </div>
  )
}

function TaskMatrix({
  tasks,
  selectedId,
  selectedNode,
  onSelect,
  onSelectNode,
  onOpenWorkflow,
  onNotify,
  writable,
  readyNodeUuids = new Set<string>(),
  selectedTaskControl,
}: {
  tasks: WorkflowTask[]
  selectedId: string
  selectedNode?: { taskUuid: string; nodeUuid: string }
  onSelect: (id: string) => void
  onSelectNode: (taskUuid: string, nodeUuid: string) => void
  onOpenWorkflow: (target: WorkflowTarget) => void
  onNotify: (message: string) => void
  writable: boolean
  readyNodeUuids?: ReadonlySet<string>
  selectedTaskControl?: ReactNode
}) {
  const maxNodeCount = Math.max(1, ...tasks.map((task) => task.nodes.length))
  const matrixWidth = TASK_IDENTITY_COLUMN_WIDTH
    + maxNodeCount * TASK_NODE_COLUMN_WIDTH
    + TASK_PROGRESS_COLUMN_WIDTH

  return (
    <div className="task-matrix-scroll">
      <div className="matrix-body" style={{ minWidth: matrixWidth }}>
        {tasks.map((task) => {
          const nodes: (TaskNode | undefined)[] = task.nodes.length ? task.nodes : [undefined]
          const columns = `${TASK_IDENTITY_COLUMN_WIDTH}px repeat(${nodes.length}, ${TASK_NODE_COLUMN_WIDTH}px) ${TASK_PROGRESS_COLUMN_WIDTH}px minmax(0, 1fr)`
          const priority = presentTaskPriority(task.priority)
          return (
            <div
              key={task.uuid}
              className={`matrix-row ${selectedId === task.uuid ? 'selected' : ''}`}
              style={{ gridTemplateColumns: columns }}
              onClick={() => onSelect(task.uuid)}
            >
              <div className={`matrix-task-cell ${selectedId === task.uuid && selectedTaskControl ? 'matrix-task-cell-with-control' : ''}`}>
                <button
                  type="button"
                  className="matrix-task-select"
                  aria-label={`打开工作流 ${task.workflowName}，Task ${task.uuid}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    onOpenWorkflow({
                      workflowUuid: task.workflowUuid,
                      revision: task.workflowRevision,
                      taskUuid: task.uuid,
                    })
                  }}
                >
                  <span className={`task-state-dot task-state-${task.status}`} />
                  <span>
                    <strong>{task.workflowName}</strong>
                    <small>{task.uuid}</small>
                    <small>{task.sample} · {task.updatedAt}</small>
                  </span>
                  <span className="matrix-task-badges">
                    <span
                      className={`matrix-task-priority matrix-task-priority-${priority.tone}`}
                      title={`任务优先级：${priority.label}`}
                    >
                      {priority.label}
                    </span>
                    <em className="matrix-task-revision">{task.workflowRevision ? `r${task.workflowRevision}` : '—'}</em>
                  </span>
                </button>
                {task.trace ? (
                  <a
                    className="matrix-trace-link"
                    href={task.trace.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`在 SigNoz 中查看 ${task.uuid} 的 Trace`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <ExternalLink size={12} />Trace
                  </a>
                ) : (
                  <button type="button" className="matrix-trace-disabled" disabled title="Trace 服务未配置">Trace</button>
                )}
                {selectedId === task.uuid && selectedTaskControl ? (
                  <div
                    className="matrix-task-inline-control"
                    onClick={(event) => event.stopPropagation()}
                  >
                    {selectedTaskControl}
                  </div>
                ) : null}
              </div>
              {nodes.map((node, index) => (
                <NodeMarker
                  key={node?.uuid || `empty-${task.uuid}`}
                  node={node}
                  index={index}
                  selected={Boolean(node && selectedNode?.taskUuid === task.uuid && selectedNode.nodeUuid === node.uuid)}
                  ready={Boolean(
                    node
                    && task.uuid === selectedId
                    && readyNodeUuids.has(node.uuid)
                  )}
                  writable={writable}
                  onSelect={() => {
                    if (node) onSelectNode(task.uuid, node.uuid)
                  }}
                  onNotify={onNotify}
                />
              ))}
              <div className="matrix-progress-cell"><strong>{task.progress}%</strong><span><i style={{ width: `${task.progress}%` }} /></span></div>
              <div className="matrix-row-tail" aria-hidden="true" />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function jsonEvidence(value: unknown, emptyLabel: string) {
  if (value === undefined || value === null) return emptyLabel
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function TaskNodeInspector({ task, node, onClose }: { task: WorkflowTask; node: TaskNode; onClose: () => void }) {
  return (
    <section className="panel task-node-inspector" role="region" aria-label="节点运行详情">
      <header>
        <div>
          <span>NODE EXECUTION</span>
          <h2>{node.name}</h2>
          <p>{task.uuid} · {node.job?.uuid || '尚未创建 Job'} · 第 {node.job?.attempt || 1} 次尝试</p>
        </div>
        <div className="node-inspector-status">
          <span className={`task-state-dot task-state-${task.status}`} />
          <strong>{nodeStatusLabels[node.status]}</strong>
          <button type="button" aria-label="关闭节点运行详情" onClick={onClose}><X size={16} /></button>
        </div>
      </header>
      <div className="node-evidence-grid">
        <article>
          <div><strong>实际运行参数</strong><small>WorkflowNodeJob.param</small></div>
          <pre>{jsonEvidence(node.job?.param, '节点尚未进入调度，暂无实际参数')}</pre>
        </article>
        <article>
          <div><strong>运行结果</strong><small>WorkflowNodeJob.return_info</small></div>
          <pre>{jsonEvidence(node.job?.returnInfo, '节点尚未返回运行结果')}</pre>
        </article>
        <article>
          <div><strong>实时反馈</strong><small>feedback_data</small></div>
          <pre>{jsonEvidence(node.job?.feedbackData, '暂无反馈数据')}</pre>
        </article>
        <article>
          <div><strong>错误信息</strong><small>{node.job?.startedAt || '未开始'} → {node.job?.finishedAt || '未结束'}</small></div>
          <pre>{jsonEvidence(node.job?.errorInfo, '暂无错误')}</pre>
        </article>
      </div>
    </section>
  )
}

type JsonSchema = Record<string, any>

function effectiveSchema(schema: Record<string, unknown>): JsonSchema {
  const record = schema as JsonSchema
  if (Array.isArray(record.anyOf)) {
    const branch = record.anyOf.find((item: unknown) => (
      item && typeof item === 'object' && (item as JsonSchema).type !== 'null'
    ))
    return branch ? effectiveSchema(branch as Record<string, unknown>) : record
  }
  return record
}

function scalarResourceSlotSchema(schema: Record<string, unknown>): JsonSchema | undefined {
  const record = schema as JsonSchema
  if (record.$slot === 'ResourceSlot') return record
  if (!Array.isArray(record.anyOf)) return undefined
  return record.anyOf
    .filter((item: unknown) => item && typeof item === 'object')
    .map((item: unknown) => scalarResourceSlotSchema(item as Record<string, unknown>))
    .find(Boolean)
}

function initialFieldValue(field: ContractField): string {
  if (field.defaultValue === undefined || field.defaultValue === null) return ''
  if (scalarResourceSlotSchema(field.schema) && typeof field.defaultValue === 'object') {
    return String((field.defaultValue as Record<string, unknown>).uuid || '')
  }
  if (typeof field.defaultValue === 'object') return JSON.stringify(field.defaultValue, null, 2)
  return String(field.defaultValue)
}

function parseFieldValue(field: ContractField, value: string): unknown {
  const schema = effectiveSchema(field.schema)
  if (scalarResourceSlotSchema(field.schema)) return { uuid: value }
  if (schema.type === 'integer') {
    const parsed = Number(value)
    if (!Number.isInteger(parsed)) throw new Error(`参数 ${field.name} 必须是整数`)
    if (schema.minimum !== undefined && parsed < Number(schema.minimum)) throw new Error(`参数 ${field.name} 不能小于 ${schema.minimum}`)
    if (schema.maximum !== undefined && parsed > Number(schema.maximum)) throw new Error(`参数 ${field.name} 不能大于 ${schema.maximum}`)
    return parsed
  }
  if (schema.type === 'number') {
    const parsed = Number(value)
    if (!Number.isFinite(parsed)) throw new Error(`参数 ${field.name} 必须是数字`)
    if (schema.minimum !== undefined && parsed < Number(schema.minimum)) throw new Error(`参数 ${field.name} 不能小于 ${schema.minimum}`)
    if (schema.maximum !== undefined && parsed > Number(schema.maximum)) throw new Error(`参数 ${field.name} 不能大于 ${schema.maximum}`)
    return parsed
  }
  if (schema.type === 'boolean') return value === 'true'
  if (schema.type === 'array' || schema.type === 'object') {
    let parsed: unknown
    try {
      parsed = JSON.parse(value)
    } catch {
      throw new Error(`参数 ${field.name} 必须是有效 JSON`)
    }
    if (schema.type === 'array' && !Array.isArray(parsed)) throw new Error(`参数 ${field.name} 必须是 JSON 数组`)
    if (schema.type === 'object' && (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))) {
      throw new Error(`参数 ${field.name} 必须是 JSON 对象`)
    }
    return parsed
  }
  if (schema.minLength !== undefined && value.length < Number(schema.minLength)) throw new Error(`参数 ${field.name} 长度不足`)
  if (schema.maxLength !== undefined && value.length > Number(schema.maxLength)) throw new Error(`参数 ${field.name} 长度超限`)
  if (schema.pattern && !new RegExp(String(schema.pattern)).test(value)) throw new Error(`参数 ${field.name} 格式不正确`)
  return value
}

export function serialiseTaskInput(fields: ContractField[], values: Record<string, string>) {
  const entries: [string, unknown][] = []
  fields.forEach((field) => {
    const value = (values[field.name] || '').trim()
    if (!value) {
      if (field.required && field.defaultValue === undefined) {
        throw new Error(`参数 ${field.name} 为必填项`)
      }
      return
    }
    entries.push([field.name, parseFieldValue(field, value)])
  })
  return Object.fromEntries(entries)
}

function CreateTaskDialog({
  workflows,
  materials,
  connected,
  onClose,
  onNotify,
}: {
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onClose: () => void
  onNotify: (message: string) => void
}) {
  const queryClient = useQueryClient()
  const dialogRef = useRef<HTMLElement>(null)
  const [workflowUuid, setWorkflowUuid] = useState(workflows[0]?.uuid || '')
  const workflow = workflows.find((item) => item.uuid === workflowUuid) || workflows[0]
  const [description, setDescription] = useState('从实验运营控制台创建')
  const [input, setInput] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!workflow) return
    setInput(Object.fromEntries(workflow.inputContract.map((field) => [field.name, initialFieldValue(field)])))
  }, [workflow])

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const dialog = dialogRef.current
    const focusableSelector = 'button:not([disabled]), select:not([disabled]), input:not([disabled]), textarea:not([disabled])'
    dialog?.querySelector<HTMLElement>(focusableSelector)?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialog) return
      const focusable = [...dialog.querySelectorAll<HTMLElement>(focusableSelector)]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable.at(-1)!
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocus?.focus()
    }
  }, [onClose])

  const mutation = useMutation({
    mutationFn: () => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      if (!workflow) throw new Error('请选择可运行的工作流')
      return createWorkflowTask({
        workflowUuid: workflow.uuid,
        description,
        input: serialiseTaskInput(workflow.inputContract, input),
      })
    },
    onMutate: () => onClose(),
    onSuccess: (created) => {
      onNotify(`任务 ${created.uuid || ''} 已提交到 Edge`)
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
    },
    onError: (error) => onNotify(`任务提交失败：${error instanceof Error ? error.message : '未知错误'}`),
  })

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section ref={dialogRef} className="task-dialog" role="dialog" aria-modal="true" aria-labelledby="create-task-title" aria-describedby="create-task-description">
        <form onSubmit={(event) => { event.preventDefault(); if (connected && workflow) mutation.mutate() }}>
          <header><div><span>WORKFLOW RUN</span><h2 id="create-task-title">创建实验任务</h2><p id="create-task-description">仅提交工作流公开输入，中间节点参数由发布修订冻结。</p></div><button type="button" onClick={onClose} aria-label="关闭"><X size={18} /></button></header>
          <div className="dialog-content">
          <label className="form-field"><span>工作流</span><select value={workflowUuid} onChange={(event) => setWorkflowUuid(event.target.value)}>{workflows.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · r{item.revision}</option>)}</select></label>
          <label className="form-field"><span>任务描述</span><input value={description} onChange={(event) => setDescription(event.target.value)} /></label>
          <div className="form-section-heading"><div><strong>运行输入</strong><small>{workflow?.inputContract.length || 0} 个公开参数</small></div><span><ShieldAlert size={14} />发布修订</span></div>
          <div className="task-input-grid">
            {workflow?.inputContract.length ? workflow.inputContract.map((field) => {
              const schema = effectiveSchema(field.schema)
              const resourceSlot = scalarResourceSlotSchema(field.schema)
              const allowedTemplates = Array.isArray(resourceSlot?.allowed_resource_template_uuids)
                ? new Set(resourceSlot.allowed_resource_template_uuids.map(String))
                : undefined
              const materialOptions = resourceSlot
                ? materials.filter((material) => !allowedTemplates || (material.resourceTemplateUuid && allowedTemplates.has(material.resourceTemplateUuid)))
                : []
              return (
              <label className={`form-field ${resourceSlot ? 'resource-slot-field' : ''}`} key={field.name}>
                <span>{field.name}<em>{field.required ? `必填 · ${field.type}` : field.type}</em></span>
                {resourceSlot ? (
                  <select required={field.required} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">{materialOptions.length ? '选择 Edge 物料' : '没有符合模板约束的物料'}</option>
                    {materialOptions.map((material) => (
                      <option key={material.uuid} value={material.uuid}>{material.name} · {material.currentLocation.label}</option>
                    ))}
                  </select>
                ) : schema.type === 'boolean' ? (
                  <select required={field.required} value={input[field.name] ?? ''} onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}>
                    <option value="">{field.required ? '请选择' : '未设置'}</option>
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : schema.type === 'array' || schema.type === 'object' ? (
                  <textarea
                    required={field.required}
                    value={input[field.name] || ''}
                    onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}
                    placeholder={schema.type === 'array' ? '[ ... ]' : '{ ... }'}
                    rows={4}
                  />
                ) : (
                  <input
                    type={schema.type === 'number' || schema.type === 'integer' ? 'number' : 'text'}
                    required={field.required}
                    min={schema.minimum}
                    max={schema.maximum}
                    step={schema.type === 'integer' ? 1 : schema.multipleOf ?? 'any'}
                    minLength={schema.minLength}
                    maxLength={schema.maxLength}
                    pattern={schema.pattern}
                    value={input[field.name] || ''}
                    onChange={(event) => setInput((current) => ({ ...current, [field.name]: event.target.value }))}
                    placeholder={field.defaultValue === undefined ? field.type : String(field.defaultValue)}
                  />
                )}
              </label>
              )
            }) : <div className="no-input-note"><Circle size={15} />该工作流没有公开输入，可直接创建任务。</div>}
          </div>
          {!connected ? <div className="dialog-warning"><AlertCircle size={16} />Edge 未连接，当前不能提交真实任务。</div> : null}
          </div>
          <footer><Button type="button" onClick={onClose}>取消</Button><Button type="submit" tone="primary" icon={mutation.isPending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />} disabled={!connected || !workflow || mutation.isPending}>提交任务</Button></footer>
        </form>
      </section>
    </div>
  )
}

export function TasksPage({
  tasks,
  workflows,
  materials,
  connected,
  onRefresh,
  onNotify,
  onOpenWorkflow,
  startupMode = 'product',
}: {
  tasks: WorkflowTask[]
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onRefresh: () => void
  onNotify: (message: string) => void
  onOpenWorkflow: (target: WorkflowTarget) => void
  startupMode?: 'develop' | 'product'
}) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [selectedId, setSelectedId] = useState(tasks[0]?.uuid || '')
  const [selectedNodeRef, setSelectedNodeRef] = useState<{ taskUuid: string; nodeUuid: string }>()
  const [selectedStepNodeUuid, setSelectedStepNodeUuid] = useState('')

  const selectTask = useCallback((taskUuid: string) => {
    setSelectedId(taskUuid)
    setSelectedNodeRef(undefined)
  }, [])

  const selectNode = useCallback((taskUuid: string, nodeUuid: string) => {
    setSelectedId(taskUuid)
    setSelectedNodeRef({ taskUuid, nodeUuid })
  }, [])

  useEffect(() => {
    if (!tasks.some((task) => task.uuid === selectedId)) setSelectedId(tasks[0]?.uuid || '')
  }, [tasks, selectedId])

  useEffect(() => {
    if (!selectedNodeRef) return
    const task = tasks.find((item) => item.uuid === selectedNodeRef.taskUuid)
    if (!task?.nodes.some((node) => node.uuid === selectedNodeRef.nodeUuid)) {
      setSelectedNodeRef(undefined)
    }
  }, [tasks, selectedNodeRef])

  const filtered = useMemo(() => tasks.filter((task) => matchesFilter(task, filter)), [tasks, filter])
  const selectedSummary = filtered.find((task) => task.uuid === selectedId) || filtered[0]
  const selectedDetailQuery = useQuery({
    queryKey: ['workflow-task-detail', selectedSummary?.uuid],
    queryFn: ({ signal }) => loadWorkflowTaskDetail(selectedSummary!.uuid, materials, signal),
    enabled: connected && Boolean(selectedSummary) && selectedNodeRef?.taskUuid === selectedSummary?.uuid,
    staleTime: 10_000,
  })
  const selected = selectedDetailQuery.data && selectedDetailQuery.data.uuid === selectedSummary?.uuid
    ? { ...selectedDetailQuery.data, trace: selectedSummary.trace }
    : selectedSummary
  const selectedNodeTask = selectedNodeRef
    ? selected?.uuid === selectedNodeRef.taskUuid
      ? selected
      : tasks.find((task) => task.uuid === selectedNodeRef.taskUuid)
    : undefined
  const selectedNode = selectedNodeTask?.nodes.find((node) => node.uuid === selectedNodeRef?.nodeUuid)
  const runningTasks = tasks.filter((task) => task.nodes.some((node) => node.status === 'running' || node.status === 'canceling'))
  const selectedIsTerminal = Boolean(selected && ['succeeded', 'failed', 'canceled', 'timeout'].includes(selected.status))
  const stepStateQuery = useQuery({
    queryKey: ['workflow-task-step-state', selected?.uuid],
    queryFn: ({ signal }) => loadWorkflowTaskStepState(selected!.uuid, signal),
    enabled: startupMode === 'develop' && connected && Boolean(selected) && !selectedIsTerminal,
    refetchInterval: 2_000,
  })
  const stepState = stepStateQuery.data
  const effectiveExecutionMode = stepState?.executionMode || selected?.executionMode || 'normal'
  const readyNodeUuids = useMemo(
    () => new Set(stepState?.candidates.map((candidate) => candidate.nodeUuid) || []),
    [stepState?.candidates],
  )
  useEffect(() => {
    if (!stepState?.requiresSelection) {
      setSelectedStepNodeUuid(stepState?.candidates[0]?.nodeUuid || '')
      return
    }
    if (!stepState.candidates.some((candidate) => candidate.nodeUuid === selectedStepNodeUuid)) {
      setSelectedStepNodeUuid('')
    }
  }, [stepState, selectedStepNodeUuid])
  const controlMutation = useMutation({
    mutationFn: ({ type, targetNodeUuid }: { type: 'step' | 'pause' | 'resume' | 'cancel'; targetNodeUuid?: string }) => {
      if (!connected) throw new Error('Edge 未连接，写操作已暂停')
      return commandWorkflowTask(selected?.uuid || '', type, targetNodeUuid)
    },
    onSuccess: (_command, variables) => {
      const labels = { step: '单步命令已提交', pause: '已进入单步切换', resume: '已继续自动运行', cancel: '取消命令已提交' }
      onNotify(labels[variables.type])
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] }, { cancelRefetch: false })
      void queryClient.invalidateQueries({ queryKey: ['workflow-task-step-state', selected?.uuid] })
    },
    onError: (error) => onNotify(`任务控制失败：${error instanceof Error ? error.message : '未知错误'}`),
  })

  const counts: Record<TaskFilter, number> = {
    all: tasks.length,
    running: tasks.filter((task) => matchesFilter(task, 'running')).length,
    waiting: tasks.filter((task) => matchesFilter(task, 'waiting')).length,
    failed: tasks.filter((task) => matchesFilter(task, 'failed')).length,
    succeeded: tasks.filter((task) => matchesFilter(task, 'succeeded')).length,
  }
  const summaryCards: { label: string; value: number; tone: string; icon: LucideIcon }[] = [
    { label: '运行中', value: counts.running, tone: 'blue', icon: Activity },
    { label: '等待资源', value: counts.waiting, tone: 'amber', icon: Clock3 },
    { label: '今日完成', value: counts.succeeded, tone: 'green', icon: Check },
    { label: '需要处理', value: counts.failed, tone: 'red', icon: AlertCircle },
  ]
  const selectedTaskControl = startupMode === 'develop' && selected && !selectedIsTerminal ? (
    <div className="task-step-inline" aria-label="Task 行内单步调度控制">
      <header>
        <span>
          <strong>{effectiveExecutionMode === 'normal' ? '自动运行' : effectiveExecutionMode === 'switching_to_step' ? '正在切换' : '单步调试'}</strong>
          <small>{effectiveExecutionMode === 'switching_to_step' ? '等待在途 Job 结束' : '调度模式'}</small>
        </span>
        <code>{effectiveExecutionMode}</code>
      </header>
      {effectiveExecutionMode === 'step' && stepState?.candidates.length ? (
        <label className="task-step-inline-candidate">
          <span>下一步节点</span>
          <select
            aria-label="下一步节点"
            value={selectedStepNodeUuid}
            disabled={!stepState.requiresSelection}
            onChange={(event) => setSelectedStepNodeUuid(event.target.value)}
          >
            {stepState.requiresSelection && !selectedStepNodeUuid ? <option value="">请选择可执行节点</option> : null}
            {stepState.candidates.map((candidate) => (
              <option key={candidate.nodeUuid} value={candidate.nodeUuid}>
                {candidate.name}{candidate.deviceId ? ` · ${candidate.deviceId}` : ''}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {effectiveExecutionMode === 'step' && !stepStateQuery.isFetching && !stepState?.candidates.length ? (
        <small className="task-step-inline-note">当前没有可执行节点</small>
      ) : null}
      <div className="task-step-inline-actions">
        {effectiveExecutionMode === 'normal' ? (
          <Button icon={<Pause size={12} />} disabled={!connected || controlMutation.isPending} onClick={() => controlMutation.mutate({ type: 'pause' })}>切换为单步</Button>
        ) : null}
        {effectiveExecutionMode === 'switching_to_step' ? <Button icon={<Pause size={12} />} disabled>等待切换</Button> : null}
        {effectiveExecutionMode === 'step' ? (
          <>
            <Button tone="primary" icon={<StepForward size={12} />} disabled={!connected || controlMutation.isPending || !stepState?.canStep || (stepState.requiresSelection && !selectedStepNodeUuid)} onClick={() => controlMutation.mutate({ type: 'step', targetNodeUuid: selectedStepNodeUuid || stepState?.candidates[0]?.nodeUuid })}>
              {stepState?.inFlightJobCount ? '当前节点运行中' : '执行下一步'}
            </Button>
            <Button icon={<Play size={12} />} disabled={!connected || controlMutation.isPending || Boolean(stepState?.inFlightJobCount)} onClick={() => controlMutation.mutate({ type: 'resume' })}>继续自动运行</Button>
          </>
        ) : null}
        <Button tone="danger" icon={<Square size={12} />} disabled={!connected || controlMutation.isPending} onClick={() => controlMutation.mutate({ type: 'cancel' })}>取消任务</Button>
      </div>
    </div>
  ) : undefined

  return (
    <div className="page tasks-page">
      <PageHeader
        eyebrow="PARALLEL TASK MONITOR"
        title="并行任务运行矩阵"
        description="不同工作流连续逐行展示；节点轨道同步横向滚动，运行到哪个节点，哪个节点就亮起。"
        actions={
          <>
            <Button icon={<RefreshCw size={16} />} onClick={onRefresh}>刷新状态</Button>
            <Button tone="primary" icon={<Plus size={17} />} disabled={!workflows.length} onClick={() => workflows[0] && onOpenWorkflow({ workflowUuid: workflows[0].uuid, revision: workflows[0].revision })}>前往工作流创建</Button>
          </>
        }
      />

      <section className="task-summary-strip">
        {summaryCards.map(({ label, value, tone, icon: Icon }) => (
          <div className={`task-summary-item summary-${tone}`} key={label}><span><Icon size={17} /></span><p><small>{label}</small><strong>{value}</strong></p></div>
        ))}
      </section>

      <Panel className="parallel-board">
        <div className="task-toolbar">
          <div className="filter-tabs">
            {([
              ['all', '全部'], ['running', '运行中'], ['waiting', '等待'], ['failed', '异常'], ['succeeded', '已完成'],
            ] as [TaskFilter, string][]).map(([key, label]) => (
              <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{label}<span>{counts[key]}</span></button>
            ))}
          </div>
          <div className="matrix-legend" aria-label="节点状态颜色">
            <span><i className="node-done" />运行成功</span>
            <span><i className="node-running" />正在运行</span>
            <span><i className="node-waiting" />等待运行</span>
            <span><i className="node-failed" />运行失败</span>
            <span><i className="node-pending" />未运行</span>
          </div>
        </div>
        {filtered.length ? (
          <TaskMatrix
            tasks={filtered}
            selectedId={selected?.uuid || ''}
            selectedNode={selectedNodeRef}
            onSelect={selectTask}
            onSelectNode={selectNode}
            onOpenWorkflow={onOpenWorkflow}
            onNotify={onNotify}
            writable={connected}
            readyNodeUuids={selected?.uuid ? readyNodeUuids : new Set<string>()}
            selectedTaskControl={selectedTaskControl}
          />
        ) : <EmptyState title="当前筛选没有任务" description="选择其他状态，或创建一个新的工作流任务。" />}
      </Panel>

      {selectedNodeTask && selectedNode ? (
        <TaskNodeInspector task={selectedNodeTask} node={selectedNode} onClose={() => setSelectedNodeRef(undefined)} />
      ) : null}

      <section className="task-detail-grid">
        <Panel className="selected-task-card">
          <PanelHeader title="选中任务" description="点击矩阵中的任意行切换" action={selected ? <StatusBadge status={selected.status} /> : null} />
          {selected ? (
            <>
              <div className="selected-task-identity"><span><FlaskConical size={18} /></span><div><strong>{selected.uuid}</strong><small>{selected.workflowName}</small></div><ChevronRight size={17} /></div>
              <dl className="selected-task-properties">
                <div><dt>样品</dt><dd>{selected.sample}</dd></div>
                <div><dt>当前节点</dt><dd>{selected.current}</dd></div>
                <div><dt>整体进度</dt><dd>{selected.progress}%</dd></div>
                <div><dt>更新时间</dt><dd>{selected.updatedAt}</dd></div>
              </dl>
              {selected.trace ? (
                <a
                  className="selected-task-trace-link"
                  href={selected.trace.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label="在 SigNoz 中查看 Trace"
                >
                  <span><ExternalLink size={15} />{selected.trace.mode === 'trace' ? '查看完整 Trace' : '检索历史调度 Trace'}</span>
                  <code>{selected.trace.traceId || selected.uuid}</code>
                </a>
              ) : null}
            </>
          ) : <EmptyState title="没有选中任务" description="从矩阵中选择任务查看详情。" />}
        </Panel>

        <Panel className="running-jobs-card">
          <PanelHeader title="正在运行的节点" description={`${runningTasks.length} 个并行 Task`} action={<span className="live-badge"><i />SSE-ready</span>} />
          <div className="running-job-list">
            {runningTasks.length ? runningTasks.map((task) => {
              const active = task.nodes.find((node) => node.status === 'running' || node.status === 'canceling')
              return (
                <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="active-job-pulse" /><div><strong>{active?.name || task.current}</strong><small>{task.uuid} · {task.sample}</small></div><em>{task.progress}%</em></button>
              )
            }) : <EmptyState title="没有正在运行的节点" description="Edge 当前任务均已结束或尚未开始。" />}
          </div>
        </Panel>

        <Panel className="task-attention-card">
          <PanelHeader title="阻塞与异常" description="按操作优先级展示" action={<span className="count-badge">{counts.waiting + counts.failed}</span>} />
          <div className="mini-attention-list">
            {tasks.filter((task) => matchesFilter(task, 'waiting')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="warn"><Clock3 size={15} /></span><div><strong>{task.uuid} 等待资源</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {tasks.filter((task) => matchesFilter(task, 'failed')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => selectTask(task.uuid)}><span className="danger"><AlertCircle size={15} /></span><div><strong>{task.uuid} 需要处理</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {!counts.waiting && !counts.failed ? <EmptyState title="没有阻塞或异常" description="当前任务队列运行正常。" /> : null}
          </div>
        </Panel>
      </section>

    </div>
  )
}
