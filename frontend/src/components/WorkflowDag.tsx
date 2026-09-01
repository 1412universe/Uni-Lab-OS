import { useEffect, useMemo, useRef, useState } from 'react'
import { Braces, CircleDot, Layers3, ListTree, Network, PackageOpen, TriangleAlert } from 'lucide-react'
import { layoutWorkflowGraph, type PositionedWorkflowNode } from '../lib/workflowGraphLayout'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'

function nodeTypeLabel(node: WorkflowGraphNode) {
  if (node.kind === 'material_source') return '物料源'
  if (node.kind === 'group') return '分组'
  return node.deviceId || node.type || '工作流节点'
}

function WorkflowNodeCard({
  positioned,
  materialInputs = [],
}: {
  positioned: PositionedWorkflowNode
  materialInputs?: WorkflowGraphNode[]
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
  return (
    <article
      className={`workflow-dag-node ${isMaterial ? 'material-source' : ''} ${node.disabled ? 'disabled' : ''}`}
      aria-label={[node.name, ...accessibleDetails, accessibleMaterialInputs].filter(Boolean).join('，')}
      data-node-uuid={node.uuid}
      data-rank={positioned.rank}
      data-band={positioned.band}
      style={{ left: positioned.x, top: positioned.y, width: positioned.width, height: positioned.height }}
      title={`${node.name}\n${alias}\n${typeLabel}\n${node.uuid}`}
    >
      <span className="workflow-dag-node-icon">
        {isMaterial ? <CircleDot size={17} /> : <Braces size={17} />}
      </span>
      <span className="workflow-dag-node-copy">
        <strong>{node.name}</strong>
        <small>{order}{alias}</small>
      </span>
      {materialInputs.length ? (
        <span
          className="workflow-dag-node-materials"
          aria-label={`${materialInputs.length} 个物料输入：${materialInputs.map((material) => material.name).join('、')}`}
          title={materialInputs.map((material) => material.name).join('、')}
        >
          <PackageOpen size={10} />{materialInputs.length}
        </span>
      ) : null}
      {node.disabled ? <em>已禁用</em> : null}
    </article>
  )
}

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
  const [ranksPerBand, setRanksPerBand] = useState(4)
  const [viewMode, setViewMode] = useState<'focus' | 'complete'>('focus')
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
    const workflowEdges = edges.filter((edge) => !materialEdges.includes(edge))
    const materialInputsByTarget = new Map<string, WorkflowGraphNode[]>()
    materialEdges.forEach((edge) => {
      const material = nodeByUuid.get(edge.sourceNodeUuid)
      if (!material || material.kind !== 'material_source') return
      const inputs = materialInputsByTarget.get(edge.targetNodeUuid) || []
      inputs.push(material)
      materialInputsByTarget.set(edge.targetNodeUuid, inputs)
    })
    return {
      nodeByUuid,
      materialNodes,
      materialEdges,
      materialInputsByTarget,
      workflowNodes: nodes.filter((node) => node.kind !== 'material_source'),
      workflowEdges,
    }
  }, [edges, nodes])
  const visibleNodes = viewMode === 'focus' ? graphProjection.workflowNodes : nodes
  const visibleEdges = viewMode === 'focus' ? graphProjection.workflowEdges : edges
  const layout = useMemo(
    () => layoutWorkflowGraph(visibleNodes, visibleEdges, { ranksPerBand }),
    [ranksPerBand, visibleEdges, visibleNodes],
  )

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

  return (
    <section ref={regionRef} className="workflow-dag-region" role="region" aria-label="发布修订拓扑" tabIndex={0}>
      <div className="workflow-dag-summary">
        <span><strong>{nodes.length}</strong> 个节点</span>
        <span><strong>{edges.length}</strong> 条权威依赖</span>
        <span><strong>{layout.groups.length}</strong> 个分组</span>
        {viewMode === 'focus' ? (
          <span>{graphProjection.workflowEdges.length} 条流程依赖 · {graphProjection.materialEdges.length} 条物料输入</span>
        ) : null}
        <em>蛇形布局 · 每行最多 {layout.ranksPerBand} 个拓扑层</em>
        <button
          type="button"
          className="workflow-dag-view-toggle"
          aria-label={viewMode === 'focus' ? '查看完整 DAG' : '查看主流程'}
          onClick={() => setViewMode((current) => current === 'focus' ? 'complete' : 'focus')}
        >
          {viewMode === 'focus' ? <Network size={13} /> : <ListTree size={13} />}
          {viewMode === 'focus' ? '完整 DAG' : '主流程'}
        </button>
      </div>
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
                  return (
                    <article
                      className="workflow-dag-material"
                      aria-label={`${material.name}，物料源`}
                      data-node-uuid={material.uuid}
                      key={material.uuid}
                    >
                      <CircleDot size={13} />
                      <div><strong>{material.name}</strong><small>物料源</small></div>
                      <div className="workflow-dag-material-targets">
                        {outgoingEdges.map((edge) => {
                          const target = graphProjection.nodeByUuid.get(edge.targetNodeUuid)
                          const targetName = target?.authoringResultName || target?.name || edge.targetNodeUuid
                          return (
                            <span
                              data-edge-uuid={edge.uuid}
                              key={edge.uuid}
                              role="img"
                              aria-label={`依赖：${material.name} → ${target?.name || targetName}`}
                              title={`输入到 ${targetName}`}
                            >→ {targetName}</span>
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
          <div className="workflow-dag-scroll">
          <div className="workflow-dag-stage" style={{ width: layout.width, height: layout.height }}>
            <svg className="workflow-dag-edges" width={layout.width} height={layout.height}>
              <defs>
                <marker id="workflow-dag-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M 0 0 L 8 4 L 0 8 z" />
                </marker>
              </defs>
              {layout.edges.map(({ edge, source, target, path }) => (
                <path
                  className="workflow-dag-edge"
                  d={path}
                  data-edge-uuid={edge.uuid}
                  key={edge.uuid}
                  markerEnd="url(#workflow-dag-arrow)"
                  role="img"
                  aria-label={`依赖：${source.node.name} → ${target.node.name}`}
                />
              ))}
            </svg>
            {layout.groups.map((group) => (
              <div
                className="workflow-dag-group"
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
                    style={{ left: frame.x, top: frame.y, width: frame.width, height: frame.height }}
                  >
                    <div className="workflow-dag-group-title"><Layers3 size={14} /><strong>{group.node.name}</strong></div>
                  </div>
                ))}
                <span className="sr-only">{group.children.map((child) => child.node.name).join('、')}</span>
              </div>
            ))}
            {layout.nodes.map((node) => (
              <WorkflowNodeCard
                key={node.node.uuid}
                positioned={node}
                materialInputs={viewMode === 'focus' ? graphProjection.materialInputsByTarget.get(node.node.uuid) : undefined}
              />
            ))}
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
