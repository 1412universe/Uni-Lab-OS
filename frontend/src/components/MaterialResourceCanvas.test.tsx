import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MaterialResourceCanvas } from './MaterialResourceCanvas'
import { ResourceHoverCard } from './ResourceHoverCard'
import { buildResourceUsage } from '../lib/resourceUsage'
import { transferSnapshot, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'
import type { MaterialUsageContext } from './MaterialUsagePanel'

function context(snapshot = transferSnapshot()): MaterialUsageContext {
  return { model: buildResourceUsage(snapshot, [usageTask()]), tasks: [usageTask()], stale: false, loading: false, updatedAt: 0, refresh: vi.fn(), onOpenTask: vi.fn() }
}
function canvas(ctx: MaterialUsageContext) {
  return <MaterialResourceCanvas model={ctx.model!} context={ctx} onSelect={vi.fn()} onClear={vi.fn()} onDetails={vi.fn()} />
}
afterEach(() => localStorage.clear())
describe('画布实时占用交互', () => {
  it('shows all six stirrer sites across heights together and updates inventory in both rows', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[0].material.name = 'S04 磁搅'
    snapshot.nodes[1].material.name = 'S04磁搅工位仓'
    snapshot.nodes[1].sites = Array.from({ length: 6 }, (_, index) => ({
      uuid: `s04-${index + 1}`, name: `S04${index + 1}`, sort_order: index,
      position_x: 92.1 + (index % 3) * 225, position_y: 89.4, position_z: index < 3 ? 150 : 535,
      occupied_material_uuid: index === 0 ? 'sample' : null,
    }))
    snapshot.nodes[2].current_site_uuid = 's04-1'
    const { rerender } = render(canvas(context(snapshot)))
    const group = screen.getByLabelText('设备组 S04 磁搅')
    const sites = within(group).getAllByRole('button', { name: /^库位 / })
    expect(sites).toHaveLength(6)
    expect(within(group).queryByRole('button', { name: /第 .* 层/ })).not.toBeInTheDocument()
    expect(sites[0]).toHaveStyle({ gridColumn: '1', gridRow: '1' })
    expect(sites[3]).toHaveStyle({ gridColumn: '1', gridRow: '2' })
    expect(sites[5]).toHaveStyle({ gridColumn: '3', gridRow: '2' })
    expect(sites[0]).toHaveAttribute('data-occupied', 'true')
    expect(sites[3]).toHaveAttribute('data-occupied', 'false')
    const height = group.style.height
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[1].sites[3].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 's04-4'
    rerender(canvas(context(snapshot)))
    expect(sites[0]).toHaveAttribute('data-occupied', 'false')
    expect(sites[3]).toHaveAttribute('data-occupied', 'true')
    expect(sites[3]).toHaveTextContent('测试烧杯')
    expect(group.style.height).toBe(height)
  })

  it('opens the current site owner for loading and unloading when selection came from a beaker', () => {
    const ctx = context()
    const onDetails = vi.fn()
    render(<MaterialResourceCanvas model={ctx.model!} context={ctx} selection={{ materialUuid: 'sample', request: 1 }} onSelect={vi.fn()} onClear={vi.fn()} onDetails={onDetails} />)
    fireEvent.click(screen.getByRole('button', { name: '查看详情 / 上下料' }))
    expect(onDetails).toHaveBeenCalledWith('warehouse', 'site-a')
    fireEvent.click(screen.getByRole('button', { name: '查看物料' }))
    expect(onDetails).toHaveBeenLastCalledWith('sample')
  })

  it('lights only actual inventory, then clears source and lights destination after a successful transfer', () => {
    const snapshot = transferSnapshot()
    const { rerender } = render(canvas(context(snapshot)))
    const source = screen.getByRole('button', { name: '库位 转运仓 / A01' })
    const target = screen.getByRole('button', { name: '库位 相机 / 8' })
    expect(source).toHaveAttribute('data-occupied', 'true')
    expect(source).toHaveTextContent('转移中')
    expect(target).toHaveAttribute('data-occupied', 'false')
    expect(target).toHaveTextContent('待放入')
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'true')
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    snapshot.jobs = []
    rerender(canvas(context(snapshot)))
    expect(source).toHaveAttribute('data-occupied', 'false')
    expect(source).not.toHaveTextContent('测试烧杯')
    expect(target).toHaveAttribute('data-occupied', 'true')
    expect(target).toHaveTextContent('测试烧杯')
    expect(screen.getAllByText('测试烧杯')).toHaveLength(1)
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'false')
  })

  it('moves the entire group and persists presentation only without a network write', () => {
    const ctx = context()
    const { rerender } = render(canvas(ctx))
    const group = screen.getByLabelText('设备组 转运机械臂')
    const before = parseFloat(group.style.left)
    const site = within(group).getByRole('button', { name: '库位 转运仓 / A01' })
    const fetcher = vi.spyOn(globalThis, 'fetch')
    fireEvent.keyDown(within(group).getByRole('button', { name: '移动设备及库位 转运机械臂' }), { key: 'ArrowRight' })
    expect(parseFloat(group.style.left)).toBe(before + 20)
    expect(group).toContainElement(site)
    rerender(canvas(context()))
    expect(parseFloat(group.style.left)).toBe(before + 20)
    expect(fetcher).not.toHaveBeenCalled()
    fetcher.mockRestore()
  })

  it('shows initial unbound inventory as occupied while the device stays idle', () => {
    const ctx = { ...context(), model: buildResourceUsage(usageSnapshot(), []) }
    render(canvas(ctx))
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveTextContent('未绑定 Task')
    expect(screen.getByRole('button', { name: '库位 转运仓 / A01' })).toHaveAttribute('data-occupied', 'true')
    expect(screen.getByLabelText('设备组 转运机械臂').querySelector('header')).toHaveAttribute('data-in-use', 'false')
  })

  it('keeps material task and target-site use separate in hover and follows location changes', () => {
    const snapshot = transferSnapshot()
    const { rerender } = render(<ResourceHoverCard hover={{ siteUuid: 'site-8', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('当前物料：空库位')
    expect(screen.getByRole('tooltip')).toHaveTextContent('库位使用 Task')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Task task-1')
    rerender(<ResourceHoverCard hover={{ materialUuid: 'sample', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('转运仓 / A01')
    expect(screen.getByRole('tooltip')).toHaveTextContent('Task task-1')
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    rerender(<ResourceHoverCard hover={{ materialUuid: 'sample', left: 0, top: 0 }} context={context(snapshot)} />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('相机 / 8')
    expect(screen.getByRole('tooltip')).not.toHaveTextContent('转运仓 / A01')
  })
})
