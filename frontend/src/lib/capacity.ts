import type { CapacityLimits } from '../types'

export type MaximumFields = { maximum?: string }

const unitScale: Record<string, [keyof CapacityLimits, number]> = {
  ul: ['max_volume_ul', 1], 'μl': ['max_volume_ul', 1], 'µl': ['max_volume_ul', 1], ml: ['max_volume_ul', 1000], l: ['max_volume_ul', 1e6],
  mg: ['max_mass_g', 0.001], g: ['max_mass_g', 1], kg: ['max_mass_g', 1000],
}

export function capacityValue(capacity: CapacityLimits | undefined, unit: string): string {
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension || capacity?.[dimension[0]] == null) return ''
  return String(Number((capacity[dimension[0]]! / dimension[1]).toPrecision(12)))
}

export function parseMaximum(value: string, unit: string): CapacityLimits {
  if (!value.trim()) return {}
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension) throw new Error('最大装料量需要使用体积或质量单位。')
  const maximum = Number(value) * dimension[1]
  if (!Number.isFinite(maximum) || maximum <= 0) throw new Error('最大装料量须为大于零的有限数值。')
  return { [dimension[0]]: maximum }
}

export function convertMaximum(value: string | undefined, fromUnit: string, toUnit: string): string | undefined {
  if (value === undefined) return undefined
  const from = unitScale[fromUnit.trim().toLowerCase()]
  const to = unitScale[toUnit.trim().toLowerCase()]
  if (!from || !to || from[0] !== to[0]) return undefined
  if (!value.trim()) return ''
  const amount = Number(value)
  return Number.isFinite(amount) ? String(Number((amount * from[1] / to[1]).toPrecision(12))) : value
}

export function capacityText(capacity?: CapacityLimits, unit?: string) {
  if (unit) { const value = capacityValue(capacity, unit); return value ? `${value} ${unit}` : '未配置' }
  return [capacity?.max_volume_ul == null ? '' : `${capacity.max_volume_ul / 1000} mL`, capacity?.max_mass_g == null ? '' : `${capacity.max_mass_g} g`].filter(Boolean).join(' / ') || '未配置'
}

export function stricterCapacity(...capacities: Array<CapacityLimits | null | undefined>): CapacityLimits {
  const result: CapacityLimits = {}
  for (const key of ['max_volume_ul', 'max_mass_g'] as const) {
    const values = capacities.flatMap((capacity) => capacity?.[key] == null ? [] : [capacity[key]!])
    if (values.length) result[key] = Math.min(...values)
  }
  return result
}

export function maximumError(quantity: number, unit: string, currentCapacity?: CapacityLimits, ratedCapacity?: CapacityLimits, maximum?: string): string {
  let effective = currentCapacity ?? ratedCapacity
  if (maximum !== undefined) {
    let configured: CapacityLimits
    try { configured = parseMaximum(maximum, unit) } catch (error) { return (error as Error).message }
    for (const key of ['max_volume_ul', 'max_mass_g'] as const) {
      if (configured[key] != null && ratedCapacity?.[key] != null && configured[key]! > ratedCapacity[key]!) return `最大装料量不能超过容器额定容量 ${capacityText(ratedCapacity, unit)}。`
    }
    effective = stricterCapacity(ratedCapacity, configured)
  }
  if (!effective || !Object.keys(effective).length) return ''
  const dimension = unitScale[unit.trim().toLowerCase()]
  if (!dimension) return '已配置最大装料量，请使用体积或质量单位计量。'
  const [key, factor] = dimension
  const limit = effective[key]
  if (limit != null && quantity * factor > limit + Math.max(1e-9, limit * 1e-12)) return `数量超过最大装料量 ${capacityText(effective, unit)}。`
  return ''
}
