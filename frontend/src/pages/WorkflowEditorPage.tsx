import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnSelectionChangeParams,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  CircleSlash,
  Copy,
  FileJson,
  FlaskConical,
  Hammer,
  Layers,
  Play,
  Plus,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Undo2,
  Redo2,
  X,
} from 'lucide-react'
import { Button, EmptyState, Panel, PanelHeader } from '../components/ui'
import { BatchTransferPanel } from '../components/workflow-editor/BatchTransferPanel'
import { EditorCanvas } from '../components/workflow-editor/EditorCanvas'
import { NodeLibraryPanel } from '../components/workflow-editor/NodeLibraryPanel'
import { PropertyPanel } from '../components/workflow-editor/PropertyPanel'
import { SideDrawer } from '../components/workflow-editor/SideDrawer'
import { WorkflowPicker } from '../components/workflow-editor/WorkflowPicker'
import { TemplateInsertPanel } from '../components/workflow-editor/TemplateInsertPanel'
import { WorkflowAgentPanel } from '../components/workflow-editor/WorkflowAgentPanel'
import { WorkflowRunDrawer } from '../components/workflow-editor/WorkflowRunDrawer'
import { NodeActionsContext } from '../components/workflow-editor/nodeActions'
import {
  EdgeApiError,
  commandWorkflowTask,
  createWorkflowDefinition,
  loadAllNodeTemplates,
  loadNodeTemplateDetail,
  loadActionMode,
  loadRunTask,
  loadWorkflowGraphPreflight,
  deleteWorkflow,
  duplicateWorkflow,
  loadWorkflowGraphRaw,
  renameWorkflow,
  saveCanvasLayout,
  saveWorkflowGraph,
  startWorkflowRun,
  type NodeTemplateHandle,
  type NodeTemplateSummary,
  type RawWorkflowEdge,
  type RawWorkflowNode,
} from '../lib/edgeClient'
import {
  buildHandleKindIndex,
  buildLinkedHandleIndex,
  buildNewRawNode,
  canConnectHandles,
  cloneGraphInto,
  layeredLayout,
  localGraphIssues,
  needsAutoLayout,
  nextAuthoringOrder,
  pushHistory,
  toFlowEdges,
  toFlowNodes,
  toRawEdges,
  toRawNodes,
  type EditorEdge,
  type EditorHistory,
  type EditorNode,
  type EditorSnapshot,
} from '../lib/workflowEditorModel'
import type { WorkflowDefinition, WorkflowTask } from '../types'
import {
  RUN_STATE_LABEL,
  buildRunNodeViews,
  formatRunDuration,
  isTaskTerminal,
  runStateIndex,
  type RunNodeView,
} from '../lib/workflowRunModel'
import './WorkflowEditorPage.css'

/**
 * 3D 仿真用 three.js 渲染，体积约 600KB。它只在用户点「3D 仿真」时才需要，
 * 因此走 lazy 分包，避免拖慢编辑器首屏。
 */
const SimulationSpace = lazy(() => import('../components/workflow-editor/SimulationSpace'))

interface WorkflowEditorPageProps {
  workflows: WorkflowDefinition[]
  connected: boolean
  onNotify: (message: string) => void
  /** 导入后让上层重新拉一次 Edge 快照，工作流下拉框才会出现新条目。 */
  onRefresh?: () => void
}

interface PreflightCheck {
  code: string
  status: string
  message: string
  blocking?: boolean
  nodeName?: string
}

/** 生成节点/连线 UUID；老浏览器与 jsdom 环境退回手工构造。 */
function newUuid(): string {
  const source = globalThis.crypto
  if (source && typeof source.randomUUID === 'function') return source.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const random = Math.floor(Math.random() * 16)
    const value = char === 'x' ? random : (random & 0x3) | 0x8
    return value.toString(16)
  })
}

/** 节点徽标的悬浮说明：优先耗时，其次等待原因，最后只给状态名。 */
function describeRunNode(view: RunNodeView): string {
  const parts = [RUN_STATE_LABEL[view.state]]
  if (view.startedMs !== undefined) {
    parts.push(`开始 ${new Date(view.startedMs).toLocaleTimeString('zh-CN', { hour12: false })}`)
  }
  if (view.durationMs !== undefined) parts.push(`耗时 ${formatRunDuration(view.durationMs)}`)
  if (view.waitReasonText) parts.push(view.waitReasonText)
  return parts.join(' · ')
}

function groupHandles(raw: Record<string, any>[]): Map<string, NodeTemplateHandle[]> {
  const map = new Map<string, NodeTemplateHandle[]>()
  for (const item of raw) {
    const templateUuid = String(item.workflow_node_template_uuid || '')
    if (!templateUuid) continue
    const unilab = (item.meta_data?.unilab && typeof item.meta_data.unilab === 'object')
      ? item.meta_data.unilab as Record<string, any>
      : {}
    const handle: NodeTemplateHandle = {
      uuid: String(item.uuid || ''),
      handleKey: String(item.handle_key || item.data_key || ''),
      ioType: String(item.io_type || ''),
      dataKey: String(item.data_key || item.handle_key || ''),
      dataSource: String(item.data_source || ''),
      displayName: String(item.display_name || item.handle_key || ''),
      type: String(item.type || ''),
      required: Boolean(item.required),
      editorControl: unilab.editor_control ? String(unilab.editor_control) : undefined,
      valueSchema: unilab.value_schema && typeof unilab.value_schema === 'object' ? unilab.value_schema : undefined,
      contractOrder: Number.isFinite(Number(unilab.contract_order)) ? Number(unilab.contract_order) : undefined,
    }
    const list = map.get(templateUuid) || []
    list.push(handle)
    map.set(templateUuid, list)
  }
  return map
}

/**
 * 工作流编辑器页面。
 *
 * 编辑模型是「本地草图 + 一次整图保存」：拖拽、连线、改参数都只动本地状态，
 * 点保存时才以一个 `PUT /workflows/{uuid}/graph` 事务提交，因此 revision 只在
 * 用户确认时推进一次，撤销栈也天然对齐。
 */
export function WorkflowEditorPage({ workflows, connected, onNotify, onRefresh }: WorkflowEditorPageProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const workflowUuid = searchParams.get('workflow') || ''

  const [nodes, setNodes, handleNodesChange] = useNodesState<EditorNode>([])
  const [edges, setEdges, handleEdgesChange] = useEdgesState<EditorEdge>([])
  const [templates, setTemplates] = useState<NodeTemplateSummary[]>([])
  const [templateMap, setTemplateMap] = useState<Map<string, NodeTemplateSummary>>(new Map())
  const [handlesByTemplate, setHandlesByTemplate] = useState<Map<string, NodeTemplateHandle[]>>(new Map())
  const [baselineNodes, setBaselineNodes] = useState<Map<string, RawWorkflowNode>>(new Map())
  const [baselineEdges, setBaselineEdges] = useState<Map<string, RawWorkflowEdge>>(new Map())
  const [history, setHistory] = useState<EditorHistory>({ past: [], future: [] })
  const [selection, setSelection] = useState<{ nodeId?: string; edgeId?: string; nodeIds?: string[] }>({})
  const [revision, setRevision] = useState(1)
  const [dirty, setDirty] = useState(false)
  /**
   * 只有坐标变了（自动布局 / 拖动节点），内容与后端已保存版本一致。
   *
   * 运行和预检针对的是已保存版本，纯排布变化不影响可执行语义，因此不该
   * 把「运行」按钮锁死——否则 CWM 导出的无坐标图一打开就永远跑不了。
   */
  const [layoutOnly, setLayoutOnly] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [libraryError, setLibraryError] = useState('')
  const [libraryLoading, setLibraryLoading] = useState(true)
  const [templatesReady, setTemplatesReady] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [newDialogOpen, setNewDialogOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const [preflightOpen, setPreflightOpen] = useState(false)
  const [preflightChecks, setPreflightChecks] = useState<PreflightCheck[]>([])
  const [preflightStatus, setPreflightStatus] = useState('')
  const [preflightPending, setPreflightPending] = useState(false)
  const [dataPortMode, setDataPortMode] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)
  const [insertOpen, setInsertOpen] = useState(false)
  const [simOpen, setSimOpen] = useState(false)
  /**
   * 后端实际的动作执行档位。
   *
   * `action_mode` 是**进程级**启动参数，运行期不可按任务切换；这里只做如实展示，
   * 不提供「切到仿真」的假开关（否则会让人误以为按了仿真就不动硬件）。
   */
  const [actionMode, setActionMode] = useState<'real' | 'simulate' | 'unknown'>('unknown')
  const [agentOpen, setAgentOpen] = useState(false)
  const [runTask, setRunTask] = useState<WorkflowTask | undefined>(undefined)
  const [runOpen, setRunOpen] = useState(false)
  const [runPending, setRunPending] = useState(false)
  const [runError, setRunError] = useState('')
  /** 每次布局 +1；画布收到后补一次 fitView（节点坐标变了视口不会自己跟）。 */
  const [layoutNonce, setLayoutNonce] = useState(0)
  /** 已提示过自动排布的工作流；同一份图不再重复弹。 */
  const layoutNotified = useRef<Set<string>>(new Set())

  /** `applyRawGraph` 里要用，但不想让它进依赖链：用 ref 读当前值。 */
  const dataPortModeRef = useRef(dataPortMode)
  dataPortModeRef.current = dataPortMode

  const baselineNodesRef = useRef(baselineNodes)
  const baselineEdgesRef = useRef(baselineEdges)
  baselineNodesRef.current = baselineNodes
  baselineEdgesRef.current = baselineEdges

  const pendingSnapshot = useRef<EditorSnapshot | null>(null)
  const snapshotTimer = useRef<number | undefined>(undefined)

  const currentNode = useMemo(
    () => (selection.nodeId ? nodes.find((node) => node.id === selection.nodeId) : undefined),
    [nodes, selection.nodeId],
  )
  // 刚创建的工作流还没进 Edge 快照，先在选择器里补一条占位，避免显示空白。
  const workflowOptions = useMemo(() => {
    if (!workflowUuid || workflows.some((item) => item.uuid === workflowUuid)) return workflows
    return [
      { uuid: workflowUuid, name: '当前工作流（尚未进入 Edge 列表）', revision, status: 'source' },
      ...workflows,
    ]
  }, [revision, workflowUuid, workflows])
  const currentEdge = useMemo(
    () => (selection.edgeId ? edges.find((edge) => edge.id === selection.edgeId) : undefined),
    [edges, selection.edgeId],
  )

  const snapshotNow = useCallback((): EditorSnapshot => ({
    nodes: toRawNodes(nodes, baselineNodesRef.current),
    edges: toRawEdges(edges, baselineEdgesRef.current),
  }), [nodes, edges])

  /** 内容变更（节点/连线/参数/启停）：必须保存后才能运行。 */
  const markDirty = useCallback(() => {
    setDirty(true)
    setLayoutOnly(false)
  }, [])

  /** 纯坐标变更：仍然要保存以写回 pose，但不阻塞运行与预检。 */
  const markLayoutOnly = useCallback(() => {
    setDirty(true)
    setLayoutOnly(true)
  }, [])

  /** 文本输入等高频编辑：合并成一次撤销记录，避免逐字符入栈。 */
  const beginEdit = useCallback(() => {
    if (!pendingSnapshot.current) pendingSnapshot.current = snapshotNow()
    if (snapshotTimer.current) window.clearTimeout(snapshotTimer.current)
    snapshotTimer.current = window.setTimeout(() => {
      if (pendingSnapshot.current) {
        const snapshot = pendingSnapshot.current
        pendingSnapshot.current = null
        setHistory((current) => pushHistory(current, snapshot))
      }
    }, 600)
  }, [snapshotNow])

  /** 结构变更（增删节点 / 连线 / 布局）：立即入栈。 */
  const commitEdit = useCallback(() => {
    if (snapshotTimer.current) window.clearTimeout(snapshotTimer.current)
    if (pendingSnapshot.current) {
      const snapshot = pendingSnapshot.current
      pendingSnapshot.current = null
      setHistory((current) => pushHistory(current, snapshot))
    }
  }, [])

  const applyRawGraph = useCallback((
    rawNodes: RawWorkflowNode[],
    rawEdges: RawWorkflowEdge[],
    templatesIndex: Map<string, NodeTemplateSummary>,
    handlesIndex: Map<string, NodeTemplateHandle[]>,
  ) => {
    const linked = buildLinkedHandleIndex(rawEdges)
    const kinds = buildHandleKindIndex(handlesIndex)
    const showPorts = dataPortModeRef.current
    const draftNodes = toFlowNodes(rawNodes, templatesIndex, handlesIndex, new Map(), linked, showPorts)
    const draftEdges = toFlowEdges(rawEdges, (uuid) => (uuid ? kinds.get(uuid) || 'sequence' : 'sequence'))
    const { nodeIssues } = localGraphIssues(draftNodes, draftEdges)
    setNodes(toFlowNodes(rawNodes, templatesIndex, handlesIndex, nodeIssues, linked, showPorts))
    setEdges(draftEdges)
  }, [setNodes, setEdges])

  // 参数连线模式切换：只改节点数据里的展示开关，不动原始图。
  useEffect(() => {
    setNodes((current) => current.map((node) => (
      node.data.showDataPorts === dataPortMode
        ? node
        : { ...node, data: { ...node.data, showDataPorts: dataPortMode } }
    )))
  }, [dataPortMode, setNodes])

  // 节点库：模板全量拉取一次即可，35 个模板的规模不值得做增量。
  useEffect(() => {
    const controller = new AbortController()
    setLibraryLoading(true)
    loadAllNodeTemplates(controller.signal)
      .then((items) => {
        setTemplates(items)
        setTemplateMap(new Map(items.map((item) => [item.uuid, item])))
        setTemplatesReady(true)
        setLibraryError('')
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLibraryError(error instanceof Error ? error.message : '节点模板读取失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLibraryLoading(false)
      })
    return () => controller.abort()
  }, [])

  /**
   * 把当前画布落点写回后端持久表。
   *
   * 为什么必须单独持久化：Local 模式的工作流定义目录是**进程内** `:memory:`，
   * 图由领域包里的 Python 源码在每次启动时重建，而生成器不写 `pose`。因此
   * `workflow_node.pose` 里的坐标活不过一次重启——这正是「每次切工作流都提示
   * 没有可用坐标」的根因：自动布局的结果从来没被真正写回。
   */
  const persistCanvasLayout = useCallback(async (
    uuid: string,
    laid: EditorNode[],
    options: { notifyOnError?: boolean } = {},
  ): Promise<boolean> => {
    if (!uuid || !laid.length) return false
    const positions: Record<string, { x: number; y: number }> = {}
    for (const node of laid) {
      positions[node.id] = {
        x: Math.round(node.position.x),
        y: Math.round(node.position.y),
      }
    }
    try {
      await saveCanvasLayout(uuid, positions)
      return true
    } catch (error) {
      if (options.notifyOnError !== false) {
        onNotify(`画布落点保存失败：${error instanceof Error ? error.message : '未知错误'}`)
      }
      return false
    }
  }, [onNotify])

  const loadGraph = useCallback(async (uuid: string) => {
    if (!uuid) return
    setLoading(true)
    setLoadError('')
    setConflict(false)
    try {
      const graph = await loadWorkflowGraphRaw(uuid)
      const graphHandles = groupHandles(graph.handleTemplates)
      const merged = new Map(handlesByTemplate)
      for (const [templateUuid, handles] of graphHandles) merged.set(templateUuid, handles)
      setHandlesByTemplate(merged)
      setBaselineNodes(new Map(graph.nodes.map((node) => [String(node.uuid), node])))
      setBaselineEdges(new Map(graph.edges.map((edge) => [String(edge.uuid), edge])))
      setRevision(graph.revision)
      setHistory({ past: [], future: [] })
      applyRawGraph(graph.nodes, graph.edges, templateMap, merged)
      // CWM 导出的图往往没有 `pose.position`，直接上画布会全部叠在原点。
      const probeNodes = toFlowNodes(
        graph.nodes, templateMap, merged, new Map(), buildLinkedHandleIndex(graph.edges),
        dataPortModeRef.current,
      )
      if (needsAutoLayout(probeNodes)) {
        const laid = layeredLayout(probeNodes, toFlowEdges(graph.edges))
        setNodes(laid)
        setLayoutNonce((current) => current + 1)
        // 立刻写回落点：写回成功后，下次切回来（以及重启之后）都不会再重排，
        // 也不会再弹这条提示。之前只标记「未保存」，用户不点保存就永远重排。
        const saved = await persistCanvasLayout(uuid, laid, { notifyOnError: false })
        if (saved) {
          // 内容与后端版本一致、坐标也已落盘，因此不算未保存改动，
          // 运行与预检都不该被它拦下。
          setDirty(false)
          setLayoutOnly(false)
          if (!layoutNotified.current.has(uuid)) {
            layoutNotified.current.add(uuid)
            onNotify('这些节点原本没有坐标，已按依赖分列排布并保存')
          }
        } else {
          // 落点写不回去时才需要用户介入：此时布局只在内存里，得靠整图保存带走。
          markLayoutOnly()
          if (!layoutNotified.current.has(uuid)) {
            layoutNotified.current.add(uuid)
            onNotify('这些节点没有可用坐标，已按依赖分列排布（未能写回，请点保存）')
          }
        }
      } else {
        setDirty(false)
        setLayoutOnly(false)
      }
      setSelection({})
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '工作流图读取失败')
    } finally {
      setLoading(false)
    }
  }, [applyRawGraph, handlesByTemplate, markLayoutOnly, onNotify, persistCanvasLayout, setNodes, templateMap])

  useEffect(() => {
    if (!workflowUuid) {
      setNodes([])
      setEdges([])
      setRevision(1)
      setDirty(false)
      setLayoutOnly(false)
      setHistory({ past: [], future: [] })
      return
    }
    // 等模板索引就绪后再读图：否则节点拿不到 template 投影，属性面板会缺设备名。
    if (!templatesReady) return
    void loadGraph(workflowUuid)
    // 仅在切换工作流或模板首次就绪时重载；后续模板变化不应覆盖本地草稿。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowUuid, templatesReady])

  /** 确保某模板的句柄已就绪；新拖入的模板可能不在初始图里。 */
  const ensureTemplateHandles = useCallback(async (templateUuid: string) => {
    if (handlesByTemplate.has(templateUuid)) {
      return { handles: handlesByTemplate.get(templateUuid)!, template: undefined, merged: handlesByTemplate }
    }
    const detail = await loadNodeTemplateDetail(templateUuid)
    const merged = new Map(handlesByTemplate)
    merged.set(templateUuid, detail.handles)
    setHandlesByTemplate(merged)
    return { handles: detail.handles, template: detail.template, merged }
  }, [handlesByTemplate])

  const addNodeFromTemplate = useCallback(async (
    templateUuid: string,
    position: { x: number; y: number },
  ) => {
    const template = templateMap.get(templateUuid)
    if (!template) {
      onNotify('该节点模板不在当前 Edge 目录中')
      return
    }
    try {
      const detail = await ensureTemplateHandles(templateUuid)
      beginEdit()
      const sameKind = nodes.filter((node) => node.data.raw.workflow_node_template_uuid === templateUuid).length
      const name = sameKind > 0 ? `${template.displayName} ${sameKind + 1}` : template.displayName
      // 带上当前最大序号之后的新号：既避免与既有节点撞号（后端会报「顺序不能重复」），
      // 也让新节点不会被后端按拓扑序补到既有语句中间。
      const nextOrder = nextAuthoringOrder(toRawNodes(nodes, baselineNodesRef.current))
      const raw = buildNewRawNode(template, detail.template, position, name, newUuid(), nextOrder)
      const rawNodes = [...toRawNodes(nodes, baselineNodesRef.current), raw]
      const rawEdges = toRawEdges(edges, baselineEdgesRef.current)
      setBaselineNodes((current) => new Map(current).set(raw.uuid, raw))
      applyRawGraph(rawNodes, rawEdges, templateMap, detail.merged)
      markDirty()
      setSelection({ nodeId: raw.uuid })
      commitEdit()
    } catch (error) {
      onNotify(error instanceof Error ? `添加节点失败：${error.message}` : '添加节点失败')
    }
  }, [
    applyRawGraph, beginEdit, commitEdit, edges, ensureTemplateHandles, handlesByTemplate,
    nodes, onNotify, templateMap,
  ])

  /**
   * 把另一份工作流的整段节点图克隆进当前草稿。
   *
   * 放在当前画布最右侧，节点/连线 UUID 换新，句柄 UUID 沿用（属于模板身份），
   * 参数、设备绑定、`meta_data` 原样带过来。
   */
  const insertGraph = useCallback((
    graph: { nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[]; handleTemplates: Record<string, any>[] },
    label: string,
  ) => {
    if (!workflowUuid || !graph.nodes.length) return
    const sourceHandles = groupHandles(graph.handleTemplates || [])
    const merged = new Map(handlesByTemplate)
    for (const [templateUuid, handles] of sourceHandles) merged.set(templateUuid, handles)
    setHandlesByTemplate(merged)

    const rightEdge = nodes.length ? Math.max(...nodes.map((node) => node.position.x)) + 320 : 60
    const sourceLeft = Math.min(...graph.nodes.map((node) => Number(node.pose?.position?.x) || 0))
    const currentRaws = toRawNodes(nodes, baselineNodesRef.current)
    const cloned = cloneGraphInto(
      graph.nodes,
      graph.edges,
      { x: rightEdge - sourceLeft, y: 40 },
      newUuid,
      // 作者序号必须接在现有最大序号之后，否则后端报 3003「节点顺序不能重复」。
      nextAuthoringOrder(currentRaws),
    )

    beginEdit()
    const rawNodes = [...currentRaws, ...cloned.nodes]
    const rawEdges = toRawEdges(edges, baselineEdgesRef.current)
    setBaselineNodes((current) => {
      const next = new Map(current)
      for (const node of cloned.nodes) next.set(String(node.uuid), node)
      return next
    })
    setBaselineEdges((current) => {
      const next = new Map(current)
      for (const edge of cloned.edges) next.set(String(edge.uuid), edge)
      return next
    })
    applyRawGraph(rawNodes, rawEdges, templateMap, merged)
    markDirty()
    setInsertOpen(false)
    commitEdit()
    onNotify(`已插入「${label}」：${cloned.nodes.length} 节点 / ${cloned.edges.length} 连线，保存后生效`)
  }, [
    applyRawGraph, beginEdit, commitEdit, edges, handlesByTemplate, nodes, onNotify,
    setBaselineEdges, setBaselineNodes, templateMap, workflowUuid,
  ])

  const handleConnect = useCallback((connection: Connection) => {    if (!connection.source || !connection.target) return
    if (connection.source === connection.target) return
    const duplicated = edges.some((edge) =>
      edge.source === connection.source
      && edge.target === connection.target
      && edge.sourceHandle === connection.sourceHandle
      && edge.targetHandle === connection.targetHandle)
    if (duplicated) return
    beginEdit()
    const uuid = newUuid()
    const rawEdge: RawWorkflowEdge = {
      uuid,
      source_node_uuid: connection.source,
      target_node_uuid: connection.target,
      source_handle_uuid: String(connection.sourceHandle || ''),
      target_handle_uuid: String(connection.targetHandle || ''),
      meta_data: {},
    }
    setBaselineEdges((current) => new Map(current).set(uuid, rawEdge))
    // 新边按两端句柄语义着色：拖的是参数端口就是数据线。
    const kindOf = (handleUuid: string | undefined) => {
      if (!handleUuid) return 'sequence' as const
      for (const node of nodes) {
        const handle = node.data.handles.find((item) => item.uuid === handleUuid)
        if (handle) return handle.handleKey === 'ready' ? 'sequence' as const : 'data' as const
      }
      return 'sequence' as const
    }
    setEdges((current) => [...current, ...toFlowEdges([rawEdge], kindOf)])
    markDirty()
    commitEdit()
  }, [beginEdit, commitEdit, edges, nodes, setEdges, setBaselineEdges])

  const validateConnection = useCallback((connection: Connection | EditorEdge) => {
    const sourceNode = nodes.find((node) => node.id === connection.source)
    const targetNode = nodes.find((node) => node.id === connection.target)
    if (!sourceNode || !targetNode) return false
    if (connection.source === connection.target) return false
    const sourceHandle = sourceNode.data.handles.find((handle) => handle.uuid === connection.sourceHandle)
    const targetHandle = targetNode.data.handles.find((handle) => handle.uuid === connection.targetHandle)
    if (!canConnectHandles(sourceHandle, targetHandle).ok) return false
    // 后端只允许一个目标参数有一条数据入边（"同一目标 Handle 只能有一条数据入边"）。
    if (targetHandle && targetHandle.handleKey !== 'ready') {
      const occupied = edges.some((edge) => edge.targetHandle === connection.targetHandle)
      if (occupied) return false
    }
    return true
  }, [edges, nodes])

  const patchNode = useCallback((
    nodeId: string,
    patch: { name?: string; description?: string; param?: Record<string, unknown>; disabled?: boolean },
  ) => {
    beginEdit()
    setNodes((current) => current.map((node) => {
      if (node.id !== nodeId) return node
      const raw = { ...node.data.raw }
      if (patch.name !== undefined) raw.name = patch.name
      if (patch.description !== undefined) raw.description = patch.description
      if (patch.param !== undefined) raw.param = patch.param
      if (patch.disabled !== undefined) raw.disabled = patch.disabled
      return { ...node, data: { ...node.data, raw } }
    }))
    markDirty()
  }, [beginEdit, markDirty, setNodes])

  const deleteNode = useCallback((nodeId: string) => {
    if (!nodeId) return
    beginEdit()
    setNodes((current) => current.filter((node) => node.id !== nodeId))
    setEdges((current) => current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId))
    setSelection({})
    markDirty()
    commitEdit()
  }, [beginEdit, commitEdit, setNodes, setEdges])

  /** 角标的禁用/启用：离散动作，立即入撤销栈。 */
  const toggleNodeDisabled = useCallback((nodeId: string) => {
    if (!nodeId) return
    beginEdit()
    setNodes((current) => current.map((node) => (
      node.id === nodeId
        ? { ...node, data: { ...node.data, raw: { ...node.data.raw, disabled: !node.data.raw.disabled } } }
        : node
    )))
    markDirty()
    commitEdit()
  }, [beginEdit, commitEdit, setNodes])

  const nodeActions = useMemo(
    () => ({ toggleDisabled: toggleNodeDisabled, removeNode: deleteNode }),
    [deleteNode, toggleNodeDisabled],
  )

  const deleteEdge = useCallback((edgeId: string) => {
    if (!edgeId) return
    beginEdit()
    setEdges((current) => current.filter((edge) => edge.id !== edgeId))
    setSelection({})
    markDirty()
    commitEdit()
  }, [beginEdit, commitEdit, setEdges])

  const runLayout = useCallback(() => {
    beginEdit()
    // 布局结果必须自己算出来，不能从 setNodes 的 updater 里侧面取：
    // React 的 updater 不保证同步执行，闭包外读到的会是空数组，
    // 落点也就写不回去。
    const laid = layeredLayout(nodes, edges)
    setNodes(laid)
    markLayoutOnly()
    commitEdit()
    setLayoutNonce((current) => current + 1)
    // 手动点「自动布局」同样要落盘：否则重开这份工作流又会重排一次。
    if (workflowUuid && laid.length) {
      void persistCanvasLayout(workflowUuid, laid, { notifyOnError: false }).then((saved) => {
        if (saved) {
          setDirty(false)
          setLayoutOnly(false)
        }
      })
    }
  }, [
    beginEdit, commitEdit, edges, markLayoutOnly, nodes, persistCanvasLayout, setNodes, workflowUuid,
  ])

  const handleSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    const nodeIds = params.nodes.map((node) => node.id)
    const nodeId = nodeIds[0]
    const edgeId = params.edges[0]?.id
    // nodeIds 保留完整多选（后续批量复制粘贴要用）；nodeId 仍是第一个选中项，
    // 属性面板与删除逻辑沿用单数语义，避免改动一大片既有调用点。
    setSelection({ nodeId, edgeId, nodeIds })
  }, [])

  const handleCanvasNodesChange = useCallback((changes: NodeChange<EditorNode>[]) => {
    handleNodesChange(changes)
    const structural = changes.some((change) => change.type === 'remove' || change.type === 'add')
    const dragged = changes.some((change) => change.type === 'position' && change.dragging === false)
    if (structural) markDirty()
    else if (dragged) markLayoutOnly()
  }, [handleNodesChange, markDirty, markLayoutOnly])

  const handleCanvasEdgesChange = useCallback((changes: EdgeChange<EditorEdge>[]) => {
    handleEdgesChange(changes)
    if (changes.some((change) => change.type === 'remove')) markDirty()
  }, [handleEdgesChange, markDirty])

  const undo = useCallback(() => {
    setHistory((current) => {
      if (!current.past.length) return current
      const previous = current.past[current.past.length - 1]
      const now = snapshotNow()
      applyRawGraph(previous.nodes, previous.edges, templateMap, handlesByTemplate)
      markDirty()
      return { past: current.past.slice(0, -1), future: [now, ...current.future].slice(0, 60) }
    })
  }, [applyRawGraph, handlesByTemplate, snapshotNow, templateMap])

  const redo = useCallback(() => {
    setHistory((current) => {
      if (!current.future.length) return current
      const next = current.future[0]
      const now = snapshotNow()
      applyRawGraph(next.nodes, next.edges, templateMap, handlesByTemplate)
      markDirty()
      return { past: [...current.past, now].slice(-60), future: current.future.slice(1) }
    })
  }, [applyRawGraph, handlesByTemplate, snapshotNow, templateMap])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      const typing = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
      if (typing) return
      const meta = event.metaKey || event.ctrlKey
      if (meta && event.key.toLowerCase() === 'z' && !event.shiftKey) {
        event.preventDefault()
        undo()
      } else if (meta && (event.key.toLowerCase() === 'y' || (event.key.toLowerCase() === 'z' && event.shiftKey))) {
        event.preventDefault()
        redo()
      } else if ((event.key === 'Backspace' || event.key === 'Delete') && selection.nodeId) {
        event.preventDefault()
        deleteNode(selection.nodeId)
      } else if ((event.key === 'Backspace' || event.key === 'Delete') && selection.edgeId) {
        event.preventDefault()
        deleteEdge(selection.edgeId)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [deleteEdge, deleteNode, redo, selection.edgeId, selection.nodeId, undo])

  const save = useCallback(async () => {
    if (!workflowUuid || saving) return
    commitEdit()
    setSaving(true)
    try {
      const raws = snapshotNow()
      const result = await saveWorkflowGraph(workflowUuid, revision, raws.nodes, raws.edges)
      setRevision(result.revision)
      setBaselineNodes(new Map(result.nodes.map((node) => [String(node.uuid), node])))
      setBaselineEdges(new Map(result.edges.map((edge) => [String(edge.uuid), edge])))
      setDirty(false)
      setLayoutOnly(false)
      setConflict(false)
      onNotify(`已保存，工作流修订推进到 r${result.revision}`)
      applyRawGraph(result.nodes, result.edges, templateMap, handlesByTemplate)
      // 整图里的 `pose` 只活在进程内定义目录中，重启后会被源码重新生成覆盖。
      // 因此保存内容时必须同步写一次落点表，坐标才能跨重启存活。
      const positions: Record<string, { x: number; y: number }> = {}
      for (const node of raws.nodes) {
        const position = node.pose?.position
        const x = Number(position?.x)
        const y = Number(position?.y)
        if (Number.isFinite(x) && Number.isFinite(y)) {
          positions[String(node.uuid)] = { x: Math.round(x), y: Math.round(y) }
        }
      }
      if (Object.keys(positions).length) {
        void saveCanvasLayout(workflowUuid, positions).catch(() => {
          // 落点写回失败不影响内容保存，用户下次拖动节点会再试一次。
        })
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '保存失败'
      const isConflict = (error instanceof EdgeApiError && String(error.code || '').includes('conflict'))
        || message.includes('conflict')
        || message.includes('修订冲突')
      if (isConflict) {
        setConflict(true)
        onNotify('该工作流已被其他页面改动，请载入最新版本后重试')
      } else {
        onNotify(`保存失败：${message}`)
      }
    } finally {
      setSaving(false)
    }
  }, [applyRawGraph, commitEdit, handlesByTemplate, onNotify, revision, saving, snapshotNow, templateMap, workflowUuid])

  const runPreflight = useCallback(async () => {
    if (!workflowUuid) return
    if (dirty && !layoutOnly) {
      onNotify('请先保存，预检针对的是已保存版本')
      return
    }
    setPreflightPending(true)
    setPreflightOpen(true)
    try {
      const report = await loadWorkflowGraphPreflight(workflowUuid)
      const checks = Array.isArray(report.checks) ? report.checks : []
      setPreflightStatus(String(report.status || 'unknown'))
      setPreflightChecks(checks.map((check: Record<string, any>) => ({
        code: String(check.code || ''),
        status: String(check.status || ''),
        message: String(check.message || ''),
        blocking: Boolean(check.blocking),
        nodeName: check.node_name ? String(check.node_name) : undefined,
      })))
    } catch (error) {
      setPreflightStatus('failed')
      setPreflightChecks([{
        code: 'preflight_request_failed',
        status: 'blocked',
        message: error instanceof Error ? error.message : '预检请求失败',
        blocking: true,
      }])
    } finally {
      setPreflightPending(false)
    }
  }, [dirty, layoutOnly, onNotify, workflowUuid])

  // ── 运行 ───────────────────────────────────────────────────────────────
  const runViews = useMemo(
    () => buildRunNodeViews(runTask, Boolean(runTask && (runTask.dispatchGate === 'paused' || runTask.executionMode === 'step'))),
    [runTask],
  )
  const runIndex = useMemo(() => runStateIndex(runViews), [runViews])
  const runActive = Boolean(runTask) && !isTaskTerminal(runTask)

  /** 把运行态投影到画布节点；只改 data，不碰 raw，因此不会污染待保存的草稿。 */
  useEffect(() => {
    setNodes((current) => {
      let changed = false
      const next = current.map((node) => {
        const view = runIndex.get(node.id)
        const runState = view?.state || (runTask ? 'idle' : undefined)
        const runDetail = view ? describeRunNode(view) : undefined
        if (node.data.runState === runState && node.data.runDetail === runDetail) return node
        changed = true
        return { ...node, data: { ...node.data, runState, runDetail } }
      })
      return changed ? next : current
    })
  }, [runIndex, runTask, setNodes])

  useEffect(() => {
    if (!connected) return
    let cancelled = false
    void loadActionMode().then((mode) => {
      if (!cancelled) setActionMode(mode)
    })
    return () => { cancelled = true }
  }, [connected])

  const refreshRun = useCallback(async (taskUuid: string) => {
    try {
      const next = await loadRunTask(taskUuid)
      setRunTask(next)
      setRunError('')
    } catch (error) {
      setRunError(error instanceof Error ? error.message : '任务状态读取失败')
    }
  }, [])

  // 运行中每 2 秒拉一次任务详情；终态后自动停表，避免空转轮询。
  useEffect(() => {
    if (!runTask || isTaskTerminal(runTask)) return
    const timer = window.setInterval(() => { void refreshRun(runTask.uuid) }, 2000)
    return () => window.clearInterval(timer)
  }, [refreshRun, runTask])

  /**
   * 提交一次运行。
   *
   * ``targetMode`` 决定本次任务的动作档位：``simulate`` 只校验**软件下发链路**
   * 是否正确（驱动代码照跑、不打开设备会话），``real`` 真实驱动硬件。档位随
   * 任务下发，不影响后端进程级配置，也不需要重启。
   */
  const submitRun = useCallback(async (targetMode: 'simulate' | 'real') => {
    if (!workflowUuid) return
    if (dirty && !layoutOnly) {
      onNotify('请先保存，运行使用的是已保存版本')
      return
    }
    setRunPending(true)
    setRunError('')
    setRunOpen(true)
    try {
      const created = await startWorkflowRun({
        workflowUuid,
        actionMode: targetMode,
        description: `${workflowOptions.find((item) => item.uuid === workflowUuid)?.name || '工作流编辑器'} · ${targetMode === 'simulate' ? '仿真运行' : '真机运行'}`,
      })
      const uuid = String(created.uuid || created.workflow_task_uuid || '')
      if (!uuid) throw new Error('Edge 未返回任务 UUID')
      setRunTask(undefined)
      await refreshRun(uuid)
      onNotify(`已提交${targetMode === 'simulate' ? '仿真' : '真机'}运行：${uuid.slice(0, 8)}`)
    } catch (error) {
      const message = error instanceof Error ? error.message : '运行提交失败'
      setRunError(message)
      onNotify(`运行提交失败：${message}`)
    } finally {
      setRunPending(false)
    }
  }, [dirty, layoutOnly, onNotify, refreshRun, workflowOptions, workflowUuid])

  /**
   * 提交运行前的最后一道门。
   *
   * ``mode`` 由按钮点击直接给出：不需要用户在页面切换任何本地状态，也不弹
   * 确认框——档位随任务下发，仿真与真机可以并存。
   */
  const requestRun = useCallback((mode: 'simulate' | 'real') => {
    if (!workflowUuid || runPending) return
    if (dirty && !layoutOnly) {
      onNotify('请先保存，运行使用的是已保存版本')
      return
    }
    void submitRun(mode)
  }, [dirty, layoutOnly, onNotify, runPending, submitRun, workflowUuid])

  const commandRun = useCallback(async (type: 'pause' | 'resume' | 'cancel') => {
    if (!runTask) return
    setRunPending(true)
    try {
      await commandWorkflowTask(runTask.uuid, type)
      await refreshRun(runTask.uuid)
      onNotify(type === 'pause' ? '已请求暂停' : type === 'resume' ? '已请求继续' : '已请求取消')
    } catch (error) {
      onNotify(`控制命令失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setRunPending(false)
    }
  }, [onNotify, refreshRun, runTask])

  const createWorkflow = useCallback(async () => {
    const name = newName.trim()
    if (!name) return
    try {
      const created = await createWorkflowDefinition(name)
      setNewDialogOpen(false)
      setNewName('')
      const uuid = String(created.uuid || '')
      if (!uuid) throw new Error('后端未返回工作流 UUID')
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('page', 'workflow-editor')
        next.set('workflow', uuid)
        return next
      })
      onNotify(`已创建「${name}」，拖入节点后点保存`)
    } catch (error) {
      onNotify(`创建失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }, [newName, onNotify, setSearchParams])

  // ── 工作流列表的右键操作 ──────────────────────────────────────────────
  // 这些操作都会改变 Edge 上的定义集合，因此成功后必须让上层重拉快照，
  // 否则左侧列表还是旧的。

  const createWorkflowFromPicker = useCallback(async (name: string) => {
    try {
      const created = await createWorkflowDefinition(name)
      const uuid = String(created.uuid || '')
      if (!uuid) throw new Error('后端未返回工作流 UUID')
      onRefresh?.()
      setSearchParams((current) => {
        const next = new URLSearchParams(current)
        next.set('page', 'workflow-editor')
        next.set('workflow', uuid)
        return next
      })
      onNotify(`已创建「${name}」`)
    } catch (error) {
      onNotify(`创建失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }, [onNotify, onRefresh, setSearchParams])

  const renameWorkflowFromPicker = useCallback(async (
    workflow: WorkflowDefinition,
    name: string,
  ) => {
    try {
      // 只提交名称：PATCH 会保留 tags/description/meta_data 等未提交字段。
      await renameWorkflow(workflow.uuid, name)
      onRefresh?.()
      onNotify(`已重命名为「${name}」`)
    } catch (error) {
      onNotify(`重命名失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }, [onNotify, onRefresh])

  const duplicateWorkflowFromPicker = useCallback(async (workflow: WorkflowDefinition) => {
    try {
      const created = await duplicateWorkflow(workflow.uuid)
      const uuid = String(created.uuid || '')
      onRefresh?.()
      if (uuid) {
        // 复制完直接打开副本，省一次手动切换。
        setSearchParams((current) => {
          const next = new URLSearchParams(current)
          next.set('page', 'workflow-editor')
          next.set('workflow', uuid)
          return next
        })
      }
      onNotify(`已复制「${workflow.name}」`)
    } catch (error) {
      onNotify(`复制失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }, [onNotify, onRefresh, setSearchParams])

  const deleteWorkflowFromPicker = useCallback(async (workflow: WorkflowDefinition) => {
    if (!window.confirm(`删除工作流「${workflow.name}」？该操作不可撤销。`)) return
    try {
      await deleteWorkflow(workflow.uuid)
      onRefresh?.()
      // 删的正是当前打开的这份：退回空编辑态，避免继续编辑已不存在的定义。
      if (workflow.uuid === workflowUuid) {
        setSearchParams((current) => {
          const next = new URLSearchParams(current)
          next.delete('workflow')
          return next
        })
      }
      onNotify(`已删除「${workflow.name}」`)
    } catch (error) {
      onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }, [onNotify, onRefresh, setSearchParams, workflowUuid])

  const selectWorkflow = useCallback((uuid: string) => {
    if (dirty && !window.confirm('当前有未保存改动，切换工作流会丢弃它们。继续？')) return
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('page', 'workflow-editor')
      if (uuid) next.set('workflow', uuid)
      else next.delete('workflow')
      return next
    })
  }, [dirty, setSearchParams])

  const localIssues = useMemo(() => localGraphIssues(nodes, edges).summary, [nodes, edges])
  const dataEdgeCount = useMemo(
    () => edges.filter((edge) => (edge.data?.kind || 'sequence') === 'data').length,
    [edges],
  )
  const currentWorkflow = workflows.find((item) => item.uuid === workflowUuid)
  return (
    <div className="wf-editor-page">
      <header className="wf-editor-bar">
        <div className="wf-bar-left">
          {/* 沉浸模式下侧栏隐藏，这里提供唯一的返回入口。 */}
          <button
            type="button"
            className="wf-back"
            title="返回实验运营控制台"
            aria-label="返回实验运营控制台"
            onClick={() => setSearchParams((current) => {
              const next = new URLSearchParams(current)
              next.set('page', 'overview')
              return next
            })}
          >
            <ArrowLeft size={14} />
            <span>控制台</span>
          </button>
          {/* 工作流切换移到左侧列表；这里只显示当前编辑的是哪一份。 */}
          <span className="wf-current-name" title={workflowUuid || '未选择工作流'}>
            {workflowOptions.find((item) => item.uuid === workflowUuid)?.name || '未选择工作流'}
          </span>
          {workflowUuid ? (
            <small className={`wf-dirty ${dirty ? 'is-dirty' : ''}`}>
              {!dirty
                ? `已同步 r${revision}`
                : layoutOnly ? '仅排布改动（可直接运行）' : '有未保存改动'}
            </small>
          ) : null}
        </div>
        <div className="wf-bar-right">
          <Button icon={<Undo2 size={14} />} disabled={!history.past.length} onClick={undo} title="撤销 ⌘Z">撤销</Button>
          <Button icon={<Redo2 size={14} />} disabled={!history.future.length} onClick={redo} title="重做 ⇧⌘Z">重做</Button>
          <Button
            icon={<FileJson size={14} />}
            onClick={() => setTransferOpen(true)}
            title="批量导入 / 导出 JSON"
          >导入导出</Button>
          <Button
            icon={<Copy size={14} />}
            disabled={!workflowUuid}
            onClick={() => setInsertOpen(true)}
            title="把另一份工作流的节点图插入当前画布"
          >插入模板</Button>
          <Button
            icon={<Layers size={14} />}
            disabled={!nodes.length}
            onClick={() => setSimOpen(true)}
            title="在等轴测空间里预览执行路径"
          >3D 仿真</Button>
          <Button icon={<Hammer size={14} />} disabled={!nodes.length} onClick={runLayout}>自动布局</Button>
          <Button
            icon={<Sparkles size={14} />}
            disabled={!workflowUuid || !connected}
            onClick={() => setAgentOpen((value) => !value)}
            title="打开工作流 Agent：用自然语言批量生成或修改画布"
          >Agent</Button>
          <Button
            tone="primary"
            icon={<Save size={14} />}
            disabled={!workflowUuid || !dirty || saving || !connected}
            onClick={() => void save()}
          >{saving ? '保存中…' : '保存'}</Button>
          <Button
            icon={<ShieldCheck size={14} />}
            disabled={!workflowUuid || preflightPending || (dirty && !layoutOnly)}
            onClick={() => void runPreflight()}
            title={dirty && !layoutOnly ? '请先保存再预检' : '对已保存版本做运行预检'}
          >试运行检查</Button>
          <Button
            icon={runPending ? <RefreshCw size={14} className="spin" /> : <FlaskConical size={14} />}
            disabled={!workflowUuid || !connected || runPending || (dirty && !layoutOnly)}
            onClick={() => requestRun('simulate')}
            title={(dirty && !layoutOnly)
              ? '请先保存再运行'
              : '仿真运行：驱动代码照常执行，但不打开设备会话，只校验软件下发链路是否正确'}
          >仿真运行</Button>
          <Button
            tone="primary"
            className="wf-run-btn is-danger"
            icon={runPending ? <RefreshCw size={14} className="spin" /> : <Play size={14} />}
            disabled={!workflowUuid || !connected || runPending || (dirty && !layoutOnly)}
            onClick={() => requestRun('real')}
            title={(dirty && !layoutOnly) ? '请先保存再运行' : '真机运行（会驱动 510 硬件）'}
          >真机运行</Button>
        </div>
      </header>


      {conflict ? (
        <div className="wf-conflict" role="alert">
          <div>
            <strong>修订冲突</strong>
            <span>该工作流已被其他页面或终端改动，本地草稿基于 r{revision}。</span>
          </div>
          <Button onClick={() => void loadGraph(workflowUuid)}>载入最新版本</Button>
        </div>
      ) : null}

      {!connected ? (
        <div className="wf-conflict" role="status">
          <div><strong>Edge 未连接</strong><span>当前为只读浏览；保存与预检已暂停。</span></div>
        </div>
      ) : null}

      <div className="wf-editor-main">
        {/* 左侧抽屉：工作流列表 + 节点库，默认收起，悬停/钉住才展开 */}
        <SideDrawer title="工作流 · 节点库" side="left" width={264} storageKey="unilab_wf_drawer">
          <WorkflowPicker
            workflows={workflows}
            currentUuid={workflowUuid}
            connected={connected}
            onSelect={selectWorkflow}
            onCreate={createWorkflowFromPicker}
            onRename={renameWorkflowFromPicker}
            onDuplicate={duplicateWorkflowFromPicker}
            onDelete={deleteWorkflowFromPicker}
          />
          <NodeLibraryPanel
            templates={templates}
            loading={libraryLoading}
            error={libraryError || undefined}
            onAdd={(template) => {
              const offset = nodes.length % 6
              void addNodeFromTemplate(template.uuid, { x: 80 + offset * 40, y: 80 + offset * 90 })
            }}
          />
        </SideDrawer>

        {workflowUuid ? (
          <NodeActionsContext.Provider value={nodeActions}>
            <EditorCanvas
              nodes={nodes}
              edges={edges}
              templates={templates}
              dataPortMode={dataPortMode}
              onToggleDataPortMode={() => setDataPortMode((value) => !value)}
              onNodesChange={handleCanvasNodesChange}
              onEdgesChange={handleCanvasEdgesChange}
              onConnect={handleConnect}
              onValidateConnection={validateConnection}
              onSelect={handleSelectionChange}
              onDropTemplate={(templateUuid, position) => { void addNodeFromTemplate(templateUuid, position) }}
              onLayout={runLayout}
              layoutNonce={layoutNonce}
            />
          </NodeActionsContext.Provider>
        ) : (
          <Panel className="wf-editor-empty">
            <PanelHeader
              title="选择一个工作流开始编辑"
              description="从左上角选择已有工作流，或新建一个空白工作流。"
              action={<Button tone="primary" icon={<Plus size={14} />} onClick={() => setNewDialogOpen(true)}>新建工作流</Button>}
            />
            <EmptyState
              title="编辑器以整图保存"
              description="拖入节点、连线、改参数都只改本地草稿；点保存时一次性提交，撤销栈与 revision 都对齐。"
            />
          </Panel>
        )}

        {/* 右侧抽屉：属性面板，默认收起 */}
        <SideDrawer title="属性" side="right" width={288} storageKey="unilab_wf_drawer">
          <PropertyPanel
            node={currentNode}
            edge={currentEdge}
            onPatchNode={(patch) => {
              if (!selection.nodeId) return
              patchNode(selection.nodeId, patch)
            }}
            onDeleteNode={() => deleteNode(selection.nodeId || '')}
            onDeleteEdge={() => deleteEdge(selection.edgeId || '')}
          />
        </SideDrawer>
      </div>

      <footer className="wf-editor-status">
        <span className="wf-status-item">
          {loadError
            ? <><AlertTriangle size={12} />{loadError}</>
            : loading
              ? '正在读取工作流图…'
              : nodes.length
                ? <><CheckCircle2 size={12} />{nodes.length} 节点 / {edges.length} 连线</>
                : <><CircleSlash size={12} />画布为空</>}
        </span>
        {localIssues.map((issue) => (
          <span className="wf-status-issue" key={issue}><AlertTriangle size={12} />{issue}</span>
        ))}
        <span className="wf-status-spacer" />
        <span className="wf-status-item muted">
          顺序线 {edges.length - dataEdgeCount} · 数据线 {dataEdgeCount}
          {dataPortMode ? ' · 参数端口已展开' : ''}
        </span>
        <span className="wf-status-item muted">{currentWorkflow ? currentWorkflow.uuid : '未选择工作流'}</span>
      </footer>

      {newDialogOpen ? (
        <div className="wf-dialog-mask" role="dialog" aria-modal="true">
          <form
            className="wf-dialog"
            onSubmit={(event) => {
              event.preventDefault()
              void createWorkflow()
            }}
          >
            <header>
              <strong>新建工作流</strong>
              <button type="button" aria-label="关闭" onClick={() => setNewDialogOpen(false)}><X size={15} /></button>
            </header>
            <label className="wf-field">
              <span className="wf-field-label"><strong>名称</strong></span>
              <input
                autoFocus
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="例如：金四面体 · 第三次离心"
              />
            </label>
            <footer>
              <Button type="button" onClick={() => setNewDialogOpen(false)}>取消</Button>
              <Button type="submit" tone="primary" disabled={!newName.trim()}>创建并打开</Button>
            </footer>
          </form>
        </div>
      ) : null}

      {preflightOpen ? (
        <div className="wf-drawer" role="dialog" aria-label="试运行预检">
          <header>
            <strong>试运行预检</strong>
            <span className={`wf-preflight-badge is-${preflightStatus || 'unknown'}`}>
              {preflightPending ? '检查中…' : preflightStatus || 'unknown'}
            </span>
            <button type="button" aria-label="关闭" onClick={() => setPreflightOpen(false)}><X size={15} /></button>
          </header>
          <div className="wf-drawer-body">
            {preflightChecks.length === 0 && !preflightPending
              ? <p className="wf-props-empty">没有返回检查项。</p>
              : preflightChecks.map((check, index) => (
                <article className={`wf-preflight-check is-${check.status}`} key={`${check.code}-${index}`}>
                  <span>{check.status}</span>
                  <div>
                    <strong>{check.message}</strong>
                    <code>{check.code}{check.nodeName ? ` · ${check.nodeName}` : ''}</code>
                  </div>
                </article>
              ))}
          </div>
        </div>
      ) : null}
      {transferOpen ? (
        <BatchTransferPanel
          workflowName={currentWorkflow?.name || '未命名工作流'}
          workflowType={currentWorkflow?.workflowType === 'experiment_operation' ? 'experiment_operation' : 'normal'}
          workflowUuid={workflowUuid || undefined}
          revision={revision}
          workflows={workflows}
          connected={connected}
          nodeCount={nodes.length}
          edgeCount={edges.length}
          snapshot={snapshotNow}
          onClose={() => setTransferOpen(false)}
          onNotify={onNotify}
          onImported={() => onRefresh?.()}
        />
      ) : null}

      {insertOpen ? (
        <TemplateInsertPanel
          workflows={workflows}
          currentWorkflowUuid={workflowUuid || undefined}
          onClose={() => setInsertOpen(false)}
          onNotify={onNotify}
          onInsert={insertGraph}
        />
      ) : null}

      {simOpen ? (
        <Suspense fallback={<div className="sim-loading">正在加载 3D 场景…</div>}>
          <SimulationSpace
            nodes={nodes}
            edges={edges}
            selectedNodeId={selection.nodeId}
            onSelectNode={(nodeId) => setSelection({ nodeId })}
            onClose={() => setSimOpen(false)}
          />
        </Suspense>
      ) : null}

      {agentOpen ? (
        <WorkflowAgentPanel
          graph={snapshotNow()}
          onApplyCandidate={(candidate) => {
            // 走与手工编辑同一条路径：写入基线 + 重绘 + 标记未保存。
            beginEdit()
            setBaselineNodes((current) => {
              const next = new Map(current)
              for (const node of candidate.nodes) next.set(String(node.uuid), node)
              return next
            })
            setBaselineEdges((current) => {
              const next = new Map(current)
              for (const edge of candidate.edges) next.set(String(edge.uuid), edge)
              return next
            })
            applyRawGraph(candidate.nodes, candidate.edges, templateMap, handlesByTemplate)
            markDirty()
            commitEdit()
          }}
          onClose={() => setAgentOpen(false)}
          onNotify={onNotify}
        />
      ) : null}

      {runOpen ? (
        <WorkflowRunDrawer
          task={runTask}
          tier={actionMode === 'simulate' ? 'simulate' : 'real'}
          active={runActive}
          pending={runPending}
          error={runError || undefined}
          onClose={() => setRunOpen(false)}
          onRefresh={() => { if (runTask) void refreshRun(runTask.uuid) }}
          onPause={() => void commandRun('pause')}
          onResume={() => void commandRun('resume')}
          onCancel={() => void commandRun('cancel')}
          onSelectNode={(nodeUuid) => setSelection({ nodeId: nodeUuid })}
        />
      ) : null}
    </div>
  )
}
