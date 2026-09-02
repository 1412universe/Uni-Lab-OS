import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EdgeSnapshot } from '../types'
import { loadEdgeSnapshot, loadEdgeTasks } from '../lib/edgeClient'
import { useEdgeData } from './useEdgeData'

vi.mock('../lib/edgeClient', async (importOriginal) => ({
  ...await importOriginal<typeof import('../lib/edgeClient')>(),
  loadEdgeSnapshot: vi.fn(),
  loadEdgeTasks: vi.fn(),
}))

const authoritativeSnapshot: EdgeSnapshot = {
  workflows: [],
  tasks: [],
  materials: [{
    uuid: 'material-authoritative',
    name: '权威物料',
    category: 'beaker',
    currentLocation: {
      kind: 'site',
      label: 'S6 / S061',
      siteUuid: 'site-s061',
      ownerMaterialUuid: 'station-s6',
    },
    configuredSource: 'S6 / S061',
    taskReferences: [],
    barcode: 'BKR-1',
    className: 'community.szlab.beaker',
    updatedAt: '12:00',
    isStructural: false,
    siteCount: 0,
    sites: [],
    revision: 1,
    position: [0, 0, 0],
    size: [80, 80, 100],
  }],
  materialTotal: 1,
  workflowLoaded: 1,
  workflowTotal: 1,
}

describe('useEdgeData', () => {
  beforeEach(() => {
    vi.mocked(loadEdgeSnapshot).mockReset()
    vi.mocked(loadEdgeTasks).mockReset()
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
  })

  it('clears stale authoritative projections when a later Edge refresh fails', async () => {
    vi.mocked(loadEdgeSnapshot)
      .mockResolvedValueOnce(authoritativeSnapshot)
      .mockRejectedValueOnce(new Error('Edge unavailable'))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    expect(result.current.snapshot.materials).toHaveLength(1)

    await act(async () => { await result.current.refetch() })

    await waitFor(() => expect(result.current.connection).toBe('error'))
    expect(result.current.snapshot).toMatchObject({
      workflows: [],
      tasks: [],
      materials: [],
      materialTotal: 0,
    })
  })

  it('refreshes task runtime data without reloading workflows and materials', async () => {
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    vi.mocked(loadEdgeTasks)
      .mockResolvedValueOnce([{
        uuid: 'task-new', workflowUuid: 'wf-1', workflowName: '流程', status: 'running',
        sample: 'sample-1', description: 'task', current: '运行中', progress: 0,
        updatedAt: '12:00', nodes: [], materialUuids: [], runMode: 'normal', matrixGroupKey: 'wf-1',
      }])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    expect(loadEdgeTasks).not.toHaveBeenCalled()
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
    })

    await waitFor(() => expect(result.current.snapshot.tasks[0]?.uuid).toBe('task-new'))
    expect(loadEdgeSnapshot).toHaveBeenCalledTimes(1)
    expect(loadEdgeTasks).toHaveBeenCalledTimes(1)
  })

  it('invalidates the shared task query when the scheduler SSE announces manual confirmation', async () => {
    class FakeEventSource {
      static latest: FakeEventSource | undefined
      listeners = new Map<string, EventListener>()
      closed = false
      constructor(public url: string) { FakeEventSource.latest = this }
      addEventListener(type: string, listener: EventListener) { this.listeners.set(type, listener) }
      close() { this.closed = true }
      emit(type: string) { this.listeners.get(type)?.(new Event(type)) }
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    vi.mocked(loadEdgeSnapshot).mockResolvedValue(authoritativeSnapshot)
    vi.mocked(loadEdgeTasks).mockResolvedValue([])
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result, unmount } = renderHook(() => useEdgeData(), { wrapper })

    await waitFor(() => expect(result.current.connection).toBe('connected'))
    await waitFor(() => expect(FakeEventSource.latest?.url).toBe('/api/v1/events'))
    const callsBeforeEvent = vi.mocked(loadEdgeTasks).mock.calls.length
    act(() => FakeEventSource.latest?.emit('manual_confirmation.required'))
    await waitFor(() => expect(loadEdgeTasks).toHaveBeenCalledTimes(callsBeforeEvent + 1))

    const source = FakeEventSource.latest
    unmount()
    expect(source?.closed).toBe(true)
  })
})
