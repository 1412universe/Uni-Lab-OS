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
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowTask,
  ResourceTemplateRecord,
  ActionTemplateRecord,
  ActionParameterRecord,
  OperationCategoryRecord,
  ReagentInfoRecord,
  ReagentRecord,
  ReagentHistoryRecord,
  CompoundLookupResult,
} from '../types'

type RawRecord = Record<string, any>

interface EdgeEnvelope<T> {
  code: number
  data?: T
  error?: {
    code?: string
    msg?: string
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

const taskStatuses = new Set<TaskPresentationStatus>([
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
    throw new Error(body?.error?.msg || `Edge API 返回业务错误：${body?.code ?? 'unknown'}`)
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
      append(resourceDetail('设备', identity, resourceLabel(labels.devices, identity)))
      return
    }
    if (scope === 'material_site' && resource.site_uuid) {
      const siteIdentity = String(resource.site_uuid)
      append(resourceDetail('库位', siteIdentity, resourceLabel(labels.sites, siteIdentity)))
      return
    }
    if (scope === 'material' && resource.material_uuid) {
      const identity = String(resource.material_uuid)
      append(resourceDetail('物料', identity, resourceLabel(labels.materials, identity)))
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
  let status: TaskPresentationStatus = normalisedStatus
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
    matrixGroupKey: matrixGroupKey(raw),
    trace: taskTraceReference(raw, traceUiUrl),
  }
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
    // 后端模板列表把标签放在 tags（同步请求里叫 category）；容器判定依赖其中的 "container"。
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : Array.isArray(raw.category) ? raw.category.map(String) : [],
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

export async function loadActionTemplates(signal?: AbortSignal): Promise<ActionTemplateRecord[]> {
  const page = await requestAllPages<RawRecord>('/workflow-node-templates', signal)
  return page.items.map((raw) => ({
    uuid: String(raw.uuid), name: String(raw.name || raw.uuid), displayName: String(raw.display_name || raw.name || raw.uuid), description: raw.description ? String(raw.description) : undefined,
    type: String(raw.type || ''), nodeType: String(raw.node_type || ''),
    resourceTemplate: { uuid: String(raw.resource_template?.uuid || ''), name: String(raw.resource_template?.name || ''), displayName: String(raw.resource_template?.display_name || raw.resource_template?.name || '') },
  }))
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
    sourceReagentUuid: typeof raw.meta_data?.source_reagent_uuid === 'string' ? raw.meta_data.source_reagent_uuid : undefined,
    dispenseCommandId: typeof raw.meta_data?.dispense_command_id === 'string' ? raw.meta_data.dispense_command_id : undefined,
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
      causationId: raw.causation_id ? String(raw.causation_id) : undefined,
      sourceReagentUuid: typeof extension.source_reagent_uuid === 'string' ? extension.source_reagent_uuid : undefined,
      targetReagentUuids: Array.isArray(extension.target_reagent_uuids) ? extension.target_reagent_uuids.map(String) : undefined,
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

export interface ReagentDispenseResult {
  source: { reagentUuid: string; materialUuid: string; quantity: number; quantityUnit: string; revision: number }
  targets: Array<{ materialUuid: string; reagentUuid: string; quantity: number; quantityUnit: string; revision: number }>
  replayed: boolean
}

/**
 * 把一瓶源试剂原子分装到若干空容器。
 * 走库存命令入口，`commandId` 由调用方生成并在重试时复用，服务端按它幂等重放。
 */
/**
 * 库存命令入口不走 `{code, data}` 信封：成功与业务拒绝都以 HTTP 200 返回裸的
 * `{command_id, status, result | error, error_code, replayed?}`。这里原样返回，
 * 由调用方按 `status` 判断，不能经过 `unwrapEnvelope`。
 */
async function postInventoryCommand(command: Record<string, unknown>): Promise<RawRecord> {
  const response = await fetch(`${EDGE_API_BASE}/inventory/commands`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(command),
  })
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(`${formatApiError(body, response.status)}（POST /inventory/commands）`)
  if (!body || typeof body !== 'object') throw new Error('库存命令返回了无法解析的响应（POST /inventory/commands）')
  return body as RawRecord
}

export async function dispenseReagent(payload: {
  commandId: string; sourceReagentUuid: string; expectedRevision?: number; quantityUnit: string;
  targets: Array<{ materialUuid: string; quantity: number }>; reason?: string
}): Promise<ReagentDispenseResult> {
  const response = await postInventoryCommand({
    command_id: payload.commandId,
    type: 'reagent.dispense',
    actor: 'frontend:os-console',
    payload: {
      source_reagent_uuid: payload.sourceReagentUuid,
      ...(payload.expectedRevision == null ? {} : { expected_revision: payload.expectedRevision }),
      quantity_unit: payload.quantityUnit,
      targets: payload.targets.map((target) => ({ material_uuid: target.materialUuid, quantity: target.quantity })),
      reason: payload.reason || '分装',
    },
  })
  if (response?.status !== 'completed') {
    throw new Error(String(response?.error || response?.error_code || '分装被拒绝'))
  }
  const result = (response.result || {}) as RawRecord
  const source = (result.source || {}) as RawRecord
  const targets = Array.isArray(result.targets) ? (result.targets as RawRecord[]) : []
  return {
    source: { reagentUuid: String(source.reagent_uuid || ''), materialUuid: String(source.material_uuid || ''), quantity: Number(source.quantity || 0), quantityUnit: String(source.quantity_unit || ''), revision: Number(source.revision || 0) },
    targets: targets.map((item) => ({ materialUuid: String(item.material_uuid || ''), reagentUuid: String(item.reagent_uuid || ''), quantity: Number(item.quantity || 0), quantityUnit: String(item.quantity_unit || ''), revision: Number(item.revision || 0) })),
    replayed: response.replayed === true,
  }
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

export async function publishExperimentOperation(workflowUuid: string, revision: number) {
  return writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/publications`, { revision })
}

export async function deleteExperimentOperation(workflowUuid: string) {
  return writeData<unknown>('DELETE', `/workflows/${encodeURIComponent(workflowUuid)}`)
}

export async function updateExperimentOperation(payload: {
  workflowUuid: string
  name: string
  description: string
  categoryUuid?: string
  actions: Array<{ nodeUuid?: string; templateUuid?: string; name: string; description?: string; materialUuid: string; deviceId: string; param: Record<string, unknown>; inputBindings: Record<string, { parameter: string }> }>
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
  const nodes = (Array.isArray(graph.nodes) ? graph.nodes : []).map((node: RawRecord) => {
    const edit = edits.get(String(node.uuid))
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

export async function createExperimentOperation(payload: { name: string; description: string; categoryUuid?: string; inputContract?: Record<string, unknown>; outputContract?: Record<string, unknown>; actions: Array<{ templateUuid: string; materialUuid?: string; deviceId: string; name: string; description?: string; param?: Record<string, unknown>; inputBindings?: Record<string, { parameter: string }> }> }) {
  let workflowUuid = ''
  try {
    const workflow = await writeData<RawRecord>('POST', '/workflows', {
      name: payload.name, description: payload.description, tags: ['experiment-operation'], workflow_type: 'experiment_operation',
      operation_category_uuid: payload.categoryUuid || undefined, meta_data: { unilab: { input_contract: payload.inputContract || {}, output_contract: payload.outputContract || {} } },
    })
    workflowUuid = String(workflow.uuid || '')
    if (!workflowUuid) throw new Error('OS 创建实验操作后未返回工作流 UUID')
    const createdNodes: Array<{ uuid: string; readySource?: string; readyTarget?: string }> = []
    for (let index = 0; index < payload.actions.length; index += 1) {
      const action = payload.actions[index]
      const detail = await requestData<RawRecord>(`/workflow-node-templates/${encodeURIComponent(action.templateUuid)}`)
      const handles = Array.isArray(detail.handles) ? detail.handles : []
      const createdNode = await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/nodes`, {
        workflow_node_template_uuid: action.templateUuid,
        material_uuid: action.materialUuid || undefined,
        name: action.name, description: action.description || action.name,
        pose: { x: 120 + index * 220, y: 180 },
        param: action.param || {}, execution_policy: {}, meta_data: { unilab: { sequence_index: index, input_bindings: action.inputBindings || {}, executor_binding: { mode: 'fixed', device_id: action.deviceId } } },
      })
      if (!createdNode?.uuid) throw new Error(`动作“${action.name}”已提交，但后端未返回新节点身份`)
      createdNodes.push({
        uuid: String(createdNode.uuid),
        readySource: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'source')?.uuid,
        readyTarget: handles.find((handle: RawRecord) => handle.handle_key === 'ready' && handle.io_type === 'target')?.uuid,
      })
    }
    for (let index = 1; index < createdNodes.length; index += 1) {
      const source = createdNodes[index - 1]
      const target = createdNodes[index]
      if (!source.readySource || !target.readyTarget) throw new Error('动作模板缺少 ready 控制句柄，无法建立顺序依赖')
      await writeData<RawRecord>('POST', `/workflows/${encodeURIComponent(workflowUuid)}/edges`, {
        source_node_uuid: source.uuid, target_node_uuid: target.uuid,
        source_handle_uuid: source.readySource, target_handle_uuid: target.readyTarget,
        description: '实验操作顺序依赖', meta_data: { unilab: { generated_by: 'operation-builder' } },
      })
    }
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
  const devices: Record<string, string> = Object.create(null)
  const materials: Record<string, string> = Object.create(null)
  const sites: Record<string, string> = Object.create(null)
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

  deviceRows.forEach((device) => {
    const binding = device.binding || {}
    const material = device.material || {}
    const label = binding.name || material.name || binding.local_id || binding.material_uuid
    remember(devices, binding.local_id, label)
    remember(devices, binding.material_uuid, label)
    remember(devices, material.uuid, label)
  })
  return { devices, materials, sites }
}

async function taskWithJobs(
  raw: RawRecord,
  workflowsByUuid: Map<string, WorkflowDefinition>,
  knownMaterialUuids: ReadonlySet<string>,
  traceUiUrl: string,
  waitResourceLabels: WaitResourceLabels,
  signal?: AbortSignal,
) {
  const jobs = await requestData<RawRecord[]>(
    `/workflow-tasks/${encodeURIComponent(raw.uuid)}/jobs`,
    signal,
  )
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

const terminalTaskStatusValues = ['succeeded', 'failed', 'canceled', 'timeout'] as const

function selectRecentTerminalTasks(pages: PageData<RawRecord>[], limit = 20): RawRecord[] {
  const terminalStatuses = new Set<string>(terminalTaskStatusValues)
  return pages
    .flatMap((page) => page.items || [])
    .filter((task) => terminalStatuses.has(String(task.status)))
    .sort((left, right) => {
      const timeOrder = String(right.create_time || '').localeCompare(String(left.create_time || ''))
      return timeOrder || String(right.uuid || '').localeCompare(String(left.uuid || ''))
    })
    .slice(0, limit)
}

export async function loadEdgeSnapshot(signal?: AbortSignal): Promise<EdgeSnapshot> {
  const [readiness, workflowPage, terminalTaskPages, runningTaskPage, pendingTaskPage, cancelingTaskPage, attentionTaskPage, materialPage, materialGraph, deviceRows] = await Promise.all([
    requestJson<RawRecord>('/readiness', signal),
    requestAllPages<RawRecord>('/workflows', signal),
    Promise.all(terminalTaskStatusValues.map((status) => requestData<PageData<RawRecord>>(
      `/workflow-tasks?status=${status}&page=1&page_size=20`,
      signal,
    ))),
    requestAllPages<RawRecord>('/workflow-tasks?status=running', signal),
    requestAllPages<RawRecord>('/workflow-tasks?status=pending', signal),
    requestAllPages<RawRecord>('/workflow-tasks?status=canceling', signal),
    requestAllPages<RawRecord>('/workflow-tasks?cleanup_status=requires_attention', signal),
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
  const taskRowsByUuid = new Map<string, RawRecord>()
  const taskRows = [
    ...selectRecentTerminalTasks(terminalTaskPages),
    ...(runningTaskPage.items || []),
    ...(pendingTaskPage.items || []),
    ...(cancelingTaskPage.items || []),
    ...(attentionTaskPage.items || []),
  ]
  taskRows.forEach((task) => taskRowsByUuid.set(String(task.uuid), task))
  const tasks = await mapWithConcurrency(
    [...taskRowsByUuid.values()],
    6,
    (task) => taskWithJobs(
      task,
      workflowsByUuid,
      knownMaterialUuids,
      traceUiUrl,
      waitResourceLabels,
      signal,
    ),
  )
  const terminalTaskStatuses = new Set<TaskPresentationStatus>(terminalTaskStatusValues)
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
    const references = materialReferences.get(material.uuid)
    return references ? { ...enriched, taskReferences: references } : enriched
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
  signal?: AbortSignal,
): Promise<RunPreflightReport> {
  const report = await requestData<RawRecord>(
    `/workflows/${encodeURIComponent(workflowUuid)}/run-preflight?run_mode=normal`,
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
