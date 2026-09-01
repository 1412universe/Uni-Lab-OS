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
    expect(() => unwrapEnvelope({ code: 1000, error: { msg: 'invalid cursor' } })).toThrow('invalid cursor')
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

  it('does not reinterpret a non-contract task status alias', () => {
    expect(adaptTask({ uuid: 'task-alias', status: 'success' }).status).toBe('unknown')
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

  it('groups only identical frozen task matrix definitions', () => {
    const snapshot = {
      workflow: { uuid: 'wf-1', name: '冻结流程', revision: 3, create_time: '2026-01-01', update_time: '2026-01-01' },
      nodes: [{ uuid: 'node-1', name: '加液', param: { static_mode: 'fast' }, create_time: '2026-01-01' }],
      edges: [],
    }
    const task = (uuid: string, revision = 3, param = { volume: 1 }, edge = false) => adaptTask({
      uuid,
      workflow_uuid: 'wf-1',
      execution_kind: 'workflow',
      status: 'running',
      run_mode: 'normal',
      workflow_snapshot: {
        ...snapshot,
        workflow: { ...snapshot.workflow, revision },
        edges: edge ? [{ uuid: 'edge-1' }] : [],
      },
      execution_plan: {
        version: 1,
        nodes: [{ uuid: 'node-1', kind: 'device_action', topological_index: 0, param }],
        edges: edge ? [{ source_handle_uuid: 'a', target_handle_uuid: 'b' }] : [],
        handles: [],
      },
    })

    expect(task('task-a', 3, { volume: 1 }).matrixGroupKey).toBe(
      task('task-b', 3, { volume: 2 }).matrixGroupKey,
    )
    const reordered = adaptTask({
      run_mode: 'normal',
      status: 'running',
      execution_kind: 'workflow',
      workflow_uuid: 'wf-1',
      uuid: 'task-reordered',
      execution_plan: {
        handles: [],
        edges: [],
        nodes: [{ topological_index: 0, kind: 'device_action', uuid: 'node-1', param: { volume: 9 } }],
        version: 1,
      },
      workflow_snapshot: {
        edges: [],
        nodes: [{ update_time: '2026-09-01', param: { static_mode: 'fast' }, name: '加液', uuid: 'node-1' }],
        workflow: { update_time: '2026-09-01', create_time: '2025-01-01', revision: 3, name: '冻结流程', uuid: 'wf-1' },
      },
    })
    expect(task('task-a', 3).matrixGroupKey).toBe(reordered.matrixGroupKey)
    expect(task('task-a', 3).matrixGroupKey).not.toBe(task('task-b', 4).matrixGroupKey)
    expect(task('task-a', 3).matrixGroupKey).not.toBe(task('task-b', 3, { volume: 1 }, true).matrixGroupKey)
    expect(adaptTask({ uuid: 'task-missing-a' }).matrixGroupKey).not.toBe(
      adaptTask({ uuid: 'task-missing-b' }).matrixGroupKey,
    )
  })

  it('collects exact material UUID references from plans, jobs, and ResourceSlot inputs', () => {
    const task = adaptTask(
      {
        uuid: 'task-materials',
        workflow_uuid: 'wf-1',
        status: 'running',
        input: {
          sample: { uuid: 'material-input' },
          batches: [{ uuid: 'material-input-array-a' }, { uuid: 'material-input-array-b' }],
        },
        execution_plan: {
          nodes: [{
            uuid: 'node-1',
            material_uuid: 'material-plan',
            param: {
              plate: { uuid: 'material-param' },
              tips: [{ uuid: 'material-tip-a' }, { uuid: 'material-tip-b' }],
            },
            param_schema: {
              type: 'object',
              properties: {
                goal: {
                  type: 'object',
                  properties: {
                    plate: { $slot: 'ResourceSlot' },
                    tips: { type: 'array', items: { $slot: 'ResourceSlot' } },
                  },
                },
              },
            },
          }],
        },
      },
      [{
        workflow_node_uuid: 'node-1',
        status: 'running',
        material_uuid: 'material-job',
        control_data: { actual_executor: { material_uuid: 'material-executor' } },
        return_info: { material: { uuid: 'material-result' } },
        expected_change_set: { material_uuid: 'material-change-set' },
        param: { plate: { uuid: 'material-job-param' }, tips: [] },
      }],
      '物料流程',
      [
        { name: 'sample', type: 'ResourceSlot', schema: { $slot: 'ResourceSlot' } },
        { name: 'batches', type: 'array', schema: { type: 'array', items: { $slot: 'ResourceSlot' } } },
      ],
    )

    expect(new Set(task.materialUuids)).toEqual(new Set([
      'material-input',
      'material-input-array-a',
      'material-input-array-b',
      'material-plan',
      'material-param',
      'material-tip-a',
      'material-tip-b',
      'material-job-param',
      'material-job',
      'material-executor',
      'material-result',
      'material-change-set',
    ]))
  })

  it('keeps configured source separate from an unresolved current location', () => {
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
      configuredSource: 'S3 / L1B4',
      currentLocation: { kind: 'unresolved', label: '权威位置尚未读取' },
      sourceGraph: 'szlab.json',
      resourceTemplateUuid: 'template-beaker',
      taskReferences: [],
    })
  })

  it('uses the current Site authority instead of prefixing it with the original source station', () => {
    const moved = adaptMaterial({
      uuid: 'mat-moved',
      name: '已转运烧杯',
      meta_data: { source_node_id: 's3_unused_beaker__L1B4' },
      current_site: {
        uuid: 'site-s061',
        name: 'S061',
        material_uuid: 'station-s6',
        meta_data: { source_node_id: 's6_buffer' },
      },
    })
    const unassigned = adaptMaterial({
      uuid: 'mat-unassigned',
      name: '待分配烧杯',
      meta_data: { source_node_id: 's3_unused_beaker__L1B4' },
      current_site: null,
    })

    expect(moved.currentLocation).toEqual({
      kind: 'site',
      label: 'S6 / S061',
      siteUuid: 'site-s061',
      ownerMaterialUuid: 'station-s6',
    })
    expect(unassigned.currentLocation).toEqual({
      kind: 'unassigned',
      label: '未分配权威库位',
    })
  })
})

describe('loadEdgeSnapshot', () => {
  it('joins authoritative Material Graph locations and exact nonterminal task references', async () => {
    const taskRows = [
      {
        uuid: 'task-running',
        workflow_uuid: 'wf-1',
        status: 'running',
        workflow_snapshot: { workflow: { uuid: 'wf-1', name: '物料流程', revision: 1 }, nodes: [], edges: [] },
        execution_plan: { nodes: [{ uuid: 'node-1', material_uuid: 'material-child' }], edges: [], handles: [] },
      },
      {
        uuid: 'task-succeeded',
        workflow_uuid: 'wf-1',
        status: 'succeeded',
        workflow_snapshot: { workflow: { uuid: 'wf-1', name: '物料流程', revision: 1 }, nodes: [], edges: [] },
        execution_plan: { nodes: [{ uuid: 'node-1', material_uuid: 'material-child' }], edges: [], handles: [] },
      },
    ]
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/readiness')) return response({ status: 'ready' })
      if (url.includes('/workflows?')) {
        return response({ code: 0, data: { items: [{ uuid: 'wf-1', name: '物料流程' }], total: 1 } })
      }
      if (url.includes('/workflow-tasks/task-')) return response({ code: 0, data: [] })
      if (url.includes('/workflow-tasks?')) {
        return response({ code: 0, data: { items: taskRows, total: taskRows.length } })
      }
      if (url.endsWith('/materials/graph')) {
        return response({
          code: 0,
          data: {
            nodes: [
              {
                material: { uuid: 'material-owner', name: 'S06 工站' },
                current_site_uuid: null,
                sites: [{ uuid: 'site-s061', name: 'S061', material_uuid: 'material-owner' }],
              },
              {
                material: { uuid: 'material-child', name: '烧杯' },
                current_site_uuid: 'site-s061',
                sites: [],
              },
            ],
          },
        })
      }
      if (url.includes('/materials?')) {
        return response({ code: 0, data: { items: [{ uuid: 'material-child', name: '烧杯' }], total: 1 } })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const snapshot = await loadEdgeSnapshot()

    expect(snapshot.materials[0].currentLocation).toEqual({
      kind: 'site',
      label: 'S06 工站 / S061',
      siteUuid: 'site-s061',
      ownerMaterialUuid: 'material-owner',
    })
    expect(snapshot.materials[0].taskReferences).toEqual([
      expect.objectContaining({ taskUuid: 'task-running', taskStatus: 'running' }),
    ])
  })

  it('uses the frozen task input contract when projecting ResourceSlot references', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/readiness')) return response({ status: 'ready' })
      if (url.includes('/workflows?')) {
        return response({
          code: 0,
          data: {
            items: [{
              uuid: 'wf-1',
              name: '当前流程',
              meta_data: { unilab: { input_contract: { parameters: [] } } },
            }],
            total: 1,
          },
        })
      }
      if (url.includes('/workflow-tasks/task-frozen/jobs')) return response({ code: 0, data: [] })
      if (url.includes('/workflow-tasks?status=running')) {
        return response({
          code: 0,
          data: {
            items: [{
              uuid: 'task-frozen',
              workflow_uuid: 'wf-1',
              status: 'running',
              input: { vessel: { uuid: 'material-frozen' } },
              workflow_snapshot: {
                workflow: {
                  uuid: 'wf-1',
                  name: '冻结流程',
                  revision: 1,
                  meta_data: {
                    unilab: {
                      input_contract: {
                        parameters: [{ name: 'vessel', schema: { $slot: 'ResourceSlot' } }],
                      },
                    },
                  },
                },
                nodes: [],
                edges: [],
              },
              execution_plan: { nodes: [], edges: [], handles: [] },
            }],
            total: 1,
          },
        })
      }
      if (url.includes('/workflow-tasks?')) return response({ code: 0, data: { items: [], total: 0 } })
      if (url.endsWith('/materials/graph')) {
        return response({ code: 0, data: { nodes: [{ material: { uuid: 'material-frozen' }, sites: [] }] } })
      }
      if (url.includes('/materials?')) {
        return response({ code: 0, data: { items: [{ uuid: 'material-frozen', name: '冻结物料' }], total: 1 } })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    const snapshot = await loadEdgeSnapshot()

    expect(snapshot.materials[0].taskReferences).toEqual([
      expect.objectContaining({ taskUuid: 'task-frozen', workflowName: '冻结流程' }),
    ])
    expect(snapshot.materials[0].currentLocation).toEqual({
      kind: 'unresolved',
      label: '物料图缺少当前位置字段',
    })
  })

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
      if (url.endsWith('/materials/graph')) {
        return response({ code: 0, data: { nodes: [] } })
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
    for (const status of ['succeeded', 'failed', 'canceled', 'timeout']) {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/workflow-tasks?status=${status}&page=1&page_size=20`),
        expect.any(Object),
      )
    }
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
      if (url.endsWith('/materials/graph')) return response({ code: 0, data: { nodes: [] } })
      if (url.includes('/materials?')) return response({ code: 0, data: { items: [], total: 0, has_more: false } })
      if (url.includes('/workflow-tasks/task-1/jobs')) return response({ code: 5001, error: { msg: 'jobs unavailable' } })
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(loadEdgeSnapshot()).rejects.toThrow('jobs unavailable')
  })
})
