import { describe, expect, it } from 'vitest'
import {
  DATA_EDGE_CLASS,
  SEQUENCE_EDGE_CLASS,
  buildHandleKindIndex,
  buildNewRawNode,
  canConnectHandles,
  cloneGraphInto,
  layeredLayout,
  localGraphIssues,
  needsAutoLayout,
  READY_KEY,
  toFlowEdges,
  toFlowNodes,
  toRawEdges,
  toRawNodes,
  topologicalOrder,
  type EditorNode,
} from './workflowEditorModel'
import type { NodeTemplateHandle, NodeTemplateSummary } from './edgeClient'

const template: NodeTemplateSummary = {
  uuid: 'tmpl-1',
  name: 'execute_command',
  displayName: '执行指令',
  type: 'UniLabJsonCommand',
  nodeType: 'ILab',
  resourceTemplateUuid: 'res-1',
  resourceDisplayName: '机械手',
}

function handle(overrides: Partial<NodeTemplateHandle> & { uuid: string; handleKey: string }): NodeTemplateHandle {
  return {
    ioType: 'target',
    dataKey: overrides.handleKey,
    dataSource: 'goal',
    displayName: overrides.handleKey,
    type: 'number',
    required: false,
    ...overrides,
  }
}

const readySource = handle({ uuid: 'h-ready-src', handleKey: READY_KEY, ioType: 'source', type: 'object' })
const readyTarget = handle({ uuid: 'h-ready-tgt', handleKey: READY_KEY, ioType: 'target', type: 'object' })
const timeoutTarget = handle({ uuid: 'h-timeout', handleKey: 'timeout' })
const speedTarget = handle({ uuid: 'h-speed', handleKey: 'x_speed' })

describe('workflowEditorModel', () => {
  it('only allows ready→ready or same-key data connections', () => {
    expect(canConnectHandles(readySource, readyTarget)).toEqual({ ok: true })
    expect(canConnectHandles(readySource, timeoutTarget).ok).toBe(false)
    expect(canConnectHandles(timeoutTarget, readyTarget).ok).toBe(false)
    expect(canConnectHandles(timeoutTarget, speedTarget).ok).toBe(false)
    expect(canConnectHandles(
      handle({ uuid: 'h-src', handleKey: 'volume', ioType: 'source' }),
      handle({ uuid: 'h-dst', handleKey: 'volume' }),
    )).toEqual({ ok: true })
  })

  it('keeps untouched raw fields when serialising back to the backend shape', () => {
    const raw = {
      uuid: 'node-1',
      name: '原始名',
      type: 'UniLabJsonCommand',
      workflow_node_template_uuid: template.uuid,
      description: '原始说明',
      pose: { position: { x: 10, y: 20 }, rotation: { x: 0, y: 0, z: 0 } },
      // 编排元数据必须原样保留，整图替换下丢失即等于删除
      meta_data: { unilab: { authoring_source_order: 4, executor_binding: { device_id: 'gn_robotic_arm' } } },
    }
    const [flowNode] = toFlowNodes([raw], new Map([[template.uuid, template]]), new Map())
    const moved: EditorNode = { ...flowNode, position: { x: 300, y: 120.6 } }
    const [out] = toRawNodes([moved], new Map([['node-1', raw]]))

    expect(out.name).toBe('原始名')
    expect(out.meta_data).toEqual(raw.meta_data)
    expect(out.pose.position).toEqual({ x: 300, y: 121 })
    expect(out.pose.rotation).toEqual({ x: 0, y: 0, z: 0 })
  })

  it('round-trips edges through handle uuids', () => {
    const rawEdge = {
      uuid: 'edge-1',
      source_node_uuid: 'node-1',
      target_node_uuid: 'node-2',
      source_handle_uuid: readySource.uuid,
      target_handle_uuid: readyTarget.uuid,
      meta_data: { note: 'keep-me' },
    }
    const [flowEdge] = toFlowEdges([rawEdge])
    expect(flowEdge.sourceHandle).toBe(readySource.uuid)
    expect(flowEdge.targetHandle).toBe(readyTarget.uuid)

    const [out] = toRawEdges([flowEdge], new Map([['edge-1', rawEdge]]))
    expect(out.source_node_uuid).toBe('node-1')
    expect(out.meta_data).toEqual({ note: 'keep-me' })
  })

  it('builds a new node that satisfies the description requirement', () => {
    const raw = buildNewRawNode(template, { goal_default: { timeout: 180 } }, { x: 5, y: 9 }, '执行指令 2', 'new-uuid')
    expect(raw.uuid).toBe('new-uuid')
    expect(raw.description).toBe('执行指令 2')
    expect(raw.param).toEqual({ timeout: 180 })
    expect(raw.pose.position).toEqual({ x: 5, y: 9 })
  })

  it('lays out nodes by dependency layers and reports cycles', () => {
    const raws = [
      { uuid: 'a', name: 'A', type: 'UniLabJsonCommand', workflow_node_template_uuid: template.uuid, description: 'A' },
      { uuid: 'b', name: 'B', type: 'UniLabJsonCommand', workflow_node_template_uuid: template.uuid, description: 'B' },
    ]
    const nodes = toFlowNodes(raws, new Map([[template.uuid, template]]), new Map())
    const edges = toFlowEdges([
      { uuid: 'e1', source_node_uuid: 'a', target_node_uuid: 'b', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
    ])
    const laid = layeredLayout(nodes, edges)
    expect(laid[0].position.x).toBeLessThan(laid[1].position.x)

    const cyclic = toFlowEdges([
      { uuid: 'e1', source_node_uuid: 'a', target_node_uuid: 'b', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
      { uuid: 'e2', source_node_uuid: 'b', target_node_uuid: 'a', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
    ])
    const issues = localGraphIssues(nodes, cyclic)
    expect(issues.summary.join(' ')).toContain('环')
  })

  it('flags nodes without a description', () => {
    const nodes = toFlowNodes(
      [{ uuid: 'x', name: '未命名', type: 'UniLabJsonCommand', workflow_node_template_uuid: template.uuid, description: '' }],
      new Map([[template.uuid, template]]),
      new Map(),
    )
    const issues = localGraphIssues(nodes, [])
    expect(issues.nodeIssues.get('x')).toBe('缺少步骤说明')
  })

  it('classifies edges into sequence and data lines from handle kinds', () => {
    const kinds = buildHandleKindIndex(new Map([[template.uuid, [readySource, readyTarget, timeoutTarget]]]))
    const kindOf = (uuid: string | undefined) => (uuid ? kinds.get(uuid) || 'sequence' : 'sequence')
    const edges = toFlowEdges([
      { uuid: 'seq', source_node_uuid: 'a', target_node_uuid: 'b', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
      { uuid: 'data', source_node_uuid: 'a', target_node_uuid: 'b', source_handle_uuid: timeoutTarget.uuid, target_handle_uuid: timeoutTarget.uuid },
    ], kindOf)
    expect(edges[0].data?.kind).toBe('sequence')
    expect(edges[0].className).toBe(SEQUENCE_EDGE_CLASS)
    expect(edges[1].data?.kind).toBe('data')
    expect(edges[1].className).toBe(DATA_EDGE_CLASS)
  })

  it('clones a graph with fresh node and edge uuids but keeps template handles', () => {
    const sourceNodes = [
      { uuid: 'n1', name: 'A', pose: { position: { x: 0, y: 0 } }, meta_data: { unilab: { authoring_source_order: 1 } } },
      { uuid: 'n2', name: 'B', pose: { position: { x: 300, y: 0 } } },
    ]
    const sourceEdges = [
      { uuid: 'e1', source_node_uuid: 'n1', target_node_uuid: 'n2', source_handle_uuid: 'h-ready', target_handle_uuid: 'h-ready' },
      // 指向不存在节点的边必须被丢掉，否则整图保存会报悬空连线
      { uuid: 'e2', source_node_uuid: 'n1', target_node_uuid: 'ghost' },
    ]
    let counter = 0
    const cloned = cloneGraphInto(sourceNodes, sourceEdges, { x: 1000, y: 50 }, () => `new-${counter += 1}`)

    expect(cloned.nodes.map((node) => node.uuid)).toEqual(['new-1', 'new-2'])
    expect(cloned.nodes[0].meta_data).toEqual(sourceNodes[0].meta_data)
    expect(cloned.nodes[1].pose.position).toEqual({ x: 1300, y: 50 })
    expect(cloned.edges).toHaveLength(1)
    expect(cloned.edges[0].source_node_uuid).toBe('new-1')
    expect(cloned.edges[0].target_node_uuid).toBe('new-2')
    expect(cloned.edges[0].source_handle_uuid).toBe('h-ready')
  })

  it('detects graphs that need an automatic layout', () => {
    const stacked = toFlowNodes([
      { uuid: 'a', name: 'A', pose: {}, description: 'A' },
      { uuid: 'b', name: 'B', description: 'B' },
    ], new Map(), new Map())
    expect(needsAutoLayout(stacked)).toBe(true)

    const positioned = toFlowNodes([
      { uuid: 'a', name: 'A', pose: { position: { x: 0, y: 0 } } },
      { uuid: 'b', name: 'B', pose: { position: { x: 300, y: 0 } } },
    ], new Map(), new Map())
    expect(needsAutoLayout(positioned)).toBe(false)

    // CWM 导出的坐标可以跨到四万，fitView 之后不可读，同样退回分层布局
    const sprawled = toFlowNodes([
      { uuid: 'a', name: 'A', pose: { position: { x: 0, y: 0 } } },
      { uuid: 'b', name: 'B', pose: { position: { x: 42608, y: 48 } } },
    ], new Map(), new Map())
    expect(needsAutoLayout(sprawled)).toBe(true)

    // 部分节点缺坐标：缺的会落在原点压住已有节点，同样要重排
    const partial = toFlowNodes([
      { uuid: 'a', name: 'A', pose: { position: { x: 0, y: 0 } } },
      { uuid: 'b', name: 'B', pose: { position: { x: 300, y: 0 } } },
      { uuid: 'c', name: 'C' },
    ], new Map(), new Map())
    expect(needsAutoLayout(partial)).toBe(true)

    // 坐标全写 0,0（CWM 有时不写空对象而是写零点）也算没排过
    const zeroed = toFlowNodes([
      { uuid: 'a', name: 'A', pose: { position: { x: 0, y: 0 } } },
      { uuid: 'b', name: 'B', pose: { position: { x: 0, y: 0 } } },
    ], new Map(), new Map())
    expect(needsAutoLayout(zeroed)).toBe(true)
  })

  it('orders steps by sequence edges and keeps cycle members at the tail', () => {
    const raws = ['a', 'b', 'c', 'd'].map((id) => ({
      uuid: id, name: id, description: id, pose: { position: { x: 0, y: id.charCodeAt(0) } },
    }))
    const nodes = toFlowNodes(raws, new Map(), new Map())
    const edges = toFlowEdges([
      { uuid: 'e1', source_node_uuid: 'b', target_node_uuid: 'c', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
      { uuid: 'e2', source_node_uuid: 'c', target_node_uuid: 'd', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
    ])
    const ordered = topologicalOrder(nodes, edges).map((node) => node.id)
    expect(ordered.indexOf('b')).toBeLessThan(ordered.indexOf('c'))
    expect(ordered.indexOf('c')).toBeLessThan(ordered.indexOf('d'))
    // a 是孤立节点，但不能丢
    expect(ordered).toHaveLength(4)
  })

  it('folds a long serial chain into 15-column bands instead of one straight line', () => {
    const raws = Array.from({ length: 20 }, (_, index) => ({
      uuid: `n${index + 1}`,
      name: `N${index + 1}`,
      description: `步骤 ${index + 1}`,
      meta_data: { unilab: { authoring_source_order: index + 1 } },
    }))
    const edges = toFlowEdges(Array.from({ length: 19 }, (_, index) => ({
      uuid: `e${index + 1}`,
      source_node_uuid: `n${index + 1}`,
      target_node_uuid: `n${index + 2}`,
      source_handle_uuid: readySource.uuid,
      target_handle_uuid: readyTarget.uuid,
    })))
    const laid = layeredLayout(toFlowNodes(raws, new Map(), new Map()), edges)
    const byId = new Map(laid.map((node) => [node.id, node]))

    // 前 15 个节点横向排在同一行，x 严格递增
    for (let index = 2; index <= 15; index += 1) {
      expect(byId.get(`n${index}`)!.position.y).toBe(byId.get('n1')!.position.y)
      expect(byId.get(`n${index}`)!.position.x).toBeGreaterThan(byId.get(`n${index - 1}`)!.position.x)
    }
    // 第 16 个节点折回第一列，纵向接在上一行之下
    expect(byId.get('n16')!.position.x).toBe(byId.get('n1')!.position.x)
    expect(byId.get('n16')!.position.y).toBeGreaterThan(byId.get('n1')!.position.y)
    // 第二条带里继续向右递增
    expect(byId.get('n20')!.position.x).toBeGreaterThan(byId.get('n16')!.position.x)
  })

  it('stacks siblings in one column and writes the positions back into raw pose', () => {
    const raws = ['root', 'left', 'right'].map((id, index) => ({
      uuid: id,
      name: id,
      description: id,
      meta_data: { unilab: { authoring_source_order: index + 1 } },
    }))
    const edges = toFlowEdges([
      { uuid: 'e1', source_node_uuid: 'root', target_node_uuid: 'left', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
      { uuid: 'e2', source_node_uuid: 'root', target_node_uuid: 'right', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
    ])
    const laid = layeredLayout(toFlowNodes(raws, new Map(), new Map()), edges)
    const byId = new Map(laid.map((node) => [node.id, node]))

    // 同一层的兄弟节点共列，纵向错开
    expect(byId.get('left')!.position.x).toBe(byId.get('right')!.position.x)
    expect(byId.get('left')!.position.y).not.toBe(byId.get('right')!.position.y)
    // 布局必须落回 draft，否则下一次 applyRawGraph 会按空 pose 把节点叠回原点
    expect(byId.get('left')!.data.raw.pose.position).toEqual(byId.get('left')!.position)
  })

  it('gives unconnected nodes their own column after the ranked ones', () => {
    const raws = ['a', 'b', 'z'].map((id, index) => ({
      uuid: id,
      name: id,
      description: id,
      meta_data: { unilab: { authoring_source_order: index + 1 } },
    }))
    const edges = toFlowEdges([
      { uuid: 'e1', source_node_uuid: 'a', target_node_uuid: 'b', source_handle_uuid: readySource.uuid, target_handle_uuid: readyTarget.uuid },
    ])
    const laid = layeredLayout(toFlowNodes(raws, new Map(), new Map()), edges)
    const byId = new Map(laid.map((node) => [node.id, node]))

    expect(byId.get('z')!.position.x).toBeGreaterThan(byId.get('b')!.position.x)
    // 不能被塞进 a 那一列和别的节点重叠
    expect(byId.get('z')!.position.y).toBe(byId.get('a')!.position.y)
  })
})
