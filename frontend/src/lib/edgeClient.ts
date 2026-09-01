import type {
  ContractField,
  EdgeSnapshot,
  MaterialRecord,
  NodeStatus,
  TaskNode,
  TaskStatus,
  WorkflowDefinition,
  WorkflowTask,
} from '../types'

type RawRecord = Record<string, any>

interface EdgeEnvelope<T> {
  code: number
  data?: T
  message?: string
  msg?: string
}

interface PageData<T> {
  items: T[]
  total?: number
  has_more?: boolean
  page?: number
  page_size?: number
}

const configuredEndpoint = (import.meta.env.VITE_EDGE_API_URL as string | undefined)?.replace(/\/$/, '')
export const EDGE_API_BASE = configuredEndpoint ? `${configuredEndpoint}/api/v1` : '/api/v1'

const taskStatuses = new Set<TaskStatus>([
  'running',
  'admission_blocked',
  'succeeded',
  'failed',
  'pending',
  'paused',
  'canceling',
  'canceled',
  'timeout',
  'intervention_required',
  'execution_unknown',
  'unknown',
])

export function unwrapEnvelope<T>(body: EdgeEnvelope<T>): T {
  if (!body || body.code !== 0 || body.data === undefined) {
    throw new Error(body?.message || body?.msg || `Edge API 返回业务错误：${body?.code ?? 'unknown'}`)
  }
  return body.data
}

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${EDGE_API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.message || body?.detail || `Edge API 请求失败（${response.status}）`)
  }
  return body as T
}

async function requestData<T>(path: string, signal?: AbortSignal): Promise<T> {
  return unwrapEnvelope(await requestJson<EdgeEnvelope<T>>(path, signal))
}

async function requestAllPages<T>(path: string, signal?: AbortSignal): Promise<PageData<T>> {
  const pageSize = 100
  const items: T[] = []
  let page = 1
  let total: number | undefined

  while (page <= 100) {
    const separator = path.includes('?') ? '&' : '?'
    const result = await requestData<PageData<T>>(
      `${path}${separator}page=${page}&page_size=${pageSize}`,
      signal,
    )
    const pageItems = Array.isArray(result.items) ? result.items : []
    items.push(...pageItems)
    total = result.total ?? total

    const hasMore = result.has_more
      ?? (total !== undefined ? items.length < total : pageItems.length >= pageSize)
    if (!hasMore || pageItems.length === 0) break
    page += 1
  }

  return { items, total: total ?? items.length, has_more: false }
}

async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  mapper: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length)
  let cursor = 0
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      results[index] = await mapper(items[index])
    }
  })
  await Promise.all(workers)
  return results
}

async function postData<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${EDGE_API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.message || body?.detail || `Edge API 请求失败（${response.status}）`)
  }
  return unwrapEnvelope(body as EdgeEnvelope<T>)
}

function schemaType(schema: unknown): string {
  if (!schema || typeof schema !== 'object') return 'unknown'
  const record = schema as RawRecord
  if (Array.isArray(record.anyOf)) {
    return record.anyOf.map(schemaType).filter((value: string) => value !== 'unknown').join(' | ') || 'unknown'
  }
  const value = record.$slot ?? record.type
  return Array.isArray(value) ? value.join(' | ') : String(value || 'unknown')
}

function adaptContractFields(items: unknown, key: 'parameters' | 'outputs'): ContractField[] {
  if (!Array.isArray(items)) return []
  return items.map((item) => {
    const record = item as RawRecord
    return {
      name: String(record.name || 'unnamed'),
      type: schemaType(record.schema),
      required: Boolean(record.required),
      defaultValue: record.default,
      schema: record.schema && typeof record.schema === 'object' ? { ...record.schema } : {},
    }
  })
}

export function adaptWorkflow(raw: RawRecord): WorkflowDefinition {
  const unilab = raw.meta_data?.unilab || {}
  const inputContract = unilab.input_contract || {}
  const outputContract = unilab.output_contract || {}
  return {
    uuid: String(raw.uuid),
    name: String(raw.name || raw.display_name || raw.uuid),
    revision: Number(raw.revision || 1),
    status: String(raw.status || 'source'),
    description: String(raw.description || '来自 Uni-Lab OS 的工作流定义。'),
    nodeCount: Array.isArray(raw.nodes) ? raw.nodes.length : Number(raw.node_count || 0),
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : [],
    inputContract: adaptContractFields(inputContract.parameters, 'parameters'),
    outputContract: adaptContractFields(outputContract.outputs, 'outputs'),
    sourcePath: unilab.source_bootstrap?.relative_path,
  }
}

function normaliseTaskStatus(value: unknown): TaskStatus {
  const rawStatus = String(value || 'pending')
  if (rawStatus === 'success') return 'succeeded'
  const status = rawStatus as TaskStatus
  return taskStatuses.has(status) ? status : 'unknown'
}

function nodeName(node: RawRecord): string {
  if (node.name) return String(node.name)
  if (node.action_name) return String(node.action_name)
  if (node.kind === 'workflow_input') return '运行输入'
  if (node.kind === 'workflow_output') return '结果汇总'
  if (node.kind === 'material_source') return '物料准入'
  return String(node.kind || '待执行节点')
}

function jobNodeStatus(job: RawRecord | undefined, taskStatus: TaskStatus, node: RawRecord): NodeStatus {
  if (job) {
    const status = String(job.status)
    if (job.uncertainty_reason) return 'attention'
    if (job.wait_reason?.code || job.wait_reason?.message) return 'waiting'
    if (status === 'succeeded') return 'succeeded'
    if (status === 'running' || status === 'dispatched') return 'running'
    if (status === 'skipped') return 'skipped'
    if (status === 'cancel_requested') return 'canceling'
    if (status === 'canceled') return 'canceled'
    if (status === 'failed' || status === 'timeout') return 'failed'
    if (status === 'intervention_required' || status === 'execution_unknown') return 'attention'
    if (status === 'ready' || status.includes('wait') || status.includes('blocked')) return 'waiting'
    if (status === 'pending') return 'pending'
    return 'attention'
  }
  if (taskStatus === 'succeeded') return 'succeeded'
  if (taskStatus === 'canceled' || taskStatus === 'canceling') return 'canceled'
  if (taskStatus === 'timeout') return 'failed'
  if (taskStatus === 'intervention_required' || taskStatus === 'execution_unknown' || taskStatus === 'unknown') return 'attention'
  if (taskStatus === 'failed' && node.kind === 'workflow_output') return 'pending'
  return 'pending'
}

function timeLabel(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function adaptTask(raw: RawRecord, jobs: RawRecord[] = [], workflowName = '未命名工作流'): WorkflowTask {
  const attentionMessage = typeof raw.attention_reason === 'string'
    ? raw.attention_reason
    : raw.attention_reason?.message || raw.attention_reason?.code
  const waitMessage = raw.wait_reason?.message || raw.wait_reason?.code || attentionMessage
  const controlStatus = String(raw.control_status || 'active')
  const cleanupStatus = String(raw.cleanup_status || 'none')
  const normalisedStatus = normaliseTaskStatus(raw.status)
  let status: TaskStatus = normalisedStatus
  if (normalisedStatus === 'pending' && waitMessage) status = 'admission_blocked'
  if (controlStatus === 'paused') status = 'paused'
  if (controlStatus === 'waiting_intervention') status = 'intervention_required'
  if (controlStatus === 'waiting_reconciliation') status = 'execution_unknown'
  if (cleanupStatus === 'requires_attention' || attentionMessage) status = 'intervention_required'
  const planNodes = Array.isArray(raw.execution_plan?.nodes) ? raw.execution_plan.nodes : []
  const sortedNodes = [...planNodes].sort(
    (left, right) => Number(left.topological_index || 0) - Number(right.topological_index || 0),
  )
  const jobByNode = new Map(jobs.map((job) => [String(job.workflow_node_uuid), job]))
  const nodes: TaskNode[] = sortedNodes.map((node, index) => {
    const uuid = String(node.uuid || `node-${index}`)
    return {
      uuid,
      name: nodeName(node),
      kind: String(node.kind || node.action_type || 'device_action'),
      index: Number(node.topological_index ?? index),
      status: jobNodeStatus(jobByNode.get(uuid), status, node),
      device: node.device_id ? String(node.device_id) : undefined,
      materialUuid: node.material_uuid ? String(node.material_uuid) : undefined,
    }
  })

  const succeeded = nodes.filter((node) => node.status === 'succeeded' || node.status === 'skipped').length
  const progress = nodes.length
    ? Math.round((succeeded / nodes.length) * 100)
    : status === 'succeeded'
      ? 100
      : 0
  const activeNode = nodes.find((node) => ['running', 'waiting', 'failed', 'canceling', 'attention'].includes(node.status))
  return {
    uuid: String(raw.uuid),
    workflowUuid: String(raw.workflow_uuid || raw.workflow?.uuid || ''),
    workflowName,
    status,
    sample: String(raw.input?.sample_id || raw.input?.sample || raw.meta_data?.sample_id || '未命名样品'),
    description: String(raw.description || workflowName),
    current: String(
      waitMessage
      || activeNode?.name
      || (status === 'succeeded'
        ? '结果已汇总'
        : status === 'intervention_required'
          ? '等待人工干预'
          : status === 'execution_unknown'
            ? '等待物理结果对账'
            : status),
    ),
    progress,
    updatedAt: timeLabel(raw.update_time || raw.finished_at || raw.started_at),
    nodes,
    workflowRevision: Number(raw.workflow_snapshot?.workflow?.revision || raw.workflow?.revision || 0) || undefined,
    planSignature: sortedNodes.map((node) => String(node.uuid || '')).join('|') || 'empty-plan',
  }
}

function materialStatus(raw: RawRecord): MaterialRecord['status'] {
  const value = String(raw.data?.status || raw.meta_data?.status || raw.status || '').toLowerCase()
  if (value.includes('occup') || value.includes('claim') || value.includes('task')) return 'occupied'
  if (value.includes('reserv')) return 'reserved'
  if (value.includes('verify') || value.includes('unknown')) return 'verify'
  return 'available'
}

function materialLocation(sourceNodeId: unknown): string {
  const id = String(sourceNodeId || '')
  const [prefix, slot] = id.split('__')
  const station = prefix?.match(/^s(\d+)/i)?.[1]
  if (station && slot) return `S${station} / ${slot}`
  return id || '未分配位置'
}

export function adaptMaterial(raw: RawRecord): MaterialRecord {
  const currentSiteName = raw.current_site?.name ? String(raw.current_site.name) : ''
  const currentSiteSource = String(raw.current_site?.meta_data?.source_node_id || '')
  const station = currentSiteSource.match(/^s(\d+)/i)?.[1]
  const hasCurrentSiteProjection = Object.prototype.hasOwnProperty.call(raw, 'current_site')
  return {
    uuid: String(raw.uuid),
    name: String(raw.name || raw.uuid),
    category: String(raw.config?.category || raw.config?.rendering?.kind || 'material'),
    location: hasCurrentSiteProjection
      ? currentSiteName
        ? `${station ? `S${station} / ` : ''}${currentSiteName}`
        : '未分配权威库位'
      : materialLocation(raw.meta_data?.source_node_id),
    status: materialStatus(raw),
    barcode: String(raw.barcode || '—'),
    parentUuid: raw.parent_uuid ? String(raw.parent_uuid) : undefined,
    className: String(raw.class || 'unknown'),
    resourceTemplateUuid: raw.resource_template_uuid ? String(raw.resource_template_uuid) : undefined,
    sourceGraph: raw.meta_data?.source_graph ? String(raw.meta_data.source_graph) : undefined,
    updatedAt: timeLabel(raw.update_time),
  }
}

async function taskWithJobs(raw: RawRecord, workflowNames: Map<string, string>, signal?: AbortSignal) {
  const jobs = await requestData<RawRecord[]>(
    `/workflow-tasks/${encodeURIComponent(raw.uuid)}/jobs`,
    signal,
  )
  return adaptTask(raw, jobs, workflowNames.get(String(raw.workflow_uuid)) || '未命名工作流')
}

export async function loadEdgeSnapshot(signal?: AbortSignal): Promise<EdgeSnapshot> {
  const [readiness, workflowPage, recentTaskPage, runningTaskPage, pendingTaskPage, cancelingTaskPage, attentionTaskPage, materialPage] = await Promise.all([
    requestJson<RawRecord>('/readiness', signal),
    requestAllPages<RawRecord>('/workflows', signal),
    requestData<PageData<RawRecord>>('/workflow-tasks?page=1&page_size=20', signal),
    requestAllPages<RawRecord>('/workflow-tasks?status=running', signal),
    requestAllPages<RawRecord>('/workflow-tasks?status=pending', signal),
    requestAllPages<RawRecord>('/workflow-tasks?status=canceling', signal),
    requestAllPages<RawRecord>('/workflow-tasks?cleanup_status=requires_attention', signal),
    requestAllPages<RawRecord>('/materials', signal),
  ])
  if (readiness.status !== 'ready') throw new Error('Edge 工作流运行时尚未就绪')

  const workflows = (workflowPage.items || []).map(adaptWorkflow)
  const workflowNames = new Map(workflows.map((workflow) => [workflow.uuid, workflow.name]))
  const taskRowsByUuid = new Map<string, RawRecord>()
  const taskRows = [
    ...(recentTaskPage.items || []),
    ...(runningTaskPage.items || []),
    ...(pendingTaskPage.items || []),
    ...(cancelingTaskPage.items || []),
    ...(attentionTaskPage.items || []),
  ]
  taskRows.forEach((task) => taskRowsByUuid.set(String(task.uuid), task))
  const tasks = await mapWithConcurrency(
    [...taskRowsByUuid.values()],
    6,
    (task) => taskWithJobs(task, workflowNames, signal),
  )
  const materialUsage = new Map<string, MaterialRecord['status']>()
  tasks.forEach((task) => task.nodes.forEach((node) => {
    if (!node.materialUuid) return
    if (['running', 'waiting', 'canceling', 'attention'].includes(node.status)) {
      materialUsage.set(node.materialUuid, 'occupied')
    } else if (task.status === 'pending' && !materialUsage.has(node.materialUuid)) {
      materialUsage.set(node.materialUuid, 'reserved')
    }
  }))
  const materials = (materialPage.items || []).map((raw) => {
    const material = adaptMaterial(raw)
    const projectedStatus = materialUsage.get(material.uuid)
    return projectedStatus && material.status === 'available'
      ? { ...material, status: projectedStatus }
      : material
  })

  return {
    workflows,
    tasks,
    materials,
    materialTotal: Number(materialPage.total ?? materials.length),
    workflowLoaded: Number(readiness.workflowProgress?.loaded ?? workflows.length),
    workflowTotal: Number(readiness.workflowProgress?.total ?? workflows.length),
  }
}

export async function loadMaterialDetail(materialUuid: string, signal?: AbortSignal) {
  const material = await requestData<RawRecord>(`/materials/${encodeURIComponent(materialUuid)}`, signal)
  return adaptMaterial(material)
}

export async function loadWorkflowGraph(workflowUuid: string, signal?: AbortSignal) {
  const graph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`, signal)
  return {
    workflow: adaptWorkflow({ ...graph.workflow, nodes: graph.nodes }),
    nodes: Array.isArray(graph.nodes) ? graph.nodes : [],
    edges: Array.isArray(graph.edges) ? graph.edges : [],
  }
}

export async function createWorkflowTask({
  workflowUuid,
  input,
  description,
}: {
  workflowUuid: string
  input: Record<string, unknown>
  description: string
}) {
  return postData<RawRecord>('/workflow-tasks', {
    workflow_uuid: workflowUuid,
    run_mode: 'normal',
    input,
    description,
    meta_data: { source: 'unilabos-frontend' },
  })
}
