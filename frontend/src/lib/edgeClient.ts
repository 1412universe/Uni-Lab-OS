import type {
  ContractField,
  EdgeSnapshot,
  MaterialCurrentLocation,
  MaterialRecord,
  NodePresentationStatus,
  RunPreflightReport,
  TaskNode,
  TaskNodeWaitReason,
  TaskPresentationStatus,
  WorkflowDefinition,
  WorkflowAuthoringState,
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowSource,
  WorkflowTask,
  WorkflowTaskPriority,
  WorkflowStepState,
  ResourceTemplateRecord,
  ActionTemplateRecord,
  ControlTemplateRecord,
  ActionParameterRecord,
  OperationCategoryRecord,
  ReagentInfoRecord,
  ReagentRecord,
  ReagentHistoryRecord,
  CompoundLookupResult,
  StartupMode,
  StartupModeSwitchBlocker,
  StartupModeSwitchResult,
} from '../types'

type RawRecord = Record<string, any>

interface EdgeEnvelope<T> {
  code: number
  data?: T
  error?: {
    code?: string
    msg?: string
    details?: RawRecord
  }
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

export class EdgeApiError extends Error {
  code?: string
  details: RawRecord

  constructor(message: string, options: { code?: string; details?: RawRecord } = {}) {
    super(message)
    this.name = 'EdgeApiError'
    this.code = options.code
    this.details = options.details || {}
  }
}

const terminalTaskStatusValues = ['succeeded', 'failed', 'canceled', 'timeout'] as const
const taskStatuses = new Set<TaskPresentationStatus>([
  'running',
  'admission_blocked',
  ...terminalTaskStatusValues,
  'pending',
  'paused',
  'canceling',
  'intervention_required',
  'execution_unknown',
  'unknown',
])
const terminalTaskStatuses = new Set<TaskPresentationStatus>(terminalTaskStatusValues)

export function unwrapEnvelope<T>(body: EdgeEnvelope<T>): T {
  if (!body || body.code !== 0 || body.data === undefined) {
    throw new EdgeApiError(
      body?.error?.msg || `Edge API 返回业务错误：${body?.code ?? 'unknown'}`,
      { code: body?.error?.code, details: body?.error?.details },
    )
  }
  return body.data
}

export function startupModeSwitchBlockers(error: unknown): StartupModeSwitchBlocker[] {
  if (!(error instanceof EdgeApiError) || !Array.isArray(error.details.blockers)) return []
  return error.details.blockers.flatMap((value: unknown) => {
    if (!value || typeof value !== 'object') return []
    const blocker = value as RawRecord
    if (!blocker.task_uuid) return []
    return [{
      taskUuid: String(blocker.task_uuid),
      workflowUuid: String(blocker.workflow_uuid || ''),
      status: String(blocker.status || 'unknown'),
      cleanupStatus: String(blocker.cleanup_status || 'unknown'),
      executionKind: String(blocker.execution_kind || 'workflow'),
    }]
  })
}

async function requestJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${EDGE_API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.error?.msg || body?.detail || `Edge API 请求失败（${response.status}）`)
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

async function postData<T>(path: string, payload: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${EDGE_API_BASE}${path}`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
    signal,
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(formatApiError(body, response.status))
  return unwrapEnvelope(body as EdgeEnvelope<T>)
}

function formatApiError(body: unknown, status: number): string {
  const record = body && typeof body === 'object' ? body as RawRecord : {}
  const error = record.error && typeof record.error === 'object' ? record.error as RawRecord : {}
  const detail = record.detail
  if (typeof detail === 'string' && detail) return `${detail}（HTTP ${status}）`
  if (Array.isArray(detail)) {
    const fields = detail.map((item) => {
      const value = item && typeof item === 'object' ? item as RawRecord : {}
      const location = Array.isArray(value.loc) ? value.loc.join('.') : ''
      return `${location ? `${location}: ` : ''}${String(value.msg || '字段校验失败')}`
    }).join('；')
    if (fields) return `${fields}（HTTP ${status}）`
  }
  const message = error.msg || error.message || record.message
  return `${typeof message === 'string' && message ? message : `Edge API 请求失败（${status}）`}`
}

async function writeData<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, payload?: unknown): Promise<T> {
  const response = await fetch(`${EDGE_API_BASE}${path}`, {
    method,
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(`${formatApiError(body, response.status)}（${method} ${path}）`)
  if (body?.code === 0 && body.data === undefined) return undefined as T
  try { return unwrapEnvelope(body as EdgeEnvelope<T>) } catch (error) {
    if (error instanceof EdgeApiError) throw error
    throw new Error(`${error instanceof Error ? error.message : 'Edge API 业务错误'}（${method} ${path}）`)
  }
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
    const field: ContractField = {
      name: String(record.name || 'unnamed'),
      type: schemaType(record.schema),
      required: Boolean(record.required),
      defaultValue: record.default,
      schema: record.schema && typeof record.schema === 'object' ? { ...record.schema } : {},
    }
    if (typeof record.title === 'string') field.title = record.title
    if (typeof record.description === 'string') field.description = record.description
    if (typeof record.implicit === 'boolean') field.implicit = record.implicit
    return field
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
    workflowType: raw.workflow_type === 'experiment_operation' ? 'experiment_operation' : 'normal',
    operationCategoryUuid: raw.operation_category_uuid ? String(raw.operation_category_uuid) : undefined,
  }
}

function adaptWorkflowGraphNode(raw: RawRecord): WorkflowGraphNode {
  const unilab = raw.meta_data?.unilab || {}
  const type = String(raw.type || raw.kind || 'workflow_node')
  const normalisedType = type.toLowerCase()
  const authoringResultName = unilab.authoring_result_name
    ? String(unilab.authoring_result_name)
    : undefined
  const rawName = String(raw.name || raw.action_name || authoringResultName || raw.uuid)
  const name = type.toLowerCase() === 'material_source'
    && rawName.trim().toLowerCase() === 'material source'
    && authoringResultName
    ? authoringResultName
    : rawName
  const authoringOrderValue = unilab.authoring_source_order
  const authoringOrder = authoringOrderValue !== undefined
    && authoringOrderValue !== null
    && Number.isFinite(Number(authoringOrderValue))
    ? Number(authoringOrderValue)
    : undefined
  return {
    uuid: String(raw.uuid),
    name,
    type,
    kind: normalisedType === 'group'
      ? 'group'
      : normalisedType === 'material_source'
        ? 'material_source'
        : normalisedType === 'condition'
          ? 'condition'
          : normalisedType === 'repeat_until'
            ? 'repeat_until'
            : 'action',
    action_name: raw.action_name ? String(raw.action_name) : undefined,
    workflow_node_template_uuid: raw.workflow_node_template_uuid
      ? String(raw.workflow_node_template_uuid)
      : undefined,
    material_uuid: raw.material_uuid ? String(raw.material_uuid) : undefined,
    param: raw.param && typeof raw.param === 'object' ? raw.param : undefined,
    pose: raw.pose && typeof raw.pose === 'object' ? raw.pose : undefined,
    meta_data: raw.meta_data && typeof raw.meta_data === 'object' ? raw.meta_data : undefined,
    parentUuid: raw.parent_uuid ? String(raw.parent_uuid) : undefined,
    deviceId: unilab.executor_binding?.device_id
      ? String(unilab.executor_binding.device_id)
      : raw.device_id
        ? String(raw.device_id)
        : undefined,
    authoringOrder,
    authoringResultName,
    parallelScope: unilab.parallel_scope ? String(unilab.parallel_scope) : undefined,
    materialRole: raw.param?.flow_role || raw.params?.flow_role
      ? String(raw.param?.flow_role || raw.params?.flow_role)
      : undefined,
    description: raw.description ? String(raw.description) : undefined,
    disabled: Boolean(raw.disabled),
  }
}

function adaptWorkflowGraphEdge(raw: RawRecord): WorkflowGraphEdge {
  const sourceNodeUuid = String(raw.source_node_uuid || raw.sourceNodeUuid || '')
  const targetNodeUuid = String(raw.target_node_uuid || raw.targetNodeUuid || '')
  return {
    uuid: String(raw.uuid || `${sourceNodeUuid}->${targetNodeUuid}`),
    sourceNodeUuid,
    targetNodeUuid,
  }
}

function normaliseTaskStatus(value: unknown): TaskPresentationStatus {
  const rawStatus = String(value || 'pending')
  const status = rawStatus as TaskPresentationStatus
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

function jobNodeStatus(
  job: RawRecord | undefined,
  taskStatus: TaskPresentationStatus,
  node: RawRecord,
): NodePresentationStatus {
  if (job) {
    if (job.manual_confirmation?.status === 'pending') return 'attention'
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

const waitReasonDefaults: Record<string, { title: string; message: string }> = {
  material_unavailable: { title: '等待物料', message: '任务所需物料暂不可用' },
  device_busy: { title: '等待设备', message: '匹配设备当前正在被其他作业使用' },
  device_lock: { title: '等待设备', message: '节点所需设备当前正在被其他作业使用' },
  global_task_capacity: { title: '等待调度容量', message: '全局运行任务容量已满' },
  workflow_task_capacity: { title: '等待调度容量', message: '该工作流的运行任务容量已满' },
  job_dispatch_capacity: { title: '等待调度容量', message: '当前并行 Job 派发容量已满' },
  operation_lease: { title: '等待执行资源', message: '执行资源正在被其他作业使用' },
  resource_claimed: { title: '等待执行资源', message: '执行资源已被其他作业占用' },
  manual_intervention: { title: '等待人工操作', message: '节点需要人工处理后才能继续' },
  gripper_site_occupied: { title: '等待库位', message: '机械臂夹爪库位当前被占用' },
  site_claimed: { title: '等待库位', message: '目标库位已被其他作业预留' },
  site_group_unavailable: { title: '等待库位', message: '候选库位当前均不可用' },
  site_ingress_reserved: { title: '等待库位', message: '目标库位正在等待其他物料进入' },
  site_occupied: { title: '等待库位', message: '目标库位当前已有物料' },
  transfer_source_site_missing: { title: '等待物料', message: '待转运物料尚未进入可用来源库位' },
}

type WaitResourceScope = 'device' | 'material' | 'material_site'

export interface WaitResourceLabels {
  devices?: Readonly<Record<string, string>>
  materials?: Readonly<Record<string, string>>
  sites?: Readonly<Record<string, string>>
}

interface MutableWaitResourceLabels {
  devices: Record<string, string>
  materials: Record<string, string>
  sites: Record<string, string>
}

function waitResourceScope(value: unknown): WaitResourceScope | undefined {
  const scope = String(value || '')
  return scope === 'device' || scope === 'material' || scope === 'material_site'
    ? scope
    : undefined
}

function resourceDetail(kind: string, identity: string, label?: string): string {
  const resolvedLabel = String(label || '').trim()
  return resolvedLabel && resolvedLabel !== identity
    ? `${kind}：${resolvedLabel}（${identity}）`
    : `${kind}：${identity}`
}

function resourceLabel(
  labels: Readonly<Record<string, string>> | undefined,
  identity: string,
): string | undefined {
  return labels && Object.prototype.hasOwnProperty.call(labels, identity)
    ? labels[identity]
    : undefined
}

function waitResourceDetails(
  resources: unknown,
  labels: WaitResourceLabels,
): { details: string[]; scopes: Set<WaitResourceScope> } {
  const details: string[] = []
  const scopes = new Set<WaitResourceScope>()
  if (!Array.isArray(resources)) return { details, scopes }
  const append = (detail: string) => {
    if (!details.includes(detail)) details.push(detail)
  }
  resources.forEach((value) => {
    if (!value || typeof value !== 'object') return
    const resource = value as RawRecord
    const scope = waitResourceScope(resource.scope)
    if (!scope) return
    scopes.add(scope)
    if (scope === 'device' && resource.device_id) {
      const identity = String(resource.device_id)
      const label = String(resource.device_name || '').trim()
        || resourceLabel(labels.devices, identity)
      append(resourceDetail('设备', identity, label))
      return
    }
    if (scope === 'material_site' && resource.site_uuid) {
      const siteIdentity = String(resource.site_uuid)
      const ownerName = String(resource.material_name || '').trim()
      const siteName = String(resource.site_name || '').trim()
      const inlineLabel = ownerName && siteName
        ? `${ownerName} / ${siteName}`
        : siteName
      append(resourceDetail(
        '库位',
        siteIdentity,
        inlineLabel || resourceLabel(labels.sites, siteIdentity),
      ))
      return
    }
    if (scope === 'material' && resource.material_uuid) {
      const identity = String(resource.material_uuid)
      const label = String(resource.material_name || '').trim()
        || resourceLabel(labels.materials, identity)
      append(resourceDetail('物料', identity, label))
    }
  })
  return { details, scopes }
}

function waitTitleFromResources(scopes: Set<WaitResourceScope>, fallback: string): string {
  if (scopes.size > 1) return '等待执行资源'
  if (scopes.has('device')) return '等待设备'
  if (scopes.has('material_site')) return '等待库位'
  if (scopes.has('material')) return '等待物料'
  return fallback
}

function presentWaitReason(
  waitReason: unknown,
  node: RawRecord,
  labels: WaitResourceLabels,
): TaskNodeWaitReason | undefined {
  if (!waitReason || typeof waitReason !== 'object') return undefined
  const reason = waitReason as RawRecord
  const hasWaitFact = [
    reason.code,
    reason.message,
    reason.waiting_since,
    reason.blocking_task_uuid,
    reason.blocking_job_uuid,
  ].some((value) => String(value || '').trim())
    || (Array.isArray(reason.resources) && reason.resources.length > 0)
  if (!hasWaitFact) return undefined
  const code = String(reason.code || 'waiting_condition')
  const fallback = waitReasonDefaults[code] || { title: '等待条件', message: '节点的运行条件尚未满足' }
  const { details, scopes } = waitResourceDetails(reason.resources, labels)
  const waitsForDevice = scopes.has('device') || code === 'device_busy' || code === 'device_lock'
  if (!details.length && waitsForDevice && node.device_id) {
    const identity = String(node.device_id)
    details.push(resourceDetail('设备', identity, resourceLabel(labels.devices, identity)))
  }
  if (!details.length && code === 'material_unavailable' && node.kind === 'material_source') {
    const identity = String(node.material_uuid || node.param?.material_uuid || '').trim()
    if (identity) {
      details.push(resourceDetail('物料', identity, resourceLabel(labels.materials, identity)))
    } else {
      details.push(`物料需求：${nodeName(node)}（尚未分配具体物料）`)
    }
  }
  if (reason.blocking_task_uuid) details.push(`阻塞任务：${String(reason.blocking_task_uuid)}`)
  if (reason.blocking_job_uuid) details.push(`阻塞 Job：${String(reason.blocking_job_uuid)}`)
  return {
    code,
    title: waitTitleFromResources(scopes, fallback.title),
    message: String(reason.message || fallback.message),
    details,
    waitingSince: reason.waiting_since ? String(reason.waiting_since) : undefined,
  }
}

function timeLabel(value: unknown): string {
  if (!value) return '—'
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function canonicaliseMatrixValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonicaliseMatrixValue)
  if (!value || typeof value !== 'object') return value
  const record = value as RawRecord
  return Object.fromEntries(
    Object.keys(record)
      .sort()
      .map((key) => [key, canonicaliseMatrixValue(record[key])]),
  )
}

function matrixGroupKey(raw: RawRecord): string {
  if (!raw.workflow_snapshot || !raw.execution_plan) {
    return `task:${String(raw.uuid)}`
  }
  const plan = raw.execution_plan as RawRecord
  const nodes = Array.isArray(plan.nodes) ? plan.nodes.map((node: RawRecord) => ({
    uuid: node.uuid,
    name: node.name,
    actionName: node.action_name,
    actionType: node.action_type,
    deviceId: node.device_id,
    topologicalIndex: node.topological_index,
    disabled: Boolean(node.disabled),
  })) : []
  const edges = Array.isArray(plan.edges) ? plan.edges.map((edge: RawRecord) => ({
    sourceNodeUuid: edge.source_node_uuid,
    targetNodeUuid: edge.target_node_uuid,
  })) : []
  return JSON.stringify(canonicaliseMatrixValue({
    executionKind: raw.execution_kind,
    workflowUuid: raw.workflow_uuid,
    workflowRevision: raw.workflow_snapshot?.workflow?.revision,
    nodes,
    edges,
    runMode: raw.run_mode,
    targetNodeUuid: raw.target_node_uuid,
  }))
}

function collectSchemaMaterialUuids(
  schemaValue: unknown,
  value: unknown,
  add: (value: unknown) => void,
): void {
  if (!schemaValue || typeof schemaValue !== 'object') return
  const schema = schemaValue as RawRecord
  if (Array.isArray(schema.anyOf)) {
    schema.anyOf.forEach((member: unknown) => collectSchemaMaterialUuids(member, value, add))
    return
  }
  if (
    schema.$slot === 'ResourceSlot'
    || typeof schema['x-unilabos-material-lock'] === 'boolean'
  ) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      add((value as RawRecord).uuid)
    }
    return
  }
  if (schema.type === 'array') {
    if (Array.isArray(value)) {
      value.forEach((item) => collectSchemaMaterialUuids(schema.items, item, add))
    }
    return
  }
  if (schema.type === 'object' && schema.properties && typeof schema.properties === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return
    Object.entries(schema.properties as RawRecord).forEach(([key, childSchema]) => {
      collectSchemaMaterialUuids(childSchema, (value as RawRecord)[key], add)
    })
  }
}

function actionParamSchema(node: RawRecord): unknown {
  const schema = node.param_schema
  if (!schema || typeof schema !== 'object') return undefined
  return schema.properties?.goal || schema
}

function collectKnownMaterialEnvelopes(
  value: unknown,
  knownMaterialUuids: ReadonlySet<string>,
  add: (value: unknown) => void,
): void {
  if (Array.isArray(value)) {
    value.forEach((item) => collectKnownMaterialEnvelopes(item, knownMaterialUuids, add))
    return
  }
  if (!value || typeof value !== 'object') return
  const record = value as RawRecord
  if (typeof record.uuid === 'string' && knownMaterialUuids.has(record.uuid)) add(record.uuid)
  Object.values(record).forEach((item) => collectKnownMaterialEnvelopes(item, knownMaterialUuids, add))
}

function collectTaskMaterialUuids(
  raw: RawRecord,
  jobs: RawRecord[],
  inputContract: ContractField[],
  knownMaterialUuids?: ReadonlySet<string>,
): string[] {
  const materialUuids = new Set<string>()
  const add = (value: unknown) => {
    if (typeof value === 'string' && value) materialUuids.add(value)
  }
  if (Array.isArray(raw.material_uuids)) raw.material_uuids.forEach(add)
  const planNodes = Array.isArray(raw.execution_plan?.nodes) ? raw.execution_plan.nodes : []
  const planNodeByUuid = new Map<string, RawRecord>()
  planNodes.forEach((node: RawRecord) => {
    add(node.material_uuid)
    planNodeByUuid.set(String(node.uuid), node)
    collectSchemaMaterialUuids(actionParamSchema(node), node.param, add)
    if (knownMaterialUuids) collectKnownMaterialEnvelopes(node.param, knownMaterialUuids, add)
  })
  jobs.forEach((job) => {
    add(job.material_uuid)
    add(job.control_data?.actual_executor?.material_uuid)
    add(job.return_info?.material?.uuid)
    add(job.expected_change_set?.material_uuid)
    const planNode = planNodeByUuid.get(String(job.workflow_node_uuid))
    collectSchemaMaterialUuids(actionParamSchema(planNode || job), job.param, add)
    if (knownMaterialUuids) collectKnownMaterialEnvelopes(job.param, knownMaterialUuids, add)
  })
  inputContract.forEach((field) => {
    collectSchemaMaterialUuids(field.schema, raw.input?.[field.name], add)
  })
  if (knownMaterialUuids) collectKnownMaterialEnvelopes(raw.input, knownMaterialUuids, add)
  return [...materialUuids]
}

const SIGNOZ_TRACE_QUERY_NAME = 'A'

function schedulerTraceSearch(taskUuid: string) {
  const query = {
    queryType: 'builder',
    builder: {
      queryData: [{
        dataSource: 'traces',
        queryName: SIGNOZ_TRACE_QUERY_NAME,
        aggregateAttribute: { id: '----', dataType: '', key: '', type: '' },
        timeAggregation: 'rate',
        spaceAggregation: 'sum',
        filter: { expression: '' },
        aggregations: [{ expression: 'count() ' }],
        functions: [],
        filters: {
          items: [{
            id: 'workflow-task-uuid',
            key: { key: 'workflow.task.uuid', dataType: 'string', type: 'tag' },
            op: '=',
            value: taskUuid,
          }],
          op: 'AND',
        },
        expression: SIGNOZ_TRACE_QUERY_NAME,
        disabled: false,
        stepInterval: null,
        having: [],
        limit: null,
        orderBy: [],
        groupBy: [],
        legend: '',
        reduceTo: 'avg',
      }],
      queryFormulas: [],
      queryTraceOperator: [],
    },
    promql: [{ name: SIGNOZ_TRACE_QUERY_NAME, query: '', legend: '', disabled: false }],
    clickhouse_sql: [{ name: SIGNOZ_TRACE_QUERY_NAME, query: '', legend: '', disabled: false }],
    id: 'unilabos-task-trace',
    unit: '',
  }
  return encodeURIComponent(JSON.stringify(query))
}

function taskTraceReference(raw: RawRecord, traceUiUrl: string): WorkflowTask['trace'] {
  const traceId = typeof raw.trace_context?.trace_id === 'string'
    ? raw.trace_context.trace_id.toLowerCase()
    : ''
  if (!traceUiUrl) return undefined
  try {
    const url = new URL(traceUiUrl)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return undefined
    if (/^[0-9a-f]{32}$/.test(traceId)) {
      url.pathname = `${url.pathname.replace(/\/$/, '')}/trace/${traceId}`
      url.search = ''
      url.hash = ''
      return { traceId, url: url.toString(), mode: 'trace' }
    }
    const taskUuid = String(raw.uuid || '').trim()
    if (!taskUuid) return undefined
    url.pathname = `${url.pathname.replace(/\/$/, '')}/traces-explorer`
    url.search = ''
    url.searchParams.set('compositeQuery', schedulerTraceSearch(taskUuid))
    url.hash = ''
    return { url: url.toString(), mode: 'search' }
  } catch {
    return undefined
  }
}

export function adaptTask(
  raw: RawRecord,
  jobs: RawRecord[] = [],
  workflowName = '未命名工作流',
  inputContract: ContractField[] = [],
  knownMaterialUuids?: ReadonlySet<string>,
  traceUiUrl = '',
  waitResourceLabels: WaitResourceLabels = {},
): WorkflowTask {
  const attentionMessage = typeof raw.attention_reason === 'string'
    ? raw.attention_reason
    : raw.attention_reason?.message || raw.attention_reason?.code
  const waitMessage = raw.wait_reason?.message || raw.wait_reason?.code || attentionMessage
  const controlStatus = String(raw.control_status || 'active')
  const cleanupStatus = String(raw.cleanup_status || 'none')
  const normalisedStatus = normaliseTaskStatus(raw.status)
  const hasInFlightJob = jobs.some((job) => (
    ['dispatched', 'running', 'cancel_requested'].includes(String(job.status || ''))
  ))
  let status: TaskPresentationStatus = normalisedStatus
  if (normalisedStatus === 'pending' && waitMessage) status = 'admission_blocked'
  if (!terminalTaskStatuses.has(normalisedStatus) && controlStatus === 'paused' && !hasInFlightJob) status = 'paused'
  if (controlStatus === 'waiting_intervention') status = 'intervention_required'
  if (controlStatus === 'waiting_reconciliation') status = 'execution_unknown'
  if (cleanupStatus === 'requires_attention' || attentionMessage) status = 'intervention_required'
  const planNodes = Array.isArray(raw.execution_plan?.nodes) ? raw.execution_plan.nodes : []
  const sortedNodes = [...planNodes].sort(
    (left, right) => Number(left.topological_index || 0) - Number(right.topological_index || 0),
  )
  const jobByNode = new Map(jobs.map((job) => [String(job.workflow_node_uuid), job]))
  let nodes: TaskNode[] = sortedNodes.map((node, index) => {
    const uuid = String(node.uuid || `node-${index}`)
    const job = jobByNode.get(uuid)
    return {
      uuid,
      name: nodeName(node),
      kind: String(node.kind || node.action_type || 'device_action'),
      index: Number(node.topological_index ?? index),
      status: jobNodeStatus(job, status, node),
      device: node.device_id ? String(node.device_id) : undefined,
      materialUuid: node.material_uuid ? String(node.material_uuid) : undefined,
      waitReason: presentWaitReason(job?.wait_reason, node, waitResourceLabels),
      job: job ? {
        uuid: String(job.uuid || ''),
        attempt: job.attempt === undefined ? undefined : Number(job.attempt),
        param: job.param,
        feedbackData: job.feedback_data,
        returnInfo: job.return_info,
        errorInfo: Array.isArray(job.error_info) ? job.error_info : [],
        startedAt: job.started_at ? String(job.started_at) : undefined,
        finishedAt: job.finished_at ? String(job.finished_at) : undefined,
        manualConfirmation: job.manual_confirmation ? {
          status: String(job.manual_confirmation.status) as 'pending' | 'approved' | 'rejected' | 'timed_out' | 'canceled',
          deadlineAt: String(job.manual_confirmation.deadline_at || ''),
          actions: Array.isArray(job.manual_confirmation.actions)
            ? job.manual_confirmation.actions.filter((action: unknown) => action === 'approve' || action === 'reject')
            : [],
        } : undefined,
      } : undefined,
    }
  })
  const incomingNodeUuids = new Map<string, string[]>()
  const planEdges = Array.isArray(raw.execution_plan?.edges) ? raw.execution_plan.edges : []
  planEdges.forEach((edge: RawRecord) => {
    const sourceUuid = String(edge.source_node_uuid || '')
    const targetUuid = String(edge.target_node_uuid || '')
    if (!sourceUuid || !targetUuid) return
    incomingNodeUuids.set(targetUuid, [...(incomingNodeUuids.get(targetUuid) || []), sourceUuid])
  })
  const rawNodeByUuid = new Map(sortedNodes.map((node) => [String(node.uuid), node]))
  const taskWaitReason = status === 'admission_blocked'
    ? presentWaitReason(raw.wait_reason, {}, waitResourceLabels)
    : undefined
  if (taskWaitReason) {
    const pendingNodes = nodes.filter((node) => (
      !node.waitReason && ['pending', 'waiting'].includes(node.status)
    ))
    const materialSourceNodes = taskWaitReason.code === 'material_unavailable'
      ? pendingNodes.filter((node) => node.kind === 'material_source')
      : []
    const rootNodes = pendingNodes.filter((node) => !(incomingNodeUuids.get(node.uuid) || []).length)
    const waitingNodes = materialSourceNodes.length
      ? materialSourceNodes
      : rootNodes.length
        ? rootNodes
        : pendingNodes.slice(0, 1)
    const waitingNodeUuids = new Set(waitingNodes.map((node) => node.uuid))
    nodes = nodes.map((node) => waitingNodeUuids.has(node.uuid)
      ? {
          ...node,
          status: 'waiting',
          waitReason: presentWaitReason(
            raw.wait_reason,
            rawNodeByUuid.get(node.uuid) || {},
            waitResourceLabels,
          ) || taskWaitReason,
        }
      : node)
  }
  const nodeByUuid = new Map(nodes.map((node) => [node.uuid, node]))
  const canDeriveWaitReason = ['running', 'pending', 'admission_blocked'].includes(status)
  nodes = nodes.map((node) => {
    if (node.waitReason || !['pending', 'waiting'].includes(node.status) || !canDeriveWaitReason) return node
    const unresolved = (incomingNodeUuids.get(node.uuid) || [])
      .map((uuid) => nodeByUuid.get(uuid))
      .filter((upstream): upstream is TaskNode => Boolean(
        upstream && !['succeeded', 'skipped'].includes(upstream.status),
      ))
    if (unresolved.length) {
      return {
        ...node,
        waitReason: {
          code: 'upstream_dependency',
          title: '等待前置节点',
          message: '以下节点完成后才能运行',
          details: unresolved.map((upstream) => upstream.name),
        },
      }
    }
    return {
      ...node,
      waitReason: {
        code: 'dispatch_pending',
        title: '等待调度',
        message: '前置条件已满足，等待调度器进行下一次派发判定',
        details: [],
      },
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
    workflowName: String(raw.workflow_snapshot?.workflow?.name || workflowName),
    status,
    priority: taskPriority(raw.priority),
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
    materialUuids: collectTaskMaterialUuids(raw, jobs, inputContract, knownMaterialUuids),
    workflowRevision: Number(raw.workflow_snapshot?.workflow?.revision || raw.workflow?.revision || 0) || undefined,
    runMode: String(raw.run_mode || raw.execution_plan?.run_mode || 'normal'),
    executionMode: String(raw.execution_mode || raw.run_mode || 'normal') as WorkflowTask['executionMode'],
    controlStatus,
    matrixGroupKey: matrixGroupKey(raw),
    trace: taskTraceReference(raw, traceUiUrl),
  }
}

function taskPriority(value: unknown): WorkflowTask['priority'] {
  if (value === 'urgent' || value === 'high' || value === 'normal' || value === 'low') return value
  if (value === undefined || value === null || value === '') return 'normal'
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : 'unknown'
}

function configuredSourceLabel(sourceNodeId: unknown): string {
  const id = String(sourceNodeId || '')
  const [prefix, slot] = id.split('__')
  const station = prefix?.match(/^s(\d+)/i)?.[1]
  if (station && slot) return `S${station} / ${slot}`
  return id || '无配置来源位置'
}

function materialStructure(raw: RawRecord): { isStructural: boolean; siteCount: number } {
  const sites = Array.isArray(raw.config?.sites) ? raw.config.sites : []
  return {
    isStructural: raw.config?.logical_mount === true || sites.length > 0,
    siteCount: sites.length,
  }
}

function configuredSites(raw: RawRecord): MaterialRecord['sites'] {
  const sites = Array.isArray(raw.sites)
    ? raw.sites
    : Array.isArray(raw.config?.sites) ? raw.config.sites : []
  return sites.map((site: RawRecord, index: number) => ({
    uuid: String(site.uuid || `${raw.uuid || 'material'}-site-${index}`),
    name: String(site.name || site.id || `SITE-${index + 1}`),
    occupiedMaterialUuid: site.occupied_material_uuid ? String(site.occupied_material_uuid) : undefined,
    occupiedMaterialName: site.occupied_material_name ? String(site.occupied_material_name) : undefined,
    allowedResourceTemplateUuids: Array.isArray(site.allowed_resource_template_uuids)
      ? site.allowed_resource_template_uuids.map(String)
      : [],
  }))
}

function currentLocationFromDetail(raw: RawRecord): MaterialCurrentLocation {
  const hasCurrentSiteProjection = Object.prototype.hasOwnProperty.call(raw, 'current_site')
  if (!hasCurrentSiteProjection) {
    return { kind: 'unresolved', label: '权威位置尚未读取' }
  }
  if (!raw.current_site) {
    const structure = materialStructure(raw)
    if (structure.isStructural) {
      return { kind: 'structural', label: `结构资源 · 提供 ${structure.siteCount} 个库位`, siteCount: structure.siteCount }
    }
    return { kind: 'unassigned', label: '未分配权威库位' }
  }
  const currentSiteName = String(raw.current_site.name || raw.current_site.uuid)
  const currentSiteSource = String(raw.current_site.meta_data?.source_node_id || '')
  const station = currentSiteSource.match(/^s(\d+)/i)?.[1]
  return {
    kind: 'site',
    label: `${station ? `S${station} / ` : ''}${currentSiteName}`,
    siteUuid: String(raw.current_site.uuid),
    ownerMaterialUuid: String(raw.current_site.material_uuid),
  }
}

export function adaptMaterial(
  raw: RawRecord,
  currentLocation: MaterialCurrentLocation = currentLocationFromDetail(raw),
): MaterialRecord {
  const structure = materialStructure(raw)
  const relative = raw.relative_position || {}
  return {
    uuid: String(raw.uuid),
    name: String(raw.name || raw.uuid),
    category: String(raw.config?.category || raw.config?.rendering?.kind || 'material'),
    currentLocation,
    configuredSource: configuredSourceLabel(raw.meta_data?.source_node_id),
    sourceNodeId: raw.meta_data?.source_node_id ? String(raw.meta_data.source_node_id) : undefined,
    taskReferences: [],
    barcode: String(raw.barcode || '—'),
    parentUuid: raw.parent_uuid ? String(raw.parent_uuid) : undefined,
    className: String(raw.class || 'unknown'),
    resourceTemplateUuid: raw.resource_template_uuid ? String(raw.resource_template_uuid) : undefined,
    sourceGraph: raw.meta_data?.source_graph ? String(raw.meta_data.source_graph) : undefined,
    updatedAt: timeLabel(raw.update_time),
    ...structure,
    sites: configuredSites(raw),
    revision: Number(raw.revision || 1),
    position: [Number(relative.position_x || 0), Number(relative.position_y || 0), Number(relative.position_z || 0)],
    size: [Number(relative.width || 80), Number(relative.length || 80), Number(relative.depth || 80)],
  }
}

export async function loadResourceTemplates(signal?: AbortSignal): Promise<ResourceTemplateRecord[]> {
  const page = await requestAllPages<RawRecord>('/resource-templates', signal)
  return page.items.map((raw) => ({
    uuid: String(raw.uuid), name: String(raw.name || raw.uuid), displayName: String(raw.display_name || raw.name || raw.uuid),
    description: String(raw.description || ''), resourceType: String(raw.resource_type || raw.registry_type || 'resource'),
    availableSites: Array.isArray(raw.available_sites) ? raw.available_sites.map((site: RawRecord) => ({ name: String(site.name || site.label), label: String(site.label || site.name) })) : [],
  }))
}

export async function instantiateMaterial(payload: { resourceTemplateUuid: string; name: string; barcode: string; description?: string; siteUuid?: string }) {
  return writeData<RawRecord>('POST', '/materials', {
    resource_template_uuid: payload.resourceTemplateUuid,
    name: payload.name,
    barcode: payload.barcode,
    description: payload.description || undefined,
    ...(payload.siteUuid ? { site_placement: { action: 'place', site_uuid: payload.siteUuid } } : {}),
  })
}

export async function changeMaterialSite(materialUuid: string, revision: number, siteUuid?: string) {
  return writeData<RawRecord>('PUT', `/materials/${encodeURIComponent(materialUuid)}`, {
    expected_revision: revision,
    site_placement: siteUuid ? { action: 'place', site_uuid: siteUuid } : { action: 'remove' },
  })
}

export async function verifyMaterialBarcode(barcode: string, signal?: AbortSignal): Promise<MaterialRecord[]> {
  const result = await requestData<PageData<RawRecord>>(`/materials?barcode=${encodeURIComponent(barcode)}&page=1&page_size=20`, signal)
  return (result.items || []).map((raw) => adaptMaterial(raw))
}

/** 读取可由实验操作线性编排的设备动作模板，不包含结构控制节点。 */
export async function loadActionTemplates(signal?: AbortSignal): Promise<ActionTemplateRecord[]> {
  const page = await requestAllPages<RawRecord>('/workflow-node-templates', signal)
  return page.items.filter((raw) => !['condition', 'repeat_until'].includes(String(raw.node_type || ''))).map((raw) => ({
    uuid: String(raw.uuid), name: String(raw.name || raw.uuid), displayName: String(raw.display_name || raw.name || raw.uuid), description: raw.description ? String(raw.description) : undefined,
    type: String(raw.type || ''), nodeType: String(raw.node_type || ''),
    resourceTemplate: { uuid: String(raw.resource_template?.uuid || ''), name: String(raw.resource_template?.name || ''), displayName: String(raw.resource_template?.display_name || raw.resource_template?.name || '') },
  }))
}

/** 读取条件与循环模板，并从详情接口取得调度器实际使用的参数说明。 */
export async function loadControlTemplates(signal?: AbortSignal): Promise<ControlTemplateRecord[]> {
  const page = await requestAllPages<RawRecord>('/workflow-node-templates', signal)
  const summaries = page.items.filter((raw) => ['condition', 'repeat_until'].includes(String(raw.node_type || ''))).map((raw) => ({
    uuid: String(raw.uuid), name: String(raw.name || raw.uuid), displayName: String(raw.display_name || raw.name || raw.uuid), description: raw.description ? String(raw.description) : undefined,
    type: String(raw.type || ''), nodeType: String(raw.node_type || ''),
    resourceTemplate: { uuid: String(raw.resource_template?.uuid || ''), name: String(raw.resource_template?.name || ''), displayName: String(raw.resource_template?.display_name || raw.resource_template?.name || '') },
  }))
  return mapWithConcurrency(summaries, 4, async (summary) => {
    const detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(summary.uuid)}`, signal)
    const detailTemplate = detail.template && typeof detail.template === 'object' ? detail.template as RawRecord : {}
    const schema = detailTemplate.meta_data?.unilab?.parameter_schema
    return {
      ...summary,
      description: summary.description || (detailTemplate.description ? String(detailTemplate.description) : undefined),
      parameterSchema: schema && typeof schema === 'object' ? { ...schema } : { type: 'object' },
    }
  })
}

export async function loadActionParameters(templateUuid: string, signal?: AbortSignal): Promise<ActionParameterRecord[]> {
  const detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(templateUuid)}`, signal)
  return (Array.isArray(detail.handles) ? detail.handles : [])
    .filter((handle: RawRecord) => handle.io_type === 'target' && handle.handle_key !== 'ready' && handle.data_key)
    .map((handle: RawRecord) => ({
      handleUuid: String(handle.uuid),
      key: String(handle.data_key),
      displayName: String(handle.display_name || handle.data_key),
      required: Boolean(handle.required),
      schema: handle.meta_data?.unilab?.value_schema && typeof handle.meta_data.unilab.value_schema === 'object'
        ? { ...handle.meta_data.unilab.value_schema }
        : { type: String(handle.type || 'string').toLowerCase() },
    }))
}

export async function loadOperationCategories(signal?: AbortSignal): Promise<OperationCategoryRecord[]> {
  const data = await requestData<{ items: RawRecord[] }>('/experiment-operation-categories', signal)
  return (data.items || []).map((raw) => ({ uuid: String(raw.uuid), name: String(raw.name), sortOrder: Number(raw.sort_order || 100) }))
}

export async function loadReagentInfos(signal?: AbortSignal): Promise<ReagentInfoRecord[]> {
  const page = await requestAllPages<RawRecord>('/reagent-infos', signal)
  return page.items.map((raw) => ({
    uuid: String(raw.uuid),
    name: String(raw.name || raw.uuid),
    nameEn: raw.name_en ? String(raw.name_en) : undefined,
    aliases: Array.isArray(raw.aliases) ? raw.aliases.map(String) : [],
    cas: raw.cas ? String(raw.cas) : undefined,
    molecularFormula: raw.molecular_formula ? String(raw.molecular_formula) : undefined,
    smiles: raw.smiles ? String(raw.smiles) : undefined,
    inchiKey: raw.inchi_key ? String(raw.inchi_key) : undefined,
    molecularWeight: raw.molecular_weight == null ? undefined : Number(raw.molecular_weight),
    densityGPerMl: raw.density_g_per_ml == null ? undefined : Number(raw.density_g_per_ml),
    physicalState: ['solid', 'liquid', 'gas', 'other'].includes(raw.physical_state) ? raw.physical_state : 'unknown',
    description: raw.description ? String(raw.description) : undefined,
    metadata: raw.meta_data && typeof raw.meta_data === 'object' ? { ...raw.meta_data } : undefined,
    updatedAt: timeLabel(raw.update_time),
  }))
}

export async function lookupCompoundByCas(cas: string, signal?: AbortSignal): Promise<CompoundLookupResult> {
  const raw = await requestData<RawRecord>(`/compounds/${encodeURIComponent(cas)}`, signal)
  const compound = raw.compound && typeof raw.compound === 'object' ? raw.compound as RawRecord : undefined
  return {
    cas: String(raw.cas || cas),
    status: ['ok', 'registered', 'not_found', 'unavailable'].includes(raw.status) ? raw.status : 'unavailable',
    message: raw.message ? String(raw.message) : undefined,
    compound: compound ? {
      name: compound.name ? String(compound.name) : undefined,
      molecularFormula: compound.molecular_formula ? String(compound.molecular_formula) : undefined,
      smiles: compound.smiles ? String(compound.smiles) : undefined,
      inchiKey: compound.inchi_key ? String(compound.inchi_key) : undefined,
      molecularWeight: compound.molecular_weight == null ? undefined : Number(compound.molecular_weight),
    } : undefined,
  }
}

export async function loadReagents(signal?: AbortSignal): Promise<ReagentRecord[]> {
  const page = await requestAllPages<RawRecord>('/reagents', signal)
  return page.items.map((raw) => ({
    uuid: String(raw.uuid), materialUuid: String(raw.material_uuid), reagentInfoUuid: String(raw.reagent_info_uuid),
    name: String(raw.name || raw.reagent_info_uuid), cas: raw.cas ? String(raw.cas) : undefined,
    molecularFormula: raw.molecular_formula ? String(raw.molecular_formula) : undefined,
    physicalState: String(raw.physical_state || 'unknown'), quantity: raw.quantity == null ? undefined : Number(raw.quantity),
    quantityUnit: raw.quantity_unit ? String(raw.quantity_unit) : undefined,
    concentrationValue: raw.concentration_value == null ? undefined : Number(raw.concentration_value),
    concentrationUnit: raw.concentration_unit ? String(raw.concentration_unit) : undefined,
    densityGPerMl: raw.density_g_per_ml == null ? undefined : Number(raw.density_g_per_ml),
    containerName: raw.container_name ? String(raw.container_name) : undefined,
    containerBarcode: raw.container_barcode ? String(raw.container_barcode) : undefined,
    revision: Number(raw.revision || 1), updatedAt: timeLabel(raw.update_time),
  }))
}

export async function loadReagentHistory(materialUuid: string, signal?: AbortSignal): Promise<ReagentHistoryRecord[]> {
  const page = await requestAllPages<RawRecord>(`/materials/${encodeURIComponent(materialUuid)}/reagent-history`, signal)
  return page.items.map((raw) => {
    const changes = raw.changes && typeof raw.changes === 'object' ? raw.changes as RawRecord : {}
    const result = changes.result && typeof changes.result === 'object' ? changes.result as RawRecord : {}
    const extension = raw.extension && typeof raw.extension === 'object' ? raw.extension as RawRecord : {}
    return {
      uuid: String(raw.uuid),
      materialUuid: String(raw.material_uuid || materialUuid),
      reagentUuid: String(raw.subject_uuid || ''),
      eventType: String(raw.event_type || 'adjust'),
      operatorType: String(raw.operator_type || 'system'),
      quantityDelta: Number(raw.quantity_delta || 0),
      quantityUnit: String(raw.quantity_unit || result.quantity_unit || ''),
      revision: Number(raw.revision || result.revision || 0),
      recordedAt: String(raw.recorded_at || ''),
      resultQuantity: result.quantity == null ? undefined : Number(result.quantity),
      resultQuantityUnit: result.quantity_unit ? String(result.quantity_unit) : undefined,
      source: extension.source ? String(extension.source) : undefined,
      workflowTaskUuid: raw.workflow_task_uuid ? String(raw.workflow_task_uuid) : undefined,
      workflowNodeJobUuid: raw.workflow_node_job_uuid ? String(raw.workflow_node_job_uuid) : undefined,
      traceId: raw.trace_id ? String(raw.trace_id) : undefined,
    }
  })
}

export async function createReagentInfo(payload: {
  name: string; nameEn?: string; aliases?: string[]; cas?: string; molecularFormula?: string;
  smiles?: string; inchiKey?: string; molecularWeight?: number; densityGPerMl?: number;
  physicalState: ReagentInfoRecord['physicalState']; description?: string; metadata?: Record<string, unknown>
}) {
  return writeData<RawRecord>('POST', '/reagent-infos', {
    name: payload.name, name_en: payload.nameEn || undefined, aliases: payload.aliases || [], cas: payload.cas || '',
    molecular_formula: payload.molecularFormula || undefined, smiles: payload.smiles || undefined,
    inchi_key: payload.inchiKey || undefined, molecular_weight: payload.molecularWeight,
    density_g_per_ml: payload.densityGPerMl, physical_state: payload.physicalState,
    description: payload.description || undefined, meta_data: payload.metadata || {},
  })
}

export async function deleteReagentInfo(reagentInfoUuid: string) {
  return writeData<unknown>('DELETE', `/reagent-infos/${encodeURIComponent(reagentInfoUuid)}`)
}

export async function createReagent(payload: {
  materialUuid: string; reagentInfoUuid: string; quantity: number; quantityUnit: string;
  concentrationValue?: number; concentrationUnit?: string; source?: string; description?: string
}) {
  return writeData<RawRecord>('POST', '/reagents', {
    material_uuid: payload.materialUuid, reagent_info_uuid: payload.reagentInfoUuid,
    quantity: payload.quantity, quantity_unit: payload.quantityUnit,
    physical_state: 'unknown',
    ...(payload.concentrationValue == null || !payload.concentrationUnit ? {} : { concentration_value: payload.concentrationValue, concentration_unit: payload.concentrationUnit }),
    source: payload.source || 'frontend:os-console', description: payload.description || undefined, meta_data: {},
  })
}

export async function loadExperimentOperations(signal?: AbortSignal): Promise<WorkflowDefinition[]> {
  const page = await requestAllPages<RawRecord>('/workflows?workflow_type=experiment_operation', signal)
  return mapWithConcurrency(page.items, 6, async (raw) => {
    const graph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(raw.uuid)}/graph`, signal)
    return adaptWorkflow({ ...raw, nodes: Array.isArray(graph.nodes) ? graph.nodes : [] })
  })
}

export async function loadPublishedExperimentOperations(signal?: AbortSignal): Promise<WorkflowDefinition[]> {
  const operations = await loadExperimentOperations(signal)
  return operations.filter((operation) => operation.workflowType === 'experiment_operation' && operation.status === 'published')
}

export async function loadPublishedWorkflowContracts(signal?: AbortSignal): Promise<RawRecord[]> {
  const [contracts, operations] = await Promise.all([
    requestAllPages<RawRecord>('/published-workflow-contracts', signal),
    requestAllPages<RawRecord>('/workflows?workflow_type=experiment_operation&status=published', signal),
  ])
  // 发布合同是引用所需的参数/执行器快照，但公共合同投影不保证携带
  // workflow_type 或 tags（历史合同经常是空 tags）。以 OS 工作流列表返回的
  // 类型和 published 状态作为唯一门禁，避免把普通工作流混入，也不漏掉合法合同。
  const publishedOperations = new Map(
    operations.items.map((operation) => [String(operation.uuid || ''), operation]),
  )
  return contracts.items
    .filter((contract) => publishedOperations.has(String(contract.workflow_uuid || '')))
    .map((contract) => {
      const operation = publishedOperations.get(String(contract.workflow_uuid || ''))
      return {
        ...contract,
        workflow_type: operation?.workflow_type || 'experiment_operation',
        status: operation?.status || 'published',
        name: contract.name || operation?.name,
      }
    })
}

export async function insertCompositeWorkflow(payload: {
  parentWorkflowUuid: string
  revision: number
  contractUuid: string
  invocationUuid?: string
  deviceBindings?: Record<string, string>
  pose?: Record<string, unknown>
  param?: Record<string, unknown>
}) {
  return writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(payload.parentWorkflowUuid)}/composite-invocations`, {
    revision: payload.revision,
    contract_uuid: payload.contractUuid,
    invocation_uuid: payload.invocationUuid,
    device_bindings: payload.deviceBindings || {},
    pose: payload.pose || { x: 120, y: 180 },
    param: payload.param || {},
  })
}

export async function patchWorkflowNode(nodeUuid: string, patch: { pose?: Record<string, unknown>; param?: Record<string, unknown>; metaData?: Record<string, unknown> }) {
  return writeData<RawRecord>('PATCH', `/workflow-nodes/${encodeURIComponent(nodeUuid)}`, {
    ...(patch.pose ? { pose: patch.pose } : {}),
    ...(patch.param ? { param: patch.param } : {}),
    ...(patch.metaData ? { meta_data: patch.metaData } : {}),
  })
}

/**
 * Ensure the visible top-level nodes form the authoring sequence. Composite
 * invocations are expanded by OS together with their private child graph, so
 * the only nodes that may be connected here are the invocation roots and the
 * parent's ordinary action nodes (parent_uuid is absent).
 */
export async function ensureWorkflowSequenceEdges(workflowUuid: string, orderedNodeUuids: string[]) {
  if (orderedNodeUuids.length < 2) return
  let graph = await loadWorkflowGraph(workflowUuid)
  let revision = graph.workflow.revision
  const nodesByUuid = new Map(graph.nodes.map((node) => [String(node.uuid), node]))
  const existingPairs = new Set(graph.edges.map((edge) => `${edge.sourceNodeUuid}:${edge.targetNodeUuid}`))
  const graphHandles = Array.isArray(graph.handleTemplates) ? graph.handleTemplates : []
  const templateDetails = new Map<string, RawRecord>()
  async function publishedReadyHandle(templateUuid: string, ioType: 'source' | 'target') {
    const hex = templateUuid.replace(/-/g, '')
    if (!/^[0-9a-f]{32}$/i.test(hex) || !globalThis.crypto?.subtle) return undefined
    const namespace = new Uint8Array(hex.match(/.{2}/g)!.map((value) => Number.parseInt(value, 16)))
    const name = new TextEncoder().encode(`published-handle:${ioType}:ready`)
    const bytes = new Uint8Array(namespace.length + name.length)
    bytes.set(namespace); bytes.set(name, namespace.length)
    const digest = new Uint8Array(await globalThis.crypto.subtle.digest('SHA-1', bytes))
    digest[6] = (digest[6] & 0x0f) | 0x50; digest[8] = (digest[8] & 0x3f) | 0x80
    const formatted = Array.from(digest.slice(0, 16)).map((value) => value.toString(16).padStart(2, '0')).join('')
    return `${formatted.slice(0, 8)}-${formatted.slice(8, 12)}-${formatted.slice(12, 16)}-${formatted.slice(16, 20)}-${formatted.slice(20)}`
  }
  async function handlesFor(nodeUuid: string) {
    const node = nodesByUuid.get(nodeUuid)
    const templateUuid = String(node?.workflow_node_template_uuid || '')
    if (!templateUuid) throw new Error(`节点 ${nodeUuid} 缺少模板身份，无法建立子工作流连线`)
    let detail = templateDetails.get(templateUuid)
    if (!detail) {
      const handles = graphHandles.filter((handle) => String(handle.workflow_node_template_uuid || '') === templateUuid)
      if (handles.length) detail = { handles }
      else {
        try { detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(templateUuid)}`) }
        catch (error) {
          // Published composite templates are framework-owned and may not be
          // listed by the device catalog endpoint. Their ready handle UUID is
          // deterministic (UUIDv5), so derive it locally from the contract
          // template identity rather than making the user unable to connect.
          const nodeMeta = nodesByUuid.get(nodeUuid)?.meta_data?.unilab
          if (!nodeMeta?.composite) throw error
          const [source, target] = await Promise.all([
            publishedReadyHandle(templateUuid, 'source'), publishedReadyHandle(templateUuid, 'target'),
          ])
          if (!source || !target) throw error
          detail = { handles: [{ uuid: source, handle_key: 'ready', io_type: 'source' }, { uuid: target, handle_key: 'ready', io_type: 'target' }] }
        }
      }
      templateDetails.set(templateUuid, detail)
    }
    const handles = Array.isArray(detail.handles) ? detail.handles : []
    return {
      source: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'source'),
      target: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'target'),
    }
  }
  for (let index = 1; index < orderedNodeUuids.length; index += 1) {
    const sourceUuid = String(orderedNodeUuids[index - 1] || '')
    const targetUuid = String(orderedNodeUuids[index] || '')
    if (!sourceUuid || !targetUuid || sourceUuid === targetUuid || existingPairs.has(`${sourceUuid}:${targetUuid}`)) continue
    const sourceHandles = await handlesFor(sourceUuid)
    const targetHandles = await handlesFor(targetUuid)
    if (!sourceHandles.source?.uuid || !targetHandles.target?.uuid) {
      throw new Error('子工作流或 Action 模板缺少 ready 控制句柄，无法建立顺序连线')
    }
    const updated = await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/edges`, {
      source_node_uuid: sourceUuid,
      target_node_uuid: targetUuid,
      source_handle_uuid: sourceHandles.source.uuid,
      target_handle_uuid: targetHandles.target.uuid,
      description: '实验操作顺序依赖',
      meta_data: { unilab: { generated_by: 'operation-builder', composite_boundary: true } },
    })
    revision = Number(updated?.workflow?.revision || updated?.revision || revision + 1)
    existingPairs.add(`${sourceUuid}:${targetUuid}`)
    graph = await loadWorkflowGraph(workflowUuid)
    revision = graph.workflow.revision || revision
  }
  return revision
}

export async function publishWorkflow(workflowUuid: string, revision: number) {
  return writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/publications`, { revision })
}

export async function deleteExperimentOperation(workflowUuid: string) {
  return writeData<unknown>('DELETE', `/workflows/${encodeURIComponent(workflowUuid)}`)
}

/** 保存实验操作元数据和图节点；控制节点仅更新 param，不改其结构与模板身份。 */
export async function updateExperimentOperation(payload: {
  workflowUuid: string
  name: string
  description: string
  categoryUuid?: string
  actions: Array<{ draftId?: string; nodeUuid?: string; templateUuid?: string; name: string; description?: string; materialUuid: string; deviceId: string; param: Record<string, unknown>; inputBindings: Record<string, { parameter: string }> }>
  controls?: Array<{ draftId?: string; nodeUuid?: string; templateUuid: string; nodeType?: string; name: string; description?: string; param: Record<string, unknown> }>
  inputContract?: Record<string, unknown>
  outputContract?: Record<string, unknown>
}) {
  await writeData<RawRecord>('PUT', `/workflows/${encodeURIComponent(payload.workflowUuid)}`, {
    name: payload.name,
    description: payload.description,
    tags: ['experiment-operation'],
    workflow_type: 'experiment_operation',
    operation_category_uuid: payload.categoryUuid || null,
    meta_data: { unilab: { input_contract: payload.inputContract || {}, output_contract: payload.outputContract || {} } },
  })
  const graph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(payload.workflowUuid)}/graph`)
  const edits = new Map(payload.actions.filter((action) => action.nodeUuid).map((action) => [action.nodeUuid!, action]))
  const controlEdits = new Map((payload.controls || []).filter((control) => control.nodeUuid).map((control) => [control.nodeUuid!, control]))
  const nodes = (Array.isArray(graph.nodes) ? graph.nodes : []).map((node: RawRecord) => {
    const edit = edits.get(String(node.uuid))
    const controlEdit = controlEdits.get(String(node.uuid))
    if (!edit && !controlEdit) return node
    if (controlEdit) {
      return { ...node, name: controlEdit.name, description: controlEdit.description || controlEdit.name, param: controlEdit.param }
    }
    if (!edit) return node
    return {
      ...node,
      name: edit.name,
      material_uuid: edit.materialUuid,
      param: edit.param,
      // OS treats the material instance UUID as the authoritative fixed
      // executor identity.  Older UI state used sourceNodeId here, which
      // made an otherwise valid edit fail compilation on the next save.
      meta_data: { ...(node.meta_data || {}), unilab: { ...(node.meta_data?.unilab || {}), input_bindings: edit.inputBindings, executor_binding: { mode: 'fixed', device_id: edit.materialUuid || edit.deviceId } } },
    }
  })
  await writeData<RawRecord>('PUT', `/workflows/${encodeURIComponent(payload.workflowUuid)}/graph`, {
    revision: Number(graph.workflow?.revision),
    nodes,
    edges: Array.isArray(graph.edges) ? graph.edges : [],
  })
  // 图接口返回顺序不是作者顺序；固定执行器边必须按 sequence_index 取最后一个
  // 节点，否则编辑时新增多个节点会把第二条边的源句柄误取成当前新节点模板的
  // ready 句柄，OS 会以“Handle 不属于节点模板”拒绝整条边。
  const orderedNodes = [...nodes].sort((left, right) => (
    Number(left.meta_data?.unilab?.sequence_index ?? Number.MAX_SAFE_INTEGER)
    - Number(right.meta_data?.unilab?.sequence_index ?? Number.MAX_SAFE_INTEGER)
  ))
  const existing = orderedNodes[orderedNodes.length - 1]
  let previousUuid = existing?.uuid ? String(existing.uuid) : ''
  const detailsByNodeUuid = new Map<string, RawRecord>()
  for (const [index, action] of payload.actions.filter((item) => !item.nodeUuid).entries()) {
    if (!action.templateUuid) throw new Error(`新增动作“${action.name}”缺少模板身份`)
    const detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(action.templateUuid)}`)
    const handles = Array.isArray(detail.handles) ? detail.handles : []
    const created = await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(payload.workflowUuid)}/nodes`, {
      workflow_node_template_uuid: action.templateUuid, material_uuid: action.materialUuid || undefined, name: action.name,
      description: action.description || action.name,
      pose: { x: 120 + (nodes.length + index) * 220, y: 180 }, param: action.param || {}, execution_policy: {},
      meta_data: { unilab: { sequence_index: nodes.length + index, input_bindings: action.inputBindings || {}, executor_binding: { mode: 'fixed', device_id: action.deviceId } } },
    })
    if (!created?.uuid) throw new Error(`新增动作“${action.name}”后端未返回节点身份`)
    // 保留新节点自己的模板句柄；下一次循环的边源端必须从上一个新节点取，
    // 不能从尚未更新的 nodes 快照中猜测。
    detailsByNodeUuid.set(String(created.uuid), detail)
    if (previousUuid) {
      const source = nodes.find((node: RawRecord) => String(node.uuid) === previousUuid)
      const sourceDetail = detailsByNodeUuid.get(previousUuid)
        || (source
          ? await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(String(source.workflow_node_template_uuid))}`)
          : undefined)
      if (!sourceDetail) throw new Error(`新增动作“${action.name}”无法解析前置节点模板`)
      const sourceHandle = (Array.isArray(sourceDetail.handles) ? sourceDetail.handles : []).find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'source')
      const targetHandle = handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'target')
      if (!sourceHandle?.uuid || !targetHandle?.uuid) throw new Error(`动作“${action.name}”的模板缺少 ready 控制句柄，无法建立顺序依赖`)
      await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(payload.workflowUuid)}/edges`, { source_node_uuid: previousUuid, target_node_uuid: String(created.uuid), source_handle_uuid: sourceHandle.uuid, target_handle_uuid: targetHandle.uuid, description: '实验操作顺序依赖', meta_data: { unilab: { generated_by: 'operation-builder' } } })
    }
    previousUuid = String(created.uuid)
  }
  return { workflowUuid: payload.workflowUuid, status: 'source' as const }
}

type OperationActionInput = {
  draftId?: string
  templateUuid: string
  materialUuid?: string
  deviceId: string
  name: string
  description?: string
  param?: Record<string, unknown>
  inputBindings?: Record<string, { parameter: string }>
}

type OperationControlInput = {
  draftId?: string
  nodeUuid?: string
  templateUuid: string
  nodeType?: string
  name: string
  description?: string
  param: Record<string, unknown>
}

/** 将控制节点参数中的前端草稿 ID 换成 OS 已创建的真实节点 UUID。 */
function resolveControlParam(param: Record<string, unknown>, nodeUuids: Map<string, string>): Record<string, unknown> {
  const resolveRef = (value: unknown): unknown => typeof value === 'string' ? nodeUuids.get(value) || value : value
  const resolveList = (value: unknown): unknown => Array.isArray(value) ? value.map(resolveRef) : value
  const clone = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(clone)
    if (!value || typeof value !== 'object') return resolveRef(value)
    const record = value as RawRecord
    const next: RawRecord = {}
    Object.entries(record).forEach(([key, child]) => {
      if (key === 'node_uuid') next[key] = resolveRef(child)
      else if (['node_uuids', 'entry_node_uuids', 'exit_node_uuids', 'predecessor_node_uuids', 'successor_node_uuids'].includes(key)) next[key] = resolveList(child)
      else next[key] = clone(child)
    })
    return next
  }
  return clone(param) as Record<string, unknown>
}

/** 从控制区域提取成员关系，用于给动作节点写入 parent_uuid。 */
function controlMembership(param: Record<string, unknown>): string[] {
  const refs: string[] = []
  const append = (value: unknown) => { if (Array.isArray(value)) value.forEach((item) => { if (typeof item === 'string' && item) refs.push(item) }) }
  append(param.node_uuids)
  const branches = Array.isArray(param.branches) ? param.branches : []
  branches.forEach((branch) => { if (branch && typeof branch === 'object') append((branch as RawRecord).node_uuids) })
  return [...new Set(refs)]
}

/** 从控制区域提取各自的顺序组；条件节点的不同分支不能互相连 ready 边。 */
function controlReadyGroups(param: Record<string, unknown>): string[][] {
  const groups: string[][] = []
  const append = (value: unknown) => {
    if (Array.isArray(value)) {
      const refs = value.filter((item): item is string => typeof item === 'string' && item.length > 0)
      if (refs.length > 1) groups.push([...new Set(refs)])
    }
  }
  const branches = Array.isArray(param.branches) ? param.branches : []
  if (branches.length > 0) {
    branches.forEach((branch) => {
      if (branch && typeof branch === 'object' && !Array.isArray(branch)) {
        append((branch as RawRecord).node_uuids)
      }
    })
  } else {
    append(param.node_uuids)
  }
  return groups
}

/** 只在同一控制区域内建立 ready 顺序边；控制节点本身由 Scheduler 生成依赖边。 */
async function createOperationReadyEdges(workflowUuid: string, groups: string[][], nodeByUuid: Map<string, RawRecord>) {
  const existingGraph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`)
  const existingPairs = new Set((Array.isArray(existingGraph.edges) ? existingGraph.edges : []).map((edge: RawRecord) => `${edge.source_node_uuid}:${edge.target_node_uuid}`))
  const detailCache = new Map<string, RawRecord>()
  const handlesFor = async (nodeUuid: string) => {
    const node = nodeByUuid.get(nodeUuid)
    if (!node || String(node.type || '').toLowerCase() === 'condition' || String(node.type || '').toLowerCase() === 'repeat_until') return undefined
    const templateUuid = String(node.workflow_node_template_uuid || '')
    if (!templateUuid) return undefined
    let detail = detailCache.get(templateUuid)
    if (!detail) { detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(templateUuid)}`); detailCache.set(templateUuid, detail) }
    const handles = Array.isArray(detail.handles) ? detail.handles : []
    return {
      source: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'source'),
      target: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'target'),
    }
  }
  for (const group of groups) {
    for (let index = 1; index < group.length; index += 1) {
      const sourceUuid = String(group[index - 1] || '')
      const targetUuid = String(group[index] || '')
      if (!sourceUuid || !targetUuid || sourceUuid === targetUuid || existingPairs.has(`${sourceUuid}:${targetUuid}`)) continue
      const sourceHandles = await handlesFor(sourceUuid)
      const targetHandles = await handlesFor(targetUuid)
      // 一个区域可以只包含控制节点；这种关系由 Scheduler 的参数生成，不能
      // 强行套用设备 Action 的 ready 句柄。普通 Action 缺少 ready 句柄则是
      // 模板合同错误，必须阻止保存，避免生成无法调度的实验操作。
      const sourceIsControl = ['condition', 'repeat_until'].includes(String(nodeByUuid.get(sourceUuid)?.type || '').toLowerCase())
      const targetIsControl = ['condition', 'repeat_until'].includes(String(nodeByUuid.get(targetUuid)?.type || '').toLowerCase())
      if (sourceIsControl || targetIsControl) continue
      if (!sourceHandles?.source?.uuid || !targetHandles?.target?.uuid) throw new Error('动作模板缺少 ready 控制句柄，无法建立顺序依赖')
      await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/edges`, {
        source_node_uuid: sourceUuid, target_node_uuid: targetUuid,
        source_handle_uuid: sourceHandles.source.uuid, target_handle_uuid: targetHandles.target.uuid,
        description: '实验操作区域内顺序依赖', meta_data: { unilab: { generated_by: 'operation-builder' } },
      })
      existingPairs.add(`${sourceUuid}:${targetUuid}`)
    }
  }
}

/**
 * 生成首次建图时使用的临时控制参数。
 *
 * 工作流的输入合同要在“有节点的图”落地后再写入：OS 会拒绝一个空图引用
 * 尚未存在的工作流参数。因此首次建图只保留拓扑，把依赖工作流输入的条件
 * 临时替换为字面量；随后写入合同，再用最终参数重编译一次完整图。
 */
function stagedControlParam(param: Record<string, unknown>, nodeType: string): Record<string, unknown> {
  const cloneBindingMap = (value: unknown, fallback: unknown): Record<string, unknown> => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
    return Object.fromEntries(Object.entries(value as RawRecord).map(([key, binding]) => {
      if (binding && typeof binding === 'object' && !Array.isArray(binding) && (binding as RawRecord).kind === 'workflow_input') {
        return [key, { kind: 'literal', value: fallback }]
      }
      return [key, binding]
    }))
  }
  if (nodeType === 'condition') {
    const branches = Array.isArray(param.branches)
      ? param.branches.map((branch, index) => {
        if (!branch || typeof branch !== 'object' || Array.isArray(branch)) return branch
        const next = { ...(branch as RawRecord) }
        // 保留最后一个兜底分支；其它分支暂用 true，只验证区域拓扑。
        if (index < (param.branches as unknown[]).length - 1) next.condition = { lit: true }
        else next.condition = null
        return next
      })
      : []
    return { ...param, bindings: {}, branches }
  }
  if (nodeType === 'repeat_until') {
    return {
      ...param,
      until: { lit: true },
      bindings: {},
      initial_carry: cloneBindingMap(param.initial_carry, false),
      next_carry: cloneBindingMap(param.next_carry, true),
    }
  }
  return { ...param }
}

export async function createExperimentOperation(payload: { name: string; description: string; categoryUuid?: string; inputContract?: Record<string, unknown>; outputContract?: Record<string, unknown>; actions: OperationActionInput[]; controls?: OperationControlInput[] }) {
  let workflowUuid = ''
  try {
    const workflow = await writeData<RawRecord>('POST', '/workflows', {
      name: payload.name, description: payload.description, tags: ['experiment-operation'], workflow_type: 'experiment_operation',
      operation_category_uuid: payload.categoryUuid || undefined,
      // 领域包模式先创建空合同；输入/输出合同会在首个有节点图落地后写入，
      // 避免 OS 在空图阶段无法编译含必填参数的工作流。
      meta_data: {},
    })
    workflowUuid = String(workflow.uuid || '')
    if (!workflowUuid) throw new Error('OS 创建实验操作后未返回工作流 UUID')
    const draftToUuid = new Map<string, string>()
    const actionNodes: Array<{ uuid: string; draftId?: string; readySource?: string; readyTarget?: string; action: OperationActionInput; template: RawRecord; handles: RawRecord[] }> = []
    const controlInputs = payload.controls || []
    // 全图一次提交，避免“先建控制节点但成员尚未挂 parent_uuid”导致 OS
    // 在中间状态拒绝图。草稿 ID 只在前端存在，PUT graph 前全部换成 UUID。
    for (let index = 0; index < payload.actions.length; index += 1) {
      const action = payload.actions[index]
      const detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(action.templateUuid)}`)
      const handles = Array.isArray(detail.handles) ? detail.handles : []
      const template = detail.template && typeof detail.template === 'object'
        ? detail.template as RawRecord
        : detail
      const actionUuid = crypto.randomUUID()
      if (action.draftId) draftToUuid.set(action.draftId, actionUuid)
      actionNodes.push({
        uuid: actionUuid,
        draftId: action.draftId,
        action,
        template,
        handles,
        readySource: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'source')?.uuid,
        readyTarget: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'target')?.uuid,
      })
    }
    controlInputs.forEach((control) => { if (control.draftId) draftToUuid.set(control.draftId, crypto.randomUUID()) })
    const parentByDraft = new Map<string, string>()
    controlInputs.forEach((control) => {
      const controlDraftId = control.draftId
      if (!controlDraftId) return
      controlMembership(control.param || {}).forEach((member) => {
        const previous = parentByDraft.get(member)
        if (previous && previous !== controlDraftId) throw new Error(`节点“${member}”不能同时属于两个控制区域`)
        parentByDraft.set(member, controlDraftId)
      })
    })
    const actionGraphNodes = actionNodes.map((item, index) => {
      const action = item.action
      const parentUuid = action.draftId ? draftToUuid.get(parentByDraft.get(action.draftId) || '') : undefined
      return {
        uuid: item.uuid, workflow_node_template_uuid: action.templateUuid,
        parent_uuid: parentUuid || null, material_uuid: action.materialUuid || null,
        name: action.name, type: String(item.template.node_type || item.template.nodeType || item.template.type || 'compute'),
        icon: item.template.icon || null, pose: { x: 140 + index * 250, y: 170 },
        param: action.param || {}, footer: item.template.footer || null,
        action_name: String(item.template.name || action.name),
        action_type: String(item.template.type || 'UniLabJsonCommand'), execution_policy: {}, disabled: false,
        minimized: false, script: null, description: action.description || action.name,
        meta_data: { unilab: { sequence_index: index, authoring_source_order: index, input_bindings: action.inputBindings || {}, executor_binding: { mode: 'fixed', device_id: action.materialUuid || action.deviceId } } },
      }
    })
    const controlGraphNodes = controlInputs.map((control, index) => {
      const controlUuid = (control.draftId ? draftToUuid.get(control.draftId) : undefined) || crypto.randomUUID()
      const parentUuid = control.draftId ? draftToUuid.get(parentByDraft.get(control.draftId) || '') : undefined
      return {
        uuid: controlUuid, workflow_node_template_uuid: control.templateUuid,
        parent_uuid: parentUuid || null, material_uuid: null, name: control.name,
        type: control.nodeType || 'control', icon: null, pose: { x: 220 + index * 280, y: 430 },
        param: resolveControlParam(control.param || {}, draftToUuid), footer: null,
        action_name: control.nodeType || 'control', action_type: 'UniLabControl',
        execution_policy: {}, disabled: false, minimized: false, script: null,
        description: control.description || control.name,
        meta_data: { unilab: { executor_kind: control.nodeType || 'control', authoring_source_order: payload.actions.length + index, generated_by: 'operation-builder' } },
      }
    })
    let rawGraph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`)
    const graphNodes = [...actionGraphNodes, ...controlGraphNodes]
    const inputContract = payload.inputContract || { version: 1, parameters: [] }
    const outputContract = payload.outputContract || { version: 1, outputs: [] }
    // 输出合同中的字段名直接对应动作模板的 source handle data_key。这样
    // 前端只需填写“要对外展示的输出名”，服务端即可得到稳定的节点输出绑定；
    // 找不到同名输出时提前给出明确错误，避免 OS 在源码生成阶段返回模糊的
    // candidate_invalid/code=3003。
    const outputBindings: RawRecord = {}
    const declaredOutputs = Array.isArray((outputContract as RawRecord).outputs)
      ? ((outputContract as RawRecord).outputs as unknown[])
      : []
    for (const output of declaredOutputs) {
      if (!output || typeof output !== 'object' || Boolean((output as RawRecord).implicit)) continue
      const outputName = String((output as RawRecord).name || '').trim()
      if (!outputName) continue
      const match = actionNodes
        .flatMap((node) => node.handles.map((handle) => ({ node, handle })))
        .find(({ handle }) => handle.io_type === 'source' && handle.handle_key !== 'ready' && String(handle.data_key || '') === outputName)
      if (!match?.handle.uuid) throw new Error(`输出参数“${outputName}”没有对应的节点输出，请使用 Action 的输出名称`)
      outputBindings[outputName] = {
        kind: 'node_output',
        workflow_node_uuid: match.node.uuid,
        source_handle_uuid: match.handle.uuid,
      }
    }
    const hasContracts = (Array.isArray((inputContract as RawRecord).parameters) && ((inputContract as RawRecord).parameters as unknown[]).length > 0)
      || (Array.isArray((outputContract as RawRecord).outputs) && ((outputContract as RawRecord).outputs as unknown[]).length > 0)
      || actionGraphNodes.some((node) => Object.keys((node.meta_data?.unilab as RawRecord)?.input_bindings as RawRecord || {}).length > 0)
      || controlGraphNodes.some((node) => JSON.stringify(node.param).includes('workflow_input'))
    if (hasContracts) {
      // 先写只有拓扑的临时图，再写合同，最后提交带真实参数绑定的图。
      const stagedNodes = graphNodes.map((node) => {
        const unilab = (node.meta_data?.unilab || {}) as RawRecord
        const inputBindings = !['condition', 'repeat_until'].includes(String(node.type || '').toLowerCase())
          ? unilab.input_bindings
          : undefined
        const stagedParam = ['condition', 'repeat_until'].includes(String(node.type || '').toLowerCase())
          ? stagedControlParam(node.param || {}, String(node.type || '').toLowerCase())
          : node.param
        return {
          ...node,
          param: stagedParam,
          meta_data: { ...(node.meta_data || {}), unilab: { ...unilab, ...(inputBindings ? { input_bindings: {} } : {}) } },
        }
      })
      await writeData<RawRecord>('PUT', `/workflows/${encodeURIComponent(workflowUuid)}/graph`, {
        revision: Number(rawGraph.workflow?.revision), nodes: stagedNodes, edges: [],
      })
      await writeData<RawRecord>('PUT', `/workflows/${encodeURIComponent(workflowUuid)}`, {
        name: payload.name,
        description: payload.description,
        tags: ['experiment-operation'],
        workflow_type: 'experiment_operation',
        operation_category_uuid: payload.categoryUuid || null,
        meta_data: { unilab: { input_contract: inputContract, output_contract: outputContract, output_bindings: outputBindings } },
      })
      rawGraph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`)
    }
    await writeData<RawRecord>('PUT', `/workflows/${encodeURIComponent(workflowUuid)}/graph`, {
      revision: Number(rawGraph.workflow?.revision), nodes: graphNodes, edges: [],
    })
    const nodeByUuid = new Map(graphNodes.map((node: RawRecord) => [String(node.uuid), node]))
    const memberRefs = new Set<string>()
    const controlGroups = controlInputs.map((control) => {
      const refs = controlMembership(control.param || {}).map((ref) => draftToUuid.get(ref) || ref)
      refs.forEach((ref) => memberRefs.add(ref))
      return refs
    })
    const topLevelActions = actionNodes.filter((node) => !memberRefs.has(node.uuid)).map((node) => node.uuid)
    // 控制节点的前后关系写在其 param（predecessor/successor）中；只在没有
    // 控制区域时建立整条线性 Action ready 链，避免把分支成员错误串成一条线。
    // 控制节点下的每个条件分支是独立路径，只能在同一分支内建立顺序边；
    // 把所有分支拍平成一个组会错误地连接“if”末尾与“else”开头。
    const branchReadyGroups = controlInputs.flatMap((control) => controlReadyGroups(control.param || {}))
    const readyGroups = controlInputs.length ? branchReadyGroups : [topLevelActions]
    await createOperationReadyEdges(workflowUuid, readyGroups, nodeByUuid)
    const graph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`)
    const revision = Number(graph.workflow?.revision)
    if (!Number.isFinite(revision)) throw new Error('子工作流已写入，但无法读取当前修订，未执行发布')
    return { workflowUuid, revision, status: 'source' as const }
  } catch (error) {
    // 创建是多步 API；任何节点/连线/编译失败都撤销已创建的领域定义，避免
    // 返回列表出现没有节点的空实验操作。删除失败不覆盖原始错误，便于重试诊断。
    if (workflowUuid) {
      try { await deleteExperimentOperation(workflowUuid) } catch { /* 保留原始失败原因 */ }
    }
    throw error
  }
}

function materialSitesFromGraph(graph: RawRecord): Map<string, MaterialRecord['sites']> {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes : []
  const occupantBySite = new Map<string, { uuid: string; name: string }>()
  nodes.forEach((node: RawRecord) => {
    if (!node.current_site_uuid || !node.material?.uuid) return
    occupantBySite.set(String(node.current_site_uuid), {
      uuid: String(node.material.uuid),
      name: String(node.material.name || node.material.uuid),
    })
  })
  const sitesByOwner = new Map<string, MaterialRecord['sites']>()
  nodes.forEach((node: RawRecord) => {
    if (!node.material?.uuid || !Array.isArray(node.sites)) return
    sitesByOwner.set(String(node.material.uuid), node.sites.map((site: RawRecord, index: number) => {
      const uuid = String(site.uuid || `${node.material.uuid}-site-${index}`)
      const occupant = occupantBySite.get(uuid)
      return {
        uuid,
        name: String(site.name || site.id || `SITE-${index + 1}`),
        occupiedMaterialUuid: occupant?.uuid,
        occupiedMaterialName: occupant?.name,
        allowedResourceTemplateUuids: Array.isArray(site.allowed_resource_template_uuids)
          ? site.allowed_resource_template_uuids.map(String)
          : [],
      }
    }))
  })
  return sitesByOwner
}

function materialLocationsFromGraph(graph: RawRecord): Map<string, MaterialCurrentLocation> {
  const graphNodes = Array.isArray(graph.nodes) ? graph.nodes : []
  const siteByUuid = new Map<string, { site: RawRecord; owner: RawRecord }>()
  graphNodes.forEach((node: RawRecord) => {
    const owner = node.material || {}
    if (!Array.isArray(node.sites)) return
    node.sites.forEach((site: RawRecord) => {
      siteByUuid.set(String(site.uuid), { site, owner })
    })
  })
  const locations = new Map<string, MaterialCurrentLocation>()
  graphNodes.forEach((node: RawRecord) => {
    const materialUuid = String(node.material?.uuid)
    if (!Object.prototype.hasOwnProperty.call(node, 'current_site_uuid')) {
      locations.set(materialUuid, {
        kind: 'unresolved',
        label: '物料图缺少当前位置字段',
      })
      return
    }
    const currentSiteUuid = node.current_site_uuid
    if (!currentSiteUuid) {
      const structure = materialStructure(node.material || {})
      if (structure.isStructural) {
        locations.set(materialUuid, { kind: 'structural', label: `结构资源 · 提供 ${structure.siteCount} 个库位`, siteCount: structure.siteCount })
        return
      }
      locations.set(materialUuid, { kind: 'unassigned', label: '未分配权威库位' })
      return
    }
    const entry = siteByUuid.get(String(currentSiteUuid))
    if (!entry) {
      locations.set(materialUuid, {
        kind: 'unresolved',
        label: '库位关系未解析',
        siteUuid: String(currentSiteUuid),
      })
      return
    }
    locations.set(materialUuid, {
      kind: 'site',
      label: `${String(entry.owner.name || entry.owner.uuid)} / ${String(entry.site.name || entry.site.uuid)}`,
      siteUuid: String(entry.site.uuid),
      ownerMaterialUuid: String(entry.owner.uuid),
    })
  })
  return locations
}

function waitResourceLabelsFromCatalog(
  materialRows: RawRecord[],
  materialGraph: RawRecord,
  deviceRows: RawRecord[],
): WaitResourceLabels {
  const labels = emptyWaitResourceLabels(deviceRows)
  const { devices, materials, sites } = labels
  const remember = (target: Record<string, string>, identity: unknown, label: unknown) => {
    const normalizedIdentity = String(identity || '').trim()
    const normalizedLabel = String(label || '').trim()
    if (normalizedIdentity && normalizedLabel) target[normalizedIdentity] = normalizedLabel
  }

  materialRows.forEach((material) => {
    remember(materials, material.uuid, material.name || material.uuid)
    if (String(material.type || '') === 'device') {
      remember(devices, material.uuid, material.name || material.uuid)
    }
  })

  const graphNodes = Array.isArray(materialGraph.nodes) ? materialGraph.nodes : []
  graphNodes.forEach((node: RawRecord) => {
    const owner = node.material || {}
    const ownerName = String(owner.name || owner.uuid || '').trim()
    remember(materials, owner.uuid, ownerName)
    if (!Array.isArray(node.sites)) return
    node.sites.forEach((site: RawRecord) => {
      const siteName = String(site.name || site.uuid || '').trim()
      const qualifiedName = ownerName && siteName && ownerName !== siteName
        ? `${ownerName} / ${siteName}`
        : siteName || ownerName
      remember(sites, site.uuid, qualifiedName)
    })
  })

  return labels
}

function emptyWaitResourceLabels(deviceRows: RawRecord[]): MutableWaitResourceLabels {
  const labels: MutableWaitResourceLabels = {
    devices: Object.create(null),
    materials: Object.create(null),
    sites: Object.create(null),
  }
  deviceRows.forEach((device) => {
    const binding = device.binding || {}
    const material = device.material || {}
    const label = String(binding.name || material.name || binding.local_id || binding.material_uuid || '').trim()
    for (const identity of [binding.local_id, binding.material_uuid, material.uuid]) {
      const normalizedIdentity = String(identity || '').trim()
      if (normalizedIdentity && label) labels.devices[normalizedIdentity] = label
    }
  })
  return labels
}

async function taskWithJobs(
  raw: RawRecord,
  workflowsByUuid: Map<string, WorkflowDefinition>,
  knownMaterialUuids: ReadonlySet<string>,
  traceUiUrl: string,
  waitResourceLabels: WaitResourceLabels,
) {
  if (!Array.isArray(raw.jobs)) {
    throw new Error('Edge 任务展示投影缺少 jobs')
  }
  const jobs = raw.jobs as RawRecord[]
  const workflow = workflowsByUuid.get(String(raw.workflow_uuid))
  const frozenInputContract = adaptContractFields(
    raw.workflow_snapshot?.workflow?.meta_data?.unilab?.input_contract?.parameters,
    'parameters',
  )
  return adaptTask(
    raw,
    jobs,
    workflow?.name || '未命名工作流',
    frozenInputContract.length ? frozenInputContract : workflow?.inputContract || [],
    knownMaterialUuids,
    traceUiUrl,
    waitResourceLabels,
  )
}

let taskPresentationRowsInFlight: Promise<RawRecord[]> | undefined
const taskPresentationRequestTimeoutMs = 15_000

async function loadTaskPresentationRows(_signal?: AbortSignal): Promise<RawRecord[]> {
  // 矩阵查询由完整快照、定时刷新和 SSE 共同使用。共享内部请求不绑定任一调用
  // 方的取消信号，但设置硬超时，避免半开连接永久占住后续刷新。
  if (taskPresentationRowsInFlight) return taskPresentationRowsInFlight
  const controller = new AbortController()
  const timeout = window.setTimeout(
    () => controller.abort(),
    taskPresentationRequestTimeoutMs,
  )
  const request = requestData<PageData<RawRecord>>(
    '/workflow-task-presentations?view=matrix&terminal_limit=20',
    controller.signal,
  ).then((page) => page.items || [])
  taskPresentationRowsInFlight = request
  try {
    return await request
  } finally {
    window.clearTimeout(timeout)
    if (taskPresentationRowsInFlight === request) taskPresentationRowsInFlight = undefined
  }
}

function waitResourceLabelsFromView(
  materials: MaterialRecord[],
  deviceRows: RawRecord[],
): WaitResourceLabels {
  const labels = emptyWaitResourceLabels(deviceRows)
  materials.forEach((material) => {
    labels.materials[material.uuid] = material.name
    material.sites.forEach((site) => {
      labels.sites[site.uuid] = `${material.name} / ${site.name}`
    })
  })
  return labels
}

export async function loadEdgeTasks(
  workflows: WorkflowDefinition[],
  materials: MaterialRecord[],
  signal?: AbortSignal,
): Promise<WorkflowTask[]> {
  const [readiness, taskRows, deviceRows] = await Promise.all([
    requestJson<RawRecord>('/readiness', signal),
    loadTaskPresentationRows(signal),
    requestData<RawRecord[]>('/devices', signal).catch(() => []),
  ])
  if (readiness.status !== 'ready') throw new Error('Edge 工作流运行时尚未就绪')
  const traceUiUrl = typeof readiness.observability?.traceUiUrl === 'string'
    ? readiness.observability.traceUiUrl
    : ''
  const workflowsByUuid = new Map(workflows.map((workflow) => [workflow.uuid, workflow]))
  const knownMaterialUuids = new Set(materials.map((material) => material.uuid))
  const waitResourceLabels = waitResourceLabelsFromView(materials, deviceRows)
  return mapWithConcurrency(taskRows, 6, (task) => taskWithJobs(
    task,
    workflowsByUuid,
    knownMaterialUuids,
    traceUiUrl,
    waitResourceLabels,
  ))
}

export async function loadWorkflowTaskDetail(
  taskUuid: string,
  materials: MaterialRecord[],
  signal?: AbortSignal,
): Promise<WorkflowTask> {
  const [task, jobs] = await Promise.all([
    requestData<RawRecord>(`/workflow-tasks/${encodeURIComponent(taskUuid)}`, signal),
    requestData<RawRecord[]>(`/workflow-tasks/${encodeURIComponent(taskUuid)}/jobs`, signal),
  ])
  const frozenWorkflow = task.workflow_snapshot?.workflow
  const inputContract = adaptContractFields(
    frozenWorkflow?.meta_data?.unilab?.input_contract?.parameters,
    'parameters',
  )
  const labels = emptyWaitResourceLabels([])
  materials.forEach((material) => {
    labels.materials[material.uuid] = material.name
    material.sites.forEach((site) => {
      labels.sites[site.uuid] = `${material.name} / ${site.name}`
    })
    if (material.sourceNodeId) labels.devices[material.sourceNodeId] = material.name
    labels.devices[material.uuid] = material.name
  })
  return adaptTask(
    task,
    jobs,
    String(frozenWorkflow?.name || '未命名工作流'),
    inputContract,
    new Set(materials.map((material) => material.uuid)),
    '',
    labels,
  )
}

export function materialsWithTaskReferences(
  materials: MaterialRecord[],
  tasks: WorkflowTask[],
): MaterialRecord[] {
  const materialReferences = new Map<string, MaterialRecord['taskReferences']>()
  tasks.filter((task) => !terminalTaskStatuses.has(task.status)).forEach((task) => {
    task.materialUuids.forEach((materialUuid) => {
      const references = materialReferences.get(materialUuid) || []
      if (!references.some((reference) => reference.taskUuid === task.uuid)) {
        references.push({
          taskUuid: task.uuid,
          taskStatus: task.status,
          workflowName: task.workflowName,
          sample: task.sample,
        })
      }
      materialReferences.set(materialUuid, references)
    })
  })
  return materials.map((material) => ({
    ...material,
    taskReferences: materialReferences.get(material.uuid) || [],
  }))
}

export async function loadEdgeSnapshot(signal?: AbortSignal): Promise<EdgeSnapshot> {
  const [readiness, workflowPage, taskRows, materialPage, materialGraph, deviceRows] = await Promise.all([
    requestJson<RawRecord>('/readiness', signal),
    requestAllPages<RawRecord>('/workflows', signal),
    loadTaskPresentationRows(signal),
    requestAllPages<RawRecord>('/materials', signal),
    requestData<RawRecord>('/materials/graph', signal),
    requestData<RawRecord[]>('/devices', signal).catch(() => []),
  ])
  if (readiness.status !== 'ready') throw new Error('Edge 工作流运行时尚未就绪')
  const traceUiUrl = typeof readiness.observability?.traceUiUrl === 'string'
    ? readiness.observability.traceUiUrl
    : ''

  const workflows = (workflowPage.items || []).map(adaptWorkflow)
  const workflowsByUuid = new Map(workflows.map((workflow) => [workflow.uuid, workflow]))
  const knownMaterialUuids = new Set(
    (materialPage.items || []).map((material) => String(material.uuid)),
  )
  const waitResourceLabels = waitResourceLabelsFromCatalog(
    materialPage.items || [],
    materialGraph,
    deviceRows,
  )
  const tasks = await mapWithConcurrency(
    taskRows,
    6,
    (task) => taskWithJobs(
      task,
      workflowsByUuid,
      knownMaterialUuids,
      traceUiUrl,
      waitResourceLabels,
    ),
  )
  const materialLocations = materialLocationsFromGraph(materialGraph)
  const materialSites = materialSitesFromGraph(materialGraph)
  const graphMaterials = new Map<string, RawRecord>()
  const graphNodes = Array.isArray(materialGraph.nodes) ? materialGraph.nodes : []
  graphNodes.forEach((node: RawRecord) => {
    if (node.material?.uuid) {
      graphMaterials.set(String(node.material.uuid), {
        ...node.material,
        relative_position: node.relative_position ?? node.material.relative_position,
      })
    }
  })
  const materials = (materialPage.items || []).map((raw) => {
    // The paginated material projection can omit geometry. The graph projection is
    // the authoritative source for the hierarchy and 2.5D relative transforms.
    const graphMaterial = graphMaterials.get(String(raw.uuid))
    const geometryRaw = graphMaterial
      ? {
          ...graphMaterial,
          ...raw,
          parent_uuid: raw.parent_uuid ?? graphMaterial.parent_uuid,
          relative_position: raw.relative_position ?? graphMaterial.relative_position,
        }
      : raw
    const material = adaptMaterial(
      geometryRaw,
      materialLocations.get(String(raw.uuid)) || {
        kind: 'unresolved',
        label: '物料图缺少位置投影',
      },
    )
    const enriched = { ...material, sites: materialSites.get(material.uuid) || material.sites }
    return enriched
  })

  const materialsWithReferences = materialsWithTaskReferences(materials, tasks)

  return {
    startupMode: readiness.startupMode === 'develop' ? 'develop' : 'product',
    workflows,
    tasks,
    materials: materialsWithReferences,
    materialTotal: Number(materialPage.total ?? materials.length),
    workflowLoaded: Number(readiness.workflowProgress?.loaded ?? workflows.length),
    workflowTotal: Number(readiness.workflowProgress?.total ?? workflows.length),
  }
}

export async function loadMaterialDetail(materialUuid: string, signal?: AbortSignal) {
  const material = await requestData<RawRecord>(`/materials/${encodeURIComponent(materialUuid)}`, signal)
  return adaptMaterial(material)
}

export async function switchStartupMode(
  mode: StartupMode,
  expectedMode: StartupMode,
): Promise<StartupModeSwitchResult> {
  const result = await writeData<RawRecord>('PUT', '/startup-mode', {
    mode,
    expected_mode: expectedMode,
  })
  return {
    previousMode: result.previous_mode === 'develop' ? 'develop' : 'product',
    mode: result.mode === 'develop' ? 'develop' : 'product',
    changed: Boolean(result.changed),
    scope: 'runtime_session',
    requiresRestart: Boolean(result.requires_restart),
  }
}

export async function loadWorkflowGraph(workflowUuid: string, signal?: AbortSignal): Promise<WorkflowGraph> {
  const graph = await requestData<RawRecord>(`/workflows/${encodeURIComponent(workflowUuid)}/graph`, signal)
  const nodes = Array.isArray(graph.nodes) ? graph.nodes.map(adaptWorkflowGraphNode) : []
  return {
    workflow: adaptWorkflow({ ...graph.workflow, nodes: graph.nodes }),
    nodes,
    edges: Array.isArray(graph.edges) ? graph.edges.map(adaptWorkflowGraphEdge) : [],
    nodeTemplates: Array.isArray(graph.node_templates) ? graph.node_templates : [],
    handleTemplates: Array.isArray(graph.handle_templates) ? graph.handle_templates : [],
  }
}

function adaptWorkflowAuthoringState(value: unknown): WorkflowAuthoringState {
  const state = String(value || '')
  switch (state) {
    case 'applied':
    case 'applied_source_stale':
    case 'candidate_stale':
    case 'draft_invalid':
    case 'draft_missing':
    case 'unapplied_graph':
    case 'unapplied_source_only':
      return state
    default:
      return 'unknown'
  }
}

export async function loadWorkflowSource(workflowUuid: string, signal?: AbortSignal): Promise<WorkflowSource> {
  const authoring = await requestData<RawRecord>(
    `/workflows/${encodeURIComponent(workflowUuid)}/authoring`,
    signal,
  )
  const draft = authoring.draft
  if (!draft || typeof draft !== 'object' || typeof draft.python_source !== 'string') {
    throw new Error('该工作流没有可读取的 Python 源码')
  }
  return {
    workflowRevision: Number(authoring.workflow_revision || 1),
    state: adaptWorkflowAuthoringState(authoring.state),
    sourceUri: String(draft.source_uri || ''),
    pythonSource: draft.python_source,
  }
}

export async function importWorkflowJson(file: File, workflowType?: 'normal' | 'experiment_operation'): Promise<WorkflowDefinition> {
  let payload: unknown
  try {
    payload = JSON.parse(await file.text())
  } catch {
    throw new Error('JSON 文件格式无效')
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('JSON 工作流必须是对象')
  }
  if (workflowType) (payload as RawRecord).workflow_type = workflowType
  const imported = await postData<RawRecord>('/workflows/import', payload)
  return adaptWorkflow({ ...(imported.workflow || {}), nodes: imported.nodes || [] })
}

export async function importWorkflowPython(file: File): Promise<WorkflowDefinition> {
  const response = await fetch(`${EDGE_API_BASE}/local/workflows/import-python`, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'text/x-python',
      'X-Workflow-Filename': file.name,
    },
    body: await file.arrayBuffer(),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(body?.error?.msg || body?.detail || `Python 导入失败（${response.status}）`)
  }
  const imported = unwrapEnvelope(body as EdgeEnvelope<RawRecord>)
  return adaptWorkflow({ ...(imported.workflow || {}), nodes: imported.nodes || [] })
}

export async function loadWorkflowTaskGraph(taskUuid: string, signal?: AbortSignal): Promise<WorkflowGraph> {
  const task = await requestData<RawRecord>(`/workflow-tasks/${encodeURIComponent(taskUuid)}`, signal)
  const snapshot = task.workflow_snapshot
  if (!snapshot?.workflow || !Array.isArray(snapshot.nodes) || !Array.isArray(snapshot.edges)) {
    throw new Error('Task 没有可用的冻结工作流快照')
  }
  return {
    workflow: adaptWorkflow({ ...snapshot.workflow, nodes: snapshot.nodes }),
    nodes: snapshot.nodes.map(adaptWorkflowGraphNode),
    edges: snapshot.edges.map(adaptWorkflowGraphEdge),
  }
}

export async function loadWorkflowPreflight(
  workflowUuid: string,
  input: Record<string, unknown>,
  signal?: AbortSignal,
  runMode: 'normal' | 'step' = 'normal',
): Promise<RunPreflightReport> {
  const report = await postData<RawRecord>(
    `/workflows/${encodeURIComponent(workflowUuid)}/run-preflight`,
    { run_mode: runMode, input },
    signal,
  )
  const summary = report.summary || {}
  return {
    workflowUuid: String(report.workflow_uuid),
    workflowRevision: Number(report.workflow_revision),
    runMode: String(report.run_mode),
    status: report.status,
    canRun: Boolean(report.can_run),
    checkedAt: String(report.checked_at),
    summary: {
      executionNodeCount: Number(summary.execution_node_count || 0),
      passedCheckCount: Number(summary.passed_check_count || 0),
      blockingCheckCount: Number(summary.blocking_check_count || 0),
      deferredCheckCount: Number(summary.deferred_check_count || 0),
      confirmationRequiredCount: Number(summary.confirmation_required_count || 0),
    },
    checks: Array.isArray(report.checks)
      ? report.checks.map((check: RawRecord) => ({
          type: String(check.type),
          status: check.status,
          code: String(check.code),
          message: String(check.message),
          blocking: Boolean(check.blocking),
          nodeUuid: check.node_uuid ? String(check.node_uuid) : undefined,
          nodeName: check.node_name ? String(check.node_name) : undefined,
        }))
      : [],
  }
}

export async function createWorkflowTask({
  workflowUuid,
  input,
  description,
  runMode = 'normal',
  priority = 'normal',
}: {
  workflowUuid: string
  input: Record<string, unknown>
  description: string
  runMode?: 'normal' | 'step'
  priority?: WorkflowTaskPriority
}) {
  return postData<RawRecord>('/workflow-tasks', {
    workflow_uuid: workflowUuid,
    run_mode: runMode,
    priority,
    input,
    description,
    meta_data: { source: 'unilabos-frontend' },
  })
}

export async function loadWorkflowTaskStepState(
  taskUuid: string,
  signal?: AbortSignal,
): Promise<WorkflowStepState> {
  const state = await requestData<RawRecord>(
    `/workflow-tasks/${encodeURIComponent(taskUuid)}/step-state`,
    signal,
  )
  return {
    workflowTaskUuid: String(state.workflow_task_uuid),
    executionMode: String(state.execution_mode || 'normal') as WorkflowStepState['executionMode'],
    controlStatus: String(state.control_status || 'active'),
    inFlightJobCount: Number(state.in_flight_job_count || 0),
    requiresSelection: Boolean(state.requires_selection),
    canStep: Boolean(state.can_step),
    candidates: Array.isArray(state.candidates) ? state.candidates.map((candidate: RawRecord) => ({
      nodeUuid: String(candidate.node_uuid),
      name: String(candidate.name || candidate.node_uuid),
      kind: String(candidate.kind || 'device_action'),
      deviceId: candidate.device_id ? String(candidate.device_id) : undefined,
      actionName: candidate.action_name ? String(candidate.action_name) : undefined,
    })) : [],
  }
}

export async function commandWorkflowTask(
  taskUuid: string,
  type: 'step' | 'pause' | 'resume' | 'cancel',
  targetNodeUuid?: string,
): Promise<RawRecord> {
  const generatedKey = globalThis.crypto?.randomUUID?.()
    || `${type}-${Date.now()}-${Math.random().toString(16).slice(2)}`
  const command = await postData<RawRecord>(
    `/workflow-tasks/${encodeURIComponent(taskUuid)}/commands`,
    {
      type,
      target_node_uuid: targetNodeUuid || null,
      idempotency_key: generatedKey,
      meta_data: { source: 'unilabos-frontend' },
    },
  )
  if (command.status === 'rejected') {
    throw new Error(String(command.result?.reason || '调度器拒绝了任务控制命令'))
  }
  return command
}

export async function decideManualConfirmation(
  jobUuid: string,
  action: 'approve' | 'reject',
): Promise<RawRecord> {
  return postData<RawRecord>(
    `/workflow-node-jobs/${encodeURIComponent(jobUuid)}/manual-confirmation`,
    { action },
  )
}
