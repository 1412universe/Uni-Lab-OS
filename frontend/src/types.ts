export type PageId = 'overview' | 'materials' | 'reagents' | 'operations' | 'workflows' | 'tasks'

export type ConnectionMode = 'loading' | 'connected' | 'demo' | 'error'

/** 只用于界面展示；不会作为 Shared Interface 的 Workflow Task 状态回写。 */
export type TaskPresentationStatus =
  | 'running'
  | 'admission_blocked'
  | 'succeeded'
  | 'failed'
  | 'pending'
  | 'paused'
  | 'canceling'
  | 'canceled'
  | 'timeout'
  | 'intervention_required'
  | 'execution_unknown'
  | 'unknown'

/** 由冻结执行节点和权威 Job 状态组合出的矩阵展示状态。 */
export type NodePresentationStatus =
  | 'succeeded'
  | 'running'
  | 'waiting'
  | 'failed'
  | 'pending'
  | 'skipped'
  | 'canceling'
  | 'canceled'
  | 'attention'

/** 已归类为界面语言的节点等待原因，避免视图依赖 Edge 内部锁键或状态码。 */
export interface TaskNodeWaitReason {
  code: string
  title: string
  message: string
  details: string[]
  waitingSince?: string
}

export interface WorkflowDefinition {
  uuid: string
  name: string
  revision: number
  status: string
  description: string
  nodeCount: number
  tags: string[]
  inputContract: ContractField[]
  outputContract: ContractField[]
  sourcePath?: string
  workflowType: 'normal' | 'experiment_operation'
  operationCategoryUuid?: string
}

export interface WorkflowTarget {
  workflowUuid: string
  revision?: number
  taskUuid?: string
}

export interface WorkflowGraphNode {
  uuid: string
  name: string
  type: string
  kind: 'group' | 'material_source' | 'action'
  action_name?: string
  workflow_node_template_uuid?: string
  material_uuid?: string
  param?: Record<string, any>
  pose?: Record<string, any>
  meta_data?: Record<string, any>
  parentUuid?: string
  deviceId?: string
  authoringOrder?: number
  authoringResultName?: string
  parallelScope?: string
  materialRole?: string
  description?: string
  disabled: boolean
}

export interface WorkflowGraphEdge {
  uuid: string
  sourceNodeUuid: string
  targetNodeUuid: string
}

export interface WorkflowGraph {
  workflow: WorkflowDefinition
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
  /** 完整图返回的模板快照，包含发布子工作流的合成节点句柄。 */
  nodeTemplates?: Array<Record<string, any>>
  handleTemplates?: Array<Record<string, any>>
}

export interface ContractField {
  name: string
  type: string
  required?: boolean
  defaultValue?: unknown
  title?: string
  description?: string
  implicit?: boolean
  schema: Record<string, unknown>
}

export interface TaskNodeJobEvidence {
  uuid: string
  attempt?: number
  param: unknown
  feedbackData: unknown
  returnInfo: unknown
  errorInfo: unknown[]
  startedAt?: string
  finishedAt?: string
}

export interface TaskNode {
  uuid: string
  name: string
  kind: string
  index: number
  status: NodePresentationStatus
  device?: string
  materialUuid?: string
  waitReason?: TaskNodeWaitReason
  job?: TaskNodeJobEvidence
}

export interface WorkflowTask {
  uuid: string
  workflowUuid: string
  workflowName: string
  status: TaskPresentationStatus
  sample: string
  description: string
  current: string
  progress: number
  updatedAt: string
  nodes: TaskNode[]
  materialUuids: string[]
  workflowRevision?: number
  runMode: string
  matrixGroupKey: string
  trace?: {
    traceId?: string
    url: string
    mode: 'trace' | 'search'
  }
}

export interface MaterialTaskReference {
  taskUuid: string
  taskStatus: TaskPresentationStatus
  workflowName: string
  sample: string
}

export type MaterialCurrentLocation =
  | {
      kind: 'site'
      label: string
      siteUuid: string
      ownerMaterialUuid: string
    }
  | { kind: 'unassigned'; label: string }
  | { kind: 'structural'; label: string; siteCount: number }
  | { kind: 'unresolved'; label: string; siteUuid?: string }

export interface MaterialRecord {
  uuid: string
  name: string
  category: string
  currentLocation: MaterialCurrentLocation
  configuredSource: string
  sourceNodeId?: string
  taskReferences: MaterialTaskReference[]
  barcode: string
  parentUuid?: string
  className: string
  resourceTemplateUuid?: string
  sourceGraph?: string
  updatedAt: string
  isStructural: boolean
  siteCount: number
  sites: Array<{
    uuid: string
    name: string
    occupiedMaterialUuid?: string
    occupiedMaterialName?: string
    allowedResourceTemplateUuids?: string[]
  }>
  revision: number
  position: [number, number, number]
  size: [number, number, number]
}

export interface ResourceTemplateRecord {
  uuid: string
  name: string
  displayName: string
  description: string
  resourceType: string
  /** 模板标签；含 "container" 表示可承载试剂 / 样品 / 当前物质。 */
  tags?: string[]
  availableSites: Array<{ name: string; label: string }>
}

export interface ActionTemplateRecord {
  uuid: string
  name: string
  displayName: string
  description?: string
  type: string
  nodeType: string
  resourceTemplate: { uuid: string; name: string; displayName: string }
}

export interface ActionParameterRecord {
  handleUuid: string
  key: string
  displayName: string
  required: boolean
  schema: Record<string, unknown>
}

export interface OperationCategoryRecord {
  uuid: string
  name: string
  sortOrder: number
}

export interface ReagentInfoRecord {
  uuid: string
  name: string
  nameEn?: string
  aliases: string[]
  cas?: string
  molecularFormula?: string
  smiles?: string
  inchiKey?: string
  molecularWeight?: number
  densityGPerMl?: number
  physicalState: 'solid' | 'liquid' | 'gas' | 'other' | 'unknown'
  description?: string
  metadata?: Record<string, unknown>
  updatedAt: string
}

export interface CompoundLookupResult {
  cas: string
  status: 'ok' | 'registered' | 'not_found' | 'unavailable'
  message?: string
  compound?: {
    name?: string
    molecularFormula?: string
    smiles?: string
    inchiKey?: string
    molecularWeight?: number
  }
}

export interface ReagentRecord {
  uuid: string
  materialUuid: string
  reagentInfoUuid: string
  name: string
  cas?: string
  molecularFormula?: string
  physicalState: string
  quantity?: number
  quantityUnit?: string
  concentrationValue?: number
  concentrationUnit?: string
  densityGPerMl?: number
  containerName?: string
  containerBarcode?: string
  /** 由分装产生时指向源瓶试剂；手工录入的瓶子为空。 */
  sourceReagentUuid?: string
  dispenseCommandId?: string
  revision: number
  updatedAt: string
}

export interface ReagentHistoryRecord {
  uuid: string
  materialUuid: string
  reagentUuid: string
  eventType: 'add' | 'adjust' | 'consume' | 'remove' | string
  operatorType: string
  quantityDelta: number
  quantityUnit: string
  revision: number
  recordedAt: string
  resultQuantity?: number
  resultQuantityUnit?: string
  source?: string
  workflowTaskUuid?: string
  workflowNodeJobUuid?: string
  traceId?: string
  /** 同一次分装的所有台账共用 causation_id（即分装命令 ID）。 */
  causationId?: string
  sourceReagentUuid?: string
  targetReagentUuids?: string[]
}

export interface RunPreflightCheck {
  type: string
  status: 'passed' | 'blocked' | 'deferred' | 'confirmation_required'
  code: string
  message: string
  blocking: boolean
  nodeUuid?: string
  nodeName?: string
}

export interface RunPreflightReport {
  workflowUuid: string
  workflowRevision: number
  runMode: string
  status: 'runnable_now' | 'temporarily_unavailable' | 'invalid'
  canRun: boolean
  checkedAt: string
  summary: {
    executionNodeCount: number
    passedCheckCount: number
    blockingCheckCount: number
    deferredCheckCount: number
    confirmationRequiredCount: number
  }
  checks: RunPreflightCheck[]
}

export interface EdgeSnapshot {
  workflows: WorkflowDefinition[]
  tasks: WorkflowTask[]
  materials: MaterialRecord[]
  materialTotal: number
  workflowLoaded: number
  workflowTotal: number
}
