import { describe, expect, it } from 'vitest'
import type { TaskNode, WorkflowTask } from '../types'
import {
  buildRunLanes,
  buildRunNodeViews,
  buildRunSummary,
  deviceColor,
  formatRunDuration,
  isTaskTerminal,
  parallelismBuckets,
  peakParallelism,
  runStateFromNode,
  runStateIndex,
} from './workflowRunModel'

function node(overrides: Partial<TaskNode> = {}): TaskNode {
  return {
    uuid: 'node-1',
    name: '取料',
    kind: 'device_action',
    index: 0,
    status: 'pending',
    ...overrides,
  }
}

function task(nodes: TaskNode[], overrides: Partial<WorkflowTask> = {}): WorkflowTask {
  return {
    uuid: 'task-1',
    workflowUuid: 'wf-1',
    workflowName: '金四面体',
    status: 'running',
    priority: 'normal',
    sample: 't1',
    description: '',
    current: '',
    progress: 0,
    updatedAt: new Date().toISOString(),
    nodes,
    materialUuids: [],
    runMode: 'normal',
    executionMode: 'normal',
    dispatchGate: 'active',
    controlStatus: 'active',
    ...overrides,
  } as WorkflowTask
}

describe('runStateFromNode', () => {
  it('把 Edge 节点状态映射为画布运行态', () => {
    expect(runStateFromNode(node({ status: 'running' }), false)).toBe('running')
    expect(runStateFromNode(node({ status: 'succeeded' }), false)).toBe('succeeded')
    expect(runStateFromNode(node({ status: 'failed' }), false)).toBe('failed')
    expect(runStateFromNode(node({ status: 'skipped' }), false)).toBe('skipped')
    expect(runStateFromNode(node({ status: 'canceling' }), false)).toBe('canceled')
    expect(runStateFromNode(node({ status: 'pending' }), false)).toBe('pending')
  })

  it('wait_reason 表示依赖已清零、在抢设备，归为 ready', () => {
    const waiting = node({
      status: 'waiting',
      waitReason: { code: 'device_busy', title: '等待设备', message: '设备被占用', details: [] },
    })
    expect(runStateFromNode(waiting, false)).toBe('ready')
  })

  it('暂停时仍在跑的节点显示为 paused 而不是 running', () => {
    const running = node({ status: 'running', job: { uuid: 'j', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'running' } })
    expect(runStateFromNode(running, false)).toBe('running')
    expect(runStateFromNode(running, true)).toBe('paused')
  })

  it('attention 归到失败，避免需要人工处理的节点被漏看', () => {
    expect(runStateFromNode(node({ status: 'attention' }), false)).toBe('failed')
  })
})

describe('buildRunNodeViews / runStateIndex', () => {
  it('没有任务的节点带上设备与等待原因', () => {
    const views = buildRunNodeViews(task([
      node({ uuid: 'a', name: '取料', device: '机械臂' }),
      node({ uuid: 'b', name: '离心', device: undefined }),
    ]), false)
    expect(views).toHaveLength(2)
    expect(views[0].device).toBe('机械臂')
    expect(views[1].device).toBe('未指定设备')
    expect(runStateIndex(views).get('a')?.name).toBe('取料')
  })

  it('作业时间区间换成耗时', () => {
    const views = buildRunNodeViews(task([node({
      uuid: 'a',
      status: 'succeeded',
      job: {
        uuid: 'j', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [],
        executionStatus: 'succeeded',
        startedAt: '2026-11-15T04:00:00.000Z',
        finishedAt: '2026-11-15T04:00:30.000Z',
      },
    })]), false)
    expect(views[0].durationMs).toBe(30_000)
  })

  it('没有任务时返回空数组', () => {
    expect(buildRunNodeViews(undefined, false)).toEqual([])
  })
})

describe('buildRunLanes', () => {
  it('按设备分组，未开始的节点不进泳道', () => {
    const views = buildRunNodeViews(task([
      node({
        uuid: 'a', device: '机械臂', status: 'succeeded',
        job: { uuid: 'j1', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'succeeded', startedAt: '2026-11-15T04:00:00.000Z', finishedAt: '2026-11-15T04:00:10.000Z' },
      }),
      node({
        uuid: 'b', device: '机械臂', status: 'running',
        job: { uuid: 'j2', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'running', startedAt: '2026-11-15T04:00:10.000Z' },
      }),
      node({ uuid: 'c', device: '离心机', status: 'pending' }),
    ]), false)
    const lanes = buildRunLanes(views, Date.parse('2026-11-15T04:00:20.000Z'))
    expect(lanes).toHaveLength(1)
    expect(lanes[0].device).toBe('机械臂')
    expect(lanes[0].blocks.map((block) => block.nodeUuid)).toEqual(['a', 'b'])
    expect(lanes[0].runningCount).toBe(1)
    // 未结束的色块以「现在」为右端，并标记为开口
    expect(lanes[0].blocks[1].openEnded).toBe(true)
    expect(lanes[0].blocks[1].finishedMs).toBe(Date.parse('2026-11-15T04:00:20.000Z'))
  })

  it('同一设备上并发节点会被计数出来', () => {
    const views = buildRunNodeViews(task([
      node({
        uuid: 'a', device: '烘箱', status: 'running',
        job: { uuid: 'j1', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'running', startedAt: '2026-11-15T04:00:00.000Z' },
      }),
      node({
        uuid: 'b', device: '烘箱', status: 'running',
        job: { uuid: 'j2', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'running', startedAt: '2026-11-15T04:00:01.000Z' },
      }),
    ]), false)
    expect(buildRunLanes(views, Date.parse('2026-11-15T04:00:05.000Z'))[0].runningCount).toBe(2)
  })

  it('设备颜色稳定', () => {
    expect(deviceColor('机械臂')).toBe(deviceColor('机械臂'))
  })
})

describe('peakParallelism / buildRunSummary', () => {
  it('计算并发峰值，首尾相接不算并行', () => {
    expect(peakParallelism([[0, 10], [10, 20]])).toBe(1)
    expect(peakParallelism([[0, 10], [5, 15]])).toBe(2)
    expect(peakParallelism([[0, 10], [2, 8], [3, 4]])).toBe(3)
    expect(peakParallelism([])).toBe(0)
  })

  it('墙钟取最早开始到最晚结束，加速比为耗时总和除以墙钟', () => {
    const views = buildRunNodeViews(task([
      node({
        uuid: 'a', device: '机械臂', status: 'succeeded',
        job: { uuid: 'j1', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'succeeded', startedAt: '2026-11-15T04:00:00.000Z', finishedAt: '2026-11-15T04:00:10.000Z' },
      }),
      node({
        uuid: 'b', device: '离心机', status: 'succeeded',
        job: { uuid: 'j2', param: {}, feedbackData: {}, returnInfo: {}, errorInfo: [], executionStatus: 'succeeded', startedAt: '2026-11-15T04:00:00.000Z', finishedAt: '2026-11-15T04:00:10.000Z' },
      }),
    ]), false)
    const summary = buildRunSummary(views, Date.parse('2026-11-15T04:01:00.000Z'))
    expect(summary.wallClockMs).toBe(10_000)
    expect(summary.sumNodeDurationMs).toBe(20_000)
    expect(summary.speedup).toBe(2)
    expect(summary.peakParallelism).toBe(2)
    expect(summary.deviceCount).toBe(2)
    expect(summary.counts.succeeded).toBe(2)
  })

  it('没有开始时间时不编造墙钟与加速比', () => {
    const summary = buildRunSummary(buildRunNodeViews(task([node()]), false), Date.now())
    expect(summary.wallClockMs).toBe(0)
    expect(summary.speedup).toBe(0)
    expect(summary.peakParallelism).toBe(0)
    expect(summary.startedCount).toBe(0)
  })
})

describe('parallelismBuckets', () => {
  it('分桶后峰值与扫描线一致', () => {
    const buckets = parallelismBuckets([[0, 100], [0, 50]], 10)
    expect(Math.max(...buckets)).toBe(2)
    expect(buckets[0]).toBe(2)
    expect(buckets[buckets.length - 1]).toBe(1)
  })

  it('空输入返回全零', () => {
    expect(parallelismBuckets([], 4)).toEqual([0, 0, 0, 0])
  })
})

describe('formatRunDuration', () => {
  it('按秒/分/时给出紧凑格式', () => {
    expect(formatRunDuration(0)).toBe('0s')
    expect(formatRunDuration(1500)).toBe('1.5s')
    expect(formatRunDuration(45_000)).toBe('45s')
    expect(formatRunDuration(125_000)).toBe('2m05s')
    expect(formatRunDuration(3_930_000)).toBe('1h05m')
  })
})

describe('isTaskTerminal', () => {
  it('识别终态', () => {
    expect(isTaskTerminal(undefined)).toBe(false)
    expect(isTaskTerminal(task([]))).toBe(false)
    expect(isTaskTerminal(task([], { status: 'succeeded' }))).toBe(true)
    expect(isTaskTerminal(task([], { executionStatus: 'failed' }))).toBe(true)
  })
})
