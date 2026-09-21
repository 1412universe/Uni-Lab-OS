import { describe, expect, it } from 'vitest'
import type { WorkflowTask } from '../types'
import type { TimeScheduleSnapshot } from './timeScheduleClient'
import { buildTaskGantt } from './taskGantt'

const task = {
  uuid: 'task-1', workflowUuid: 'workflow-1', workflowName: '测试流程', status: 'pending',
  priority: 'normal', sample: 'sample-1', description: '', current: '', progress: 0,
  updatedAt: '2026-01-01T00:00:00Z', nodes: [
    { uuid: 'node-later', name: '后续动作', kind: 'device_action', index: 1, status: 'pending' },
    { uuid: 'node-first', name: '先行动作', kind: 'device_action', index: 0, status: 'pending' },
  ], materialUuids: [], runMode: 'normal', executionMode: 'normal', dispatchGate: 'active',
  controlStatus: 'active', matrixGroupKey: 'group-1',
} as WorkflowTask

const candidate = {
  revision: 'revision-1', time_origin: 1000, status: 'OPTIMAL', validated: true, error: '', nodes: [
    { key: ['task-1', 'node-later'], device_id: 'device-1', start_ms: 10_000, end_ms: 12_000, fixed: false },
    { key: ['task-1', 'node-first'], device_id: 'device-1', start_ms: 0, end_ms: 10_000, fixed: false },
  ],
} satisfies NonNullable<TimeScheduleSnapshot['latest']>

describe('任务甘特图排程展示', () => {
  it('按候选开始时间展示后续待运行节点，而不是按任务节点插入顺序', () => {
    const [row] = buildTaskGantt([task], candidate, [], 999_000)

    expect(row.bars.map((bar) => [bar.nodeUuid, bar.kind])).toEqual([
      ['node-first', 'planned'],
      ['node-later', 'planned'],
    ])
    expect(row.bars[1].startMs).toBeGreaterThan(row.bars[0].startMs)
  })
})
