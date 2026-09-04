import { useEffect, useRef } from 'react'
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
const taskEventRefreshDelayMs = 300

export function useEdgeData() {
  const queryClient = useQueryClient()
  const refreshState = useRef({
    timer: undefined as number | undefined,
    taskInFlight: false,
    taskPending: false,
    fullInFlight: false,
    mounted: true,
  })
  const scheduleTaskRefreshRef = useRef<() => void>(() => undefined)
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
  const hasSnapshot = Boolean(query.data)
  const taskQuery = useQuery({
    queryKey: ['edge-tasks'],
    queryFn: ({ signal }) => loadEdgeTasks(
      query.data?.workflows || [],
      query.data?.materials || [],
      signal,
    ),
    enabled: hasSnapshot,
    staleTime: 15_000,
  })

  useEffect(() => {
    refreshState.current.mounted = true
    return () => { refreshState.current.mounted = false }
  }, [])

  useEffect(() => {
    if (!hasSnapshot) return undefined
    const runTaskRefresh = async () => {
      const state = refreshState.current
      if (state.fullInFlight || state.taskInFlight) {
        state.taskPending = true
        return
      }
      const joinedExistingRefresh = queryClient.isFetching({ queryKey: ['edge-tasks'], exact: true }) > 0
      state.taskInFlight = true
      state.taskPending = joinedExistingRefresh
      try {
        await queryClient.refetchQueries(
          { queryKey: ['edge-tasks'], exact: true, type: 'active' },
          { cancelRefetch: false },
        )
      } finally {
        state.taskInFlight = false
        if (state.mounted && state.taskPending) scheduleTaskRefresh()
      }
    }
    const scheduleTaskRefresh = () => {
      const state = refreshState.current
      if (!state.mounted) return
      state.taskPending = true
      if (state.timer !== undefined || state.taskInFlight || state.fullInFlight) return
      state.timer = window.setTimeout(() => {
        state.timer = undefined
        state.taskPending = false
        void runTaskRefresh()
      }, taskEventRefreshDelayMs)
    }
    scheduleTaskRefreshRef.current = scheduleTaskRefresh
    const pollingTimer = window.setInterval(scheduleTaskRefresh, 30_000)
    const events = typeof EventSource === 'undefined'
      ? undefined
      : new EventSource(`${EDGE_API_BASE}/events`)
    events?.addEventListener('manual_confirmation.required', scheduleTaskRefresh)
    events?.addEventListener('manual_confirmation.resolved', scheduleTaskRefresh)
    events?.addEventListener('workflow.runtime.changed', scheduleTaskRefresh)
    return () => {
      events?.close()
      window.clearInterval(pollingTimer)
      const state = refreshState.current
      if (state.timer !== undefined) window.clearTimeout(state.timer)
      state.timer = undefined
      scheduleTaskRefreshRef.current = () => undefined
    }
  }, [hasSnapshot, queryClient])

  let connection: ConnectionMode = 'loading'
  if (query.isSuccess) connection = 'connected'
  if (query.isError || taskQuery.isError) {
    connection = demoEnabled
      ? 'demo'
      : query.data
        ? 'reconnecting'
        : 'error'
  }

  const baseSnapshot = query.data
    ?? (demoEnabled ? demoSnapshot : emptySnapshot)
  const effectiveTasks = taskQuery.data ?? baseSnapshot.tasks
  const snapshot = connection === 'error'
    ? emptySnapshot
    : {
        ...baseSnapshot,
        tasks: effectiveTasks,
        materials: materialsWithTaskReferences(baseSnapshot.materials, effectiveTasks),
      }

  const refetch = async () => {
    // 完整快照本身已经携带任务矩阵并原子更新 edge-tasks，手动刷新不能再并发
    // 发起第二份相同矩阵请求。
    const state = refreshState.current
    state.fullInFlight = true
    try {
      if (queryClient.isFetching({ queryKey: ['edge-tasks'], exact: true })) {
        await queryClient.refetchQueries(
          { queryKey: ['edge-tasks'], exact: true, type: 'active' },
          { cancelRefetch: false },
        )
      }
      const snapshotResult = await query.refetch({ cancelRefetch: false })
      return { isError: snapshotResult.isError }
    } finally {
      state.fullInFlight = false
      if (state.taskPending) scheduleTaskRefreshRef.current()
    }
  }

  return {
    ...query,
    error: query.error || taskQuery.error,
    isError: query.isError || taskQuery.isError,
    refetch,
    connection,
    snapshot,
    lastSuccessfulAt: Math.max(query.dataUpdatedAt, taskQuery.dataUpdatedAt),
    isDemoFallback: demoEnabled && (query.isError || taskQuery.isError || !query.data),
  }
}
