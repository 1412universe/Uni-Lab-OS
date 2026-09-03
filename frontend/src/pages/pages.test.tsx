import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import styles from '../styles.css?inline'
import { MaterialsPage } from './MaterialsPage'
import { OperationsPage } from './OperationsPage'
import { serialiseTaskInput, TasksPage } from './TasksPage'
import { WorkflowsPage } from './WorkflowsPage'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

function renderWithQuery(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('MaterialsPage', () => {
  it('links tree selection to the selected material relationship detail', () => {
    renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )

    const materialTree = screen.getByRole('complementary', { name: '物料目录' })
    fireEvent.click(within(materialTree).getByRole('button', { name: '250 mL 样品瓶' }))

    expect(within(materialTree).getByRole('button', { name: '250 mL 样品瓶' }).closest('[role="treeitem"]')).toHaveClass('selected')
    const detail = screen.getByLabelText('物料关系详情')
    expect(within(detail).getAllByText('250 mL 样品瓶').length).toBeGreaterThan(0)
    expect(within(detail).getAllByText(demoMaterials[1].uuid).length).toBeGreaterThan(0)
  })

  it('keeps inventory, site occupancy and material relationships in one workspace', () => {
    renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )
    expect(screen.getByRole('complementary', { name: '物料目录' })).toBeInTheDocument()
    expect(screen.getByLabelText('库位状态说明')).toBeInTheDocument()
    expect(screen.getByLabelText('物料关系详情')).toBeInTheDocument()
    expect(screen.getByText('物料关系')).toBeInTheDocument()
    expect(screen.getByText('自身库位')).toBeInTheDocument()
    expect(screen.queryByLabelText('物料 2.5D 库位场景')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '清单' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '库位' })).not.toBeInTheDocument()
  })

  it('shows where an occupied material is placed without borrowing the owner sites', () => {
    const owner = {
      ...demoMaterials[0],
      uuid: 'warehouse-1',
      name: 'S09 库位架',
      isStructural: true,
      siteCount: 2,
      currentLocation: { kind: 'structural' as const, label: '结构资源', siteCount: 2 },
      sites: [
        { uuid: 'empty-site', name: 'L0' },
        { uuid: 'site-1', name: 'L1', occupiedMaterialUuid: 'beaker-1', occupiedMaterialName: '测试烧杯' },
      ],
    }
    const occupant = {
      ...demoMaterials[1],
      uuid: 'beaker-1',
      name: '测试烧杯',
      parentUuid: owner.uuid,
      currentLocation: { kind: 'site' as const, label: 'S09 / L1', siteUuid: 'site-1', ownerMaterialUuid: owner.uuid },
    }
    renderWithQuery(
      <MaterialsPage materials={[occupant, owner]} total={2} connected={false} onNotify={vi.fn()} />,
    )

    const detail = screen.getByLabelText('物料关系详情')
    expect(within(detail).getAllByText('测试烧杯').length).toBeGreaterThan(0)
    expect(within(detail).getAllByText('S09 库位架').length).toBeGreaterThan(0)
    expect(within(detail).getByText('S09 库位架 / S09 / L1')).toBeInTheDocument()
    expect(within(detail).getByText('该物料没有库位')).toBeInTheDocument()
    expect(within(detail).queryByRole('button', { name: /L1/ })).not.toBeInTheDocument()
  })

  it('reuses the loaded material graph location in barcode verification results', async () => {
    const material = demoMaterials[0]
    vi.stubGlobal('fetch', vi.fn(async () => response({
      code: 0,
      data: { items: [{ uuid: material.uuid, name: material.name, barcode: material.barcode }], total: 1 },
    })))
    renderWithQuery(
      <MaterialsPage materials={[material]} total={1} connected={false} onNotify={vi.fn()} />,
    )

    fireEvent.click(screen.getByRole('button', { name: '扫码核验' }))
    fireEvent.change(screen.getByPlaceholderText('扫描枪回车或手动输入'), { target: { value: material.barcode } })
    fireEvent.click(screen.getByRole('button', { name: '校验条码' }))

    expect(await screen.findByText(material.currentLocation.label)).toBeInTheDocument()
    expect(screen.queryByText('权威位置尚未读取')).not.toBeInTheDocument()
  })

  it('filters loading candidates by the selected site template policy', () => {
    const notify = vi.fn()
    const owner = {
      ...demoMaterials[0],
      uuid: 'owner-1',
      name: 'S04 库位架',
      isStructural: true,
      currentLocation: { kind: 'structural' as const, label: '结构资源', siteCount: 1 },
      sites: [{ uuid: 'site-1', name: 'L1', allowedResourceTemplateUuids: ['template-allowed'] }],
    }
    const allowed = {
      ...demoMaterials[1],
      uuid: 'allowed-1',
      name: '允许物料',
      resourceTemplateUuid: 'template-allowed',
      currentLocation: { kind: 'unassigned' as const, label: '未分配权威库位' },
    }
    const rejected = {
      ...demoMaterials[2],
      uuid: 'rejected-1',
      name: '不允许物料',
      resourceTemplateUuid: 'template-rejected',
      currentLocation: { kind: 'unassigned' as const, label: '未分配权威库位' },
    }
    renderWithQuery(<MaterialsPage materials={[owner, allowed, rejected]} total={3} connected onNotify={notify} />)

    expect(screen.getByText('库位允许放置的物料')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '上料' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('option', { name: /允许物料/ })).toBeInTheDocument()
    expect(within(dialog).queryByRole('option', { name: /不允许物料/ })).not.toBeInTheDocument()
  })

  it('warns before unloading an empty site', () => {
    const notify = vi.fn()
    const owner = {
      ...demoMaterials[0],
      uuid: 'owner-2',
      name: 'S04 空库位架',
      isStructural: true,
      currentLocation: { kind: 'structural' as const, label: '结构资源', siteCount: 1 },
      sites: [{ uuid: 'empty-site', name: 'L1' }],
    }
    renderWithQuery(<MaterialsPage materials={[owner]} total={1} connected onNotify={notify} />)

    fireEvent.click(screen.getByRole('button', { name: '下料' }))
    expect(notify).toHaveBeenCalledWith('库位“L1”上没有物料，无法下料')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('keeps stale material snapshots read-only while preserving read actions', () => {
    renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )

    expect(screen.getByRole('button', { name: '从模板实例化' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '扫码核验' })).toBeEnabled()
    const loadingButton = screen.queryByRole('button', { name: '上料' })
    if (loadingButton) expect(loadingButton).toBeDisabled()
    const removalButton = screen.queryByRole('button', { name: '下料' })
    if (removalButton) expect(removalButton).toBeDisabled()
  })
})

describe('OperationsPage', () => {
  it('disables authoring entry points while the Edge snapshot is stale', () => {
    renderWithQuery(
      <OperationsPage materials={demoMaterials} connected={false} onNotify={vi.fn()} />,
    )

    expect(screen.getByRole('button', { name: '导入 Python' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '导入 JSON' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '创建实验操作' })).toBeDisabled()
  })
})

describe('WorkflowsPage', () => {
  it('renders the empty catalog without a task navigation target', () => {
    expect(() => renderWithQuery(
      <WorkflowsPage workflows={[]} materials={[]} connected={false} onNavigate={vi.fn()} onNotify={vi.fn()} />,
    )).not.toThrow()
  })

  it('opens the selected workflow Python source in a read-only viewer', async () => {
    const workflow = demoWorkflows[0]
    const pythonSource = [
      'from unilabos.workflow import Workflow',
      '',
      `def ${workflow.uuid.replaceAll('-', '_')}():`,
      '    return Workflow(name="源码查看验收")',
      '',
    ].join('\n')
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/graph')) {
        return response({ code: 0, data: { workflow, nodes: [], edges: [] } })
      }
      if (url.endsWith('/authoring')) {
        return response({
          code: 0,
          data: {
            workflow_uuid: workflow.uuid,
            workflow_revision: workflow.revision,
            state: 'applied',
            draft: {
              source_uri: `package://szlab/${workflow.sourcePath}`,
              python_source: pythonSource,
              draft_hash: `sha256:${'a'.repeat(64)}`,
              update_time: '2026-09-03T09:30:00Z',
            },
          },
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)

    renderWithQuery(
      <WorkflowsPage
        workflows={[workflow]}
        materials={demoMaterials}
        connected
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '查看源码' }))

    const dialog = await screen.findByRole('dialog', { name: `${workflow.name} 源码` })
    expect(within(dialog).getByText(workflow.sourcePath!)).toBeInTheDocument()
    expect((await within(dialog).findByLabelText('Python 源码')).textContent).toBe(pythonSource)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/workflows/${workflow.uuid}/authoring`,
      expect.objectContaining({ headers: { Accept: 'application/json' } }),
    )

    fireEvent.click(within(dialog).getByRole('button', { name: '关闭源码查看器' }))
    expect(screen.queryByRole('dialog', { name: `${workflow.name} 源码` })).not.toBeInTheDocument()
  })

  it('selects the workflow targeted by a task navigation', async () => {
    const target = demoWorkflows[1]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/workflow-tasks/task-target')) {
        return response({
          code: 0,
          data: {
            uuid: 'task-target',
            workflow_uuid: target.uuid,
            workflow_snapshot: {
              workflow: target,
              nodes: [{ uuid: 'frozen-node', name: '冻结修订节点', type: 'ILab' }],
              edges: [],
            },
          },
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    }))

    renderWithQuery(
      <WorkflowsPage
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
        targetWorkflow={{ workflowUuid: target.uuid, revision: target.revision, taskUuid: 'task-target' }}
      />,
    )

    expect(screen.getByRole('button', { name: new RegExp(`r${target.revision}`) })).toHaveClass('active')
    expect(await screen.findByRole('heading', { name: target.name })).toBeInTheDocument()
    expect(await screen.findByText('冻结修订节点')).toBeInTheDocument()
    expect(screen.getByText('Task 冻结修订')).toBeInTheDocument()
    expect(screen.queryByText('版本信息')).not.toBeInTheDocument()
  })

  it('keeps Preflight as a compact primary action and opens the real Edge report', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/graph')) {
        return response({ code: 0, data: { workflow: demoWorkflows[0], nodes: [], edges: [] } })
      }
      if (url.includes('/run-preflight')) {
        return response({
          code: 0,
          data: {
            workflow_uuid: demoWorkflows[0].uuid,
            workflow_revision: demoWorkflows[0].revision,
            run_mode: 'normal',
            status: 'temporarily_unavailable',
            can_run: false,
            checked_at: '2026-09-01T00:00:00Z',
            summary: {
              execution_node_count: 10,
              passed_check_count: 2,
              blocking_check_count: 1,
              deferred_check_count: 1,
              confirmation_required_count: 0,
            },
            checks: [],
          },
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery(
      <WorkflowsPage
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    expect(screen.queryByText('版本信息')).not.toBeInTheDocument()
    expect(screen.queryByText('资源门禁')).not.toBeInTheDocument()
    expect(screen.queryByText('Edge 已就绪')).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: '运行 Preflight' })[0])

    expect(await screen.findByText('运行前诊断')).toBeInTheDocument()
    expect(await screen.findByText('当前条件暂不可用')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新 Preflight' })).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/run-preflight'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          run_mode: 'normal',
          input: {
            target_powder_mass_g: 1,
            volume_pump_1: 10,
            volume_pump_2: 10,
            pipette_volume_raw: 5000,
          },
        }),
      }),
    )
  })

  it('switches between topology, contract and diagnostics workspaces', () => {
    renderWithQuery(
      <WorkflowsPage workflows={demoWorkflows} materials={demoMaterials} connected={false} onNavigate={vi.fn()} onNotify={vi.fn()} />,
    )
    fireEvent.click(screen.getByRole('button', { name: '合同' }))
    expect(screen.getByText('运行输入')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '诊断' }))
    expect(screen.getByText('尚未运行 Preflight')).toBeInTheDocument()
  })

  it('combines workflow inputs, material bindings and preflight into one run setup', () => {
    renderWithQuery(
      <WorkflowsPage workflows={demoWorkflows} materials={demoMaterials} connected={false} onNavigate={vi.fn()} onNotify={vi.fn()} />,
    )
    fireEvent.click(screen.getByRole('button', { name: '进入运行准备' }))
    expect(screen.getByText('工作流 × 物料运行准备')).toBeInTheDocument()
    expect(screen.getByText('运行输入与物料绑定')).toBeInTheDocument()
    expect(screen.getByText('物料上下文')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交任务' })).toBeDisabled()
  })

  it('creates a Step task only after a Step Preflight in develop mode', async () => {
    const navigate = vi.fn()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/graph')) {
        return response({ code: 0, data: { workflow: demoWorkflows[0], nodes: [], edges: [] } })
      }
      if (url.includes('/run-preflight')) {
        return response({
          code: 0,
          data: {
            workflow_uuid: demoWorkflows[0].uuid,
            workflow_revision: demoWorkflows[0].revision,
            run_mode: 'step',
            status: 'runnable_now',
            can_run: true,
            checked_at: '2026-09-03T00:00:00Z',
            summary: {},
            checks: [],
          },
        })
      }
      if (url.endsWith('/workflow-tasks') && init?.method === 'POST') {
        return response({ code: 0, data: { uuid: 'step-task-1' } })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    renderWithQuery(
      <WorkflowsPage
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected
        startupMode="develop"
        onNavigate={navigate}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '进入运行准备' }))
    fireEvent.change(screen.getByRole('combobox', { name: '运行方式' }), { target: { value: 'step' } })
    expect(screen.getByRole('combobox', { name: '任务优先级' })).toHaveValue('normal')
    fireEvent.change(screen.getByRole('combobox', { name: '任务优先级' }), { target: { value: 'high' } })
    await waitFor(() => expect(screen.getAllByRole('button', { name: '运行 Preflight' })[1]).toBeEnabled())
    fireEvent.click(screen.getAllByRole('button', { name: '运行 Preflight' })[1])
    await screen.findByText('当前可提交，派发时仍会复核')
    fireEvent.click(screen.getByRole('button', { name: '提交任务' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/workflow-tasks',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"priority":"high"'),
      }),
    ))
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/run-preflight'),
      expect.objectContaining({ body: expect.stringContaining('"run_mode":"step"') }),
    )
    expect(navigate).toHaveBeenCalledWith('tasks')
  })

  it('projects graph material sources into the composite run context', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (!String(input).endsWith('/graph')) throw new Error(`Unexpected URL: ${String(input)}`)
      return response({ code: 0, data: { workflow: demoWorkflows[0], edges: [], nodes: [{ uuid: 'source-1', name: '原料来源', type: 'material_source', param: { mode: 'existing', resource_template_uuid: demoMaterials[0].resourceTemplateUuid, mount: { uuid: 'warehouse-1' } } }] } })
    }))
    renderWithQuery(<WorkflowsPage workflows={demoWorkflows} materials={demoMaterials} connected={false} onNavigate={vi.fn()} onNotify={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: '进入运行准备' }))
    expect(await screen.findByText('原料来源')).toBeInTheDocument()
    expect(screen.getByText(/运行时自动解析/)).toBeInTheDocument()
    expect(screen.getByText('挂载资源')).toBeInTheDocument()
  })

  it('renders the complete authoritative DAG instead of inventing a linear preview', async () => {
    const workflow = {
      ...demoWorkflows[0],
      uuid: 'wf-authoritative-dag',
      name: '权威 DAG 验证流程',
      revision: 2,
      nodeCount: 0,
      inputContract: [],
      outputContract: [],
    }
    const nodes = [
      { uuid: 'source-b', name: '试剂物料', type: 'material_source', meta_data: { unilab: { authoring_source_order: 1 } } },
      { uuid: 'source-a', name: '样品物料', type: 'material_source', meta_data: { unilab: { authoring_source_order: 0 } } },
      {
        uuid: 'move',
        name: '原子物料搬运',
        type: 'ILab',
        parent_uuid: 'transfer-group',
        param: { target_device: 'camera' },
        meta_data: {
          unilab: {
            authoring_source_order: 8,
            authoring_result_name: 'beaker_at_s07',
            executor_binding: { device_id: 'robot' },
          },
        },
      },
      { uuid: 'transfer-group', name: '原子转运组', type: 'group' },
      {
        uuid: 'photo-group',
        name: '烧杯拍照',
        type: 'group',
        meta_data: { unilab: { parallel_scope: 'parallel-1' } },
      },
      {
        uuid: 'cap-group',
        name: '样品瓶开盖',
        type: 'group',
        meta_data: { unilab: { parallel_scope: 'parallel-1' } },
      },
      {
        uuid: 'photo',
        name: '拍照',
        type: 'ILab',
        parent_uuid: 'photo-group',
        meta_data: { unilab: { executor_binding: { device_id: 'camera' } } },
      },
      {
        uuid: 'cap',
        name: '开盖',
        type: 'ILab',
        parent_uuid: 'cap-group',
        meta_data: { unilab: { executor_binding: { device_id: 'capper' } } },
      },
      { uuid: 'join', name: '汇合倒液', type: 'ILab' },
      { uuid: 'stir', name: '搅拌', type: 'ILab' },
      { uuid: 'density', name: '测密度', type: 'ILab' },
      {
        uuid: 'finish',
        name: '原子物料搬运',
        type: 'ILab',
        meta_data: { unilab: { authoring_source_order: 9, authoring_result_name: 'product_at_s11' } },
      },
      { uuid: 'archive', name: '归档', type: 'ILab' },
    ]
    const edges = [
      { uuid: 'edge-1', source_node_uuid: 'source-a', target_node_uuid: 'move' },
      { uuid: 'edge-2', source_node_uuid: 'source-b', target_node_uuid: 'cap' },
      { uuid: 'edge-3', source_node_uuid: 'move', target_node_uuid: 'photo' },
      { uuid: 'edge-4', source_node_uuid: 'move', target_node_uuid: 'cap' },
      { uuid: 'edge-5', source_node_uuid: 'photo', target_node_uuid: 'join' },
      { uuid: 'edge-6', source_node_uuid: 'cap', target_node_uuid: 'join' },
      { uuid: 'edge-7', source_node_uuid: 'join', target_node_uuid: 'stir' },
      { uuid: 'edge-8', source_node_uuid: 'stir', target_node_uuid: 'density' },
      { uuid: 'edge-9', source_node_uuid: 'density', target_node_uuid: 'finish' },
      { uuid: 'edge-10', source_node_uuid: 'finish', target_node_uuid: 'archive' },
    ]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/graph')) {
        return response({
          code: 0,
          data: {
            workflow: {
              uuid: workflow.uuid,
              name: workflow.name,
              revision: workflow.revision,
              status: 'source',
              description: '验证完整节点、分组、分支与汇合。',
              meta_data: { unilab: { input_contract: {}, output_contract: {} } },
            },
            nodes,
            edges,
          },
        })
      }
      throw new Error(`Unexpected URL: ${url}`)
    }))

    renderWithQuery(
      <WorkflowsPage
        workflows={[workflow]}
        materials={demoMaterials}
        connected
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    const topology = await screen.findByRole('region', { name: '发布修订拓扑' })
    expect(await within(topology).findAllByRole('article')).toHaveLength(10)
    const materialInputs = within(topology).getByRole('region', { name: '物料输入' })
    expect(within(materialInputs).getAllByRole('article')).toHaveLength(2)
    expect(within(materialInputs).getAllByRole('article').map((item) => item.getAttribute('aria-label'))).toEqual([
      '样品物料，物料源',
      '试剂物料，物料源',
    ])
    expect(within(topology).getByText('4 条流程依赖 · 4 条并行控制 · 2 条物料输入')).toBeInTheDocument()
    expect(within(topology.querySelector('.workflow-dag-stage') as HTMLElement).getAllByRole('article')).toHaveLength(8)
    expect(within(topology).getByRole('article', { name: /归档/ })).toBeInTheDocument()
    expect(within(topology).getByRole('group', { name: '分组：原子转运组' })).toHaveTextContent('搬运')
    expect(within(topology).getAllByRole('button', { name: /^(流程依赖|物料输入|并行入口|并行汇合)：/ })).toHaveLength(edges.length)
    expect(within(topology).getByRole('button', { name: '流程依赖：汇合倒液 → 搅拌' })).toBeInTheDocument()
    const parallelControl = within(topology).getByRole('button', { name: '并行入口：原子物料搬运 → 开盖' })
    expect(parallelControl).toBeInTheDocument()
    expect(within(topology).queryByRole('button', { name: '流程依赖：拍照 → 开盖' })).not.toBeInTheDocument()
    expect(within(topology).getByRole('article', { name: '样品物料，物料源' })).toHaveTextContent('物料源')
    expect(within(topology).getByRole('article', {
      name: '原子物料搬运，beaker_at_s07，robot，物料输入：样品物料',
    })).toHaveTextContent('#09 · beaker_at_s07')
    expect(within(topology).getByRole('article', {
      name: '原子物料搬运，product_at_s11，ILab',
    })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /r2 · 13 节点/ })).toBeInTheDocument()

    fireEvent.click(parallelControl)
    expect(within(topology).getByRole('status')).toHaveTextContent('并行入口')
    expect(within(topology).getByRole('article', {
      name: /原子物料搬运，beaker_at_s07，robot，物料输入：样品物料，已选连线起点/,
    })).toBeInTheDocument()
    expect(within(topology).getByRole('article', { name: /开盖，capper.*已选连线终点/ })).toBeInTheDocument()

    fireEvent.click(within(topology).getByRole('button', { name: '查看完整 DAG' }))
    expect(within(topology).getByRole('button', { name: '查看主流程' })).toBeInTheDocument()
    expect(within(topology).queryByRole('region', { name: '物料输入' })).not.toBeInTheDocument()
    expect(within(topology.querySelector('.workflow-dag-stage') as HTMLElement).getAllByRole('article')).toHaveLength(10)
  })
})

describe('TasksPage', () => {
  it('shows authoritative Step candidates and submits the selected node', async () => {
    const onNotify = vi.fn()
    const task = {
      ...demoTasks[0],
      status: 'paused' as const,
      executionMode: 'step' as const,
      controlStatus: 'paused',
      nodes: demoTasks[0].nodes.slice(0, 2).map((node, index) => ({
        ...node,
        uuid: `step-node-${index + 1}`,
        name: `候选节点 ${index + 1}`,
        status: 'pending' as const,
      })),
    }
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith(`/workflow-tasks/${task.uuid}/step-state`)) {
        return response({
          code: 0,
          data: {
            workflow_task_uuid: task.uuid,
            execution_mode: 'step',
            control_status: 'paused',
            in_flight_job_count: 0,
            requires_selection: true,
            can_step: true,
            candidates: task.nodes.map((node) => ({ node_uuid: node.uuid, name: node.name, kind: node.kind })),
          },
        })
      }
      if (url.endsWith(`/workflow-tasks/${task.uuid}/commands`) && init?.method === 'POST') {
        return response({ code: 0, data: { status: 'succeeded', result: {} } })
      }
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const historicalTask = {
      ...task,
      uuid: 'historical-step-task',
      nodes: task.nodes.map((node, index) => ({
        ...node,
        name: `历史节点 ${index + 1}`,
      })),
    }
    renderWithQuery(
      <TasksPage
        tasks={[task, historicalTask]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected
        startupMode="develop"
        onRefresh={vi.fn()}
        onNotify={onNotify}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const taskRow = (await screen.findAllByText(task.uuid))[0].closest('.matrix-row') as HTMLElement
    const inlineControls = within(taskRow).getByLabelText('Task 行内单步调度控制')
    fireEvent.change(await within(inlineControls).findByRole('combobox', { name: '下一步节点' }), {
      target: { value: 'step-node-2' },
    })
    fireEvent.click(within(inlineControls).getByRole('button', { name: '执行下一步' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/workflow-tasks/${task.uuid}/commands`,
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"target_node_uuid":"step-node-2"'),
      }),
    ))
    expect(onNotify).toHaveBeenCalledWith('单步命令已提交')
    expect(screen.getByRole('button', { name: /候选节点 2，/ }).closest('.matrix-node')).toHaveClass('matrix-node-step-ready')
    expect(screen.getByRole('button', { name: /历史节点 2，/ }).closest('.matrix-node')).not.toHaveClass('matrix-node-step-ready')
    const selectedTaskPanel = screen.getByText('选中任务').closest('.panel') as HTMLElement
    expect(within(selectedTaskPanel).queryByRole('button', { name: '执行下一步' })).not.toBeInTheDocument()
  })

  it('highlights pending manual confirmation and submits approve by Job UUID', async () => {
    const onNotify = vi.fn()
    const task = {
      ...demoTasks[0],
      nodes: [{
        ...demoTasks[0].nodes[0],
        uuid: 'manual-node',
        name: '请检查设备现场',
        kind: 'manual_confirm',
        status: 'attention' as const,
        job: {
          uuid: 'manual-job-1',
          attempt: 1,
          param: { temperature: 25 },
          feedbackData: {},
          returnInfo: {},
          errorInfo: [],
          manualConfirmation: {
            status: 'pending' as const,
            deadlineAt: '2099-01-01T00:00:00Z',
            actions: ['approve' as const, 'reject' as const],
          },
        },
      }],
    }
    const fetchMock = vi.fn(async () => response({
      code: 0,
      data: { task: { uuid: task.uuid }, jobs: [] },
    }))
    vi.stubGlobal('fetch', fetchMock)

    renderWithQuery(
      <TasksPage
        tasks={[task]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected
        onRefresh={vi.fn()}
        onNotify={onNotify}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const marker = screen.getByRole('button', { name: /请检查设备现场，需要人工确认/ })
    expect(marker.closest('.matrix-node')).toHaveClass('matrix-node-attention')
    expect(screen.getByText(/剩余 \d+s/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批准' }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/workflow-node-jobs/manual-job-1/manual-confirmation',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ action: 'approve' }),
      }),
    ))
    await waitFor(() => expect(onNotify).toHaveBeenCalledWith(
      '人工确认已批准，设备动作将继续执行。',
    ))
  })

  it('distinguishes successful, running, waiting, failed, and not-run nodes in the matrix', () => {
    const style = document.createElement('style')
    style.textContent = styles
    document.head.appendChild(style)
    const statuses = ['succeeded', 'running', 'waiting', 'failed', 'pending'] as const
    const task = {
      ...demoTasks[0],
      nodes: statuses.map((status, index) => ({
        ...demoTasks[0].nodes[index],
        uuid: `visual-state-${status}`,
        name: `状态节点 ${status}`,
        status,
      })),
    }
    renderWithQuery(
      <TasksPage
        tasks={[task]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const legend = screen.getByLabelText('节点状态颜色')
    ;['运行成功', '正在运行', '等待运行', '运行失败', '未运行'].forEach((label) => {
      expect(within(legend).getByText(label)).toBeInTheDocument()
    })

    const nodes = statuses.map((status) => {
      const node = screen.getByRole('button', { name: new RegExp(`状态节点 ${status}，`) })
      expect(node.closest('.matrix-node')).toHaveClass(`matrix-node-${status}`)
      return node.closest('.matrix-node') as HTMLElement
    })
    const backgroundColors = nodes.map((node) => getComputedStyle(node).backgroundColor)
    expect(backgroundColors).not.toContain('rgba(0, 0, 0, 0)')
    expect(new Set(backgroundColors).size).toBe(statuses.length)
    style.remove()
  })

  it('renders one matrix row per task and lights every running task node', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    expect(container.querySelectorAll('.matrix-row')).toHaveLength(demoTasks.length)
    expect(container.querySelectorAll('.task-matrix-scroll')).toHaveLength(1)
    expect(container.querySelector('.matrix-header')).not.toBeInTheDocument()
    expect(container.querySelector('.matrix-group-title')).not.toBeInTheDocument()
    expect(container.querySelectorAll('.matrix-node-running')).toHaveLength(3)
    expect(container.querySelectorAll('.matrix-node-title').length).toBeGreaterThan(0)
    expect(container.querySelectorAll('.matrix-trace-link, .matrix-trace-disabled')).toHaveLength(demoTasks.length)
  })

  it('shows the authoritative task priority in each matrix identity card', () => {
    const tasks = [
      { ...demoTasks[0], priority: 'high' as const },
      { ...demoTasks[1], priority: 'normal' as const },
    ]
    renderWithQuery(
      <TasksPage
        tasks={tasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    expect(screen.getByText('高优先级')).toHaveClass('matrix-task-priority-high')
    expect(screen.getByText('普通优先级')).toHaveClass('matrix-task-priority-normal')
  })

  it('opens the task workflow and revision from the sticky task identity card', () => {
    const onOpenWorkflow = vi.fn()
    renderWithQuery(
      <TasksPage
        tasks={[demoTasks[0]]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={onOpenWorkflow}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /打开工作流.*Task/ }))
    expect(onOpenWorkflow).toHaveBeenCalledWith({
      workflowUuid: demoTasks[0].workflowUuid,
      revision: demoTasks[0].workflowRevision,
      taskUuid: demoTasks[0].uuid,
    })
  })

  it('opens the selected task trace in SigNoz without replacing the console', () => {
    const tracedTask = {
      ...demoTasks[0],
      trace: {
        traceId: '0123456789abcdef0123456789abcdef',
        url: 'http://127.0.0.1:30081/trace/0123456789abcdef0123456789abcdef',
        mode: 'trace' as const,
      },
    }
    renderWithQuery(
      <TasksPage
        tasks={[tracedTask]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const link = screen.getByRole('link', { name: '在 SigNoz 中查看 Trace' })
    expect(link).toHaveAttribute('href', tracedTask.trace.url)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })

  it('opens authoritative node parameters and execution results from a matrix node', () => {
    const task = {
      ...demoTasks[0],
      nodes: demoTasks[0].nodes.map((node, index) => index === 2
        ? {
            ...node,
            job: {
              uuid: 'job-dose-1',
              attempt: 2,
              param: { target_mass_g: 1.2 },
              feedbackData: { current_mass_g: 0.8 },
              returnInfo: { success: true, actual_mass_g: 1.19 },
              errorInfo: [],
            },
          }
        : node),
    }
    renderWithQuery(
      <TasksPage
        tasks={[task]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /粉桶扫码.*已完成/ }))
    const details = screen.getByRole('region', { name: '节点运行详情' })
    expect(details).toHaveTextContent('job-dose-1')
    expect(details).toHaveTextContent('target_mass_g')
    expect(details).toHaveTextContent('actual_mass_g')
  })

  it('shows a waiting reason only while its node is hovered or keyboard-focused', () => {
    const task = {
      ...demoTasks[0],
      nodes: demoTasks[0].nodes.map((node, index) => index === 4
        ? {
            ...node,
            status: 'waiting' as const,
            waitReason: {
              code: 'operation_lease',
              title: '等待库位',
              message: '目标库位正在被其他作业使用',
              details: [
                '库位：S07 工作站 / 称量位（site-s0722）',
                '物料：待称量烧杯（material-1）',
              ],
              waitingSince: '2026-09-01T09:00:00Z',
            },
          }
        : node),
    }
    renderWithQuery(
      <TasksPage
        tasks={[task]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const marker = screen.getByLabelText('转运至 S09，等待资源')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    fireEvent.mouseEnter(marker)
    expect(screen.getByRole('tooltip')).toHaveTextContent('等待库位')
    expect(screen.getByRole('tooltip')).toHaveTextContent('库位：S07 工作站 / 称量位（site-s0722）')
    expect(screen.getByRole('tooltip')).toHaveTextContent('物料：待称量烧杯（material-1）')

    fireEvent.mouseLeave(marker)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    fireEvent.focus(marker)
    expect(screen.getByRole('tooltip')).toHaveTextContent('目标库位正在被其他作业使用')
    fireEvent.keyDown(marker, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    fireEvent.focus(marker)
    fireEvent.blur(marker)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('shows the complete node title and status in a tooltip on hover', () => {
    const task = {
      ...demoTasks[0],
      nodes: demoTasks[0].nodes.map((node, index) => index === 2
        ? { ...node, name: 'dose_powder_with_two_materials_and_a_very_long_suffix' }
        : node),
    }
    renderWithQuery(
      <TasksPage
        tasks={[task]}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    const marker = screen.getByRole('button', { name: /dose_powder_with_two_materials_and_a_very_long_suffix/ })
    fireEvent.mouseEnter(marker)
    expect(screen.getByRole('tooltip')).toHaveTextContent('dose_powder_with_two_materials_and_a_very_long_suffix')
    expect(screen.getByRole('tooltip')).toHaveTextContent('已完成')
  })

  it('filters the matrix to failed tasks', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /异常1/ }))
    expect(container.querySelectorAll('.matrix-row')).toHaveLength(1)
    expect(container.querySelector('.matrix-row')).toHaveTextContent('TK-240831-015')
  })

  it('renders different workflow plans as adjacent rows in one shared scroll region', () => {
    const tasks = [
      demoTasks[0],
      {
        ...demoTasks[1],
        workflowUuid: demoWorkflows[1].uuid,
        workflowName: demoWorkflows[1].name,
        workflowRevision: demoWorkflows[1].revision,
        matrixGroupKey: 'single-node-debug-plan',
        nodes: demoTasks[1].nodes.slice(0, 4),
      },
    ]
    const { container } = renderWithQuery(
      <TasksPage tasks={tasks} workflows={demoWorkflows} materials={demoMaterials} connected={false} onRefresh={vi.fn()} onNotify={vi.fn()} onOpenWorkflow={vi.fn()} />,
    )

    expect(container.querySelectorAll('.task-matrix-scroll')).toHaveLength(1)
    expect(container.querySelectorAll('.matrix-row')).toHaveLength(2)
    expect(container.querySelector('.matrix-group')).not.toBeInTheDocument()
  })

  it('delegates task creation to the workflow run-preparation page', () => {
    const onOpenWorkflow = vi.fn()
    renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
        onOpenWorkflow={onOpenWorkflow}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '前往工作流创建' }))

    expect(onOpenWorkflow).toHaveBeenCalledWith({
      workflowUuid: demoWorkflows[0].uuid,
      revision: demoWorkflows[0].revision,
    })
  })
})

describe('serialiseTaskInput', () => {
  it('omits blank optional values and rejects invalid numeric input', () => {
    const fields = [
      { name: 'volume', type: 'integer', required: true, schema: { type: 'integer' } },
      { name: 'note', type: 'string', required: false, schema: { type: 'string' } },
    ]
    expect(serialiseTaskInput(fields, { volume: '8', note: '' })).toEqual({ volume: 8 })
    expect(() => serialiseTaskInput(fields, { volume: 'abc', note: '' })).toThrow('必须是整数')
  })

  it('does not invent a false value for an unset optional boolean', () => {
    expect(serialiseTaskInput(
      [{ name: 'skip_robot', type: 'boolean', required: false, schema: { type: 'boolean' } }],
      { skip_robot: '' },
    )).toEqual({})
  })

  it('serialises scalar ResourceSlot values with the Edge uuid envelope', () => {
    expect(serialiseTaskInput(
      [{ name: 'resource', type: 'ResourceSlot', required: true, schema: { $slot: 'ResourceSlot' } }],
      { resource: 'mat-uuid' },
    )).toEqual({ resource: { uuid: 'mat-uuid' } })
  })

  it('parses structured JSON inputs and rejects a mismatched shape', () => {
    const fields = [
      { name: 'configuration', type: 'object', required: true, schema: { type: 'object' } },
      { name: 'replicates', type: 'array', required: true, schema: { type: 'array' } },
    ]

    expect(serialiseTaskInput(fields, {
      configuration: '{"mode":"fast"}',
      replicates: '[1,2]',
    })).toEqual({ configuration: { mode: 'fast' }, replicates: [1, 2] })
    expect(() => serialiseTaskInput(fields, {
      configuration: '[]',
      replicates: '[1,2]',
    })).toThrow('必须是 JSON 对象')
  })
})
