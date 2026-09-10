import type { ResourceUsageModel, UsageDevice, UsageGraphNode, UsageSite } from './resourceUsage'

export interface CanvasPoint { x: number; y: number }
export interface SiteSection { uuid: string; name: string; columns: number; rows: number; sites: Array<{ site: UsageSite; column: number; row: number }> }
export interface CanvasBlock extends CanvasPoint { uuid: string; width: number; height: number; sections: SiteSection[] }

const number = (value: number | undefined, fallback = 0) => Number.isFinite(value) ? value! : fallback
const coordinate = (value?: number) => Math.round(number(value) * 1000) / 1000

/** 规则阵列按实际行列排布；不同高度的阵列接续展示，全部库位同时可见，不推断业务层数。 */
export function siteSections(device: UsageDevice): SiteSection[] {
  const owners = new Map<string, UsageSite[]>()
  for (const site of device.sites) owners.set(site.ownerUuid, [...(owners.get(site.ownerUuid) || []), site])
  return [...owners].map(([uuid, sites]) => {
    const heights = [...new Set(sites.map((site) => coordinate(site.pose?.position_z)))].sort((a, b) => a - b)
    let rows = 0
    let columns = 0
    const placements = heights.flatMap((z) => {
      const layer = sites.filter((site) => coordinate(site.pose?.position_z) === z)
      const xs = [...new Set(layer.map((site) => coordinate(site.pose?.position_x)))].sort((a, b) => a - b)
      const ys = [...new Set(layer.map((site) => coordinate(site.pose?.position_y)))].sort((a, b) => b - a)
      const unique = new Set(layer.map((site) => `${coordinate(site.pose?.position_x)}:${coordinate(site.pose?.position_y)}`))
      const grid = unique.size === layer.length && xs.length <= 6 && ys.length <= 6 && xs.length * ys.length <= layer.length * 1.5
      const count = grid ? xs.length : Math.min(4, layer.length)
      const rowOffset = rows
      columns = Math.max(columns, count)
      rows += grid ? ys.length : Math.ceil(layer.length / count)
      return [...layer].sort((a, b) => (a.sortOrder || 0) - (b.sortOrder || 0) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true })).map((site, i) => ({ site,
        column: grid ? xs.indexOf(coordinate(site.pose?.position_x)) : i % count,
        row: rowOffset + (grid ? ys.indexOf(coordinate(site.pose?.position_y)) : Math.floor(i / count)),
      }))
    })
    return { uuid, name: sites[0].ownerName, columns, rows, sites: placements }
  })
}

/** 组合父级平移、旋转及缩放，取得用于二维排布的参考位置。 */
function worldPosition(node: UsageGraphNode, nodes: Map<string, UsageGraphNode>): CanvasPoint {
  let point = { x: number(node.relative_position?.position_x), y: number(node.relative_position?.position_y) }
  let parent = nodes.get(node.material.parent_uuid || '')
  const visited = new Set([node.material.uuid])
  while (parent && !visited.has(parent.material.uuid)) {
    visited.add(parent.material.uuid)
    const pose = parent.relative_position
    const angle = number(pose?.rotation_z) * Math.PI / 180
    const x = point.x * number(pose?.scale_x, 1), y = point.y * number(pose?.scale_y, 1)
    point = { x: number(pose?.position_x) + x * Math.cos(angle) - y * Math.sin(angle), y: number(pose?.position_y) + x * Math.sin(angle) + y * Math.cos(angle) }
    parent = nodes.get(parent.material.parent_uuid || '')
  }
  return point
}

/** 固定资源拓扑决定初始布局，任务和物料占用不参与排序；扩展间距防止设备块重叠。 */
export function layoutMaterialCanvas(model: ResourceUsageModel): CanvasBlock[] {
  const nodes = new Map(model.nodes.map((node) => [node.material.uuid, node]))
  const depth = (uuid: string) => {
    const visited = new Set<string>()
    let node = nodes.get(uuid)
    while (node?.material.parent_uuid && !visited.has(node.material.uuid)) { visited.add(node.material.uuid); node = nodes.get(node.material.parent_uuid) }
    return visited.size
  }
  const items = model.devices.map((device) => {
    const sections = siteSections(device).sort((a, b) => depth(a.uuid) - depth(b.uuid) || a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
    const columns = Math.max(2, ...sections.map((section) => section.columns))
    const rows = Math.max(1, ...sections.map((section) => section.rows))
    const node = nodes.get(device.uuid)
    return { uuid: device.uuid, sections, width: Math.max(260, columns * 76 + 20), height: 104 + rows * 58,
      point: node ? worldPosition(node, nodes) : { x: 0, y: 0 } }
  }).sort((a, b) => b.point.y - a.point.y || a.point.x - b.point.x || a.uuid.localeCompare(b.uuid))
  const minX = Math.min(0, ...items.map((item) => item.point.x))
  const maxY = Math.max(0, ...items.map((item) => item.point.y))
  const blocks: CanvasBlock[] = []
  for (const item of items) {
    const x = 40 + (item.point.x - minX) * .65
    let y = 40 + (maxY - item.point.y) * .65
    // 只在初始排布时避让；状态刷新不会推动其它设备，拖动坐标由展示配置保留。
    let collision: CanvasBlock | undefined
    do {
      collision = blocks.find((block) => x < block.x + block.width + 40 && x + item.width + 40 > block.x && y < block.y + block.height + 40 && y + item.height + 40 > block.y)
      if (collision) y = collision.y + collision.height + 40
    } while (collision)
    blocks.push({ uuid: item.uuid, sections: item.sections, width: item.width, height: item.height, x, y })
  }
  return blocks
}

/** 只把直接设备占用和正在执行的动作作为设备点亮依据，不把库位存货或预留冒充设备使用。 */
export function deviceTaskIds(device: UsageDevice): string[] {
  return [...new Set([
    ...device.holdings.filter((holding) => holding.resourceUuid === device.uuid && holding.state !== 'reserved' && !holding.retained && holding.state !== 'uncertain').map((holding) => holding.taskUuid),
    ...device.activities.map(({ task }) => task.uuid),
    ...(device.executions || []).map((job) => job.task_uuid),
  ].filter(Boolean))]
}

export function locateCanvasResource(model: ResourceUsageModel, materialUuid?: string, siteUuid?: string) {
  if (!materialUuid && !siteUuid) return { device: undefined, site: undefined }
  const site = siteUuid ? model.sites.get(siteUuid) : [...model.sites.values()].find((entry) => entry.materialUuid === materialUuid)
  const device = site ? model.devices.find((entry) => entry.sites.some((item) => item.uuid === site.uuid))
    : model.devices.find((entry) => entry.uuid === materialUuid || entry.sites.some((item) => item.ownerUuid === materialUuid))
  return { device, site }
}
