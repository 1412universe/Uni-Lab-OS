import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import { MaterialsPage } from './MaterialsPage'
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
    renderWithQuery(<MaterialsPage materials={[owner, allowed, rejected]} total={3} connected={false} onNotify={notify} />)

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
    renderWithQuery(<MaterialsPage materials={[owner]} total={1} connected={false} onNotify={notify} />)

    fireEvent.click(screen.getByRole('button', { name: '下料' }))
    expect(notify).toHaveBeenCalledWith('库位“L1”上没有物料，无法下料')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})

describe('WorkflowsPage', () => {
  it('shows a neutral state until it reads the real Edge Preflight report', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
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

    expect(screen.getAllByText('尚未检查').length).toBeGreaterThan(0)
    expect(screen.queryByText('Edge 已就绪')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '运行 Preflight' }))

    expect(await screen.findByText('当前条件暂不可用')).toBeInTheDocument()
    expect(screen.getByText('1 项延后 · 0 项人工确认')).toBeInTheDocument()
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
  it('renders one matrix row per task and lights every running task node', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    expect(container.querySelectorAll('.matrix-row')).toHaveLength(demoTasks.length)
    expect(container.querySelectorAll('.matrix-node-running')).toHaveLength(3)
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
              details: ['库位：S0722（物料 material-1）'],
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
      />,
    )

    const marker = screen.getByLabelText('转运至 S09，等待资源')
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    fireEvent.mouseEnter(marker)
    expect(screen.getByRole('tooltip')).toHaveTextContent('等待库位')
    expect(screen.getByRole('tooltip')).toHaveTextContent('库位：S0722（物料 material-1）')

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

  it('filters the matrix to failed tasks', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /异常1/ }))
    expect(container.querySelectorAll('.matrix-row')).toHaveLength(1)
    expect(container.querySelector('.matrix-row')).toHaveTextContent('TK-240831-015')
  })

  it('separates different frozen execution plans even when workflow UUID is shared', () => {
    const tasks = [
      demoTasks[0],
      { ...demoTasks[1], matrixGroupKey: 'single-node-debug-plan', workflowRevision: 4 },
    ]
    const { container } = renderWithQuery(
      <TasksPage tasks={tasks} workflows={demoWorkflows} materials={demoMaterials} connected={false} onRefresh={vi.fn()} onNotify={vi.fn()} />,
    )

    expect(container.querySelectorAll('.matrix-group')).toHaveLength(2)
  })

  it('renders ResourceSlot inputs as Edge material selectors', () => {
    renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    fireEvent.change(screen.getByLabelText('工作流'), { target: { value: demoWorkflows[1].uuid } })

    expect(screen.getByLabelText(/resource必填/)).toHaveValue('')
    expect(screen.getAllByRole('option', { name: /500 mL 烧杯/ })).toHaveLength(3)
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
