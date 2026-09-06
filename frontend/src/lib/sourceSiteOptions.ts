import type { ContractField, MaterialRecord, WorkflowGraph } from '../types'

/** 读取发布图的来源绑定，并从权威库位占用中投影启动选项。 */
export function sourceSiteOptions(graph: WorkflowGraph | undefined, fields: ContractField[], materials: MaterialRecord[]) {
  const byId = new Map(materials.map((material) => [material.uuid, material]))
  const result = new Map<string, Array<{ value: string; label: string; materialUuid: string }>>()
  for (const node of graph?.nodes || []) {
    const parameter = node.meta_data?.unilab?.material_source_site_binding?.parameter
    if (node.kind !== 'material_source' || typeof parameter !== 'string') continue
    const field = fields.find((item) => item.name === parameter)
    const owner = byId.get(node.param?.mount?.uuid)
    const allowed = field?.schema.enum
    const range = node.param?.slot_range
    result.set(parameter, (owner?.sites || []).flatMap((site) => {
      const material = site.occupiedMaterialUuid ? byId.get(site.occupiedMaterialUuid) : undefined
      if (!material || material.resourceTemplateUuid !== node.param?.resource_template_uuid ||
          material.currentLocation.kind !== 'site' || material.currentLocation.siteUuid !== site.uuid ||
          material.currentLocation.ownerMaterialUuid !== owner?.uuid ||
          (Array.isArray(allowed) && !allowed.includes(site.name)) ||
          (Array.isArray(range) && !range.includes(site.uuid))) return []
      return [{ value: site.name, label: `${owner.name} / ${site.name} · ${material.name}`, materialUuid: material.uuid }]
    }))
  }
  return result
}
