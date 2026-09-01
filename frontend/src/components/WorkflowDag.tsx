import { useEffect, useMemo, useRef, useState } from 'react'
import { Braces, CircleDot, Layers3, TriangleAlert } from 'lucide-react'
import { layoutWorkflowGraph, type PositionedWorkflowNode } from '../lib/workflowGraphLayout'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'

function nodeTypeLabel(node: WorkflowGraphNode) {
  if (node.kind === 'material_source') return '物料源'
  if (node.kind === 'group') return '分组'
  return node.deviceId || node.type || '工作流节点'
}

function WorkflowNodeCard({ positioned }: { positioned: PositionedWorkflowNode }) {
  const { node } = positioned
  const isMaterial = node.kind === 'material_source'
  const typeLabel = nodeTypeLabel(node)
  return (
    <article
      className={`workflow-dag-node ${isMaterial ? 'material-source' : ''} ${node.disabled ? 'disabled' : ''}`}
      aria-label={`${node.name}，${typeLabel}`}
      data-node-uuid={node.uuid}
      data-rank={positioned.rank}
      data-band={positioned.band}
      style={{ left: positioned.x, top: positioned.y, width: positioned.width, height: positioned.height }}
      title={`${node.name}\n${typeLabel}\n${node.uuid}`}
    >
      <span className="workflow-dag-node-icon">
        {isMaterial ? <CircleDot size={17} /> : <Braces size={17} />}
      </span>
      <span className="workflow-dag-node-copy">
        <strong>{node.name}</strong>
        <small>{typeLabel}</small>
      </span>
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
  const layout = useMemo(
    () => layoutWorkflowGraph(nodes, edges, { ranksPerBand }),
    [nodes, edges, ranksPerBand],
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
        <span><strong>{layout.edges.length}</strong> 条依赖</span>
        <span><strong>{layout.groups.length}</strong> 个分组</span>
        <em>蛇形布局 · 每行最多 {layout.ranksPerBand} 个拓扑层</em>
      </div>
      {loading && !nodes.length ? <div className="workflow-dag-state">正在计算拓扑布局…</div> : null}
      {error ? <div className="workflow-dag-state error"><TriangleAlert size={16} />工作流图读取失败，未生成推测拓扑。</div> : null}
      {!loading && !error && !nodes.length ? <div className="workflow-dag-state">该修订没有工作流节点。</div> : null}
      {nodes.length ? (
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
              <WorkflowNodeCard key={node.node.uuid} positioned={node} />
            ))}
          </div>
        </div>
      ) : null}
      {layout.warnings.length ? (
        <div className="workflow-dag-warning"><TriangleAlert size={14} />{layout.warnings.join(' ')}</div>
      ) : null}
    </section>
  )
}
