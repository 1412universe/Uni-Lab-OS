import type {
  ContractField,
  EdgeSnapshot,
  MaterialCurrentLocation,
  MaterialRecord,
  NodePresentationStatus,
  RunPreflightReport,
  TaskNode,
  TaskPresentationStatus,
  WorkflowDefinition,
  WorkflowTask,
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
  if (!response.ok) {
    throw new Error(body?.error?.msg || body?.detail || `Edge API 请求失败（${response.status}）`)
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

function withoutStorageTimes(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value
  const semantic = { ...(value as RawRecord) }
  delete semantic.create_time
  delete semantic.update_time
  delete semantic.deleted_at
  return semantic
}

function matrixWorkflowSnapshot(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value
  const snapshot = { ...(value as RawRecord) }
  snapshot.workflow = withoutStorageTimes(snapshot.workflow)
  ;[
    'nodes',
    'edges',
    'inventory_requirements',
    'node_templates',
    'handle_templates',
  ].forEach((key) => {
    if (Array.isArray(snapshot[key])) {
      snapshot[key] = snapshot[key].map(withoutStorageTimes)
    }
  })
  delete snapshot.create_time
  delete snapshot.update_time
  delete snapshot.deleted_at
  return snapshot
}

function matrixGroupKey(raw: RawRecord): string {
  if (!raw.workflow_snapshot || !raw.execution_plan) {
    return `task:${String(raw.uuid)}`
  }
  const executionPlan = raw.execution_plan && typeof raw.execution_plan === 'object'
    ? { ...raw.execution_plan }
    : {}
  if (Array.isArray(executionPlan.nodes)) {
    executionPlan.nodes = executionPlan.nodes.map((node: RawRecord) => {
      const definition = { ...node }
      delete definition.param
      return definition
    })
  }
  return JSON.stringify(canonicaliseMatrixValue({
    executionKind: raw.execution_kind,
    revisionFingerprint: raw.revision_fingerprint ?? null,
    workflowUuid: raw.workflow_uuid,
    workflowRevision: raw.workflow_snapshot?.workflow?.revision,
    workflowSnapshot: matrixWorkflowSnapshot(raw.workflow_snapshot),
    executionPlan,
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

export function adaptTask(
  raw: RawRecord,
  jobs: RawRecord[] = [],
  workflowName = '未命名工作流',
  inputContract: ContractField[] = [],
  knownMaterialUuids?: ReadonlySet<string>,
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
  }
}

function configuredSourceLabel(sourceNodeId: unknown): string {
  const id = String(sourceNodeId || '')
  const [prefix, slot] = id.split('__')
  const station = prefix?.match(/^s(\d+)/i)?.[1]
  if (station && slot) return `S${station} / ${slot}`
  return id || '无配置来源位置'
}

function currentLocationFromDetail(raw: RawRecord): MaterialCurrentLocation {
  const hasCurrentSiteProjection = Object.prototype.hasOwnProperty.call(raw, 'current_site')
  if (!hasCurrentSiteProjection) {
    return { kind: 'unresolved', label: '权威位置尚未读取' }
  }
  if (!raw.current_site) {
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
  return {
    uuid: String(raw.uuid),
    name: String(raw.name || raw.uuid),
    category: String(raw.config?.category || raw.config?.rendering?.kind || 'material'),
    currentLocation,
    configuredSource: configuredSourceLabel(raw.meta_data?.source_node_id),
    taskReferences: [],
    barcode: String(raw.barcode || '—'),
    parentUuid: raw.parent_uuid ? String(raw.parent_uuid) : undefined,
    className: String(raw.class || 'unknown'),
    resourceTemplateUuid: raw.resource_template_uuid ? String(raw.resource_template_uuid) : undefined,
    sourceGraph: raw.meta_data?.source_graph ? String(raw.meta_data.source_graph) : undefined,
    updatedAt: timeLabel(raw.update_time),
  }
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

async function taskWithJobs(
  raw: RawRecord,
  workflowsByUuid: Map<string, WorkflowDefinition>,
  knownMaterialUuids: ReadonlySet<string>,
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
  const [readiness, workflowPage, terminalTaskPages, runningTaskPage, pendingTaskPage, cancelingTaskPage, attentionTaskPage, materialPage, materialGraph] = await Promise.all([
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
  ])
  if (readiness.status !== 'ready') throw new Error('Edge 工作流运行时尚未就绪')

  const workflows = (workflowPage.items || []).map(adaptWorkflow)
  const workflowsByUuid = new Map(workflows.map((workflow) => [workflow.uuid, workflow]))
  const knownMaterialUuids = new Set(
    (materialPage.items || []).map((material) => String(material.uuid)),
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
    (task) => taskWithJobs(task, workflowsByUuid, knownMaterialUuids, signal),
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
  const materials = (materialPage.items || []).map((raw) => {
    const material = adaptMaterial(
      raw,
      materialLocations.get(String(raw.uuid)) || {
        kind: 'unresolved',
        label: '物料图缺少位置投影',
      },
    )
    const references = materialReferences.get(material.uuid)
    return references ? { ...material, taskReferences: references } : material
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
