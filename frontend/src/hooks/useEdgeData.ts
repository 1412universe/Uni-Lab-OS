import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import { EDGE_API_BASE, loadEdgeSnapshot, loadEdgeTasks, materialsWithTaskReferences } from '../lib/edgeClient'
import type { ConnectionMode, EdgeSnapshot } from '../types'

const demoSnapshot: EdgeSnapshot = {
  startupMode: 'develop',
  workflows: demoWorkflows,
  tasks: demoTasks,
  materials: demoMaterials,
  materialTotal: 48,
  workflowLoaded: demoWorkflows.length,
  workflowTotal: demoWorkflows.length,
}

const emptySnapshot: EdgeSnapshot = {
  startupMode: 'product',
  workflows: [],
  tasks: [],
  materials: [],
  materialTotal: 0,
  workflowLoaded: 0,
  workflowTotal: 0,
}

const demoEnabled = import.meta.env.VITE_ENABLE_DEMO_DATA === 'true'

export function useEdgeData() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['edge-snapshot'],
    queryFn: async ({ signal }) => {
      const snapshot = await loadEdgeSnapshot(signal)
      queryClient.setQueryData(['edge-tasks'], snapshot.tasks)
      return snapshot
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })
  const taskQuery = useQuery({
    queryKey: ['edge-tasks'],
    queryFn: ({ signal }) => loadEdgeTasks(
      query.data?.workflows || [],
      query.data?.materials || [],
      signal,
    ),
    enabled: query.isSuccess && Boolean(query.data),
    staleTime: 15_000,
    refetchInterval: 15_000,
  })

  useEffect(() => {
    if (!query.isSuccess || typeof EventSource === 'undefined') return undefined
    const events = new EventSource(`${EDGE_API_BASE}/events`)
    const refreshTasks = () => {
      void queryClient.invalidateQueries({ queryKey: ['edge-tasks'] })
    }
    events.addEventListener('manual_confirmation.required', refreshTasks)
    events.addEventListener('manual_confirmation.resolved', refreshTasks)
    events.addEventListener('workflow.runtime.changed', refreshTasks)
    return () => events.close()
  }, [query.isSuccess, queryClient])

  let connection: ConnectionMode = 'loading'
  if (query.isSuccess) connection = 'connected'
  if (query.isError || taskQuery.isError) connection = demoEnabled ? 'demo' : 'error'

  const baseSnapshot = query.isError
    ? (demoEnabled ? demoSnapshot : emptySnapshot)
    : (query.data ?? (demoEnabled ? demoSnapshot : emptySnapshot))
  const effectiveTasks = taskQuery.data ?? baseSnapshot.tasks
  const snapshot = connection === 'error'
    ? emptySnapshot
    : {
        ...baseSnapshot,
        tasks: effectiveTasks,
        materials: materialsWithTaskReferences(baseSnapshot.materials, effectiveTasks),
      }

  const refetch = async () => {
    const [snapshotResult, taskResult] = await Promise.all([
      query.refetch(),
      taskQuery.refetch(),
    ])
    return { isError: snapshotResult.isError || taskResult.isError }
  }

  return {
    ...query,
    error: query.error || taskQuery.error,
    isError: query.isError || taskQuery.isError,
    refetch,
    connection,
    snapshot,
    isDemoFallback: demoEnabled && (query.isError || taskQuery.isError || !query.data),
  }
}
