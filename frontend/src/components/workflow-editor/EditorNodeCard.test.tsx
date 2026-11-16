import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { EditorNodeCard } from './EditorNodeCard'
import { NodeActionsContext, type EditorNodeActions } from './nodeActions'
import type { EditorNode } from '../../lib/workflowEditorModel'

/**
 * 节点卡片是纯展示组件，句柄用替身渲染成带 id 的 span，
 * 这样能在 jsdom 里断言「哪些端口被画出来」。
 */
vi.mock('@xyflow/react', async () => {
  const React = await import('react')
  return {
    Handle: ({ id, type, title }: Record<string, any>) =>
      React.createElement('span', { 'data-handle': id, 'data-port-type': type, title }),
    Position: { Left: 'left', Right: 'right' },
  }
})

const handles = [
  { uuid: 'h-ready-src', handleKey: 'ready', ioType: 'source', dataKey: 'ready', dataSource: 'goal', displayName: 'ready', type: 'object', required: false },
  { uuid: 'h-ready-tgt', handleKey: 'ready', ioType: 'target', dataKey: 'ready', dataSource: 'goal', displayName: 'ready', type: 'object', required: false },
  { uuid: 'h-timeout', handleKey: 'timeout', ioType: 'target', dataKey: 'timeout', dataSource: 'goal', displayName: 'timeout', type: 'number', required: true },
  { uuid: 'h-weight', handleKey: 'weight', ioType: 'target', dataKey: 'weight', dataSource: 'goal', displayName: 'weight', type: 'number', required: false },
]

function makeNode(overrides: Partial<EditorNode['data']> = {}): EditorNode {
  return {
    id: 'node-1',
    type: 'unilab',
    position: { x: 0, y: 0 },
    data: {
      raw: { uuid: 'node-1', name: '称量加样', description: '称 3 mg', kind: 'action', disabled: false },
      handles,
      subtitle: 'gn_solid_weighing · weigh',
      linkedSourceHandles: [],
      linkedTargetHandles: ['h-weight'],
      showDataPorts: false,
      ...overrides,
    },
  } as EditorNode
}

function renderCard(data: EditorNode['data'], actions?: EditorNodeActions, selected = false) {
  const props = { data, selected, id: 'node-1', type: 'unilab', dragging: false, isConnectable: true, zIndex: 0, positionAbsoluteX: 0, positionAbsoluteY: 0 } as any
  if (!actions) return render(<EditorNodeCard {...props} />)
  return render(
    <NodeActionsContext.Provider value={actions}>
      <EditorNodeCard {...props} />
    </NodeActionsContext.Provider>,
  )
}

describe('EditorNodeCard', () => {
  it('only renders the ready ports plus already-linked data ports by default', () => {
    const { container } = renderCard(makeNode().data)
    const rendered = [...container.querySelectorAll('[data-handle]')].map((node) => node.getAttribute('data-handle'))
    expect(rendered).toContain('h-ready-src')
    expect(rendered).toContain('h-ready-tgt')
    // 已连线的参数端口保留锚点，未连线的 timeout 不出现
    expect(rendered).toContain('h-weight')
    expect(rendered).not.toContain('h-timeout')
    expect(container.querySelector('.wf-port-rail')).toBeNull()
  })

  it('expands a parameter rail when data-port mode is on', () => {
    const { container } = renderCard(makeNode({ showDataPorts: true }).data)
    const rows = container.querySelectorAll('.wf-port-row')
    expect(rows).toHaveLength(2)
    expect(screen.getByText('超时')).toBeInTheDocument()
    expect(screen.getByText('加料重量')).toBeInTheDocument()
    expect(screen.getByText('s')).toBeInTheDocument()
    const rendered = [...container.querySelectorAll('[data-handle]')].map((node) => node.getAttribute('data-handle'))
    expect(rendered).toContain('h-timeout')
    expect(rendered).toContain('h-weight')
  })

  it('marks nodes that lost their description as having an issue', () => {
    renderCard(makeNode({ issue: '缺少步骤说明' }).data)
    expect(screen.getByText('缺少步骤说明')).toBeInTheDocument()
  })

  it('shows the two corner dots only on a selected node and wires their actions', () => {
    const actions = { toggleDisabled: vi.fn(), removeNode: vi.fn() }
    const { container } = renderCard(makeNode().data, actions, true)

    expect(container.querySelector('.wf-node.is-selected')).toBeTruthy()
    // 角标是卡片直接子节点，不进头部文档流
    expect(container.querySelector('.wf-node > .wf-node-del')).toBeTruthy()
    expect(container.querySelector('.wf-node > .wf-node-dis')).toBeTruthy()
    expect(container.querySelector('.wf-node-head > .wf-node-del')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '禁用 称量加样' }))
    expect(actions.toggleDisabled).toHaveBeenCalledWith('node-1')

    fireEvent.click(screen.getByRole('button', { name: '删除 称量加样' }))
    expect(actions.removeNode).toHaveBeenCalledWith('node-1')
  })

  it('turns the disable dot into an enable dot and hides it for group nodes', () => {
    const actions = { toggleDisabled: vi.fn(), removeNode: vi.fn() }
    const { container } = renderCard(makeNode({
      raw: { uuid: 'node-1', name: '称量加样', description: '称 3 mg', kind: 'action', disabled: true },
    }).data, actions, true)

    expect(container.querySelector('.wf-node-dis.is-on')?.textContent).toBe('↻')
    expect(screen.getByRole('button', { name: '启用 称量加样' })).toBeInTheDocument()
  })

  it('keeps only the delete dot on group nodes', () => {
    const { container } = renderCard(makeNode({
      raw: { uuid: 'node-1', name: '注释', description: '分组', kind: 'group' },
    }).data, undefined, true)
    expect(container.querySelector('.wf-node-dis')).toBeNull()
    expect(container.querySelector('.wf-node-del')).toBeTruthy()
  })
})
