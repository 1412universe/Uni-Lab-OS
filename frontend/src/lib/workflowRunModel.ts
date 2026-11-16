/**
 * 工作流「运行」的展示模型层。
 *
 * 与 ChemWorldModel 编辑器 `wfrun.js` 的语义对齐，但数据源换成 Edge 的
 * Task / Job 权威投影：
 *
 * - 节点状态词表沿用 Edge 的 NodePresentationStatus（pending / waiting /
 *   running / succeeded / failed / skipped / attention …），不在这里另造一套；
 * - 设备泳道 = 同一台设备上的节点时间条，用来直观看「同设备必须串行、
 *   异设备可以并行」；
 * - 并行度曲线由每个节点 Job 的 `started_at → finished_at` 区间求并集得到，
 *   与墙钟时间、节点耗时总和、加速比同源，避免三处各算一遍。
 *
 * 本文件只做纯函数变换，不发起请求，便于单测覆盖。
 */
import type { TaskNode, TaskNodeJobEvidence, WorkflowTask } from '../types'

/** 节点在画布上的运行态着色；`idle` 表示本次运行没有覆盖到该节点。 */
export type RunNodeState =
  | 'idle'
  | 'pending'
  | 'ready'
  | 'running'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'canceled'

export const RUN_STATE_LABEL: Record<RunNodeState, string> = {
  idle: '未参与本次运行',
  pending: '待执行',
  ready: '就绪·抢设备',
  running: '执行中',
  paused: '已暂停',
  succeeded: '成功',
  failed: '失败',
  skipped: '已跳过',
  canceled: '已取消',
}

export const RUN_STATE_ICON: Record<RunNodeState, string> = {
  idle: '·',
  pending: '○',
  ready: '◔',
  running: '▶',
  paused: '⏸',
  succeeded: '✔',
  failed: '✖',
  skipped: '⤿',
  canceled: '⏹',
}

/** 泳道/曲线配色；与画布节点描边共用一张表。 */
export const RUN_STATE_COLOR: Record<RunNodeState, string> = {
  idle: '#cbd5e1',
  pending: '#cbd5e1',
  ready: '#ca8a04',
  running: '#2563eb',
  paused: '#d97706',
  succeeded: '#16a34a',
  failed: '#dc2626',
  skipped: '#d4d4d8',
  canceled: '#a1a1aa',
}

const DEVICE_PALETTE = ['#2563eb', '#0d9488', '#d97706', '#7c3aed', '#db2777', '#0891b2', '#65a30d', '#ea580c', '#4f46e5', '#059669']

/** 同一台设备恒定取同一颜色，刷新/重跑都不会换色。 */
export function deviceColor(device: string): string {
  let hash = 0
  for (let index = 0; index < device.length; index += 1) {
    hash = (hash * 31 + device.charCodeAt(index)) & 0x7fffffff
  }
  return DEVICE_PALETTE[hash % DEVICE_PALETTE.length]
}

/** Edge 节点状态 → 画布运行态。`attention` 归到失败，避免漏看需要人工处理的节点。 */
export function runStateFromNode(node: TaskNode, paused: boolean): RunNodeState {
  const jobStatus = String(node.job?.executionStatus || '')
  if (paused && jobStatus === 'running') return 'paused'
  switch (node.status) {
    case 'running':
      return 'running'
    case 'succeeded':
      return 'succeeded'
    case 'failed':
    case 'attention':
      return 'failed'
    case 'skipped':
      return 'skipped'
    case 'canceling':
    case 'canceled':
      return 'canceled'
    case 'waiting':
    case 'pending':
    default:
      // 依赖已清零、在抢设备锁的节点在 Edge 侧表现为 pending + wait_reason。
      return node.waitReason ? 'ready' : 'pending'
  }
}

export interface RunNodeView {
  uuid: string
  name: string
  device: string
  state: RunNodeState
  startedMs?: number
  finishedMs?: number
  durationMs?: number
  waitReasonText?: string
}

function timeOf(job: TaskNodeJobEvidence | undefined): { startedMs?: number; finishedMs?: number } {
  const started = job?.startedAt ? Date.parse(job.startedAt) : Number.NaN
  const finished = job?.finishedAt ? Date.parse(job.finishedAt) : Number.NaN
  return {
    startedMs: Number.isFinite(started) ? started : undefined,
    finishedMs: Number.isFinite(finished) ? finished : undefined,
  }
}

export function buildRunNodeViews(task: WorkflowTask | undefined, paused: boolean): RunNodeView[] {
  if (!task) return []
  return task.nodes.map((node) => {
    const { startedMs, finishedMs } = timeOf(node.job)
    return {
      uuid: node.uuid,
      name: node.name,
      device: node.device || '未指定设备',
      state: runStateFromNode(node, paused),
      startedMs,
      finishedMs,
      durationMs: startedMs !== undefined && finishedMs !== undefined ? finishedMs - startedMs : undefined,
      waitReasonText: node.waitReason?.title || node.waitReason?.message,
    }
  })
}

/** 画布节点 uuid → 运行态；供 `EditorNodeCard` 直接查表。 */
export function runStateIndex(views: RunNodeView[]): Map<string, RunNodeView> {
  return new Map(views.map((view) => [view.uuid, view]))
}

export interface RunLaneBlock {
  nodeUuid: string
  name: string
  state: RunNodeState
  startedMs: number
  finishedMs: number
  openEnded: boolean
}

export interface RunLane {
  device: string
  color: string
  blocks: RunLaneBlock[]
  /** 该设备上当前正在执行的节点数；>1 说明有并发，属于异常情况需显式暴露。 */
  runningCount: number
  totalCount: number
}

/**
 * 按设备分组成泳道。
 *
 * 只收录已经有开始时间的节点：还没下发的节点没有可信时间，画在轴上等于编造。
 * 未开始的节点由运行抽屉的「待执行」清单展示。
 */
export function buildRunLanes(views: RunNodeView[], now: number): RunLane[] {
  const byDevice = new Map<string, RunLane>()
  for (const view of views) {
    if (view.startedMs === undefined) continue
    const lane = byDevice.get(view.device) || {
      device: view.device,
      color: deviceColor(view.device),
      blocks: [],
      runningCount: 0,
      totalCount: 0,
    }
    const openEnded = view.finishedMs === undefined
    lane.blocks.push({
      nodeUuid: view.uuid,
      name: view.name,
      state: view.state,
      startedMs: view.startedMs,
      finishedMs: view.finishedMs ?? now,
      openEnded,
    })
    if (view.state === 'running') lane.runningCount += 1
    lane.totalCount += 1
    byDevice.set(view.device, lane)
  }
  return [...byDevice.values()]
}

export interface RunSummary {
  counts: Partial<Record<RunNodeState, number>>
  /** 同时执行的节点数峰值。 */
  peakParallelism: number
  /** 墙钟耗时：最早开始 → 最晚结束（或当前时刻）。 */
  wallClockMs: number
  /** 各节点耗时之和；与墙钟相除即为并行加速比。 */
  sumNodeDurationMs: number
  /** sum / wallClock，单节点时约等于 1。 */
  speedup: number
  deviceCount: number
  startedCount: number
  totalCount: number
}

export function buildRunSummary(views: RunNodeView[], now: number): RunSummary {
  const counts: Partial<Record<RunNodeState, number>> = {}
  for (const view of views) counts[view.state] = (counts[view.state] || 0) + 1

  const spans = views
    .filter((view) => view.startedMs !== undefined)
    .map((view) => [view.startedMs as number, view.finishedMs ?? now] as const)

  let wallClockMs = 0
  if (spans.length) {
    const start = Math.min(...spans.map(([value]) => value))
    const end = Math.max(...spans.map(([, value]) => value))
    wallClockMs = Math.max(0, end - start)
  }

  const sumNodeDurationMs = spans.reduce((total, [start, end]) => total + Math.max(0, end - start), 0)
  const devices = new Set(views.filter((view) => view.startedMs !== undefined).map((view) => view.device))

  return {
    counts,
    peakParallelism: peakParallelism(spans),
    wallClockMs,
    sumNodeDurationMs,
    speedup: wallClockMs > 0 ? sumNodeDurationMs / wallClockMs : 0,
    deviceCount: devices.size,
    startedCount: spans.length,
    totalCount: views.length,
  }
}

/** 扫描线求并发峰值；避免逐毫秒采样，节点数上千也不会卡。 */
export function peakParallelism(spans: ReadonlyArray<readonly [number, number]>): number {
  const events: Array<[number, number]> = []
  for (const [start, end] of spans) {
    if (end <= start) continue
    events.push([start, 1], [end, -1])
  }
  // 同一时刻先处理结束再处理开始，避免首尾相接被误算成并行。
  events.sort((left, right) => (left[0] - right[0]) || (left[1] - right[1]))
  let current = 0
  let peak = 0
  for (const [, delta] of events) {
    current += delta
    if (current > peak) peak = current
  }
  return peak
}

/** 并行度直方图分桶；`drawParallelBars` 直接消费。 */
export function parallelismBuckets(
  spans: ReadonlyArray<readonly [number, number]>,
  bucketCount = 96,
): number[] {
  const buckets = new Array<number>(bucketCount).fill(0)
  if (!spans.length) return buckets
  const start = Math.min(...spans.map(([value]) => value))
  const end = Math.max(...spans.map(([, value]) => value))
  const span = Math.max(1, end - start)
  for (const [from, to] of spans) {
    const first = Math.max(0, Math.floor(((from - start) / span) * bucketCount))
    const last = Math.min(bucketCount, Math.max(first + 1, Math.ceil(((to - start) / span) * bucketCount)))
    for (let index = first; index < last; index += 1) buckets[index] += 1
  }
  return buckets
}

/** 秒 / 分 / 时的紧凑时长格式，与 CWM 编辑器一致。 */
export function formatRunDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '0s'
  const totalSeconds = ms / 1000
  if (totalSeconds < 60) return `${totalSeconds.toFixed(totalSeconds < 10 ? 1 : 0)}s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  if (minutes < 60) return `${minutes}m${String(seconds).padStart(2, '0')}s`
  const hours = Math.floor(minutes / 60)
  return `${hours}h${String(minutes % 60).padStart(2, '0')}m`
}

export function formatRunClock(ms: number | undefined): string {
  if (ms === undefined) return '—'
  const date = new Date(ms)
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

/** 任务是否已进入终态；决定运行抽屉是否继续轮询。 */
export function isTaskTerminal(task: WorkflowTask | undefined): boolean {
  if (!task) return false
  return ['succeeded', 'failed', 'canceled', 'timeout'].includes(task.executionStatus || task.status)
}
