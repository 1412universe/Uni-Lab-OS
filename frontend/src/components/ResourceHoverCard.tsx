import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import type { MaterialUsageContext } from './MaterialUsagePanel'
import { deviceTaskIds } from '../lib/materialCanvasLayout'
import { taskColor } from '../lib/resourceUsage'

export interface ResourceHover { materialUuid?: string; siteUuid?: string; left: number; top: number }

/** 库位使用与物料关联分别展示；悬浮信息始终取当前快照，不缓存转运前的位置。 */
export function ResourceHoverCard({ hover, context }: { hover?: ResourceHover; context: MaterialUsageContext }) {
  const model = context.model
  if (!hover || !model) return null
  const site = hover.siteUuid ? model.sites.get(hover.siteUuid) : undefined
  const uuid = hover.materialUuid || site?.materialUuid
  const material = model.nodes.find((node) => node.material.uuid === uuid)?.material
  const currentSite = [...model.sites.values()].find((entry) => entry.materialUuid === uuid)
  const device = model.devices.find((entry) => entry.uuid === uuid)
  const bindings = model.bindings.filter((binding) => binding.material_uuid === uuid)
  const materialTasks = [...new Set([...bindings.map((binding) => binding.workflow_task_uuid),
    ...model.holdings.filter((holding) => holding.resourceUuid === uuid).map((holding) => holding.taskUuid),
    ...model.devices.flatMap((entry) => entry.executions || []).filter((job) => job.expected_change_set?.material_uuid === uuid).map((job) => job.task_uuid),
  ].filter(Boolean))]
  const siteTasks = [...new Set([...(site?.holdings || []).map((holding) => holding.taskUuid), ...(site?.transfers || []).map((transfer) => transfer.job.task_uuid)].filter(Boolean))]
  const renderTasks = (ids: string[]) => ids.map((id) => {
    const task = context.tasks.find((entry) => entry.uuid === id)
    const jobs = model.devices.flatMap((entry) => entry.executions || []).filter((job) => job.task_uuid === id)
    return <div className="resource-hover-task" key={id} style={{ '--usage-color': taskColor(id) } as CSSProperties}><div>{task?.workflowName || jobs[0]?.task_name || 'Task'}</div><code>Task {id}</code>{jobs.length ? <small>节点：{[...new Set(jobs.map((job) => job.node_name))].join('、')}</small> : null}</div>
  })
  const top = Math.max(12, Math.min(hover.top + 10, window.innerHeight - 360))
  const left = Math.max(12, Math.min(hover.left, window.innerWidth - 325))
  return createPortal(<div className="resource-hover-card" role="tooltip" style={{ left, top }}><strong>{site ? `库位 · ${site.name}` : `${device ? '设备' : '物料'} · ${material?.name || uuid}`}</strong>
    {context.stale ? <p>最后一次快照，当前状态可能已变化</p> : null}
    {site ? <><small>所属容器：{site.ownerName}</small><p>当前物料：{site.materialName || '空库位'}</p><small>物料关联 Task</small>{site.materialUuid ? materialTasks.length ? renderTasks(materialTasks) : <p>未绑定 Task</p> : <p>无物料</p>}<small>库位使用 Task</small>{siteTasks.length ? renderTasks(siteTasks) : <p>没有 Task 锁定或使用此库位</p>}{site.transfers?.map((transfer) => <p key={`${transfer.job.uuid}:${transfer.direction}`}>{transfer.uncertain ? '转移结果待确认' : transfer.direction === 'out' ? '物料转移中，保留最近确认位置' : '转运目标，尚未确认物料到位'}</p>)}</>
      : <><p>当前位置：{currentSite ? `${currentSite.ownerName} / ${currentSite.name}` : device ? '画布设备' : '未分配库位'}</p><small>{device ? '设备使用 Task' : '物料关联 Task'}</small>{device ? deviceTaskIds(device).length ? renderTasks(deviceTaskIds(device)) : <p>当前没有 Task 使用</p> : materialTasks.length ? renderTasks(materialTasks) : <p>未绑定 Task</p>}</>}
  </div>, document.body)
}
