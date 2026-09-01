import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from 'react'
import {
  Braces,
  CircleDot,
  Layers3,
  ListTree,
  Network,
  PackageOpen,
  RotateCcw,
  TriangleAlert,
  X,
} from 'lucide-react'
import {
  applyWorkflowNodeOffsets,
  layoutWorkflowGraph,
  type PositionedWorkflowNode,
  type WorkflowNodeOffset,
} from '../lib/workflowGraphLayout'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'

type WorkflowEdgeKind = 'dependency' | 'material' | 'parallel_entry' | 'parallel_join'
type EdgeEndpoint = 'source' | 'target'

const edgeKindLabels: Record<WorkflowEdgeKind, string> = {
  dependency: '流程依赖',
  material: '物料输入',
  parallel_entry: '并行入口',
  parallel_join: '并行汇合',
}

function nodeTypeLabel(node: WorkflowGraphNode) {
  if (node.kind === 'material_source') return '物料源'
  if (node.kind === 'group') return '分组'
  return node.deviceId || node.type || '工作流节点'
}

function nodeParallelScope(node: WorkflowGraphNode | undefined, nodesByUuid: Map<string, WorkflowGraphNode>) {
  if (!node) return undefined
  if (node.parallelScope) return node.parallelScope
  return node.parentUuid ? nodesByUuid.get(node.parentUuid)?.parallelScope : undefined
}

function classifyEdge(
  edge: WorkflowGraphEdge,
  nodesByUuid: Map<string, WorkflowGraphNode>,
): WorkflowEdgeKind {
  const source = nodesByUuid.get(edge.sourceNodeUuid)
  const target = nodesByUuid.get(edge.targetNodeUuid)
  if (source?.kind === 'material_source') return 'material'
  const sourceScope = nodeParallelScope(source, nodesByUuid)
  const targetScope = nodeParallelScope(target, nodesByUuid)
  if (targetScope && sourceScope !== targetScope) return 'parallel_entry'
  if (sourceScope && sourceScope !== targetScope) return 'parallel_join'
  return 'dependency'
}

function edgeDescription(kind: WorkflowEdgeKind) {
  if (kind === 'material') return '物料来源与消费节点的输入关系'
  if (kind === 'parallel_entry') return '前置条件满足后，该节点进入并行分支；不表示物料被复制'
  if (kind === 'parallel_join') return '并行分支完成后，后续节点才能继续'
  return '前置节点完成后，后续节点才能继续'
}

function WorkflowNodeCard({
  positioned,
  materialInputs = [],
  edgeEndpoint,
  dimmed,
  dragging,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  previewingMaterials,
  onMaterialPreviewStart,
  onMaterialPreviewEnd,
}: {
  positioned: PositionedWorkflowNode
  materialInputs?: WorkflowGraphNode[]
  edgeEndpoint?: EdgeEndpoint
  dimmed?: boolean
  dragging?: boolean
  onPointerDown: (event: PointerEvent<HTMLElement>) => void
  onPointerMove: (event: PointerEvent<HTMLElement>) => void
  onPointerUp: (event: PointerEvent<HTMLElement>) => void
  previewingMaterials?: boolean
  onMaterialPreviewStart: (materialUuids: string[]) => void
  onMaterialPreviewEnd: () => void
}) {
  const { node } = positioned
  const isMaterial = node.kind === 'material_source'
  const typeLabel = nodeTypeLabel(node)
  const alias = node.authoringResultName && node.authoringResultName !== node.name
    ? node.authoringResultName
    : typeLabel
  const order = node.authoringOrder === undefined ? '' : `#${String(node.authoringOrder + 1).padStart(2, '0')} · `
  const accessibleDetails = [...new Set([alias, typeLabel])]
  const accessibleMaterialInputs = materialInputs.length
    ? `物料输入：${materialInputs.map((material) => material.name).join('、')}`
    : undefined
  const endpointLabel = edgeEndpoint === 'source'
    ? '已选连线起点'
    : edgeEndpoint === 'target'
      ? '已选连线终点'
      : undefined
  return (
    <article
      className={[
        'workflow-dag-node',
        isMaterial ? 'material-source' : '',
        node.disabled ? 'disabled' : '',
        edgeEndpoint ? `edge-${edgeEndpoint}` : '',
        dimmed ? 'edge-dimmed' : '',
        dragging ? 'dragging' : '',
      ].filter(Boolean).join(' ')}
      aria-label={[node.name, ...accessibleDetails, accessibleMaterialInputs, endpointLabel].filter(Boolean).join('，')}
      aria-grabbed={dragging}
      data-node-uuid={node.uuid}
      data-rank={positioned.rank}
      data-band={positioned.band}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{ left: positioned.x, top: positioned.y, width: positioned.width, height: positioned.height }}
      title={`${node.name}\n${alias}\n${typeLabel}\n拖动可调整位置\n${node.uuid}`}
    >
      <span className="workflow-dag-node-icon">
        {isMaterial ? <CircleDot size={17} /> : <Braces size={17} />}
      </span>
      <span className="workflow-dag-node-copy">
        <strong>{node.name}</strong>
        <small>{order}{alias}</small>
      </span>
      {materialInputs.length ? (
        <button
          type="button"
          className="workflow-dag-node-materials"
          aria-label={`按住查看 ${materialInputs.length} 个物料输入：${materialInputs.map((material) => material.name).join('、')}`}
          aria-pressed={previewingMaterials}
          title={materialInputs.map((material) => material.name).join('、')}
          onPointerDown={(event) => {
            if (event.button !== 0) return
            event.stopPropagation()
            event.currentTarget.setPointerCapture?.(event.pointerId)
            onMaterialPreviewStart(materialInputs.map((material) => material.uuid))
          }}
          onPointerMove={(event) => {
            event.stopPropagation()
            const bounds = event.currentTarget.getBoundingClientRect()
            const outside = event.clientX < bounds.left
              || event.clientX > bounds.right
              || event.clientY < bounds.top
              || event.clientY > bounds.bottom
            if (!outside) return
            if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
            onMaterialPreviewEnd()
          }}
          onPointerUp={(event) => {
            event.stopPropagation()
            if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
              event.currentTarget.releasePointerCapture(event.pointerId)
            }
            onMaterialPreviewEnd()
          }}
          onPointerCancel={(event) => {
            event.stopPropagation()
            onMaterialPreviewEnd()
          }}
          onPointerLeave={onMaterialPreviewEnd}
          onLostPointerCapture={onMaterialPreviewEnd}
          onClick={(event) => event.stopPropagation()}
        >
          <PackageOpen size={10} />{materialInputs.length}
        </button>
      ) : null}
      {node.disabled ? <em>已禁用</em> : null}
    </article>
  )
}

interface DragSession {
  pointerId: number
  startClientX: number
  startClientY: number
  nodeUuids: string[]
  initialOffsets: Record<string, WorkflowNodeOffset>
  bounds: { minX: number; minY: number; maxX: number; maxY: number }
}

type DragBounds = DragSession['bounds']

export function WorkflowDag({
  nodes,
  edges,
  loading,
  error,
}: {
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
  loading: boolean
  error: boolean
}) {
  const regionRef = useRef<HTMLElement>(null)
  const dragSessionRef = useRef<DragSession | null>(null)
  const [ranksPerBand, setRanksPerBand] = useState(4)
  const [viewMode, setViewMode] = useState<'focus' | 'complete'>('focus')
  const [selectedEdgeUuid, setSelectedEdgeUuid] = useState<string>()
  const [nodeOffsets, setNodeOffsets] = useState<Record<string, WorkflowNodeOffset>>({})
  const [draggingNodeUuids, setDraggingNodeUuids] = useState<Set<string>>(new Set())
  const [previewMaterialUuids, setPreviewMaterialUuids] = useState<Set<string>>(new Set())
  const graphProjection = useMemo(() => {
    const nodeByUuid = new Map(nodes.map((node) => [node.uuid, node]))
    const materialNodes = nodes
      .filter((node) => node.kind === 'material_source')
      .sort((left, right) => (
        (left.authoringOrder ?? Number.MAX_SAFE_INTEGER) - (right.authoringOrder ?? Number.MAX_SAFE_INTEGER)
          || left.uuid.localeCompare(right.uuid)
      ))
    const materialNodeUuids = new Set(materialNodes.map((node) => node.uuid))
    const materialEdges = edges.filter((edge) => (
      materialNodeUuids.has(edge.sourceNodeUuid) || materialNodeUuids.has(edge.targetNodeUuid)
    ))
    const materialEdgeUuids = new Set(materialEdges.map((edge) => edge.uuid))
    const workflowEdges = edges.filter((edge) => !materialEdgeUuids.has(edge.uuid))
    const materialInputsByTarget = new Map<string, WorkflowGraphNode[]>()
    materialEdges.forEach((edge) => {
      const material = nodeByUuid.get(edge.sourceNodeUuid)
      if (!material || material.kind !== 'material_source') return
      const inputs = materialInputsByTarget.get(edge.targetNodeUuid) || []
      inputs.push(material)
      materialInputsByTarget.set(edge.targetNodeUuid, inputs)
    })
    const edgeKindByUuid = new Map(edges.map((edge) => [edge.uuid, classifyEdge(edge, nodeByUuid)]))
    const parallelControlCount = workflowEdges.filter((edge) => {
      const kind = edgeKindByUuid.get(edge.uuid)
      return kind === 'parallel_entry' || kind === 'parallel_join'
    }).length
    return {
      nodeByUuid,
      materialNodes,
      materialEdges,
      materialInputsByTarget,
      workflowNodes: nodes.filter((node) => node.kind !== 'material_source'),
      workflowEdges,
      edgeKindByUuid,
      parallelControlCount,
      dependencyCount: workflowEdges.length - parallelControlCount,
    }
  }, [edges, nodes])
  const visibleNodes = viewMode === 'focus' ? graphProjection.workflowNodes : nodes
  const visibleEdges = viewMode === 'focus' ? graphProjection.workflowEdges : edges
  const automaticLayout = useMemo(
    () => layoutWorkflowGraph(visibleNodes, visibleEdges, { ranksPerBand }),
    [ranksPerBand, visibleEdges, visibleNodes],
  )
  const layout = useMemo(
    () => applyWorkflowNodeOffsets(automaticLayout, nodeOffsets),
    [automaticLayout, nodeOffsets],
  )
  const selectedEdge = selectedEdgeUuid
    ? edges.find((edge) => edge.uuid === selectedEdgeUuid)
    : undefined
  const selectedSource = selectedEdge ? graphProjection.nodeByUuid.get(selectedEdge.sourceNodeUuid) : undefined
  const selectedTarget = selectedEdge ? graphProjection.nodeByUuid.get(selectedEdge.targetNodeUuid) : undefined
  const selectedKind = selectedEdge ? graphProjection.edgeKindByUuid.get(selectedEdge.uuid) : undefined
  const hasCustomLayout = Object.keys(nodeOffsets).length > 0

  useEffect(() => {
    const region = regionRef.current
    if (!region) return
    const updateRanksPerBand = () => {
      if (!region.clientWidth) return
      setRanksPerBand(Math.max(2, Math.min(6, Math.floor((region.clientWidth + 76) / (188 + 76)))))
    }
    updateRanksPerBand()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(updateRanksPerBand)
    observer.observe(region)
    return () => observer.disconnect()
  }, [])

  const selectEdge = (edgeUuid: string) => {
    setSelectedEdgeUuid((current) => current === edgeUuid ? undefined : edgeUuid)
  }

  const selectEdgeWithKeyboard = (event: KeyboardEvent<SVGGElement>, edgeUuid: string) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    selectEdge(edgeUuid)
  }

  const startDrag = (
    event: PointerEvent<HTMLElement>,
    requestedNodeUuids: string[],
    requestedBounds?: DragBounds,
  ) => {
    if (event.button !== 0 || !requestedNodeUuids.length) return
    event.preventDefault()
    const draggedNodes = layout.nodes.filter((node) => requestedNodeUuids.includes(node.node.uuid))
    if (!draggedNodes.length && !requestedBounds) return
    const initialOffsets = Object.fromEntries(requestedNodeUuids.map((uuid) => [
      uuid,
      nodeOffsets[uuid] || { x: 0, y: 0 },
    ]))
    dragSessionRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      nodeUuids: requestedNodeUuids,
      initialOffsets,
      bounds: requestedBounds || {
        minX: Math.min(...draggedNodes.map((node) => node.x)),
        minY: Math.min(...draggedNodes.map((node) => node.y)),
        maxX: Math.max(...draggedNodes.map((node) => node.x + node.width)),
        maxY: Math.max(...draggedNodes.map((node) => node.y + node.height)),
      },
    }
    setDraggingNodeUuids(new Set(requestedNodeUuids))
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  const startNodeDrag = (event: PointerEvent<HTMLElement>, positioned: PositionedWorkflowNode) => {
    const group = positioned.groupUuid
      ? automaticLayout.groups.find((candidate) => candidate.node.uuid === positioned.groupUuid)
      : undefined
    startDrag(event, group?.children.map((child) => child.node.uuid) || [positioned.node.uuid])
  }

  const startGroupDrag = (event: PointerEvent<HTMLElement>, groupUuid: string) => {
    const group = layout.groups.find((candidate) => candidate.node.uuid === groupUuid)
    if (!group) return
    const childUuids = group.children.map((child) => child.node.uuid)
    const frameBounds = group.frames.length
      ? {
          minX: Math.min(...group.frames.map((frame) => frame.x)),
          minY: Math.min(...group.frames.map((frame) => frame.y)),
          maxX: Math.max(...group.frames.map((frame) => frame.x + frame.width)),
          maxY: Math.max(...group.frames.map((frame) => frame.y + frame.height)),
        }
      : undefined
    startDrag(event, childUuids.length ? childUuids : [groupUuid], childUuids.length ? undefined : frameBounds)
  }

  const moveDrag = (event: PointerEvent<HTMLElement>) => {
    const session = dragSessionRef.current
    if (!session || session.pointerId !== event.pointerId) return
    const requestedX = event.clientX - session.startClientX
    const requestedY = event.clientY - session.startClientY
    const deltaX = Math.max(-session.bounds.minX, Math.min(layout.width - session.bounds.maxX, requestedX))
    const deltaY = Math.max(-session.bounds.minY, Math.min(layout.height - session.bounds.maxY, requestedY))
    setNodeOffsets((current) => {
      const next = { ...current }
      session.nodeUuids.forEach((uuid) => {
        const initial = session.initialOffsets[uuid]
        next[uuid] = { x: initial.x + deltaX, y: initial.y + deltaY }
      })
      return next
    })
  }

  const endDrag = (event: PointerEvent<HTMLElement>) => {
    const session = dragSessionRef.current
    if (!session || session.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    dragSessionRef.current = null
    setDraggingNodeUuids(new Set())
    setPreviewMaterialUuids(new Set())
  }

  const changeViewMode = () => {
    setViewMode((current) => current === 'focus' ? 'complete' : 'focus')
    setNodeOffsets({})
    setDraggingNodeUuids(new Set())
    dragSessionRef.current = null
  }

  const endpointFor = (nodeUuid: string): EdgeEndpoint | undefined => {
    if (selectedEdge?.sourceNodeUuid === nodeUuid) return 'source'
    if (selectedEdge?.targetNodeUuid === nodeUuid) return 'target'
    return undefined
  }

  const clearEdgeSelectionFromBlankSpace = (event: MouseEvent<HTMLElement>) => {
    const target = event.target
    if (!(target instanceof Element)) return
    if (target.closest('.workflow-dag-edge-control, .workflow-dag-node, .workflow-dag-group-frame')) return
    setSelectedEdgeUuid(undefined)
  }

  return (
    <section ref={regionRef} className="workflow-dag-region" role="region" aria-label="发布修订拓扑" tabIndex={0}>
      <div className="workflow-dag-summary">
        <span><strong>{nodes.length}</strong> 个节点</span>
        <span><strong>{edges.length}</strong> 条权威连线</span>
        <span><strong>{layout.groups.length}</strong> 个分组</span>
        {viewMode === 'focus' ? (
          <span>{graphProjection.dependencyCount} 条流程依赖 · {graphProjection.parallelControlCount} 条并行控制 · {graphProjection.materialEdges.length} 条物料输入</span>
        ) : null}
        <em>蛇形布局 · 每行最多 {layout.ranksPerBand} 个拓扑层</em>
        <button
          type="button"
          className="workflow-dag-view-toggle"
          aria-label="恢复自动布局"
          disabled={!hasCustomLayout}
          onClick={() => setNodeOffsets({})}
        >
          <RotateCcw size={13} />恢复布局
        </button>
        <button
          type="button"
          className="workflow-dag-view-toggle"
          aria-label={viewMode === 'focus' ? '查看完整 DAG' : '查看主流程'}
          onClick={changeViewMode}
        >
          {viewMode === 'focus' ? <Network size={13} /> : <ListTree size={13} />}
          {viewMode === 'focus' ? '完整 DAG' : '主流程'}
        </button>
      </div>
      <div className="workflow-dag-legend" aria-label="连线图例">
        <span className="dependency">流程依赖</span>
        <span className="material">物料输入</span>
        <span className="parallel">并行入口 / 汇合</span>
        <small>点击连线查看两端节点 · 拖动分组内任一节点会移动整个分组</small>
      </div>
      {selectedEdge && selectedSource && selectedTarget && selectedKind ? (
        <div className={`workflow-dag-relation ${selectedKind}`} role="status">
          <strong>{edgeKindLabels[selectedKind]}</strong>
          <span>{selectedSource.name} → {selectedTarget.name}</span>
          <small>{edgeDescription(selectedKind)}</small>
          <button type="button" aria-label="清除连线选择" onClick={() => setSelectedEdgeUuid(undefined)}><X size={13} /></button>
        </div>
      ) : null}
      {loading && !nodes.length ? <div className="workflow-dag-state">正在计算拓扑布局…</div> : null}
      {error ? <div className="workflow-dag-state error"><TriangleAlert size={16} />工作流图读取失败，未生成推测拓扑。</div> : null}
      {!loading && !error && !nodes.length ? <div className="workflow-dag-state">该修订没有工作流节点。</div> : null}
      {nodes.length ? (
        <>
          {viewMode === 'focus' && graphProjection.materialNodes.length ? (
            <section className="workflow-dag-materials" role="region" aria-label="物料输入">
              <div className="workflow-dag-materials-heading">
                <span><PackageOpen size={14} /><strong>物料输入</strong></span>
                <small>{graphProjection.materialNodes.length} 个来源 · 在消费节点上就近标记，不绘制跨画布长线</small>
              </div>
              <div className="workflow-dag-material-list">
                {graphProjection.materialNodes.map((material) => {
                  const outgoingEdges = graphProjection.materialEdges.filter((edge) => edge.sourceNodeUuid === material.uuid)
                  const endpoint = endpointFor(material.uuid)
                  return (
                    <article
                      className={`workflow-dag-material ${endpoint ? `edge-${endpoint}` : ''} ${previewMaterialUuids.has(material.uuid) ? 'material-preview' : ''}`}
                      aria-label={[material.name, '物料源', endpoint === 'source' ? '已选连线起点' : endpoint === 'target' ? '已选连线终点' : undefined].filter(Boolean).join('，')}
                      data-node-uuid={material.uuid}
                      key={material.uuid}
                    >
                      <CircleDot size={13} />
                      <div><strong>{material.name}</strong><small>物料源</small></div>
                      <div className="workflow-dag-material-targets">
                        {outgoingEdges.map((edge) => {
                          const target = graphProjection.nodeByUuid.get(edge.targetNodeUuid)
                          const targetName = target?.authoringResultName || target?.name || edge.targetNodeUuid
                          const label = `${edgeKindLabels.material}：${material.name} → ${target?.name || targetName}`
                          return (
                            <button
                              className={selectedEdgeUuid === edge.uuid ? 'selected' : ''}
                              data-edge-uuid={edge.uuid}
                              key={edge.uuid}
                              type="button"
                              aria-label={label}
                              aria-pressed={selectedEdgeUuid === edge.uuid}
                              title={`输入到 ${targetName}`}
                              onClick={() => selectEdge(edge.uuid)}
                            >→ {targetName}</button>
                          )
                        })}
                        {!outgoingEdges.length ? <span>未连接</span> : null}
                      </div>
                    </article>
                  )
                })}
              </div>
            </section>
          ) : null}
          <div className="workflow-dag-scroll" onClick={clearEdgeSelectionFromBlankSpace}>
            <div
              className="workflow-dag-stage"
              style={{ width: layout.width, height: layout.height }}
            >
              <svg className="workflow-dag-edges" width={layout.width} height={layout.height}>
                <defs>
                  {(['dependency', 'material', 'parallel_entry', 'parallel_join'] as WorkflowEdgeKind[]).map((kind) => (
                    <marker
                      className={`workflow-dag-arrow ${kind}`}
                      id={`workflow-dag-arrow-${kind}`}
                      key={kind}
                      viewBox="0 0 8 8"
                      refX="7"
                      refY="4"
                      markerWidth="7"
                      markerHeight="7"
                      orient="auto-start-reverse"
                    >
                      <path d="M 0 0 L 8 4 L 0 8 z" />
                    </marker>
                  ))}
                </defs>
                {layout.edges.map(({ edge, source, target, path }) => {
                  const kind = graphProjection.edgeKindByUuid.get(edge.uuid) || 'dependency'
                  const selected = edge.uuid === selectedEdgeUuid
                  const label = `${edgeKindLabels[kind]}：${source.node.name} → ${target.node.name}`
                  return (
                    <g
                      className={`workflow-dag-edge-control ${kind} ${selected ? 'selected' : ''}`}
                      data-edge-uuid={edge.uuid}
                      key={edge.uuid}
                      role="button"
                      tabIndex={0}
                      aria-label={label}
                      aria-pressed={selected}
                      onClick={() => selectEdge(edge.uuid)}
                      onKeyDown={(event) => selectEdgeWithKeyboard(event, edge.uuid)}
                    >
                      <path className="workflow-dag-edge-hit" d={path} aria-hidden="true" />
                      <path
                        className="workflow-dag-edge"
                        d={path}
                        markerEnd={`url(#workflow-dag-arrow-${kind})`}
                        aria-hidden="true"
                      />
                    </g>
                  )
                })}
              </svg>
              {layout.groups.map((group) => (
                <div
                  className={`workflow-dag-group ${draggingNodeUuids.has(group.node.uuid) || group.children.some((child) => draggingNodeUuids.has(child.node.uuid)) ? 'dragging' : ''}`}
                  data-node-uuid={group.node.uuid}
                  data-rank={group.rank}
                  data-band={group.band}
                  key={group.node.uuid}
                  role="group"
                  aria-label={`分组：${group.node.name}`}
                >
                  {group.frames.map((frame, index) => (
                    <div
                      className="workflow-dag-group-frame"
                      key={`${group.node.uuid}-${index}`}
                      onPointerDown={(event) => startGroupDrag(event, group.node.uuid)}
                      onPointerMove={moveDrag}
                      onPointerUp={endDrag}
                      onPointerCancel={endDrag}
                      style={{ left: frame.x, top: frame.y, width: frame.width, height: frame.height }}
                    >
                      <div className="workflow-dag-group-title"><Layers3 size={14} /><strong>{group.node.name}</strong></div>
                    </div>
                  ))}
                  <span className="sr-only">{group.children.map((child) => child.node.name).join('、')}</span>
                </div>
              ))}
              {layout.nodes.map((positioned) => {
                const endpoint = endpointFor(positioned.node.uuid)
                const materialInputs = viewMode === 'focus'
                  ? graphProjection.materialInputsByTarget.get(positioned.node.uuid)
                  : undefined
                return (
                  <WorkflowNodeCard
                    key={positioned.node.uuid}
                    positioned={positioned}
                    materialInputs={materialInputs}
                    edgeEndpoint={endpoint}
                    dimmed={Boolean(selectedEdge) && !endpoint}
                    dragging={draggingNodeUuids.has(positioned.node.uuid)}
                    onPointerDown={(event) => startNodeDrag(event, positioned)}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    previewingMaterials={Boolean(materialInputs?.some((material) => previewMaterialUuids.has(material.uuid)))}
                    onMaterialPreviewStart={(materialUuids) => setPreviewMaterialUuids(new Set(materialUuids))}
                    onMaterialPreviewEnd={() => setPreviewMaterialUuids(new Set())}
                  />
                )
              })}
            </div>
          </div>
        </>
      ) : null}
      {layout.warnings.length ? (
        <div className="workflow-dag-warning"><TriangleAlert size={14} />{layout.warnings.join(' ')}</div>
      ) : null}
    </section>
  )
}
