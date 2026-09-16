import { EDGE_API_BASE } from './edgeClient'

export interface PlannedNode {
  key: [string, string]
  device_id: string
  start_ms: number
  end_ms: number
  fixed: boolean
}

export interface TimeScheduleSnapshot {
  enabled: boolean
  algorithm: string
  active_solves: number
  input_error: string
  input: { revision: string; partial_tasks: string[]; blocked: [string, string, string][] }
  latest: null | {
    revision: string
    time_origin: number
    status: string
    validated: boolean
    error: string
    nodes: PlannedNode[]
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('时间计划数据格式无效')
  return value as Record<string, unknown>
}

function finite(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('时间计划包含无效时间')
  return value
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw new Error('时间计划缺少身份或状态')
  return value
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('时间计划列表格式无效')
  return value.map(text)
}

/** 此接口直接返回只读候选，不使用任务接口的 envelope，也不产生执行授权。 */
export async function loadTimeSchedule(signal?: AbortSignal): Promise<TimeScheduleSnapshot> {
  const response = await fetch(`${EDGE_API_BASE}/scheduler/time-plan`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`时间计划暂不可用（HTTP ${response.status}）`)
  const raw = record(await response.json())
  if (typeof raw.enabled !== 'boolean') throw new Error('时间计划缺少启用状态')
  const input = raw.input === undefined ? {} : record(raw.input)
  const blocked = (input.blocked === undefined ? [] : input.blocked)
  if (!Array.isArray(blocked)) throw new Error('时间计划待定原因格式无效')
  const result: TimeScheduleSnapshot = {
    enabled: raw.enabled,
    algorithm: text(raw.algorithm),
    active_solves: raw.active_solves === undefined ? 0 : finite(raw.active_solves),
    input_error: raw.input_error === undefined ? '' : text(raw.input_error),
    input: {
      revision: input.revision === undefined ? '' : text(input.revision),
      partial_tasks: input.partial_tasks === undefined ? [] : strings(input.partial_tasks),
      blocked: blocked.map((item) => {
        const fields = strings(item)
        if (fields.length !== 3) throw new Error('时间计划待定原因缺少节点身份')
        return [fields[0], fields[1], fields[2]]
      }),
    },
    latest: null,
  }
  if (raw.latest != null) {
    const latest = record(raw.latest)
    if (!Array.isArray(latest.nodes) || typeof latest.validated !== 'boolean') throw new Error('时间候选格式无效')
    result.latest = {
      revision: text(latest.revision), time_origin: finite(latest.time_origin),
      status: text(latest.status), validated: latest.validated,
      error: text(latest.error),
      nodes: latest.nodes.map((value) => {
        const node = record(value)
        const key = strings(node.key)
        const start = finite(node.start_ms)
        const end = finite(node.end_ms)
        if (key.length !== 2 || start < 0 || end < start || typeof node.fixed !== 'boolean') throw new Error('候选节点身份或时间区间无效')
        return { key: [key[0], key[1]], device_id: text(node.device_id), start_ms: start, end_ms: end, fixed: node.fixed }
      }),
    }
  }
  return result
}
