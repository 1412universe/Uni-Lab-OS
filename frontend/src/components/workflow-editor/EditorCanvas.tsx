import { useCallback, useEffect, useRef } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnSelectionChangeParams,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { NodeTemplateSummary } from '../../lib/edgeClient'
import type { EditorEdge, EditorNode } from '../../lib/workflowEditorModel'
import { EditorNodeCard } from './EditorNodeCard'

const nodeTypes = { unilab: EditorNodeCard }

interface EditorCanvasProps {
  nodes: EditorNode[]
  edges: EditorEdge[]
  templates: NodeTemplateSummary[]
  dataPortMode: boolean
  onToggleDataPortMode: () => void
  onNodesChange: (changes: NodeChange<EditorNode>[]) => void
  onEdgesChange: (changes: EdgeChange<EditorEdge>[]) => void
  onConnect: (connection: Connection) => void
  onValidateConnection: (connection: Connection | EditorEdge) => boolean
  onSelect: (selection: OnSelectionChangeParams) => void
  onDropTemplate: (templateUuid: string, position: { x: number; y: number }) => void
  onLayout: () => void
  /** 布局计数器；变化后自动 fitView，否则节点飞走了视口还停在原地。 */
  layoutNonce: number
}

function CanvasInner({
  nodes,
  edges,
  templates,
  dataPortMode,
  onToggleDataPortMode,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onValidateConnection,
  onSelect,
  onDropTemplate,
  onLayout,
  layoutNonce,
}: EditorCanvasProps) {
  const wrapper = useRef<HTMLDivElement | null>(null)
  const { screenToFlowPosition, fitView, zoomIn, zoomOut } = useReactFlow()

  // 布局只改节点坐标，视口不会跟着动。等 React Flow 量完新尺寸再补一次 fitView，
  // 否则用户看到的是「点了按钮什么也没发生」。
  useEffect(() => {
    if (!layoutNonce) return
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.12, duration: 260 })
    }, 60)
    return () => window.clearTimeout(timer)
  }, [fitView, layoutNonce])

  const handleDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    const templateUuid = event.dataTransfer.getData('application/unilab-node-template')
    if (!templateUuid) return
    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
    onDropTemplate(templateUuid, position)
  }, [onDropTemplate, screenToFlowPosition])

  const handleDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes('application/unilab-node-template')) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  return (
    <div className={`wf-canvas ${dataPortMode ? 'is-data-mode' : ''}`} ref={wrapper}>
      <ReactFlow<EditorNode, EditorEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={onValidateConnection}
        onSelectionChange={onSelect}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        deleteKeyCode={null}
        selectionOnDrag
        multiSelectionKeyCode={['Shift', 'Meta', 'Control']}
        minZoom={0.05}
        maxZoom={2}
        fitView
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ animated: false }}
        connectionLineStyle={{ strokeWidth: 1.6 }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#d0d5dd" />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          nodeColor={(node) => {
            const kind = String((node.data as EditorNode['data'] | undefined)?.raw?.kind || 'action')
            if (kind === 'condition') return '#b96b10'
            if (kind === 'repeat_until') return '#7657d8'
            return '#3b67f2'
          }}
          maskColor="rgba(243,245,248,.72)"
        />
        <div className="wf-canvas-tools" role="toolbar" aria-label="画布视图">
          <button type="button" onClick={() => void zoomIn()} title="放大">＋</button>
          <button type="button" onClick={() => void zoomOut()} title="缩小">−</button>
          <button type="button" onClick={() => void fitView({ padding: 0.15 })} title="适应画布">⤢</button>
          <button type="button" onClick={onLayout} title="按依赖分层重新排布">自动布局</button>
          <button
            type="button"
            className={`wf-tool-toggle ${dataPortMode ? 'is-on' : ''}`}
            onClick={onToggleDataPortMode}
            title="展开每个节点的参数端口，可直接拖出数据线"
            aria-pressed={dataPortMode}
          >
            参数连线
          </button>
          <span>{nodes.length} 节点 · {edges.length} 连线 · {templates.length} 模板</span>
        </div>
        <div className="wf-canvas-legend" aria-hidden="true">
          <span className="wf-legend-line is-sequence" />执行顺序
          <span className="wf-legend-line is-data" />数据传递
          {dataPortMode ? <b>拖端口连线 · 同参数名才能接</b> : <b>点「参数连线」展开端口</b>}
        </div>
      </ReactFlow>
    </div>
  )
}

/** 画布容器；`ReactFlowProvider` 必须包在取 `useReactFlow` 的子组件外层。 */
export function EditorCanvas(props: EditorCanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  )
}
