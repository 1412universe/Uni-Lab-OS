import { describe, expect, it } from 'vitest'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'
import { layoutWorkflowGraph } from './workflowGraphLayout'

function node(uuid: string, authoringOrder: number): WorkflowGraphNode {
  return { uuid, name: uuid.toUpperCase(), type: 'ILab', kind: 'action', authoringOrder, disabled: false }
}

function edge(sourceNodeUuid: string, targetNodeUuid: string): WorkflowGraphEdge {
  return {
    uuid: `${sourceNodeUuid}-${targetNodeUuid}`,
    sourceNodeUuid,
    targetNodeUuid,
  }
}

describe('layoutWorkflowGraph', () => {
  it('keeps parallel nodes in one topological rank and wraps later ranks as a snake', () => {
    const layout = layoutWorkflowGraph(
      [node('f', 5), node('c', 2), node('a', 0), node('e', 4), node('b', 1), node('d', 3)],
      [edge('a', 'b'), edge('a', 'c'), edge('b', 'd'), edge('c', 'd'), edge('d', 'e'), edge('e', 'f')],
      { ranksPerBand: 3 },
    )
    const positions = new Map(layout.nodes.map((item) => [item.node.uuid, item]))

    expect(positions.get('b')?.rank).toBe(1)
    expect(positions.get('c')?.rank).toBe(1)
    expect(positions.get('b')?.x).toBe(positions.get('c')?.x)
    expect(positions.get('d')?.x).toBeGreaterThan(positions.get('b')!.x)

    expect(positions.get('e')?.band).toBe(1)
    expect(positions.get('e')?.x).toBe(positions.get('d')?.x)
    expect(positions.get('e')?.y).toBeGreaterThan(positions.get('d')!.y)
    expect(positions.get('f')?.x).toBeLessThan(positions.get('e')!.x)
  })

  it('calculates child ranks independently and uses groups only as presentation frames', () => {
    const group: WorkflowGraphNode = {
      uuid: 'group',
      name: '展示分组',
      type: 'group',
      kind: 'group',
      authoringOrder: 0,
      disabled: false,
    }
    const first = { ...node('first', 1), parentUuid: group.uuid }
    const last = { ...node('last', 3), parentUuid: group.uuid }
    const layout = layoutWorkflowGraph(
      [group, first, node('middle', 2), last],
      [edge('first', 'middle'), edge('middle', 'last')],
    )
    const positions = new Map(layout.nodes.map((item) => [item.node.uuid, item]))

    expect(positions.get('first')?.rank).toBe(0)
    expect(positions.get('middle')?.rank).toBe(1)
    expect(positions.get('last')?.rank).toBe(2)
    expect(layout.groups[0].children.map((child) => child.node.uuid)).toEqual(['first', 'last'])
    expect(layout.groups[0].frames).toHaveLength(2)
    expect(layout.groups[0].frames.every((frame) => {
      const middle = positions.get('middle')!
      const middleCenterX = middle.x + middle.width / 2
      const middleCenterY = middle.y + middle.height / 2
      return middleCenterX < frame.x
        || middleCenterX > frame.x + frame.width
        || middleCenterY < frame.y
        || middleCenterY > frame.y + frame.height
    })).toBe(true)
    expect(layout.warnings).toEqual([])
  })
})
