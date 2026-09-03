import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationsPage } from './OperationsPage'

vi.mock('../lib/edgeClient', async () => {
  const actual = await vi.importActual<typeof import('../lib/edgeClient')>('../lib/edgeClient')
  return {
    ...actual,
    loadExperimentOperations: vi.fn(async () => []),
    loadOperationCategories: vi.fn(async () => []),
    loadActionTemplates: vi.fn(async () => []),
    loadActionParameters: vi.fn(async () => []),
    loadActionOutputs: vi.fn(async () => []),
    loadWorkflowGraph: vi.fn(),
    loadControlTemplates: vi.fn(async () => [
      {
        uuid: 'condition-template',
        name: 'condition',
        displayName: '条件',
        description: '按条件选择分支。',
        type: 'condition',
        nodeType: 'condition',
        resourceTemplate: { uuid: 'host-template', name: 'host_node', displayName: '工作流控制' },
        parameterSchema: { type: 'object', required: ['branches'] },
      },
      {
        uuid: 'repeat-template',
        name: 'repeat_until',
        displayName: '重复直到',
        description: '满足条件后退出循环。',
        type: 'repeat_until',
        nodeType: 'repeat_until',
        resourceTemplate: { uuid: 'host-template', name: 'host_node', displayName: '工作流控制' },
        parameterSchema: { type: 'object', required: ['max_iterations'] },
      },
    ]),
  }
})

afterEach(() => vi.unstubAllGlobals())

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <OperationsPage materials={[]} connected onNotify={vi.fn()} />
    </QueryClientProvider>,
  )
}

describe('OperationsPage upstream output binding', () => {
  /** 验证用户只能从目标节点之前的兼容输出中选择，并记录真实来源句柄。 */
  it('offers outputs from an earlier action and lets the user select one as the input source', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadActionTemplates).mockResolvedValueOnce([
      {
        uuid: 'action-template-1',
        name: 'measure',
        displayName: '测量一',
        type: 'device_action',
        nodeType: 'compute',
        resourceTemplate: { uuid: 'device-template', name: 'device', displayName: '设备' },
      },
      {
        uuid: 'action-template-2',
        name: 'measure_again',
        displayName: '测量二',
        type: 'device_action',
        nodeType: 'compute',
        resourceTemplate: { uuid: 'device-template', name: 'device', displayName: '设备' },
      },
    ])
    vi.mocked(edgeClient.loadActionParameters).mockResolvedValue([{
      handleUuid: 'input-handle',
      key: 'sample_id',
      displayName: '样品',
      required: true,
      schema: { type: 'string' },
    }])
    vi.mocked(edgeClient.loadActionOutputs).mockResolvedValue([{
      handleUuid: 'output-handle',
      key: 'sample_id',
      displayName: '样品',
      schema: { type: 'string' },
    }])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))
    const firstAdd = await screen.findByRole('button', { name: /测量一\s*measure/ })
    await act(async () => { fireEvent.click(firstAdd) })
    await screen.findByLabelText('设备实例 测量一')
    const secondAdd = await screen.findByRole('button', { name: /测量二\s*measure_again/ })
    await act(async () => { fireEvent.click(secondAdd) })
    let providers = await screen.findAllByLabelText('参数来源 测量二 sample_id')
    await waitFor(() => expect(screen.getAllByLabelText('参数来源 测量二 sample_id')).toHaveLength(1))
    providers = screen.getAllByLabelText('参数来源 测量二 sample_id')

    const provider = providers[0]
    expect(within(provider).getByRole('option', { name: '上游节点输出' })).toBeEnabled()
    fireEvent.change(provider, { target: { value: 'upstream' } })

    const upstreamSelect = screen.getByLabelText<HTMLSelectElement>('上游输出 测量二 sample_id')
    const upstreamOption = screen.getByRole<HTMLOptionElement>('option', { name: '测量一 · 样品' })
    fireEvent.change(upstreamSelect, { target: { value: upstreamOption.value } })

    expect(upstreamSelect.value).toBe(upstreamOption.value)
    expect(upstreamSelect.value).toMatch(/\|output-handle$/)

    fireEvent.click(screen.getByRole('button', { name: '关闭节点参数' }))
    fireEvent.click(screen.getByRole('button', { name: '上移 测量二' }))
    fireEvent.click(screen.getByRole('button', { name: '配置参数 测量二' }))
    expect(screen.getByLabelText<HTMLSelectElement>('参数来源 测量二 sample_id').value).toBe('literal')
  })

  /** 验证编辑器按作者顺序还原乱序返回的节点，避免把合法上游误判成下游。 */
  it('restores upstream choices by sequence index when an existing graph returns nodes out of order', async () => {
    const edgeClient = await import('../lib/edgeClient')
    vi.mocked(edgeClient.loadExperimentOperations).mockResolvedValueOnce([{
      uuid: 'workflow-1', name: '乱序工作流', revision: 1, status: 'source', description: '', nodeCount: 2,
      tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation',
    }])
    vi.mocked(edgeClient.loadWorkflowGraph).mockResolvedValueOnce({
      workflow: {
        uuid: 'workflow-1', name: '乱序工作流', revision: 1, status: 'source', description: '', nodeCount: 2,
        tags: [], inputContract: [], outputContract: [], workflowType: 'experiment_operation',
      },
      nodes: [
        { uuid: 'target-node', name: '目标动作', type: 'device_action', kind: 'action', workflow_node_template_uuid: 'target-template', material_uuid: 'target-device', meta_data: { unilab: { sequence_index: 1 } }, disabled: false },
        { uuid: 'source-node', name: '来源动作', type: 'device_action', kind: 'action', workflow_node_template_uuid: 'source-template', material_uuid: 'source-device', meta_data: { unilab: { sequence_index: 0 } }, disabled: false },
      ],
      edges: [{ uuid: 'data-edge', sourceNodeUuid: 'source-node', targetNodeUuid: 'target-node', sourceHandleUuid: 'source-output', targetHandleUuid: 'target-input', metaData: { unilab: { edge_kind: 'data' } } }],
    })
    vi.mocked(edgeClient.loadActionParameters).mockImplementation(async (templateUuid) => templateUuid === 'target-template' ? [{
      handleUuid: 'target-input', key: 'sample_id', displayName: '样品', required: true, schema: { type: ['number', 'null'] },
    }] : [])
    vi.mocked(edgeClient.loadActionOutputs).mockImplementation(async (templateUuid) => templateUuid === 'source-template' ? [{
      handleUuid: 'source-output', key: 'sample_id', displayName: '样品', schema: { type: ['integer', 'null'] },
    }] : [])

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '编辑 乱序工作流 workflow-1' }))
    fireEvent.click(await screen.findByRole('button', { name: '配置参数 目标动作' }))

    const provider = await screen.findByLabelText('参数来源 目标动作 sample_id')
    fireEvent.change(provider, { target: { value: 'upstream' } })
    expect(screen.getByRole('option', { name: '来源动作 · 样品' })).toBeInTheDocument()
  })
})

describe('OperationsPage control-node authoring', () => {
  it('lets a user add and fill condition/repeat nodes without editing one large JSON blob', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: '创建实验操作' }))

    const conditionAction = await screen.findByRole('button', { name: '加入画布 条件' })
    fireEvent.click(conditionAction)
    expect(within(screen.getByLabelText('实验操作节点画布')).getByText('条件')).toBeInTheDocument()
    expect(screen.getByText('条件分支')).toBeInTheDocument()
    expect(screen.queryByLabelText('条件 参数 JSON')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '加入画布 重复直到' }))
    expect(within(screen.getByLabelText('实验操作节点画布')).getByText('重复直到')).toBeInTheDocument()
    expect(screen.getByText('循环体执行哪些节点')).toBeInTheDocument()
    expect(screen.getByLabelText('重复直到 第一轮初值')).toBeInTheDocument()
    expect(screen.getByLabelText('重复直到 下一轮值')).toBeInTheDocument()
    expect(screen.queryByLabelText('重复直到 循环变量绑定')).not.toBeInTheDocument()
  })
})
