/**
 * 工作流 JSON 的批量导入 / 导出载荷处理。
 *
 * 后端 `POST /workflows/import` 只认 `{name, workflow_type, data:{nodes, edges}}`，
 * 而跨机器流转的文件还可能是 CWM 的 `{target_lab_uuid, name, data:{...}}`、或者
 * 编辑器自己打的批量包。这里统一归一化成后端能吃的形状，解析失败时给出可读原因，
 * 而不是把整批导入整体吞掉。
 */
import type { RawWorkflowEdge, RawWorkflowNode } from './edgeClient'

export type WorkflowType = 'normal' | 'experiment_operation'

/** 后端导入接口的最小载荷。 */
export interface WorkflowImportPayload {
  name: string
  workflow_type: WorkflowType
  data: { nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[] }
}

/** 导出文件头：只作溯源，不影响导入。 */
export interface WorkflowExportMeta {
  version: number
  exported_at: string
  workflow_uuid?: string
  revision?: number
  node_count: number
  edge_count: number
}

export interface WorkflowExportFile extends WorkflowImportPayload {
  unilab_export: WorkflowExportMeta
}

export interface WorkflowBatchExportFile {
  unilab_batch_export: { version: number; exported_at: string; count: number }
  workflows: WorkflowExportFile[]
}

export interface ParsedImport {
  payload: WorkflowImportPayload
  /** 原始文件名，用于结果回显。 */
  source: string
}

export interface ImportFailure {
  source: string
  reason: string
}

function asRecord(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : {}
}

function sanitizeFilename(name: string): string {
  return (name || 'workflow').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 60) || 'workflow'
}

/** 组装单个导出文件。 */
export function buildExportFile(
  name: string,
  workflowType: WorkflowType,
  nodes: RawWorkflowNode[],
  edges: RawWorkflowEdge[],
  options: { workflowUuid?: string; revision?: number } = {},
): WorkflowExportFile {
  return {
    unilab_export: {
      version: 1,
      exported_at: new Date().toISOString(),
      workflow_uuid: options.workflowUuid,
      revision: options.revision,
      node_count: nodes.length,
      edge_count: edges.length,
    },
    name: name || '未命名工作流',
    workflow_type: workflowType,
    data: { nodes, edges },
  }
}

/** 组装批量导出包。 */
export function buildBatchExportFile(files: WorkflowExportFile[]): WorkflowBatchExportFile {
  return {
    unilab_batch_export: { version: 1, exported_at: new Date().toISOString(), count: files.length },
    workflows: files,
  }
}

/** 浏览器内下载 JSON；不依赖后端，纯前端落地。 */
export function downloadJson(filename: string, value: unknown): void {
  const text = JSON.stringify(value, null, 2)
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename.endsWith('.json') ? filename : `${filename}.json`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  // Safari 需要在同一轮事件循环之后再释放。
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** 导出文件名：`工作流名-r修订.json`。 */
export function exportFilename(name: string, revision?: number): string {
  const base = sanitizeFilename(name)
  return revision ? `${base}-r${revision}.json` : `${base}.json`
}

function normalizeOne(value: unknown, source: string): ParsedImport | ImportFailure {
  const raw = asRecord(value)
  const data = asRecord(raw.data)
  const nodes = Array.isArray(data.nodes) ? data.nodes : Array.isArray(raw.nodes) ? raw.nodes : null
  const edges = Array.isArray(data.edges) ? data.edges : Array.isArray(raw.edges) ? raw.edges : []
  if (!nodes) return { source, reason: '文件里没有 data.nodes，未识别为工作流 JSON' }
  if (!nodes.length) return { source, reason: '节点列表为空，没有可导入的内容' }
  const workflowType = String(raw.workflow_type || data.workflow_type || 'normal')
  return {
    payload: {
      name: String(raw.name || data.name || '导入的工作流'),
      workflow_type: workflowType === 'experiment_operation' ? 'experiment_operation' : 'normal',
      data: { nodes: nodes as RawWorkflowNode[], edges: edges as RawWorkflowEdge[] },
    },
    source,
  }
}

/**
 * 解析一个文件的内容；可能产出多条导入任务（批量包），也可能整体失败。
 */
export function parseImportPayload(
  text: string,
  source: string,
): { items: ParsedImport[]; failures: ImportFailure[] } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    return { items: [], failures: [{ source, reason: `不是合法 JSON：${error instanceof Error ? error.message : '解析失败'}` }] }
  }
  const raw = asRecord(parsed)
  const batch = Array.isArray(raw.workflows) ? raw.workflows : null
  if (batch) {
    const items: ParsedImport[] = []
    const failures: ImportFailure[] = []
    batch.forEach((entry, index) => {
      const result = normalizeOne(entry, `${source} #${index + 1}`)
      if ('reason' in result) failures.push(result)
      else items.push(result)
    })
    return { items, failures }
  }
  const single = normalizeOne(parsed, source)
  return 'reason' in single ? { items: [], failures: [single] } : { items: [single], failures: [] }
}
