import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  adaptMaterial,
  adaptTask,
  adaptWorkflow,
  loadEdgeSnapshot,
  unwrapEnvelope,
} from './edgeClient'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

describe('unwrapEnvelope', () => {
  it('returns data for a successful Edge response', () => {
    expect(unwrapEnvelope({ code: 0, data: { items: [1, 2] } })).toEqual({ items: [1, 2] })
  })

  it('rejects an Edge business error even if HTTP succeeded', () => {
    expect(() => unwrapEnvelope({ code: 1000, message: 'invalid cursor' })).toThrow('invalid cursor')
  })
})

describe('Edge view model adapters', () => {
  it('adapts workflow metadata and contracts', () => {
    const workflow = adaptWorkflow({
      uuid: 'wf-1',
      name: 'S06 加液生产流程',
      revision: 2,
      status: 'source',
      description: '加液流程',
      meta_data: {
        unilab: {
          input_contract: {
            parameters: [{ name: 'volume', required: true, default: 8, schema: { type: 'integer' } }],
          },
          output_contract: { outputs: [{ name: 'result', schema: { type: 'string' } }] },
          source_bootstrap: { relative_path: 'workflows/s06.py' },
        },
      },
      nodes: [{ uuid: 'node-1' }],
    })

    expect(workflow).toMatchObject({
      uuid: 'wf-1',
      name: 'S06 加液生产流程',
      revision: 2,
      nodeCount: 1,
      sourcePath: 'workflows/s06.py',
    })
    expect(workflow.inputContract[0]).toEqual({
      name: 'volume',
      type: 'integer',
      required: true,
      defaultValue: 8,
      schema: { type: 'integer' },
    })
  })

  it('derives task node state and progress from authoritative jobs', () => {
    const task = adaptTask(
      {
        uuid: 'task-1',
        workflow_uuid: 'wf-1',
        status: 'running',
        description: '联调任务',
        input: { sample_id: 'sample-1' },
        update_time: '2026-08-31T18:00:00Z',
        execution_plan: {
          nodes: [
            { uuid: 'node-1', kind: 'workflow_input', topological_index: 0 },
            { uuid: 'node-2', kind: 'device_action', action_name: 'add_liquid', device_id: 'S06', topological_index: 1 },
            { uuid: 'node-3', kind: 'workflow_output', topological_index: 2 },
          ],
        },
      },
      [
        { workflow_node_uuid: 'node-1', status: 'succeeded', topological_index: 0 },
        { workflow_node_uuid: 'node-2', status: 'running', topological_index: 1 },
      ],
      'S06 加液生产流程',
    )

    expect(task.progress).toBe(33)
    expect(task.current).toBe('add_liquid')
    expect(task.nodes.map((node) => node.status)).toEqual(['succeeded', 'running', 'pending'])
  })

  it('preserves skipped, cancellation, and intervention node states', () => {
    const rawTask = {
      uuid: 'task-state-map',
      workflow_uuid: 'wf-1',
      status: 'canceling',
      execution_plan: {
        nodes: [
          { uuid: 'node-1', topological_index: 0 },
          { uuid: 'node-2', topological_index: 1 },
          { uuid: 'node-3', topological_index: 2 },
        ],
      },
    }
    const task = adaptTask(rawTask, [
      { workflow_node_uuid: 'node-1', status: 'skipped' },
      { workflow_node_uuid: 'node-2', status: 'cancel_requested' },
      { workflow_node_uuid: 'node-3', status: 'intervention_required' },
    ])

    expect(task.status).toBe('canceling')
    expect(task.nodes.map((node) => node.status)).toEqual(['skipped', 'canceling', 'attention'])
  })

  it('projects wait codes and control holds instead of reporting a task as normally running', () => {
    const blocked = adaptTask({
      uuid: 'task-blocked',
      workflow_uuid: 'wf-1',
      status: 'pending',
      wait_reason: { code: 'global_task_capacity' },
      execution_plan: { nodes: [] },
    })
    const intervention = adaptTask({
      uuid: 'task-intervention',
      workflow_uuid: 'wf-1',
      status: 'running',
      control_status: 'waiting_intervention',
      execution_plan: { nodes: [] },
    })

    expect(blocked).toMatchObject({ status: 'admission_blocked', current: 'global_task_capacity' })
    expect(intervention).toMatchObject({ status: 'intervention_required', current: '等待人工干预' })
  })

  it('lights Job wait reasons and surfaces cleanup attention', () => {
    const task = adaptTask({
      uuid: 'task-cleanup',
      workflow_uuid: 'wf-1',
      status: 'succeeded',
      cleanup_status: 'requires_attention',
      attention_reason: 'inventory_claim_uncertain',
      execution_plan: { nodes: [{ uuid: 'node-1', topological_index: 0 }] },
    }, [{ workflow_node_uuid: 'node-1', status: 'pending', wait_reason: { code: 'device_lock' } }])

    expect(task.status).toBe('intervention_required')
    expect(task.current).toBe('inventory_claim_uncertain')
    expect(task.nodes[0].status).toBe('waiting')
  })

  it('adapts material category and physical source location', () => {
    const material = adaptMaterial({
      uuid: 'mat-1',
      name: '烧杯 500 mL',
      barcode: 'BKR-001',
      class: 'community.szlab.beaker',
      resource_template_uuid: 'template-beaker',
      update_time: '2026-08-31T18:00:00Z',
      config: { category: 'beaker' },
      meta_data: { source_graph: 'szlab.json', source_node_id: 's3_unused_beaker__L1B4' },
    })

    expect(material).toMatchObject({
      uuid: 'mat-1',
      category: 'beaker',
      location: 'S3 / L1B4',
      sourceGraph: 'szlab.json',
      resourceTemplateUuid: 'template-beaker',
      status: 'available',
    })
  })

  it('uses the current Site authority instead of prefixing it with the original source station', () => {
    const moved = adaptMaterial({
      uuid: 'mat-moved',
      name: '已转运烧杯',
      meta_data: { source_node_id: 's3_unused_beaker__L1B4' },
      current_site: { name: 'S061', meta_data: { source_node_id: 's6_buffer' } },
    })
    const unassigned = adaptMaterial({
      uuid: 'mat-unassigned',
      name: '待分配烧杯',
      meta_data: { source_node_id: 's3_unused_beaker__L1B4' },
      current_site: null,
    })

    expect(moved.location).toBe('S6 / S061')
    expect(unassigned.location).toBe('未分配权威库位')
  })
})

describe('loadEdgeSnapshot', () => {
  it('follows Edge material pagination and separately requests every active task state', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/readiness')) {
        return response({ status: 'ready', workflowProgress: { loaded: 1, total: 1 } })
      }
      if (url.includes('/workflows?')) {
        return response({ code: 0, data: { items: [{ uuid: 'wf-1', name: '测试工作流' }], total: 1, has_more: false } })
      }
      if (url.includes('/workflow-tasks?')) {
        return response({ code: 0, data: { items: [], total: 0, has_more: false } })
      }
      if (url.includes('/materials?') && url.includes('page=1')) {
        return response({
          code: 0,
          data: {
            items: Array.from({ length: 100 }, (_, index) => ({ uuid: `material-${index}`, name: `物料 ${index}` })),
            total: 101,
            has_more: true,
          },
        })
      }
      if (url.includes('/materials?') && url.includes('page=2')) {
        return response({ code: 0, data: { items: [{ uuid: 'material-100', name: '物料 100' }], total: 101, has_more: false } })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const snapshot = await loadEdgeSnapshot()

    expect(snapshot.materials).toHaveLength(101)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/materials?page=2&page_size=100'),
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/workflow-tasks?status=running&page=1&page_size=100'),
      expect.any(Object),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/workflow-tasks?cleanup_status=requires_attention&page=1&page_size=100'),
      expect.any(Object),
    )
  })

  it('fails the snapshot when an authoritative Job projection cannot be read', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/readiness')) return response({ status: 'ready' })
      if (url.includes('/workflows?')) {
        return response({ code: 0, data: { items: [{ uuid: 'wf-1', name: '测试工作流' }], total: 1, has_more: false } })
      }
      if (url.includes('/workflow-tasks?')) {
        return response({ code: 0, data: { items: [{ uuid: 'task-1', workflow_uuid: 'wf-1', status: 'running' }], total: 1, has_more: false } })
      }
      if (url.includes('/materials?')) return response({ code: 0, data: { items: [], total: 0, has_more: false } })
      if (url.includes('/workflow-tasks/task-1/jobs')) return response({ code: 5001, message: 'jobs unavailable' })
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(loadEdgeSnapshot()).rejects.toThrow('jobs unavailable')
  })
})
