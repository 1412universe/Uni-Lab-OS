import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { WorkflowGraphEdge, WorkflowGraphNode } from '../types'
import { WorkflowDag } from './WorkflowDag'

function action(uuid: string, name: string, order: number, parentUuid?: string): WorkflowGraphNode {
  return {
    uuid,
    name,
    type: 'ILab',
    kind: 'action',
    authoringOrder: order,
    parentUuid,
    disabled: false,
  }
}

function group(
  uuid: string,
  name: string,
  order: number,
  parallelScope?: string,
): WorkflowGraphNode {
  return {
    uuid,
    name,
    type: 'group',
    kind: 'group',
    authoringOrder: order,
    parallelScope,
    disabled: false,
  }
}

function edge(uuid: string, sourceNodeUuid: string, targetNodeUuid: string): WorkflowGraphEdge {
  return { uuid, sourceNodeUuid, targetNodeUuid }
}

describe('WorkflowDag', () => {
  it('labels a parallel control edge and highlights both endpoints when selected', () => {
    const nodes = [
      action('setup-beaker', '烧杯准备完成', 0),
      action('setup-vial', '样品瓶准备完成', 1),
      group('photo-group', '烧杯在 S05 拍照', 2, 'parallel-scope'),
      action('photo', 'S05 烧杯拍照检测', 3, 'photo-group'),
      group('cap-group', '样品瓶在 S08 开盖', 4, 'parallel-scope'),
      action('cap', 'S08 样品瓶开关盖', 5, 'cap-group'),
      action('join', '原子取料倒液放料', 6),
    ]
    const edges = [
      edge('entry-beaker', 'setup-beaker', 'photo'),
      edge('entry-barrier', 'setup-vial', 'photo'),
      edge('entry-vial', 'setup-vial', 'cap'),
      edge('join-photo', 'photo', 'join'),
      edge('join-cap', 'cap', 'join'),
    ]
    render(<WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />)

    const selectedEdge = screen.getByRole('button', {
      name: '并行入口：样品瓶准备完成 → S05 烧杯拍照检测',
    })
    fireEvent.click(selectedEdge)

    expect(selectedEdge).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('article', { name: /样品瓶准备完成.*已选连线起点/ })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /S05 烧杯拍照检测.*已选连线终点/ })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('并行入口')
    expect(screen.getByRole('status')).toHaveTextContent('样品瓶准备完成 → S05 烧杯拍照检测')
  })

  it('moves every member with its group and can restore automatic layout', () => {
    const nodes = [
      group('transfer-group', '组合动作', 0),
      action('pick', '取料', 1, 'transfer-group'),
      action('place', '放料', 2, 'transfer-group'),
      action('finish', '完成', 3),
    ]
    const edges = [edge('pick-place', 'pick', 'place'), edge('place-finish', 'place', 'finish')]
    const { container } = render(
      <WorkflowDag nodes={nodes} edges={edges} loading={false} error={false} />,
    )
    const pick = screen.getByRole('article', { name: /^取料/ })
    const place = screen.getByRole('article', { name: /^放料/ })
    const finish = screen.getByRole('article', { name: /^完成/ })
    const frame = container.querySelector('.workflow-dag-group-frame') as HTMLElement
    const path = container.querySelector('[data-edge-uuid="pick-place"] .workflow-dag-edge') as SVGPathElement
    const initial = {
      pickX: Number.parseFloat(pick.style.left),
      pickY: Number.parseFloat(pick.style.top),
      placeX: Number.parseFloat(place.style.left),
      placeY: Number.parseFloat(place.style.top),
      finishX: Number.parseFloat(finish.style.left),
      finishY: Number.parseFloat(finish.style.top),
      frameX: Number.parseFloat(frame.style.left),
      frameY: Number.parseFloat(frame.style.top),
      path: path.getAttribute('d'),
    }

    fireEvent.pointerDown(pick, { pointerId: 1, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(pick, { pointerId: 1, buttons: 1, clientX: 110, clientY: 110 })
    fireEvent.pointerMove(pick, { pointerId: 1, buttons: 1, clientX: 140, clientY: 125 })
    fireEvent.pointerUp(pick, { pointerId: 1, clientX: 140, clientY: 125 })

    expect(Number.parseFloat(pick.style.left)).toBe(initial.pickX + 40)
    expect(Number.parseFloat(pick.style.top)).toBe(initial.pickY + 25)
    expect(Number.parseFloat(place.style.left)).toBe(initial.placeX + 40)
    expect(Number.parseFloat(place.style.top)).toBe(initial.placeY + 25)
    expect(Number.parseFloat(finish.style.left)).toBe(initial.finishX)
    expect(Number.parseFloat(finish.style.top)).toBe(initial.finishY)
    expect(Number.parseFloat(frame.style.left)).toBe(initial.frameX + 40)
    expect(Number.parseFloat(frame.style.top)).toBe(initial.frameY + 25)
    expect(path.getAttribute('d')).not.toBe(initial.path)

    fireEvent.click(screen.getByRole('button', { name: '恢复自动布局' }))
    expect(Number.parseFloat(pick.style.left)).toBe(initial.pickX)
    expect(Number.parseFloat(place.style.left)).toBe(initial.placeX)
    expect(Number.parseFloat(frame.style.left)).toBe(initial.frameX)
  })

  it('moves an empty group frame', () => {
    const { container } = render(
      <WorkflowDag nodes={[group('empty-group', '空分组', 0)]} edges={[]} loading={false} error={false} />,
    )
    const frame = container.querySelector('.workflow-dag-group-frame') as HTMLElement
    const initialX = Number.parseFloat(frame.style.left)
    const initialY = Number.parseFloat(frame.style.top)

    fireEvent.pointerDown(frame, { pointerId: 2, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(frame, { pointerId: 2, buttons: 1, clientX: 130, clientY: 120 })
    fireEvent.pointerUp(frame, { pointerId: 2, clientX: 130, clientY: 120 })

    expect(Number.parseFloat(frame.style.left)).toBe(initialX + 30)
    expect(Number.parseFloat(frame.style.top)).toBe(initialY + 20)
  })
})
