/**
 * 工作流编辑器的画布模型层。
 *
 * 设计约定：
 * 1. 后端原始 snake_case 节点/边是唯一权威数据结构，画布只持有它的投影。
 * 2. 保存走一次整图替换（`PUT /workflows/{uuid}/graph`），因此所有编辑先在
 *    本地累积，撤销栈也基于同一份原始快照，不会因为每次拖拽就推进 revision。
 * 3. 句柄 uuid 来自模板，不在前端臆造；连线只允许语义匹配的两端。
 */
import type { Edge, Node } from '@xyflow/react'
import type {
  NodeTemplateHandle,
  NodeTemplateSummary,
  RawWorkflowEdge,
  RawWorkflowNode,
} from './edgeClient'

/** 画布节点携带的展示与回写信息。 */
export interface EditorNodeData extends Record<string, unknown> {
  raw: RawWorkflowNode
  template?: NodeTemplateSummary
  handles: NodeTemplateHandle[]
  subtitle: string
  /** 已被连线占用的出口句柄 uuid；画布只为这些句柄渲染端口。 */
  linkedSourceHandles: string[]
  /** 已被连线占用的入口句柄 uuid。 */
  linkedTargetHandles: string[]
  /** 是否展开全部参数端口（用于拖拽数据传递线）。 */
  showDataPorts: boolean
  /** 本地静态校验发现的问题；后端整图校验才是最终判定。 */
  issue?: string
  /** 本次运行的节点状态；未参与运行时为空，画布不额外着色。 */
  runState?: string
  /** 状态补充说明（等待原因 / 耗时），作为徽标 title。 */
  runDetail?: string
}

/** 每个节点已被连线占用的句柄；只为它们渲染端口，避免 48 个输入口堆满画布。 */
export interface LinkedHandleIndex {
  source: Map<string, string[]>
  target: Map<string, string[]>
}

export function buildLinkedHandleIndex(edges: RawWorkflowEdge[]): LinkedHandleIndex {
  const source = new Map<string, string[]>()
  const target = new Map<string, string[]>()
  for (const edge of edges) {
    const sourceNode = String(edge.source_node_uuid || '')
    const targetNode = String(edge.target_node_uuid || '')
    if (sourceNode && edge.source_handle_uuid) {
      const list = source.get(sourceNode) || []
      list.push(String(edge.source_handle_uuid))
      source.set(sourceNode, list)
    }
    if (targetNode && edge.target_handle_uuid) {
      const list = target.get(targetNode) || []
      list.push(String(edge.target_handle_uuid))
      target.set(targetNode, list)
    }
  }
  return { source, target }
}

/** 数据传递线在画布上的样式类；顺序依赖线另用一类。 */
export const SEQUENCE_EDGE_CLASS = 'wf-edge-sequence'
export const DATA_EDGE_CLASS = 'wf-edge-data'

/** 连线语义：`sequence` 只表达执行先后，`data` 表示参数从上游取值。 */
export type EdgeKind = 'sequence' | 'data'

export type EditorNode = Node<EditorNodeData, 'unilab'>
export type EditorEdge = Edge<{ raw: RawWorkflowEdge; kind: EdgeKind }, 'unilab'>

export const READY_KEY = 'ready'

/**
 * 句柄 uuid → 连线语义。
 *
 * 句柄 uuid 全局唯一（由模板发布时生成），因此可以跨模板建一张扁平索引；
 * 连线只带句柄 uuid，没有这张表就只能靠 `ready` 字面量猜。
 */
export function buildHandleKindIndex(
  handlesByTemplate: Map<string, NodeTemplateHandle[]>,
): Map<string, EdgeKind> {
  const index = new Map<string, EdgeKind>()
  for (const handles of handlesByTemplate.values()) {
    for (const handle of handles) {
      if (handle.uuid) index.set(handle.uuid, handle.handleKey === READY_KEY ? 'sequence' : 'data')
    }
  }
  return index
}

/** 节点在画布上的落点；`pose.position` 是后端既有约定。 */
export function nodePosition(raw: RawWorkflowNode): { x: number; y: number } {
  const position = raw.pose?.position
  const x = Number(position?.x)
  const y = Number(position?.y)
  return {
    x: Number.isFinite(x) ? x : 0,
    y: Number.isFinite(y) ? y : 0,
  }
}

function nodeSubtitle(raw: RawWorkflowNode): string {
  const action = raw.action_name ? String(raw.action_name) : ''
  const device = raw.meta_data?.unilab?.executor_binding?.device_id
    ? String(raw.meta_data.unilab.executor_binding.device_id)
    : ''
  if (device && action) return `${device} · ${action}`
  return device || action || String(raw.type || '')
}

/** 原始节点 → 画布节点。 */
export function toFlowNodes(
  raws: RawWorkflowNode[],
  templates: Map<string, NodeTemplateSummary>,
  handles: Map<string, NodeTemplateHandle[]>,
  issues: Map<string, string> = new Map(),
  linked: LinkedHandleIndex = { source: new Map(), target: new Map() },
  showDataPorts = false,
): EditorNode[] {
  return raws.map((raw) => {
    const templateUuid = raw.workflow_node_template_uuid
      ? String(raw.workflow_node_template_uuid)
      : ''
    const template = templates.get(templateUuid)
    const nodeId = String(raw.uuid)
    return {
      id: nodeId,
      type: 'unilab',
      position: nodePosition(raw),
      data: {
        raw,
        template,
        handles: handles.get(templateUuid) || [],
        subtitle: nodeSubtitle(raw),
        linkedSourceHandles: linked.source.get(nodeId) || [],
        linkedTargetHandles: linked.target.get(nodeId) || [],
        showDataPorts,
        issue: issues.get(nodeId),
      },
    } satisfies EditorNode
  })
}

/** 原始边 → 画布边；句柄 uuid 直接作为画布端口标识。 */
export function toFlowEdges(
  raws: RawWorkflowEdge[],
  kindOf: (handleUuid: string | undefined) => EdgeKind = () => 'sequence',
): EditorEdge[] {
  return raws
    .filter((raw) => raw.source_node_uuid && raw.target_node_uuid)
    .map((raw) => {
      // 任一端是数据句柄即按数据线着色：语义上数据边两端 data_key 必然相同。
      const sourceKind = kindOf(raw.source_handle_uuid ? String(raw.source_handle_uuid) : undefined)
      const targetKind = kindOf(raw.target_handle_uuid ? String(raw.target_handle_uuid) : undefined)
      const kind: EdgeKind = sourceKind === 'data' || targetKind === 'data' ? 'data' : 'sequence'
      return {
        id: String(raw.uuid),
        source: String(raw.source_node_uuid),
        target: String(raw.target_node_uuid),
        sourceHandle: raw.source_handle_uuid ? String(raw.source_handle_uuid) : undefined,
        targetHandle: raw.target_handle_uuid ? String(raw.target_handle_uuid) : undefined,
        className: kind === 'data' ? DATA_EDGE_CLASS : SEQUENCE_EDGE_CLASS,
        data: { raw, kind },
      } satisfies EditorEdge
    })
}

/**
 * 画布节点 → 原始节点。
 *
 * 以基线原始节点为底稿再覆盖可编辑字段：整图替换语义下，任何没被重新发送
 * 的字段都会消失（`meta_data.unilab` 里的编排元数据尤其关键）。
 */
export function toRawNodes(
  nodes: EditorNode[],
  baseline: Map<string, RawWorkflowNode>,
): RawWorkflowNode[] {
  return nodes.map((node) => {
    const base = baseline.get(node.id)
    const previousPose = base?.pose && typeof base.pose === 'object' ? base.pose : {}
    const position = { x: Math.round(node.position.x), y: Math.round(node.position.y) }
    return {
      ...(base || {}),
      uuid: node.id,
      name: String(node.data.raw.name || ''),
      type: String(node.data.raw.type || 'UniLabJsonCommand'),
      workflow_node_template_uuid: node.data.raw.workflow_node_template_uuid || undefined,
      param: node.data.raw.param ?? null,
      description: node.data.raw.description ?? '',
      disabled: Boolean(node.data.raw.disabled),
      minimized: Boolean(node.data.raw.minimized),
      meta_data: node.data.raw.meta_data || {},
      pose: { ...previousPose, position },
    }
  })
}

/** 画布边 → 原始边。 */
export function toRawEdges(edges: EditorEdge[], baseline: Map<string, RawWorkflowEdge>): RawWorkflowEdge[] {
  return edges.map((edge) => {
    const base = baseline.get(edge.id)
    return {
      ...(base || {}),
      uuid: edge.id,
      source_node_uuid: edge.source,
      target_node_uuid: edge.target,
      source_handle_uuid: String(edge.sourceHandle || ''),
      target_handle_uuid: String(edge.targetHandle || ''),
      meta_data: base?.meta_data || {},
    }
  })
}

/**
 * 拖入新节点时的原始节点骨架；`description` 是后端强制的展示字段。
 *
 * ``authoringSourceOrder`` 不给时节点不带顺序号，后端会按拓扑序补齐（见
 * `_authoring_ordered_nodes`）。调用方若能拿到当前最大序号，应当传入
 * `nextAuthoringOrder(nodes)`，避免把新节点补到既有语句之间而打乱作者排布。
 */
export function buildNewRawNode(
  template: NodeTemplateSummary,
  detailTemplate: Record<string, unknown> | undefined,
  position: { x: number; y: number },
  name: string,
  uuid: string,
  authoringSourceOrder?: number,
): RawWorkflowNode {
  const defaultParam = detailTemplate?.goal_default
  const meta_data: Record<string, unknown> = {}
  if (typeof authoringSourceOrder === 'number' && Number.isFinite(authoringSourceOrder)) {
    // 结果变量名与序号同源，保持与后端渲染器一致的稳定形状。
    meta_data.unilab = {
      authoring_source_order: authoringSourceOrder,
      authoring_result_name: `step_${String(authoringSourceOrder + 1).padStart(3, '0')}`,
    }
  }
  return {
    uuid,
    name,
    type: template.type || 'UniLabJsonCommand',
    workflow_node_template_uuid: template.uuid,
    param: defaultParam && typeof defaultParam === 'object' ? { ...(defaultParam as Record<string, unknown>) } : {},
    description: name,
    disabled: false,
    minimized: false,
    meta_data,
    pose: { position: { x: Math.round(position.x), y: Math.round(position.y) } },
  }
}

/**
 * 连线语义校验。
 *
 * 依赖边必须是 source 侧 `ready` 对 target 侧 `ready`；数据边要求两端
 * `data_key` 相同且 Handle 类型一致。后端 `validate_graph` 对这些条件逐条
 * 硬校验（类型不兼容直接整图拒绝），这里提前拦住无效拖拽。
 */
export function canConnectHandles(
  source: NodeTemplateHandle | undefined,
  target: NodeTemplateHandle | undefined,
): { ok: true } | { ok: false; reason: string } {
  if (!source || !target) return { ok: false, reason: '缺少端口定义，无法连线' }
  if (source.ioType !== 'source' || target.ioType !== 'target') {
    return { ok: false, reason: '只能从右侧输出端口连到左侧输入端口' }
  }
  if (source.handleKey === READY_KEY || target.handleKey === READY_KEY) {
    if (source.handleKey !== READY_KEY || target.handleKey !== READY_KEY) {
      return { ok: false, reason: '执行顺序端口 ready 只能连接另一端的 ready' }
    }
    return { ok: true }
  }
  if (source.dataKey !== target.dataKey) {
    return { ok: false, reason: `参数名不一致：${source.dataKey} → ${target.dataKey}` }
  }
  if (source.type && target.type && source.type !== target.type) {
    return { ok: false, reason: `端口类型不兼容：${source.type} → ${target.type}` }
  }
  return { ok: true }
}

/** 端口句柄是否可作为连线起点（有出向能力）。 */
export function isConnectableSource(handle: NodeTemplateHandle): boolean {
  return handle.ioType === 'source'
}

/** 分层布局参数；默认值对齐本机工作流的实际规模（最长的一份是 148 节点纯串行链）。 */
export interface LayoutOptions {
  /** 列间距（节点卡片宽 216px，留出连线空间）。 */
  columnStride?: number
  /** 列内行间距。 */
  rowStride?: number
  /** 每行最多几列，超过就换到下一行。 */
  maxColumns?: number
  /** 行与行之间的额外留白。 */
  bandGap?: number
  origin?: { x: number; y: number }
}

const DEFAULT_LAYOUT = {
  columnStride: 320,
  rowStride: 156,
  maxColumns: 15,
  bandGap: 104,
  origin: { x: 48, y: 48 },
}

/** 作者源码顺序用于同层稳定排序；缺失的排到最后。 */
function authoringOrderOf(node: EditorNode): number {
  const raw = node.data.raw || {}
  const unilab = raw.meta_data && typeof raw.meta_data === 'object' ? raw.meta_data.unilab : undefined
  const value = Number(unilab?.authoring_source_order)
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER
}

/**
 * 依赖图分层布局（瀑布式多列）。
 *
 * 本机所有工作流都是 `edges = nodes - 1` 的纯串行链，如果单纯「一层一列」往右排，
 * 148 个节点会拉成一条 47,000px 长的直线——既不可读也没法 fitView。因此列数到
 * `maxColumns` 就折行：每个折行带都从 `origin.x` 重新开始，纵向接在上一带最矮列
 * 之下，形成瀑布。同层多节点按上游重心排序，减少连线交叉；孤立节点单独成列排在末尾。
 *
 * 注意：这里会同时把落点写回 `raw.pose.position`。布局结果必须进 draft 模型，
 * 否则任何一次 `applyRawGraph`（改属性、插模板、撤销）都会按空 pose 重建节点，
 * 一眼回到全部叠在原点。
 */
export function layeredLayout(
  nodes: EditorNode[],
  edges: EditorEdge[],
  options: LayoutOptions = {},
): EditorNode[] {
  if (!nodes.length) return nodes
  const cfg = { ...DEFAULT_LAYOUT, ...options }
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const ids = new Set(byId.keys())

  const indegree = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  const incoming = new Map<string, string[]>()
  const touched = new Set<string>()
  for (const id of ids) {
    indegree.set(id, 0)
    outgoing.set(id, [])
    incoming.set(id, [])
  }
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
    outgoing.get(edge.source)!.push(edge.target)
    incoming.get(edge.target)!.push(edge.source)
    touched.add(edge.source)
    touched.add(edge.target)
  }

  // 1) 分层：从入度 0 出发逐层推进。环上的节点入度永不归零，按作者顺序各自成列兜底。
  const rankOf = new Map<string, number>()
  const pendingIndegree = new Map(indegree)
  let frontier = nodes.filter((node) => (indegree.get(node.id) || 0) === 0).map((node) => node.id)
  let rank = 0
  while (frontier.length) {
    const next: string[] = []
    for (const id of frontier) {
      if (rankOf.has(id)) continue
      rankOf.set(id, rank)
      for (const child of outgoing.get(id) || []) {
        const remaining = (pendingIndegree.get(child) || 0) - 1
        pendingIndegree.set(child, remaining)
        if (remaining <= 0) next.push(child)
      }
    }
    frontier = next
    rank += 1
  }
  const cyclic = nodes
    .filter((node) => !rankOf.has(node.id))
    .sort((left, right) => authoringOrderOf(left) - authoringOrderOf(right) || left.id.localeCompare(right.id))
  for (const node of cyclic) {
    rankOf.set(node.id, rank)
    rank += 1
  }

  const columns: string[][] = Array.from({ length: rank }, () => [])
  const ranked = nodes
    .filter((node) => touched.has(node.id))
    .sort((left, right) => (
      (rankOf.get(left.id) || 0) - (rankOf.get(right.id) || 0)
      || authoringOrderOf(left) - authoringOrderOf(right)
      || left.id.localeCompare(right.id)
    ))
  for (const node of ranked) columns[rankOf.get(node.id) || 0].push(node.id)

  // 2) 层内排序：按相邻层节点的列内位置取重心，两趟来回足以消掉大部分交叉。
  const rowOf = new Map<string, number>()
  const reindex = () => {
    for (const column of columns) column.forEach((id, index) => rowOf.set(id, index))
  }
  reindex()
  const sortColumn = (column: string[], neighbors: Map<string, string[]>) => {
    const scored = column.map((id, index) => {
      const refs = (neighbors.get(id) || [])
        .map((neighbor) => rowOf.get(neighbor))
        .filter((value): value is number => value !== undefined)
      const barycenter = refs.length ? refs.reduce((sum, value) => sum + value, 0) / refs.length : index
      return { id, barycenter, order: authoringOrderOf(byId.get(id)!), index }
    })
    scored.sort((left, right) => (
      left.barycenter - right.barycenter || left.order - right.order || left.index - right.index
    ))
    scored.forEach((entry, index) => rowOf.set(entry.id, index))
    return scored.map((entry) => entry.id)
  }
  for (let pass = 0; pass < 2; pass += 1) {
    for (let index = 1; index < columns.length; index += 1) {
      columns[index] = sortColumn(columns[index], incoming)
    }
    for (let index = columns.length - 2; index >= 0; index -= 1) {
      columns[index] = sortColumn(columns[index], outgoing)
    }
  }

  // 3) 孤立节点不参与分层：各自成列接在末尾，避免和排序好的链挤在同一列。
  for (const node of nodes) {
    if (touched.has(node.id)) continue
    columns.push([node.id])
  }

  // 4) 落点：每 maxColumns 列折一行，行内都从 origin.x 重新开始。
  const bandOf = (column: number) => Math.floor(column / cfg.maxColumns)
  const bandHeights = new Map<number, number>()
  columns.forEach((column, index) => {
    const band = bandOf(index)
    bandHeights.set(band, Math.max(bandHeights.get(band) || 1, Math.max(1, column.length)))
  })
  const bandOffsets = new Map<number, number>()
  let cursorY = cfg.origin.y
  for (const band of [...bandHeights.keys()].sort((left, right) => left - right)) {
    bandOffsets.set(band, cursorY)
    cursorY += (bandHeights.get(band) || 1) * cfg.rowStride + cfg.bandGap
  }

  const placed = new Map<string, { x: number; y: number }>()
  columns.forEach((column, index) => {
    const x = cfg.origin.x + (index % cfg.maxColumns) * cfg.columnStride
    const baseY = bandOffsets.get(bandOf(index)) ?? cfg.origin.y
    column.forEach((id, row) => {
      placed.set(id, { x: Math.round(x), y: Math.round(baseY + row * cfg.rowStride) })
    })
  })

  return nodes.map((node) => {
    const point = placed.get(node.id) || cfg.origin
    const previousPose = node.data.raw?.pose && typeof node.data.raw.pose === 'object' ? node.data.raw.pose : {}
    return {
      ...node,
      position: point,
      data: {
        ...node.data,
        raw: { ...node.data.raw, pose: { ...previousPose, position: { x: point.x, y: point.y } } },
      },
    }
  })
}

/** 本地结构检查：环、缺描述、悬空连线；用于画布即时提示。 */
export function localGraphIssues(
  nodes: EditorNode[],
  edges: EditorEdge[],
): { nodeIssues: Map<string, string>; summary: string[] } {
  const nodeIssues = new Map<string, string>()
  const summary: string[] = []
  const ids = new Set(nodes.map((node) => node.id))

  for (const node of nodes) {
    const description = String(node.data.raw.description || '').trim()
    if (!description) nodeIssues.set(node.id, '缺少步骤说明')
  }

  const dangling = edges.filter((edge) => !ids.has(edge.source) || !ids.has(edge.target))
  if (dangling.length) summary.push(`${dangling.length} 条连线指向已删除的节点`)

  // Kahn 拓扑排序，剩余即环
  const indegree = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  for (const id of ids) {
    indegree.set(id, 0)
    outgoing.set(id, [])
  }
  for (const edge of edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
    outgoing.get(edge.source)!.push(edge.target)
  }
  const queue = [...ids].filter((id) => (indegree.get(id) || 0) === 0)
  let resolved = 0
  while (queue.length) {
    const id = queue.shift()!
    resolved += 1
    for (const child of outgoing.get(id) || []) {
      const remaining = (indegree.get(child) || 0) - 1
      indegree.set(child, remaining)
      if (remaining === 0) queue.push(child)
    }
  }
  if (resolved !== ids.size) summary.push('依赖存在环，后端会拒绝保存')

  // 顶层且未被任何连线触及的节点会被后端自动包进 with parallel()：
  // 它们之间没有拓扑依赖，运行期同时开始（同一设备的动作仍由设备锁排队）。
  // 这里只做只读提示，不拦截保存。
  const isolated = nodes.filter(
    (node) => !edges.some((edge) => edge.source === node.id || edge.target === node.id),
  )
  const parallelizable = isolated.filter((node) => String(node.data.raw.type || '') !== 'group')
  if (parallelizable.length >= 2) {
    summary.push(`${parallelizable.length} 个节点互不连线，将并行执行（同一设备仍会排队）`)
  } else if (isolated.length && nodes.length > 1) {
    summary.push(`${isolated.length} 个节点未连线`)
  }

  return { nodeIssues, summary }
}

/**
 * 是否需要自动排布。
 *
 * 本机目录里 148 节点的那份工作流 `pose` 全是 `{}`（CWM 导出时不写坐标），
 * 直接上画布会所有节点叠在原点；另一份坐标跨度到 4 万，`fitView` 之后什么都看不清。
 * 部分节点缺坐标同样要排：缺的那些会全部落在原点，和已有节点叠在一起。
 * 两种情况都退回分层布局，并标记为已修改（坐标要能写回）。
 */
export function needsAutoLayout(nodes: EditorNode[]): boolean {
  if (nodes.length < 2) return false
  const positioned = nodes.filter((node) => {
    const position = node.data.raw?.pose?.position
    return Number.isFinite(Number(position?.x)) && Number.isFinite(Number(position?.y))
  })
  if (positioned.length !== nodes.length) return true
  const xs = nodes.map((node) => node.position.x)
  const ys = nodes.map((node) => node.position.y)
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))
  // 跨度不足 8px 等于全叠在一起（CWM 有时写 0,0 而不是空对象）。
  return span < 8 || span > 12000
}

/** 拓扑序（执行顺序）；环上剩余节点按画布 y 坐标补在尾部，不丢节点。 */
export function topologicalOrder(nodes: EditorNode[], edges: EditorEdge[]): EditorNode[] {
  const ids = new Set(nodes.map((node) => node.id))
  const indegree = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  for (const id of ids) {
    indegree.set(id, 0)
    outgoing.set(id, [])
  }
  const sequenceEdges = edges.filter((edge) => (edge.data?.kind || 'sequence') === 'sequence')
  for (const edge of sequenceEdges) {
    if (!ids.has(edge.source) || !ids.has(edge.target)) continue
    indegree.set(edge.target, (indegree.get(edge.target) || 0) + 1)
    outgoing.get(edge.source)!.push(edge.target)
  }
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const queue = [...ids]
    .filter((id) => (indegree.get(id) || 0) === 0)
    .sort((left, right) => (byId.get(left)!.position.y - byId.get(right)!.position.y))
  const ordered: EditorNode[] = []
  const seen = new Set<string>()
  while (queue.length) {
    const id = queue.shift()!
    if (seen.has(id)) continue
    seen.add(id)
    ordered.push(byId.get(id)!)
    for (const child of outgoing.get(id) || []) {
      const remaining = (indegree.get(child) || 0) - 1
      indegree.set(child, remaining)
      if (remaining === 0) queue.push(child)
    }
  }
  // 环上的节点：按 y 坐标追加，保证仿真能走完整个图。
  const rest = nodes
    .filter((node) => !seen.has(node.id))
    .sort((left, right) => left.position.y - right.position.y)
  return [...ordered, ...rest]
}

/** 模板插入结果：新节点/新边，以及旧 UUID → 新 UUID 的映射。 */
export interface ClonedGraph {
  nodes: RawWorkflowNode[]
  edges: RawWorkflowEdge[]
  idMap: Map<string, string>
}

/**
 * 把另一份工作流的节点/边克隆进当前草稿。
 *
 * 三件事必须重写，否则整图保存会被后端拒绝：
 * 1. 节点/连线 UUID —— 后端要求 UUID 在全局唯一，复用会报「已被其他工作流占用」。
 * 2. `meta_data.unilab.authoring_source_order` —— 后端校验「作者源码节点顺序不能
 *    重复」（3003），同工作流内插入第二段必然撞号，因此整体平移到 `orderBase` 之后。
 * 3. `authoring_result_name` —— 与序号同源的 `step_xxx` 名，一并按新序号重写。
 * 句柄 UUID 属于模板身份，可以照抄。
 */
export function cloneGraphInto(
  sourceNodes: RawWorkflowNode[],
  sourceEdges: RawWorkflowEdge[],
  offset: { x: number; y: number },
  makeUuid: () => string,
  orderBase = 0,
): ClonedGraph {
  const idMap = new Map<string, string>()
  for (const node of sourceNodes) {
    const uuid = String(node.uuid || '')
    if (uuid) idMap.set(uuid, makeUuid())
  }
  const nodes = sourceNodes.map((node, index) => {
    const position = nodePosition(node)
    const unilab = node.meta_data?.unilab
    let metaData = node.meta_data
    if (unilab && typeof unilab === 'object') {
      const originalOrder = Number(unilab.authoring_source_order)
      const order = orderBase + (Number.isFinite(originalOrder) ? originalOrder : index)
      metaData = {
        ...node.meta_data,
        unilab: {
          ...unilab,
          // 没有作者序号的节点（手工拖入的）不加这个字段，避免伪造编排来源。
          ...(Number.isFinite(originalOrder) ? { authoring_source_order: order } : {}),
          ...(typeof unilab.authoring_result_name === 'string'
            ? { authoring_result_name: `step_${String(order + 1).padStart(3, '0')}` }
            : {}),
        },
      }
    }
    return {
      ...node,
      uuid: idMap.get(String(node.uuid || '')) || makeUuid(),
      meta_data: metaData,
      pose: {
        ...(node.pose && typeof node.pose === 'object' ? node.pose : {}),
        position: {
          x: Math.round(position.x + offset.x),
          y: Math.round(position.y + offset.y),
        },
      },
    } satisfies RawWorkflowNode
  })
  const edges = sourceEdges
    .filter((edge) => idMap.has(String(edge.source_node_uuid || '')) && idMap.has(String(edge.target_node_uuid || '')))
    .map((edge) => ({
      ...edge,
      uuid: makeUuid(),
      source_node_uuid: idMap.get(String(edge.source_node_uuid))!,
      target_node_uuid: idMap.get(String(edge.target_node_uuid))!,
    }) satisfies RawWorkflowEdge)
  return { nodes, edges, idMap }
}

/** 当前图里最大的作者序号 + 1；插入模板时用来避让既有序号。 */
export function nextAuthoringOrder(raws: RawWorkflowNode[]): number {
  let max = -1
  for (const raw of raws) {
    const value = Number(raw.meta_data?.unilab?.authoring_source_order)
    if (Number.isFinite(value) && value > max) max = value
  }
  return max + 1
}

/** 节点绑定的设备 ID；仿真空间按它把步骤落到工位上。 */
export function nodeDeviceId(raw: RawWorkflowNode): string {
  const binding = raw.meta_data?.unilab?.executor_binding
  const deviceId = binding && typeof binding === 'object' ? String(binding.device_id || '') : ''
  return deviceId || String(raw.meta_data?.unilab?.composite?.device_id || '') || ''
}

/** 撤销栈：保存原始节点/边快照，避免与画布内部状态纠缠。 */
export interface EditorSnapshot {  nodes: RawWorkflowNode[]
  edges: RawWorkflowEdge[]
}

export interface EditorHistory {
  past: EditorSnapshot[]
  future: EditorSnapshot[]
}

export function pushHistory(
  history: EditorHistory,
  snapshot: EditorSnapshot,
  limit = 60,
): EditorHistory {
  const past = [...history.past, snapshot]
  return {
    past: past.length > limit ? past.slice(past.length - limit) : past,
    future: [],
  }
}
