import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OperationsPage } from './OperationsPage'

vi.mock('../lib/edgeClient', async () => {
  const actual = await vi.importActual<typeof import('../lib/edgeClient')>('../lib/edgeClient')
  return {
    ...actual,
    loadExperimentOperations: vi.fn(async () => []),
    loadOperationCategories: vi.fn(async () => []),
    loadActionTemplates: vi.fn(async () => []),
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
