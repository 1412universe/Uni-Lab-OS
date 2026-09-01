export type PageId = 'overview' | 'materials' | 'workflows' | 'tasks'

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
}

export interface ContractField {
  name: string
  type: string
  required?: boolean
  defaultValue?: unknown
  schema: Record<string, unknown>
}

export interface TaskNode {
  uuid: string
  name: string
  kind: string
  index: number
  status: NodePresentationStatus
  device?: string
  materialUuid?: string
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
  | { kind: 'unresolved'; label: string; siteUuid?: string }

export interface MaterialRecord {
  uuid: string
  name: string
  category: string
  currentLocation: MaterialCurrentLocation
  configuredSource: string
  taskReferences: MaterialTaskReference[]
  barcode: string
  parentUuid?: string
  className: string
  resourceTemplateUuid?: string
  sourceGraph?: string
  updatedAt: string
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
