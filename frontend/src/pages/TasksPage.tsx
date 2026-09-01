import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { LucideIcon } from 'lucide-react'
import {
  Activity,
  AlertCircle,
  Check,
  ChevronRight,
  Circle,
  Clock3,
  FlaskConical,
  GitBranch,
  LoaderCircle,
  Plus,
  RefreshCw,
  Send,
  ShieldAlert,
  X,
} from 'lucide-react'
import { createWorkflowTask } from '../lib/edgeClient'
import type { ContractField, MaterialRecord, TaskNode, WorkflowDefinition, WorkflowTask } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/ui'

type TaskFilter = 'all' | 'running' | 'waiting' | 'failed' | 'succeeded'

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

function matchesFilter(task: WorkflowTask, filter: TaskFilter) {
  if (filter === 'all') return true
  if (filter === 'running') return task.status === 'running' || task.status === 'canceling'
  if (filter === 'waiting') return ['admission_blocked', 'paused'].includes(task.status)
  if (filter === 'failed') return ['failed', 'timeout', 'intervention_required', 'execution_unknown', 'unknown'].includes(task.status)
  return task.status === 'succeeded'
}

function NodeMarker({ node, index }: { node?: TaskNode; index: number }) {
  const status = node?.status || 'pending'
  const waitReason = node?.waitReason
  const markerRef = useRef<HTMLDivElement>(null)
  const tooltipId = useId()
  const [tooltipVisible, setTooltipVisible] = useState(false)
  const [tooltipPosition, setTooltipPosition] = useState({ left: 0, top: 0, above: false })
  const showTooltip = useCallback(() => {
    if (!waitReason || !markerRef.current) return
    const rect = markerRef.current.getBoundingClientRect()
    const above = rect.top > 190
    const halfWidth = 142
    setTooltipPosition({
      left: Math.min(Math.max(rect.left + rect.width / 2, halfWidth + 10), window.innerWidth - halfWidth - 10),
      top: above ? rect.top - 10 : rect.bottom + 10,
      above,
    })
    setTooltipVisible(true)
  }, [waitReason])

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

  useEffect(() => {
    if (!waitReason) setTooltipVisible(false)
  }, [waitReason])

  const label = `${node?.name || `节点 ${index + 1}`}，${nodeStatusLabels[status]}`
  return (
    <div
      ref={markerRef}
      className={`matrix-node matrix-node-${status}`}
      title={waitReason ? undefined : label}
      tabIndex={waitReason ? 0 : undefined}
      aria-label={label}
      aria-describedby={waitReason && tooltipVisible ? tooltipId : undefined}
      onMouseEnter={showTooltip}
      onMouseLeave={() => setTooltipVisible(false)}
      onFocus={showTooltip}
      onBlur={() => setTooltipVisible(false)}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setTooltipVisible(false)
      }}
    >
      <span>
        {status === 'succeeded' || status === 'skipped'
          ? <Check size={13} />
          : status === 'running' || status === 'canceling'
            ? <LoaderCircle size={13} />
            : status === 'failed' || status === 'canceled' || status === 'attention'
              ? <X size={13} />
              : index + 1}
      </span>
      {waitReason && tooltipVisible && typeof document !== 'undefined' && createPortal(
        <div
          id={tooltipId}
          role="tooltip"
          className={`node-wait-tooltip ${tooltipPosition.above ? 'node-wait-tooltip-above' : 'node-wait-tooltip-below'}`}
          style={{ left: tooltipPosition.left, top: tooltipPosition.top }}
        >
          <strong>{waitReason.title}</strong>
          <p>{waitReason.message}</p>
          {waitReason.details.length > 0 && (
            <ul>{waitReason.details.map((detail) => <li key={detail}>{detail}</li>)}</ul>
          )}
          {waitReason.waitingSince && <small>等待开始：{waitReason.waitingSince}</small>}
        </div>,
        document.body,
      )}
    </div>
  )
}

function TaskMatrixGroup({
  tasks,
  selectedId,
  onSelect,
}: {
  tasks: WorkflowTask[]
  selectedId: string
  onSelect: (id: string) => void
}) {
  const referenceTask = [...tasks].sort((left, right) => right.nodes.length - left.nodes.length)[0]
  const stages = referenceTask?.nodes || []
  const columns = `220px repeat(${Math.max(stages.length, 1)}, minmax(96px, 1fr)) 86px`

  return (
    <section className="matrix-group">
      <div className="matrix-group-title"><GitBranch size={15} /><strong>{tasks[0]?.workflowName}</strong><span>{tasks[0]?.workflowRevision ? `r${tasks[0].workflowRevision} · ` : ''}{tasks[0]?.runMode || 'normal'} · {tasks.length} 个任务</span></div>
      <div className="task-matrix-scroll">
        <div className="matrix-header" style={{ gridTemplateColumns: columns }}>
          <div className="matrix-task-heading">Task / 样品</div>
          {stages.length ? stages.map((stage, index) => (
            <div key={stage.uuid}><span>{String(index + 1).padStart(2, '0')}</span><strong>{stage.name}</strong></div>
          )) : <div><span>—</span><strong>等待执行图</strong></div>}
          <div><span>RUN</span><strong>进度</strong></div>
        </div>
        <div className="matrix-body">
          {tasks.map((task) => (
            <div
              key={task.uuid}
              className={`matrix-row ${selectedId === task.uuid ? 'selected' : ''}`}
              style={{ gridTemplateColumns: columns }}
              onClick={() => onSelect(task.uuid)}
            >
              <button type="button" className="matrix-task-cell" onClick={() => onSelect(task.uuid)}>
                <span className={`task-state-dot task-state-${task.status}`} />
                <div><strong>{task.uuid}</strong><small>{task.sample} · {task.updatedAt}</small></div>
              </button>
              {(stages.length ? stages : [{ uuid: 'empty' } as TaskNode]).map((stage, index) => (
                <NodeMarker key={stage.uuid} node={task.nodes.find((node) => node.uuid === stage.uuid)} index={index} />
              ))}
              <div className="matrix-progress-cell"><strong>{task.progress}%</strong><span><i style={{ width: `${task.progress}%` }} /></span></div>
            </div>
          ))}
        </div>
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
      if (!workflow) throw new Error('请选择可运行的工作流')
      return createWorkflowTask({
        workflowUuid: workflow.uuid,
        description,
        input: serialiseTaskInput(workflow.inputContract, input),
      })
    },
    onSuccess: async (created) => {
      onNotify(`任务 ${created.uuid || ''} 已提交到 Edge`)
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
      onClose()
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
}: {
  tasks: WorkflowTask[]
  workflows: WorkflowDefinition[]
  materials: MaterialRecord[]
  connected: boolean
  onRefresh: () => void
  onNotify: (message: string) => void
}) {
  const [filter, setFilter] = useState<TaskFilter>('all')
  const [selectedId, setSelectedId] = useState(tasks[0]?.uuid || '')
  const [createOpen, setCreateOpen] = useState(false)
  const closeCreateDialog = useCallback(() => setCreateOpen(false), [])

  useEffect(() => {
    if (!tasks.some((task) => task.uuid === selectedId)) setSelectedId(tasks[0]?.uuid || '')
  }, [tasks, selectedId])

  const filtered = useMemo(() => tasks.filter((task) => matchesFilter(task, filter)), [tasks, filter])
  const groups = useMemo(() => {
    const grouped = new Map<string, WorkflowTask[]>()
    filtered.forEach((task) => {
      const key = `${task.workflowUuid}:r${task.workflowRevision ?? 'unknown'}:${task.matrixGroupKey}`
      grouped.set(key, [...(grouped.get(key) || []), task])
    })
    return [...grouped.entries()]
  }, [filtered])
  const selected = filtered.find((task) => task.uuid === selectedId) || filtered[0]
  const runningTasks = tasks.filter((task) => task.nodes.some((node) => node.status === 'running' || node.status === 'canceling'))

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

  return (
    <div className="page tasks-page">
      <PageHeader
        eyebrow="PARALLEL TASK MONITOR"
        title="并行任务运行矩阵"
        description="每行一个 Task，按工作流分组并横向对齐真实执行节点；运行到哪个节点，哪个节点就亮起。"
        actions={
          <>
            <Button icon={<RefreshCw size={16} />} onClick={onRefresh}>刷新状态</Button>
            <Button tone="primary" icon={<Plus size={17} />} onClick={() => setCreateOpen(true)}>创建任务</Button>
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
          <div className="matrix-legend"><span><i className="node-done" />已完成</span><span><i className="node-running" />正在运行</span><span><i className="node-waiting" />等待</span><span><i className="node-failed" />失败</span></div>
        </div>
        {groups.length ? groups.map(([key, group]) => <TaskMatrixGroup key={key} tasks={group} selectedId={selected?.uuid || ''} onSelect={setSelectedId} />) : <EmptyState title="当前筛选没有任务" description="选择其他状态，或创建一个新的工作流任务。" />}
      </Panel>

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
            </>
          ) : <EmptyState title="没有选中任务" description="从矩阵中选择任务查看详情。" />}
        </Panel>

        <Panel className="running-jobs-card">
          <PanelHeader title="正在运行的节点" description={`${runningTasks.length} 个并行 Task`} action={<span className="live-badge"><i />SSE-ready</span>} />
          <div className="running-job-list">
            {runningTasks.length ? runningTasks.map((task) => {
              const active = task.nodes.find((node) => node.status === 'running' || node.status === 'canceling')
              return (
                <button key={task.uuid} onClick={() => setSelectedId(task.uuid)}><span className="active-job-pulse" /><div><strong>{active?.name || task.current}</strong><small>{task.uuid} · {task.sample}</small></div><em>{task.progress}%</em></button>
              )
            }) : <EmptyState title="没有正在运行的节点" description="Edge 当前任务均已结束或尚未开始。" />}
          </div>
        </Panel>

        <Panel className="task-attention-card">
          <PanelHeader title="阻塞与异常" description="按操作优先级展示" action={<span className="count-badge">{counts.waiting + counts.failed}</span>} />
          <div className="mini-attention-list">
            {tasks.filter((task) => matchesFilter(task, 'waiting')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => setSelectedId(task.uuid)}><span className="warn"><Clock3 size={15} /></span><div><strong>{task.uuid} 等待资源</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {tasks.filter((task) => matchesFilter(task, 'failed')).slice(0, 2).map((task) => <button key={task.uuid} onClick={() => setSelectedId(task.uuid)}><span className="danger"><AlertCircle size={15} /></span><div><strong>{task.uuid} 需要处理</strong><small>{task.current}</small></div><ChevronRight size={15} /></button>)}
            {!counts.waiting && !counts.failed ? <EmptyState title="没有阻塞或异常" description="当前任务队列运行正常。" /> : null}
          </div>
        </Panel>
      </section>

      {createOpen ? <CreateTaskDialog workflows={workflows} materials={materials} connected={connected} onClose={closeCreateDialog} onNotify={onNotify} /> : null}
    </div>
  )
}
