import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, PointerEvent as ReactPointerEvent } from 'react'
import { Boxes, Cpu, Focus, GripVertical, Minus, Plus, RotateCcw, X } from 'lucide-react'
import { EDGE_API_BASE } from '../lib/edgeClient'
import { deviceTaskIds, layoutMaterialCanvas, locateCanvasResource } from '../lib/materialCanvasLayout'
import type { CanvasPoint } from '../lib/materialCanvasLayout'
import { taskColor, usageNeedsAttention } from '../lib/resourceUsage'
import type { ResourceUsageModel } from '../lib/resourceUsage'
import type { MaterialUsageContext } from './MaterialUsagePanel'
import { TaskChip } from './MaterialUsagePanel'

export interface CanvasSelection { materialUuid: string; siteUuid?: string; request: number }
type View = CanvasPoint & { scale: number }
const storageKey = `unilab:material-canvas:v1:${EDGE_API_BASE}`

function readPositions(): Record<string, CanvasPoint> {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || '{}')
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, CanvasPoint] => {
      const point = entry[1] as CanvasPoint | null
      return Boolean(point && Number.isFinite(point.x) && Number.isFinite(point.y))
    }))
  } catch { return {} }
}

/** 设备标题与库位共用一个变换容器；拖动只保存本浏览器的展示位置。 */
export function MaterialResourceCanvas({ model, context, selection, onSelect, onClear, onDetails, visibleIds }: {
  model: ResourceUsageModel; context: MaterialUsageContext; selection?: CanvasSelection
  onSelect: (materialUuid: string, siteUuid?: string) => void; onClear: () => void
  onDetails: (materialUuid: string, siteUuid?: string) => void; visibleIds?: Set<string>
}) {
  const viewport = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<View>({ x: 24, y: 24, scale: .7 })
  const [positions, setPositions] = useState(readPositions)
  const positionsRef = useRef(positions)
  const [sections, setSections] = useState<Record<string, string>>({})
  const drag = useRef<{ id?: string; client: CanvasPoint; origin: CanvasPoint; scale: number; moved: boolean } | null>(null)
  const blocks = useMemo(() => layoutMaterialCanvas(model), [model])
  const devices = new Map(model.devices.map((device) => [device.uuid, device]))
  const shown = blocks.filter((block) => !visibleIds || visibleIds.has(block.uuid) || devices.get(block.uuid)!.sites.some((site) => visibleIds.has(site.ownerUuid) || Boolean(site.materialUuid && visibleIds.has(site.materialUuid))))
  const located = locateCanvasResource(model, selection?.materialUuid, selection?.siteUuid)
  const selectedName = located.site?.name || model.nodes.find((node) => node.material.uuid === selection?.materialUuid)?.material.name
  const initialFit = useRef(false)

  function savePositions(next: Record<string, CanvasPoint>) {
    positionsRef.current = next
    setPositions(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* 浏览器禁用存储时仍允许本次查看。 */ }
  }
  function fit() {
    if (!shown.length || !viewport.current) return
    const points = shown.map((block) => ({ ...block, ...(positionsRef.current[block.uuid] || {}) }))
    const x = Math.min(...points.map((p) => p.x)), y = Math.min(...points.map((p) => p.y))
    const width = Math.max(...points.map((p) => p.x + p.width)) - x
    const height = Math.max(...points.map((p) => p.y + p.height)) - y
    const rect = viewport.current.getBoundingClientRect()
    const scale = Math.max(.15, Math.min(1, (rect.width - 70) / width, (rect.height - 85) / height))
    setView({ x: (rect.width - width * scale) / 2 - x * scale, y: (rect.height - height * scale) / 2 - y * scale, scale })
  }
  useEffect(() => {
    if (initialFit.current || !shown.length) return
    const rect = viewport.current?.getBoundingClientRect()
    if (rect?.width) {
      initialFit.current = true
      const primary = model.devices.find((device) => device.isDevice && device.sites.length)
      const block = shown.find((entry) => entry.uuid === primary?.uuid) || shown[0]
      const point = positionsRef.current[block.uuid] || block
      const scale = Math.min(.85, (rect.width - 50) / block.width, (rect.height - 80) / block.height)
      setView({ scale, x: (rect.width - block.width * scale) / 2 - point.x * scale, y: (rect.height - block.height * scale) / 2 - point.y * scale })
    }
  })
  // 选择同一个烧杯后若发生转运，焦点跟随新库位；普通占用轮询不会重置视口。
  useEffect(() => {
    if (!selection || !located.device) return
    const block = blocks.find((entry) => entry.uuid === located.device!.uuid)
    if (!block) return
    const owner = located.site?.ownerUuid || selection.materialUuid
    const section = block.sections.find((entry) => entry.uuid === owner) || block.sections[0]
    if (section) {
      setSections((current) => ({ ...current, [block.uuid]: section.uuid }))
    }
    const point = positionsRef.current[block.uuid] || block
    const rect = viewport.current?.getBoundingClientRect()
    if (rect?.width) {
      const scale = Math.min(1, (rect.width - 60) / block.width, (rect.height - 80) / block.height)
      setView({ x: (rect.width - block.width * scale) / 2 - point.x * scale, y: (rect.height - block.height * scale) / 2 - point.y * scale, scale })
    }
  // 只响应显式选择或所选物料的实际位置变化。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection?.request, located.device?.uuid, located.site?.uuid])

  useEffect(() => {
    const element = viewport.current
    if (!element) return
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest('.resource-canvas-popover, select')) return
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const x = event.clientX - rect.left, y = event.clientY - rect.top
      setView((current) => {
        const scale = Math.max(.15, Math.min(2, current.scale * Math.exp(-event.deltaY * .002)))
        const factor = scale / current.scale
        return { scale, x: x - (x - current.x) * factor, y: y - (y - current.y) * factor }
      })
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [])

  function startDrag(event: ReactPointerEvent, id?: string) {
    if (event.button !== 0) return
    if (!id && (event.target as HTMLElement).closest('button, select, .resource-canvas-popover')) return
    event.stopPropagation()
    const block = blocks.find((entry) => entry.uuid === id)
    drag.current = { id, client: { x: event.clientX, y: event.clientY }, origin: id ? positionsRef.current[id] || block! : view, scale: view.scale, moved: false }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function moveDrag(event: ReactPointerEvent) {
    const current = drag.current
    if (!current) return
    const dx = event.clientX - current.client.x, dy = event.clientY - current.client.y
    if (Math.abs(dx) + Math.abs(dy) < 4 && !current.moved) return
    current.moved = true
    if (current.id) {
      const next = { ...positionsRef.current, [current.id]: { x: current.origin.x + dx / current.scale, y: current.origin.y + dy / current.scale } }
      positionsRef.current = next; setPositions(next)
    } else setView((view) => ({ ...view, x: current.origin.x + dx, y: current.origin.y + dy }))
  }
  function endDrag() {
    if (drag.current?.id && drag.current.moved) savePositions(positionsRef.current)
    drag.current = null
  }
  function zoom(factor: number) {
    const rect = viewport.current?.getBoundingClientRect()
    setView((current) => {
      const scale = Math.max(.15, Math.min(2, current.scale * factor)), ratio = scale / current.scale
      const x = (rect?.width || 0) / 2, y = (rect?.height || 0) / 2
      return { scale, x: x - (x - current.x) * ratio, y: y - (y - current.y) * ratio }
    })
  }

  return <section className="resource-canvas-shell" aria-label="设备与库位画布">
    <div className="resource-canvas-legend"><span><Cpu size={15} />设备：Task 使用时点亮</span><span><Boxes size={15} />库位：有物料时点亮</span><span><i className="task" />Task 色标</span><span><i className="stock" />有料 · 未绑定 Task</span><span><i />空闲 / 空库位</span></div>
    <div className="resource-canvas-viewport" ref={viewport} onPointerDown={(event) => startDrag(event)} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag}>
      {!shown.length ? <div className="resource-canvas-empty"><Boxes size={32} /><strong>暂无匹配资源</strong><span>可清除筛选，或在左侧选择物料查看详情。</span></div> : null}
      <div className="resource-canvas-plane" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}>
        {shown.map((block) => {
          const device = devices.get(block.uuid)!
          const taskIds = deviceTaskIds(device)
          const attention = device.holdings.some(usageNeedsAttention)
          const section = block.sections.find((entry) => entry.uuid === sections[block.uuid]) || block.sections[0]
          const point = positions[block.uuid] || block
          return <article key={block.uuid} aria-label={`设备组 ${device.name}`} className={`resource-device-block ${located.device?.uuid === block.uuid ? 'selected' : ''}`} style={{ left: point.x, top: point.y, width: block.width, height: block.height }}>
            <header className={`resource-device-head ${taskIds.length ? 'lit' : ''}`} data-in-use={Boolean(taskIds.length)} style={{ '--usage-color': taskColor(taskIds[0] || '') } as CSSProperties}>
              <button className="resource-device-drag" aria-label={`移动设备及库位 ${device.name}`} title="拖动设备和全部库位；方向键微调" onPointerDown={(event) => startDrag(event, block.uuid)} onKeyDown={(event) => {
                const delta: Record<string, CanvasPoint> = { ArrowLeft: { x: -20, y: 0 }, ArrowRight: { x: 20, y: 0 }, ArrowUp: { x: 0, y: -20 }, ArrowDown: { x: 0, y: 20 } }
                if (!delta[event.key]) return
                event.preventDefault(); savePositions({ ...positionsRef.current, [block.uuid]: { x: point.x + delta[event.key].x, y: point.y + delta[event.key].y } })
              }}><GripVertical size={17} /></button>
              <button className="resource-device-title" data-usage-material={device.uuid} aria-label={`查看设备 ${device.name}`} onClick={() => onSelect(device.uuid)}><strong>{device.name}</strong><small>{taskIds.length ? taskIds.map((id) => `Task ${id.slice(0, 8)}`).join(' · ') : attention ? '占用待处理' : '空闲'}</small></button>
              <span className="resource-device-state">{attention ? '⚠' : <i />}{taskIds.length ? '使用中' : attention ? '待确认' : '空闲'}</span>
            </header>
            <div className="resource-device-sites">
              <div className="resource-site-section"><select aria-label={`${device.name} 库位分组`} value={section?.uuid || ''} disabled={!section} onChange={(event) => setSections((current) => ({ ...current, [block.uuid]: event.target.value }))}>{block.sections.map((entry) => <option key={entry.uuid} value={entry.uuid}>{entry.name}</option>)}{!section ? <option>未配置库位</option> : null}</select><span>{device.sites.filter((site) => site.materialUuid).length}/{device.sites.length} 有料</span></div>
              {section ? <div className="resource-site-cells" style={{ gridTemplateColumns: `repeat(${section.columns}, minmax(0, 1fr))`, gridTemplateRows: `repeat(${section.rows}, 54px)` }}>{section.sites.map(({ site, column, row }) => {
                const ids = [...new Set(site.bindings.map((binding) => binding.workflow_task_uuid))]
                const color = ids.length ? taskColor(ids[0]) : '#069c91'
                const transfers = site.transfers || []
                const warning = transfers.some((transfer) => transfer.uncertain) || site.holdings.some(usageNeedsAttention)
                const marker = warning ? '位置待确认' : transfers.some((transfer) => transfer.direction === 'out') ? '转移中' : transfers.some((transfer) => transfer.direction === 'in') ? '待放入' : site.holdings.length ? '已锁定' : ''
                return <button key={site.uuid} aria-label={`库位 ${site.ownerName} / ${site.name}`} data-site-uuid={site.uuid} data-usage-site={site.uuid} data-occupied={Boolean(site.materialUuid)} className={`resource-site-cell ${site.materialUuid ? 'lit' : ''} ${located.site?.uuid === site.uuid ? 'selected' : ''} ${warning ? 'attention' : ''}`} style={{ gridColumn: column + 1, gridRow: row + 1, '--usage-color': color } as CSSProperties} onClick={() => onSelect(site.ownerUuid, site.uuid)}><strong>{site.name}</strong><span data-usage-material={site.materialUuid}>{site.materialName || '空'}</span>{marker ? <small>{marker}</small> : site.materialUuid ? <small>{ids.length ? `Task ${ids[0].slice(0, 8)}${ids.length > 1 ? ` +${ids.length - 1}` : ''}` : '未绑定 Task'}</small> : null}</button>
              })}</div> : <p className="resource-device-no-sites">该设备未配置库位</p>}
            </div>
          </article>
        })}
      </div>
      {selection && selectedName ? <aside className="resource-canvas-popover" aria-label="画布选中资源"><header><strong>{selectedName}</strong><button aria-label="关闭资源预览" onClick={onClear}><X size={16} /></button></header>{located.site ? <><p>当前物料：<b>{located.site.materialName || '空库位'}</b></p>{located.site.materialUuid && !located.site.bindings.length ? <p>未绑定 Task</p> : null}{located.site.bindings.map((binding) => <TaskChip key={binding.uuid} uuid={binding.workflow_task_uuid} tasks={context.tasks} onClick={() => context.onOpenTask(binding.workflow_task_uuid)} />)}{located.site.transfers?.map((transfer) => <p key={`${transfer.job.uuid}:${transfer.direction}`} className="resource-warning">{transfer.uncertain ? '转移结果待确认' : transfer.direction === 'in' ? '目标库位，等待实际放入' : '正在转移，保留最近确认位置'}</p>)}</> : <p>{located.device ? `${located.device.sites.length} 个库位 · ${located.device.sites.filter((site) => site.materialUuid).length} 个有料` : '当前未定位到画布库位'}</p>}{located.device && !located.site ? deviceTaskIds(located.device).map((uuid) => <TaskChip key={uuid} uuid={uuid} tasks={context.tasks} onClick={() => context.onOpenTask(uuid)} />) : null}<footer><button onClick={() => onDetails(located.site?.ownerUuid || selection.materialUuid, located.site?.uuid || selection.siteUuid)}>查看详情{located.site ? ' / 上下料' : ''}</button>{located.site?.materialUuid ? <button onClick={() => onDetails(located.site!.materialUuid!)}>查看物料</button> : null}</footer></aside> : null}
      <div className="resource-canvas-help">拖动空白平移 · 滚轮缩放 · 拖动设备左侧手柄整体移动</div>
      <div className="resource-canvas-controls"><button aria-label="缩小画布" onClick={() => zoom(1 / 1.2)}><Minus size={16} /></button><span>{Math.round(view.scale * 100)}%</span><button aria-label="放大画布" onClick={() => zoom(1.2)}><Plus size={16} /></button><button onClick={fit}><Focus size={15} />适应画布</button><button title="仅重置本浏览器的展示布局" onClick={() => { savePositions({}); onClear(); requestAnimationFrame(fit) }}><RotateCcw size={15} />重置排布</button></div>
    </div>
  </section>
}
