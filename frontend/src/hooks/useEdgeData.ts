import { useQuery } from '@tanstack/react-query'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import { loadEdgeSnapshot } from '../lib/edgeClient'
import type { ConnectionMode, EdgeSnapshot } from '../types'

const demoSnapshot: EdgeSnapshot = {
  workflows: demoWorkflows,
  tasks: demoTasks,
  materials: demoMaterials,
  materialTotal: 48,
  workflowLoaded: demoWorkflows.length,
  workflowTotal: demoWorkflows.length,
}

const emptySnapshot: EdgeSnapshot = {
  workflows: [],
  tasks: [],
  materials: [],
  materialTotal: 0,
  workflowLoaded: 0,
  workflowTotal: 0,
}

const demoEnabled = import.meta.env.VITE_ENABLE_DEMO_DATA === 'true'

export function useEdgeData() {
  const query = useQuery({
    queryKey: ['edge-snapshot'],
    queryFn: ({ signal }) => loadEdgeSnapshot(signal),
    refetchInterval: 15_000,
  })

  let connection: ConnectionMode = 'loading'
  if (query.isSuccess) connection = 'connected'
  if (query.isError) connection = demoEnabled ? 'demo' : 'error'

  const snapshot = query.isError
    ? (demoEnabled ? demoSnapshot : emptySnapshot)
    : (query.data ?? (demoEnabled ? demoSnapshot : emptySnapshot))

  return {
    ...query,
    connection,
    snapshot,
    isDemoFallback: demoEnabled && (query.isError || !query.data),
  }
}
