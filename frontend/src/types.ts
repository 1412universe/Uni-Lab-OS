export type PageId = 'overview' | 'materials' | 'workflows' | 'tasks'

export type ConnectionMode = 'loading' | 'connected' | 'demo' | 'error'

export type TaskStatus =
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

export type NodeStatus =
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
  status: NodeStatus
  device?: string
  materialUuid?: string
}

export interface WorkflowTask {
  uuid: string
  workflowUuid: string
  workflowName: string
  status: TaskStatus
  sample: string
  description: string
  current: string
  progress: number
  updatedAt: string
  nodes: TaskNode[]
  workflowRevision?: number
  planSignature: string
}

export interface MaterialRecord {
  uuid: string
  name: string
  category: string
  location: string
  status: 'available' | 'occupied' | 'reserved' | 'verify'
  barcode: string
  parentUuid?: string
  className: string
  resourceTemplateUuid?: string
  sourceGraph?: string
  updatedAt: string
}

export interface EdgeSnapshot {
  workflows: WorkflowDefinition[]
  tasks: WorkflowTask[]
  materials: MaterialRecord[]
  materialTotal: number
  workflowLoaded: number
  workflowTotal: number
}
