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
  it('links Electron-style tree selection to the 2.5D scene', () => {
    const { container } = renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )

    const materialTree = screen.getByRole('complementary', { name: '物料目录' })
    fireEvent.click(within(materialTree).getByRole('button', { name: '250 mL 样品瓶' }))

    expect(within(materialTree).getByRole('button', { name: '250 mL 样品瓶' }).closest('[role="treeitem"]')).toHaveClass('selected')
    expect(container.querySelector('.lab-oblique-object.selected')).toHaveAttribute('aria-label', '250 mL 样品瓶')
  })

  it('keeps inventory, site occupancy and the 2.5D scene in one workspace', () => {
    renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )
    expect(screen.getByRole('complementary', { name: '物料目录' })).toBeInTheDocument()
    expect(screen.getByLabelText('库位状态说明')).toBeInTheDocument()
    expect(screen.getByLabelText('物料 2.5D 库位场景')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '清单' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '库位' })).not.toBeInTheDocument()
  })

  it('shows the authoritative owner sites when an occupied material is selected', () => {
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
      currentLocation: { kind: 'site' as const, label: 'S09 / L1', siteUuid: 'site-1', ownerMaterialUuid: owner.uuid },
    }
    renderWithQuery(
      <MaterialsPage materials={[occupant, owner]} total={2} connected={false} onNotify={vi.fn()} />,
    )

    expect(screen.getByText('测试烧杯 · 详细库位')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /L1/ })).toHaveTextContent('测试烧杯')
    expect(screen.getByText('已占用：测试烧杯')).toBeInTheDocument()
    expect(screen.queryByLabelText('L1，未占用')).not.toBeInTheDocument()
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
