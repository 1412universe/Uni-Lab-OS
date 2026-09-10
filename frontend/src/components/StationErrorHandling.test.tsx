import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StationErrorHandling } from './StationErrorHandling'
import { useStationErrorHandling } from '../hooks/useStationErrorHandling'
import { HANDLING_INTENT_STORAGE, STATION_QUERY_KEY } from '../lib/stationErrorClient'
import { handlingAction, handlingDevice, handlingSnapshot, handlingTask, manualSnapshot } from '../test/stationHandlingFixtures'

vi.mock('../lib/edgeClient', async () => ({
  ...await vi.importActual<typeof import('../lib/edgeClient')>('../lib/edgeClient'),
  loadActionTemplates: vi.fn(async () => [{ uuid: 'action-1', resourceTemplate: { uuid: 'probe-template' } }]),
}))

let state = handlingSnapshot()
let handler: ((path: string, body: Record<string, unknown>) => Response | Promise<Response>) | undefined
let posts: Array<{ path: string; body: Record<string, unknown>; key: string }> = []
const envelope = (value: unknown) => new Response(JSON.stringify({ code: 0, data: value }), { status: 200 })
const applied = () => envelope({ command_uuid: 'command-1', status: 'APPLIED', result: {}, station: state })
const notify = vi.fn()

beforeEach(() => {
  state = handlingSnapshot(); posts = []; handler = undefined; sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    const path = input.replace('/api/v1/stations/local', '')
    if (options?.method === 'POST') {
      const body = JSON.parse(String(options.body || '{}'))
      posts.push({ path, body, key: new Headers(options.headers).get('Idempotency-Key') || '' })
      return handler ? handler(path, body) : applied()
    }
    if (path === '/error-handling') return envelope(structuredClone(state))
    if (path.endsWith('/actions')) return envelope([handlingAction])
    if (path.endsWith('/inventory')) return envelope({ instances: [{ edge_uuid: 'bottle', name: '样品瓶', parent_uuid: '', version: 4 }, { edge_uuid: 'rack', name: '样品架', parent_uuid: '', version: 8 }], sites: [{ uuid: 'site-1', material_uuid: 'rack', name: 'A1', update_time: 'site-v2', occupied_material_uuid: null }] })
    if (path === '/resource-dispositions') return envelope([{ uuid: 'lock-1', lock_key: 'device:probe', state: 'active', occupancy_kind: 'execution_lock', version: 'lock-v3', workflow_task_uuid: 'task-1' }])
    if (path.startsWith('/control-commands/')) return envelope({ command_uuid: path.split('/').pop(), status: 'PENDING', result: {}, station: state })
    throw new Error(`未匹配的测试请求 ${input}`)
  }))
})
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals() })

function Harness({ connected = true }: { connected?: boolean }) {
  const controller = useStationErrorHandling(connected, notify)
  return <StationErrorHandling controller={controller} materials={[handlingDevice]} tasks={[handlingTask]} />
}
function setup(connected = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><Harness connected={connected} /></QueryClientProvider>)
  return { client, ...view }
}
async function chooseManualAction() {
  const device = await screen.findByLabelText('选择设备')
  await waitFor(() => expect(screen.getByRole('option', { name: /软件探针/ })).toBeInTheDocument())
  fireEvent.change(device, { target: { value: 'device-2' } })
  await screen.findByRole('option', { name: /探针动作/ })
  fireEvent.change(screen.getByLabelText('选择动作'), { target: { value: 'action-1' } })
  await screen.findByLabelText('持续秒数')
}

describe('工站错误处置交互', () => {
  it('自动弹出三选一，关闭后仍可从暂停横幅重新打开', async () => {
    setup()
    expect(await screen.findByRole('dialog')).toHaveTextContent('注入节点错误')
    for (const name of ['取消当前任务', '重试当前节点', '进入手动处理']) expect(screen.getByRole('button', { name })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /跳过/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '稍后处理' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('工站调度已暂停')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '处理节点错误' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(posts).toHaveLength(0)
  })

  it('重试携带用户确认的快照与当前版本，防止双击重复提交', async () => {
    handler = async () => { await new Promise((resolve) => setTimeout(resolve, 20)); state.errors = []; state.mode = 'RUNNING'; return applied() }
    setup()
    fireEvent.click(await screen.findByLabelText('我已核对现场，确认可以恢复工站工作流调度'))
    const retry = screen.getByRole('button', { name: '重试当前节点' })
    fireEvent.click(retry); fireEvent.click(retry)
    await screen.findByText('错误处置已完成')
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toMatchObject({ action: 'retry_current_node', expected_station_version: 7, expected_decision_version: 2, observed_error_epoch: 3, resume_requested: true, operator_confirmed: true, confirm_snapshot_id: 'local:7:3' })
    expect(posts[0].key).toBeTruthy()
  })

  it('没有恢复确认时取消任务保持暂停，之后可以单独恢复', async () => {
    handler = (_path, body) => { state.errors = []; if (body.confirm_snapshot_id) state.mode = 'RUNNING'; state.station_version += 1; state.snapshot_id = `local:${state.station_version}:3`; return applied() }
    setup()
    fireEvent.click(await screen.findByRole('button', { name: '取消当前任务' }))
    const resume = await screen.findByRole('button', { name: '恢复工站调度' })
    expect(posts[0].body).toMatchObject({ action: 'cancel_task', resume_requested: false, operator_confirmed: false })
    expect(resume).toBeDisabled()
    fireEvent.click(screen.getByLabelText('我已核对现场，确认可以恢复工站工作流调度'))
    fireEvent.click(resume)
    await screen.findByText('错误处置已完成')
    expect(posts[1].path).toBe('/resume')
    expect(posts[1].body).toMatchObject({ expected_station_version: 8, confirm_snapshot_id: 'local:8:3' })
  })

  it('人工确认恢复后，其他已知错误仍可处理，横幅按真实调度状态展示', async () => {
    state.errors.push({ ...state.errors[0], decision_id: 'decision-2', source_task_uuid: 'task-2' })
    handler = () => { state.errors = state.errors.slice(1); state.mode = 'RUNNING'; state.station_version += 1; state.snapshot_id = 'local:8:3'; return applied() }
    setup()
    fireEvent.click(await screen.findByLabelText('我已核对现场，确认可以恢复工站工作流调度'))
    fireEvent.click(screen.getByRole('button', { name: '重试当前节点' }))
    await screen.findByText('还有节点错误待处理')
    expect(screen.queryByText('工站调度已暂停')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试当前节点' })).toBeEnabled()
  })

  it('新错误到达后清除旧恢复确认，409 不会自动重发决定', async () => {
    const { client } = setup()
    fireEvent.click(await screen.findByLabelText('我已核对现场，确认可以恢复工站工作流调度'))
    state.station_version = 8; state.error_epoch = 4; state.snapshot_id = 'local:8:4'
    await act(async () => { client.setQueryData(STATION_QUERY_KEY, structuredClone(state)) })
    await waitFor(() => expect(screen.getByLabelText('我已核对现场，确认可以恢复工站工作流调度')).not.toBeChecked())
    handler = () => { state.station_version = 9; state.snapshot_id = 'local:9:4'; return new Response(JSON.stringify({ detail: 'station_version_conflict' }), { status: 409 }) }
    fireEvent.click(screen.getByRole('button', { name: '重试当前节点' }))
    await screen.findByText(/工站状态已变化，请核对刷新后的错误与现场状态/)
    expect(posts).toHaveLength(1)
    expect(sessionStorage.getItem(HANDLING_INTENT_STORAGE)).toBeNull()
  })

  it('进入手动处理、执行其他设备动作、处理完成后返回重试或取消', async () => {
    handler = (path) => {
      if (path.endsWith('/decision')) state = manualSnapshot()
      if (path.endsWith('/manual-actions')) state.manual_actions = [{ uuid: 'manual-job', workflow_task_uuid: 'manual-task', edge_command_uuid: 'manual-command', control_data: { dispatch_payload: { action: 'probe_once', device_id: 'software-probe' } }, status: 'running', start_state: 'START_COMMITTED', meta_data: { session_id: 'session-1', source_task_uuid: 'task-1', source_job_uuid: 'job-1', source_node_id: 'node-1' } }]
      if (path.endsWith('/complete')) { state.active_session!.stage = 'DECISION_REQUIRED'; state.active_session!.version = 2; state.errors[0].stage = 'DECISION_REQUIRED'; state.errors[0].decision_version = 4 }
      return applied()
    }
    setup()
    fireEvent.click(await screen.findByRole('button', { name: '进入手动处理' }))
    await chooseManualAction()
    fireEvent.change(screen.getByLabelText('持续秒数'), { target: { value: '1.5' } })
    fireEvent.change(screen.getByLabelText('启用'), { target: { value: 'false' } })
    fireEvent.click(screen.getByRole('button', { name: '执行单点动作' }))
    await screen.findByText('probe_once')
    expect(posts[1]).toMatchObject({ path: '/sessions/session-1/manual-actions', body: { device_id: 'device-2', action_id: 'action-1', action_version: 'sha256:action-v1', parameters: { duration: 1.5, enabled: false }, expected_session_version: 1 } })
    expect(posts[1].body).not.toHaveProperty('claim_uuid')
    expect(screen.getByRole('link', { name: '查看关联 Task Trace' })).toHaveAttribute('href', handlingTask.trace?.url)
    fireEvent.click(screen.getByRole('button', { name: '处理完成，返回重试／取消选择' }))
    await screen.findByRole('button', { name: '返回手动处理' })
    expect(screen.getByRole('button', { name: '重试当前节点' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '取消当前任务' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '执行单点动作' })).not.toBeInTheDocument()
    expect(state.mode).toBe('PAUSED')
    handler = () => applied()
    fireEvent.click(screen.getByRole('button', { name: '重试当前节点' }))
    await waitFor(() => expect(posts).toHaveLength(4))
    expect(posts[3].body).toMatchObject({ session_id: 'session-1', expected_session_version: 2, expected_decision_version: 4 })
  })

  it('网络结果未知时保留原请求，刷新页面后按相同幂等键确认', async () => {
    state = manualSnapshot()
    handler = () => { throw new TypeError('Failed to fetch') }
    const first = setup()
    await chooseManualAction()
    fireEvent.click(screen.getByRole('button', { name: '执行单点动作' }))
    await screen.findByRole('button', { name: '确认原请求结果' })
    expect(screen.getByRole('button', { name: '执行单点动作' })).toBeDisabled()
    const original = structuredClone(posts[0])
    first.unmount()
    handler = () => applied()
    setup()
    fireEvent.click(await screen.findByRole('button', { name: '确认原请求结果' }))
    await waitFor(() => expect(posts).toHaveLength(2))
    expect(posts[1]).toEqual(original)
    await waitFor(() => expect(sessionStorage.getItem(HANDLING_INTENT_STORAGE)).toBeNull())
  })

  it('已受理待应用命令使用原命令继续应用', async () => {
    state.errors[0].stage = 'APPLYING'
    state.control_commands = [{ command_uuid: 'durable-command', operation: 'decision:decision-1', status: 'PENDING', needs_apply: true }]
    setup()
    fireEvent.click(await screen.findByRole('button', { name: '继续应用' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].path).toBe('/control-commands/durable-command/apply')
    expect(screen.queryByRole('button', { name: '重试当前节点' })).not.toBeInTheDocument()
  })

  it('断线后保留错误快照和未确认请求，但禁用所有写入', async () => {
    sessionStorage.setItem(HANDLING_INTENT_STORAGE, JSON.stringify({ path: '/errors/decision-1/decision', body: { action: 'retry_current_node' }, label: '重试当前节点', key: 'original-key' }))
    const { client, rerender } = setup()
    await screen.findByRole('button', { name: '确认原请求结果' })
    rerender(<QueryClientProvider client={client}><Harness connected={false} /></QueryClientProvider>)
    expect(screen.getByRole('button', { name: '确认原请求结果' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重试当前节点' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '进入手动处理' })).toBeDisabled()
    expect(sessionStorage.getItem(HANDLING_INTENT_STORAGE)).toContain('original-key')
    expect(posts).toHaveLength(0)
  })

  it('从界面登记上下料并传递物料与库位版本', async () => {
    state = manualSnapshot(); setup()
    fireEvent.click(await screen.findByRole('tab', { name: '上下料登记' }))
    await screen.findAllByRole('option', { name: '样品瓶' })
    fireEvent.change(screen.getByLabelText('登记物料 1'), { target: { value: 'bottle' } })
    fireEvent.change(screen.getByLabelText('登记目标 1'), { target: { value: 'rack' } })
    fireEvent.change(screen.getByLabelText('登记库位 1'), { target: { value: 'site-1' } })
    fireEvent.change(screen.getByLabelText('登记原因'), { target: { value: '现场已放入 A1' } })
    fireEvent.click(screen.getByLabelText('我已核对实际上下料情况，确认登记位置与现场一致'))
    fireEvent.click(screen.getByRole('button', { name: '保存上下料登记' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].body).toMatchObject({ changes: [{ material_uuid: 'bottle', parent_uuid: 'rack', site_uuid: 'site-1' }], expected_inventory_versions: { bottle: 4, 'site-1': 'site-v2' }, expected_session_version: 1 })
  })

  it('只释放用户明确选择的逻辑占用，携带该记录版本', async () => {
    state = manualSnapshot(); setup()
    fireEvent.click(await screen.findByRole('tab', { name: '逻辑占用' }))
    const occupancy = await screen.findByRole('checkbox', { name: /device:probe.*执行占用/ })
    fireEvent.click(occupancy)
    fireEvent.change(screen.getByLabelText('释放原因'), { target: { value: '设备已人工归位' } })
    fireEvent.click(screen.getByLabelText('我已核对现场，确认所选逻辑占用可以释放'))
    fireEvent.click(screen.getByRole('button', { name: '释放所选逻辑占用' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].body).toMatchObject({ occupancy_ids: ['lock-1'], expected_versions: { 'lock-1': 'lock-v3' }, decision_id: 'decision-1', session_id: 'session-1', operation: 'release' })
  })
})
