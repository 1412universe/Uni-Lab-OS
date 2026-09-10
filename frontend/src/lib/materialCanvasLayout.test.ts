import { describe, expect, it } from 'vitest'
import { buildResourceUsage, materialsFromUsage } from './resourceUsage'
import { deviceTaskIds, layoutMaterialCanvas, locateCanvasResource } from './materialCanvasLayout'
import { transferSnapshot, usageLock, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'

describe('设备与库位画布布局', () => {
  it('does not select an arbitrary empty site before the user selects anything', () => {
    expect(locateCanvasResource(buildResourceUsage(usageSnapshot(), []))).toEqual({ device: undefined, site: undefined })
  })
  it('keeps all blocks fixed as the beaker moves across devices and follows the new site', () => {
    const snapshot = transferSnapshot()
    const before = buildResourceUsage(snapshot, [usageTask()])
    const blocks = layoutMaterialCanvas(before)
    expect(before.sites.get('site-8')).toMatchObject({ materialUuid: undefined, transfers: [{ direction: 'in' }] })
    snapshot.nodes[1].sites[0].occupied_material_uuid = null
    snapshot.nodes[3].sites[0].occupied_material_uuid = 'sample'
    snapshot.nodes[2].current_site_uuid = 'site-8'
    snapshot.nodes[2].material.parent_uuid = 'camera'
    snapshot.jobs = []
    const after = buildResourceUsage(snapshot, [usageTask()])
    expect(after.sites.get('site-a')?.materialUuid).toBeUndefined()
    expect(after.sites.get('site-8')?.materialUuid).toBe('sample')
    expect(locateCanvasResource(after, 'sample').device?.uuid).toBe('camera')
    expect(materialsFromUsage(snapshot).find((material) => material.uuid === 'sample')?.currentLocation).toMatchObject({ siteUuid: 'site-8' })
    expect(layoutMaterialCanvas(after).map(({ uuid, x, y, width, height }) => ({ uuid, x, y, width, height }))).toEqual(blocks.map(({ uuid, x, y, width, height }) => ({ uuid, x, y, width, height })))
    expect(deviceTaskIds(after.devices.find((device) => device.uuid === 'robot')!)).toEqual([])
  })

  it('does not light idle devices from stock or site reservations', () => {
    const snapshot = transferSnapshot(); snapshot.jobs = []
    snapshot.occupancies = [usageLock('material/warehouse/site/site-b/exclusive', { state: 'reserved' })]
    const model = buildResourceUsage(snapshot, [])
    expect(model.sites.get('site-a')?.materialUuid).toBe('sample')
    expect(deviceTaskIds(model.devices.find((device) => device.uuid === 'robot')!)).toEqual([])
  })

  it('preserves coordinates and shows different heights together without overlapping equipment', () => {
    const snapshot = structuredClone(usageSnapshot())
    snapshot.nodes[0].relative_position = { position_x: 1480, position_y: 80 }
    snapshot.nodes[1].sites = [
      { uuid: 'a', name: '下层左', position_x: 10, position_y: 0, position_z: 0 },
      { uuid: 'b', name: '下层右', position_x: 90, position_y: 0, position_z: 0 },
      { uuid: 'c', name: '上层左', position_x: 10, position_y: 0, position_z: 100 },
    ]
    expect(materialsFromUsage(snapshot)[0].position).toEqual([1480, 80, 0])
    const layout = layoutMaterialCanvas(buildResourceUsage(snapshot, []))
    const robot = layout.find((block) => block.uuid === 'robot')!
    expect(robot.sections[0]).toMatchObject({ columns: 2, rows: 2 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'a')).toMatchObject({ column: 0, row: 0 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'b')).toMatchObject({ column: 1, row: 0 })
    expect(robot.sections[0].sites.find(({ site }) => site.uuid === 'c')).toMatchObject({ column: 0, row: 1 })
    for (const a of layout) for (const b of layout) if (a.uuid !== b.uuid) {
      expect(a.x >= b.x + b.width || b.x >= a.x + a.width || a.y >= b.y + b.height || b.y >= a.y + a.height).toBe(true)
    }
  })

  it('shows a failed transfer as uncertain without claiming that the target contains material', () => {
    const snapshot = transferSnapshot()
    snapshot.jobs![0].status = 'failed'
    snapshot.occupancies = [usageLock('material/warehouse/site/site-a/exclusive', { state: 'uncertain' })]
    const model = buildResourceUsage(snapshot, [])
    expect(model.sites.get('site-a')?.materialUuid).toBe('sample')
    expect(model.sites.get('site-8')).toMatchObject({ materialUuid: undefined, transfers: [{ uncertain: true, direction: 'in' }] })
  })
})
