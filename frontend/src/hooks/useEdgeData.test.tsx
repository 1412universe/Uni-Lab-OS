import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { EdgeSnapshot } from '../types'
import { loadEdgeSnapshot } from '../lib/edgeClient'
import { useEdgeData } from './useEdgeData'

vi.mock('../lib/edgeClient', () => ({ loadEdgeSnapshot: vi.fn() }))

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
  beforeEach(() => vi.mocked(loadEdgeSnapshot).mockReset())

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
})
