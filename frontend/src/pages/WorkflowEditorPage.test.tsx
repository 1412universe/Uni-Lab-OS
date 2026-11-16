import { MemoryRouter } from 'react-router-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WorkflowEditorPage } from './WorkflowEditorPage'
import type { WorkflowDefinition } from '../types'

/** 布局完成后画布会补一次 fitView；这里要能断言它真的被调了。 */
const { fitViewMock } = vi.hoisted(() => ({ fitViewMock: vi.fn(async () => {}) }))

/**
 * 画布本身由 React Flow 负责，jsdom 没有布局引擎也不会真正渲染节点。
 * 这里把 `@xyflow/react` 替换成最小替身，专注验证编辑器的数据流：
 * 模板装载、节点追加、整图保存的提交载荷、预检结果渲染。
 */
vi.mock('@xyflow/react', async () => {
  const React = await import('react')
  type AnyProps = Record<string, any>
  const passthrough = ({ children }: { children?: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children)
  const useStatePair = (initial: unknown[]) => {
    const [state, setState] = React.useState(initial)
    return [state, setState, () => {}]
  }
  return {
    ReactFlowProvider: passthrough,
    ReactFlow: ({ nodes, edges, children }: AnyProps) => React.createElement(
      'div',
      { 'data-testid': 'rf-canvas' },
      React.createElement('span', { 'data-testid': 'rf-count' }, `${(nodes || []).length}/${(edges || []).length}`),
      children,
    ),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Handle: () => null,
    Position: { Left: 'left', Right: 'right' },
    BackgroundVariant: { Dots: 'dots' },
    useReactFlow: () => ({
      screenToFlowPosition: (point: unknown) => point,
      fitView: fitViewMock,
      zoomIn: async () => {},
      zoomOut: async () => {},
    }),
    useNodesState: useStatePair,
    useEdgesState: useStatePair,
  }
})

afterEach(() => {
  vi.unstubAllGlobals()
  // 抽屉的展开/钉住状态存在 localStorage；用例之间必须清掉，否则前一条用例
  // 留下的「收起」会让后续用例找不到节点库与属性面板。
  localStorage.clear()
})

const workflow: WorkflowDefinition = {
  uuid: 'wf-1',
  name: '金四面体 · 打样',
  revision: 3,
  status: 'source',
  description: '',
  nodeCount: 1,
  tags: [],
  inputContract: [],
  outputContract: [],
  workflowType: 'normal',
}

const TEMPLATE_UUID = 'tmpl-arm-1'
const READY_SOURCE = 'handle-ready-source'
const READY_TARGET = 'handle-ready-target'

const templateSummary = {
  uuid: TEMPLATE_UUID,
  name: 'reset_arm',
  display_name: '机械手复位',
  type: 'UniLabJsonCommand',
  node_type: 'ILab',
  resource_template: { uuid: 'res-arm', name: 'community.gn_lab.gn_robotic_arm', display_name: '机械手' },
}

const handleTemplates = [
  {
    uuid: READY_SOURCE,
    workflow_node_template_uuid: TEMPLATE_UUID,
    handle_key: 'ready',
    io_type: 'source',
    data_key: 'ready',
    type: 'object',
  },
  {
    uuid: READY_TARGET,
    workflow_node_template_uuid: TEMPLATE_UUID,
    handle_key: 'ready',
    io_type: 'target',
    data_key: 'ready',
    type: 'object',
  },
  {
    uuid: 'handle-timeout',
    workflow_node_template_uuid: TEMPLATE_UUID,
    handle_key: 'timeout',
    io_type: 'target',
    data_key: 'timeout',
    type: 'number',
    required: true,
    meta_data: { unilab: { contract_order: 0, editor_control: 'variable_selector' } },
  },
]

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

function renderEditor(search: string, connected = true, notify = vi.fn()) {
  return render(
    <MemoryRouter initialEntries={[search]}>
      <WorkflowEditorPage workflows={[workflow]} connected={connected} onNotify={notify} />
    </MemoryRouter>,
  )
}

describe('WorkflowEditorPage', () => {
  it('loads node templates by device and renders the selected workflow graph', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              description: '开场复位',
              pose: { position: { x: 40, y: 60 } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1')

    expect(await screen.findByText('机械手')).toBeInTheDocument()
    expect(screen.getByText('机械手复位')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))
    expect(screen.getByText(/已同步 r3/)).toBeInTheDocument()
  })

  it('submits the whole graph with a non-empty description when saving a newly added node', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates') && (!init || init.method === undefined)) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph') && (!init || init.method === undefined)) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      if (url.includes('/workflow-node-templates/')) {
        return jsonResponse({
          code: 0,
          data: { template: { uuid: TEMPLATE_UUID, goal_default: {} }, handles: handleTemplates },
        })
      }
      if (url.endsWith('/workflows/wf-1/graph') && init?.method === 'PUT') {
        const payload = JSON.parse(String(init.body))
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 4 },
            nodes: payload.nodes,
            edges: payload.edges,
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderEditor('/?page=workflow-editor&workflow=wf-1')

    const libraryItem = await screen.findByRole('button', { name: /机械手复位/ })
    fireEvent.click(libraryItem)

    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))

    const saveButton = screen.getByRole('button', { name: /保存/ })
    await waitFor(() => expect(saveButton).toBeEnabled())
    fireEvent.click(saveButton)

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([input, init]) => String(input).endsWith('/workflows/wf-1/graph') && init?.method === 'PUT',
      )
      expect(putCall).toBeTruthy()
    })

    const putCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input).endsWith('/workflows/wf-1/graph') && init?.method === 'PUT',
    )!
    const payload = JSON.parse(String((putCall[1] as RequestInit).body))
    expect(payload.revision).toBe(3)
    expect(payload.nodes).toHaveLength(1)
    expect(payload.nodes[0].name).toBe('机械手复位')
    expect(payload.nodes[0].description).toBe('机械手复位')
    expect(payload.nodes[0].workflow_node_template_uuid).toBe(TEMPLATE_UUID)
    expect(payload.nodes[0].pose.position).toEqual(expect.objectContaining({ x: expect.any(Number) }))
  })

  it('toggles parameter port mode without touching the graph payload', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              description: '开场复位',
              pose: { position: { x: 40, y: 60 } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    const { container } = renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))

    const toggle = screen.getByRole('button', { name: '参数连线' })
    expect(container.querySelector('.wf-canvas.is-data-mode')).toBeNull()
    fireEvent.click(toggle)
    await waitFor(() => expect(container.querySelector('.wf-canvas.is-data-mode')).toBeTruthy())
    // 展开口只改展示，不产生未保存改动
    expect(screen.getByText(/已同步 r3/)).toBeInTheDocument()
  })

  it('opens the batch transfer drawer with export and import sections', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              description: '开场复位',
              pose: { position: { x: 40, y: 60 } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))

    fireEvent.click(screen.getByRole('button', { name: '导入导出' }))
    const transferDialog = await screen.findByRole('dialog', { name: '批量导入导出' })
    expect(transferDialog).toBeInTheDocument()
    // 按钮文字包在 Button 的 <span> 里，用文本查询更稳。
    expect(transferDialog.textContent).toContain('导出当前草稿')
    expect(screen.getByText('导出当前草稿').closest('button')).toBeEnabled()
    expect(screen.getByText(/选择 JSON 文件/)).toBeInTheDocument()
  })

  it('opens the 3D simulation space and lists execution steps', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              description: '开场复位',
              pose: { position: { x: 40, y: 60 } },
              meta_data: { unilab: { executor_binding: { device_id: 'gn_robotic_arm' } } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))

    fireEvent.click(screen.getByRole('button', { name: '3D 仿真' }))
    // 3D 场景由 React.lazy 加载 three.js；jsdom 没有 WebGL，只断言外壳与步骤清单。
    const dialog = await screen.findByRole('dialog', { name: '工作流 3D 仿真空间' })
    expect(dialog).toBeInTheDocument()
    expect(dialog.querySelectorAll('.sim-steps button')).toHaveLength(1)
    expect(dialog.textContent).toContain('1 步')
    expect(screen.getByRole('button', { name: '播放' })).toBeInTheDocument()
  })

  it('persists coordinates when the user clicks auto layout', async () => {
    const layoutPuts: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/canvas-layout') && init?.method === 'PUT') {
        layoutPuts.push(String(init.body || ''))
        return jsonResponse({ code: 0, data: { node_count: 2 } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        const nodes = ['n1', 'n2'].map((uuid, index) => ({
          uuid,
          name: `步骤 ${uuid}`,
          type: 'UniLabJsonCommand',
          workflow_node_template_uuid: TEMPLATE_UUID,
          description: uuid,
          // 已有坐标，因此加载时不会自动布局
          pose: { position: { x: 48 + index * 320, y: 48 } },
        }))
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes,
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('2/0'))
    expect(layoutPuts).toHaveLength(0)

    // 抽屉里可能还有同名按钮；这里只点工具栏那一个。
    const toolbarButtons = screen.getAllByRole('button', { name: '自动布局' })
    fireEvent.click(toolbarButtons[0])

    // 手动布局也必须落盘：否则重开这份工作流又会重排一次。
    await waitFor(() => expect(layoutPuts).toHaveLength(1))
    const persisted = JSON.parse(layoutPuts[0]).positions
    expect(Object.keys(persisted).sort()).toEqual(['n1', 'n2'])
  })

  it('lays out graphs that have no coordinates once, and persists the result', async () => {
    const notify = vi.fn()
    const layoutPuts: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/canvas-layout') && init?.method === 'PUT') {
        layoutPuts.push(String(init.body || ''))
        return jsonResponse({ code: 0, data: { node_count: 3 } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        // CWM 导出的图没有 pose，三个节点串行
        const nodes = ['n1', 'n2', 'n3'].map((uuid) => ({
          uuid,
          name: `步骤 ${uuid}`,
          type: 'UniLabJsonCommand',
          workflow_node_template_uuid: TEMPLATE_UUID,
          description: uuid,
          pose: {},
        }))
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes,
            edges: [
              { uuid: 'e1', source_node_uuid: 'n1', target_node_uuid: 'n2', source_handle_uuid: READY_SOURCE, target_handle_uuid: READY_TARGET },
              { uuid: 'e2', source_node_uuid: 'n2', target_node_uuid: 'n3', source_handle_uuid: READY_SOURCE, target_handle_uuid: READY_TARGET },
            ],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1', true, notify)
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('3/2'))

    // 提示只弹一次，且必须说明「已保存」——落点不写回就会每次切换工作流重排一遍。
    expect(notify).toHaveBeenCalledTimes(1)
    expect(String(notify.mock.calls[0][0])).toContain('已按依赖分列排布并保存')
    // 落点立刻落盘：不再需要用户手动保存坐标。
    await waitFor(() => expect(layoutPuts).toHaveLength(1))
    const persisted = JSON.parse(layoutPuts[0]).positions
    expect(Object.keys(persisted).sort()).toEqual(['n1', 'n2', 'n3'])
    expect(persisted.n1.x).toEqual(expect.any(Number))
    // 坐标已持久化 ⇒ 内容与后端一致，工具栏应回到「已同步」，不该再标记未保存。
    await waitFor(() => expect(screen.getByText(/已同步 r3/)).toBeInTheDocument())
    // 布局后视口必须跟上，否则看起来「点了没反应」
    await waitFor(() => expect(fitViewMock).toHaveBeenCalled())
  })

  it('skips auto layout when the persisted canvas layout already answers the graph', async () => {
    const notify = vi.fn()
    const layoutPuts: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/canvas-layout') && init?.method === 'PUT') {
        layoutPuts.push(String(init.body || ''))
        return jsonResponse({ code: 0, data: { node_count: 0 } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        // 后端已把持久落点叠加回来：节点带真实坐标，不该再重排。
        const nodes = ['n1', 'n2'].map((uuid, index) => ({
          uuid,
          name: `步骤 ${uuid}`,
          type: 'UniLabJsonCommand',
          workflow_node_template_uuid: TEMPLATE_UUID,
          description: uuid,
          pose: { position: { x: 48 + index * 320, y: 48 } },
        }))
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes,
            edges: [
              { uuid: 'e1', source_node_uuid: 'n1', target_node_uuid: 'n2', source_handle_uuid: READY_SOURCE, target_handle_uuid: READY_TARGET },
            ],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    }))

    renderEditor('/?page=workflow-editor&workflow=wf-1', true, notify)
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('2/1'))

    // 有坐标就不该弹「没有可用坐标」，也不该写回落点。
    expect(notify).not.toHaveBeenCalled()
    expect(layoutPuts).toHaveLength(0)
    expect(screen.getByText(/已同步 r3/)).toBeInTheDocument()
  })

  it('blocks saving when Edge is disconnected', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({
      code: 0,
      data: { items: [], has_more: false },
    })))

    renderEditor('/?page=workflow-editor&workflow=wf-1', false)

    expect(await screen.findByText('Edge 未连接')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled()
  })

  it('shows the backend action mode instead of guessing it', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/readiness')) {
        return jsonResponse({ status: 'ready', phase: 'ready', workflowRuntime: 'ready', actionMode: 'real' })
      }
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              pose: { position: { x: 40, y: 60 } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      return jsonResponse({ code: 0, data: {} })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))

    // 档位指示器与横幅已按需求移除；真机档的可辨识性改由「真机运行」按钮承担。
    await waitFor(() => expect(screen.getByRole('button', { name: /真机运行/ })).toBeEnabled())
    expect(document.querySelector('.wf-action-mode')).toBeNull()
    expect(document.querySelector('.wf-real-banner')).toBeNull()
    const runButton = screen.getByRole('button', { name: /真机运行/ })
    expect(runButton.className).toContain('is-danger')
  })

  it('submits a real run on a single click, without a confirmation dialog', async () => {
    const taskPosts: string[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/readiness')) {
        return jsonResponse({ status: 'ready', phase: 'ready', workflowRuntime: 'ready', actionMode: 'real' })
      }
      if (url.includes('/workflow-node-templates')) {
        return jsonResponse({ code: 0, data: { items: [templateSummary], has_more: false } })
      }
      if (url.endsWith('/workflows/wf-1/graph')) {
        return jsonResponse({
          code: 0,
          data: {
            workflow: { uuid: 'wf-1', name: workflow.name, revision: 3 },
            nodes: [{
              uuid: 'node-1',
              name: '机械手复位',
              type: 'UniLabJsonCommand',
              workflow_node_template_uuid: TEMPLATE_UUID,
              pose: { position: { x: 40, y: 60 } },
            }],
            edges: [],
            node_templates: [],
            handle_templates: handleTemplates,
          },
        })
      }
      if (url.endsWith('/workflow-tasks') && init?.method === 'POST') {
        taskPosts.push(String(init.body || ''))
        return jsonResponse({
          code: 0,
          data: { uuid: 'task-1', workflow_uuid: 'wf-1', workflow_task_uuid: 'task-1' },
        })
      }
      if (url.includes('/workflow-tasks/task-1')) {
        return jsonResponse({ code: 0, data: { uuid: 'task-1', workflow_uuid: 'wf-1', status: 'running', nodes: [], jobs: [] } })
      }
      return jsonResponse({ code: 0, data: {} })
    })
    vi.stubGlobal('fetch', fetchMock)

    renderEditor('/?page=workflow-editor&workflow=wf-1')
    await waitFor(() => expect(screen.getByTestId('rf-count')).toHaveTextContent('1/0'))
    await waitFor(() => expect(screen.getByRole('button', { name: /真机运行/ })).toBeEnabled())

    fireEvent.click(screen.getByRole('button', { name: /真机运行/ }))
    // 点一次就提交：不出现任何确认弹层
    await waitFor(() => expect(taskPosts).toHaveLength(1))
    expect(screen.queryByText('确认真机运行')).not.toBeInTheDocument()
    expect(JSON.parse(taskPosts[0])).toMatchObject({ workflow_uuid: 'wf-1', run_mode: 'normal' })
  })
})
