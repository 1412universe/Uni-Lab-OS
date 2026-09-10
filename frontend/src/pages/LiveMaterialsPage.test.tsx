import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { LiveMaterialsPage } from './LiveMaterialsPage'
import { usageLock, usageSnapshot, usageTask } from '../test/resourceUsageFixtures'

afterEach(() => { vi.unstubAllGlobals(); Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal') })

it('integrates live usage into the existing material tree and follows a device site to the task', async () => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute('open', '') } })
  const snapshot = usageSnapshot([usageLock('material/warehouse/site/site-b/exclusive')])
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => ({ code: 0, data:
    url.endsWith('/materials/graph') ? { nodes: snapshot.nodes }
      : url.endsWith('/resource-usage') ? { occupancies: snapshot.occupancies, jobs: [], material_bindings: [] }
      : url.includes('/materials/') ? snapshot.nodes.find((node) => url.endsWith(node.material.uuid))?.material || {}
      : { items: [], total: 0 },
  }) })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onOpenTask = vi.fn()
  const { unmount } = render(<QueryClientProvider client={client}><LiveMaterialsPage materials={[]} tasks={[usageTask()]} connected onNotify={vi.fn()} onRefresh={vi.fn()} onOpenTask={onOpenTask} /></QueryClientProvider>)
  await screen.findByLabelText('设备与库位画布')
  const tree = screen.getByLabelText('物料目录')
  expect(within(tree).getByRole('button', { name: '转运机械臂' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '库位 转运仓 / A02' }))
  fireEvent.click(screen.getByRole('button', { name: '查看详情 / 上下料' }))
  const site = await screen.findByLabelText('库位任务关联')
  expect(site).toHaveTextContent('A02 · 当前存放：空库位')
  expect(site).toHaveTextContent('task-1')
  fireEvent.click(within(site).getByRole('button', { name: '查看任务与执行锁' }))
  expect(onOpenTask).toHaveBeenCalledWith('task-1')
  fireEvent.click(screen.getByRole('button', { name: '关闭物料详情' }))
  fireEvent.change(screen.getByLabelText('筛选资源状态'), { target: { value: 'attention' } })
  expect(screen.getByText('暂无匹配资源')).toBeInTheDocument()
  expect(screen.queryByLabelText('当前资源使用情况')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '清除筛选' }))
  expect(screen.getByLabelText('设备组 转运机械臂')).toBeInTheDocument()
  fireEvent.click(within(tree).getByRole('button', { name: '展开 转运仓' }))
  fireEvent.mouseOver(within(tree).getByRole('button', { name: '测试烧杯' }))
  expect(screen.getByRole('tooltip')).toHaveTextContent('物料 · 测试烧杯')
  expect(screen.getByRole('tooltip')).toHaveTextContent('未绑定 Task')
  unmount(); client.clear()
})
