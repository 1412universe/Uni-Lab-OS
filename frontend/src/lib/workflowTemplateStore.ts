/**
 * 本地工作流模板库（localStorage）。
 *
 * 语义是**引用而非快照**：只记住来源工作流 uuid + 名称，插入时现读该工作流
 * 最新图。因此源工作流后续被修改，模板插入的内容也跟着变；源工作流被删掉时
 * 模板条目会标记为失效，而不是留着一份过期的副本让人误以为是冻结版本。
 *
 * 之所以不用后端：现有 `/workflow-node-templates` 是「单个设备动作」的模板
 * （带 resource_template_uuid / goal schema），装不下一整段子图；要做子图模板
 * 需要新表与新 CRUD。当前需求只要单机自用，本地存储足够且不引入协议变更。
 */
import type { WorkflowDefinition } from '../types'

const STORAGE_KEY = 'unilab:workflow-templates'

/** 一条模板引用：只存身份与展示信息，图内容插入时再取。 */
export interface WorkflowTemplateRef {
  /** 模板自身 id，与来源工作流 uuid 解耦，允许同一工作流被存成多个模板。 */
  id: string
  /** 展示名，默认取来源工作流名。 */
  name: string
  /** 来源工作流 uuid —— 插入时按它现读最新图。 */
  sourceWorkflowUuid: string
  /** 记录时的来源修订号，仅用于展示「基于 rN」。 */
  sourceRevision: number
  createdAt: string
}

function newTemplateId(): string {
  const cryptoObj = globalThis.crypto
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID()
  return `tpl-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function sanitize(value: unknown): WorkflowTemplateRef | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const sourceWorkflowUuid = String(raw.sourceWorkflowUuid || '').trim()
  if (!sourceWorkflowUuid) return null
  return {
    id: String(raw.id || newTemplateId()),
    name: String(raw.name || '未命名模板'),
    sourceWorkflowUuid,
    sourceRevision: Number.isFinite(Number(raw.sourceRevision)) ? Number(raw.sourceRevision) : 1,
    createdAt: String(raw.createdAt || new Date().toISOString()),
  }
}

/** 读取全部模板；localStorage 不可用或内容损坏时返回空列表而不是抛错。 */
export function loadWorkflowTemplates(): WorkflowTemplateRef[] {
  try {
    const text = window.localStorage.getItem(STORAGE_KEY)
    if (!text) return []
    const parsed: unknown = JSON.parse(text)
    if (!Array.isArray(parsed)) return []
    return parsed.map(sanitize).filter((item): item is WorkflowTemplateRef => item !== null)
  } catch {
    return []
  }
}

function persist(next: WorkflowTemplateRef[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // 隐私模式或配额满：本次列表仍返回，只是不落盘。
  }
}

/** 把一份工作流存成模板引用；同名同源重复添加时直接返回已有条目。 */
export function addWorkflowTemplate(
  workflow: Pick<WorkflowDefinition, 'uuid' | 'name' | 'revision'>,
  name?: string,
): WorkflowTemplateRef {
  const current = loadWorkflowTemplates()
  const existing = current.find((item) => item.sourceWorkflowUuid === workflow.uuid)
  if (existing && !name) return existing
  const entry: WorkflowTemplateRef = {
    id: newTemplateId(),
    name: (name || workflow.name || '未命名模板').trim(),
    sourceWorkflowUuid: workflow.uuid,
    sourceRevision: Number(workflow.revision) || 1,
    createdAt: new Date().toISOString(),
  }
  persist([entry, ...current.filter((item) => item.id !== entry.id)])
  return entry
}

/** 重命名模板条目（不动来源工作流）。 */
export function renameWorkflowTemplate(id: string, name: string): WorkflowTemplateRef[] {
  const next = loadWorkflowTemplates().map((item) => (
    item.id === id ? { ...item, name: name.trim() || item.name } : item
  ))
  persist(next)
  return next
}

/** 删除模板引用；只影响本地列表，不碰来源工作流。 */
export function removeWorkflowTemplate(id: string): WorkflowTemplateRef[] {
  const next = loadWorkflowTemplates().filter((item) => item.id !== id)
  persist(next)
  return next
}
