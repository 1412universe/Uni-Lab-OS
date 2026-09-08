import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { StartupModeDialog } from './components/StartupModeDialog'
import { useEdgeData } from './hooks/useEdgeData'
import { EdgeApiError, startupModeSwitchBlockers, switchStartupMode } from './lib/edgeClient'
import { pageFromSearch, searchForWorkflow, searchWithPage, workflowTargetFromSearch } from './lib/routes'
import { MaterialsPage } from './pages/MaterialsPage'
import { OverviewPage } from './pages/OverviewPage'
import { TasksPage } from './pages/TasksPage'
import { WorkflowsPage } from './pages/WorkflowsPage'
import { OperationsPage } from './pages/OperationsPage'
import { ReagentsPage } from './pages/ReagentsPage'
import type { PageId, StartupModeSwitchBlocker, WorkflowTarget } from './types'

export default function App() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = pageFromSearch(`?${searchParams.toString()}`)
  const workflowTarget = workflowTargetFromSearch(`?${searchParams.toString()}`)
  const { snapshot, connection, error, refetch, lastSuccessfulAt } = useEdgeData()
  const [toast, setToast] = useState('')
  const [modeDialogOpen, setModeDialogOpen] = useState(false)
  const [modeSwitchPending, setModeSwitchPending] = useState(false)
  const [modeSwitchError, setModeSwitchError] = useState('')
  const [modeSwitchBlockers, setModeSwitchBlockers] = useState<StartupModeSwitchBlocker[]>([])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const navigate = useCallback((nextPage: PageId) => {
    setSearchParams((current) => new URLSearchParams(searchWithPage(`?${current.toString()}`, nextPage)))
  }, [setSearchParams])

  const openWorkflow = useCallback((target: WorkflowTarget) => {
    setSearchParams((current) => new URLSearchParams(searchForWorkflow(
      `?${current.toString()}`,
      target,
    )))
  }, [setSearchParams])

  const refresh = useCallback(() => {
    void refetch().then((result) => {
      setToast(result.isError ? '刷新失败，已保留最后一次成功的只读快照' : 'Edge 状态已刷新')
    })
  }, [refetch])

  const openModeDialog = useCallback(() => {
    if (connection !== 'connected') {
      setToast('Edge 未连接，当前不能切换模式')
      return
    }
    setModeSwitchError('')
    setModeSwitchBlockers([])
    setModeDialogOpen(true)
  }, [connection])

  const closeModeDialog = useCallback(() => {
    setModeDialogOpen(false)
  }, [])

  const confirmModeSwitch = useCallback(async () => {
    const currentMode = snapshot.startupMode
    const targetMode = currentMode === 'develop' ? 'product' : 'develop'
    setModeSwitchPending(true)
    setModeSwitchError('')
    setModeSwitchBlockers([])
    try {
      const result = await switchStartupMode(targetMode, currentMode)
      const refreshed = await refetch()
      setModeDialogOpen(false)
      setToast(refreshed.isError
        ? `已切换至${result.mode === 'develop' ? '开发模式' : '生产模式'}，但页面数据刷新失败，请手动刷新`
        : `已切换至${result.mode === 'develop' ? '开发模式' : '生产模式'}`)
    } catch (error) {
      if (error instanceof EdgeApiError && error.code === 'startup_mode_conflict') {
        await refetch()
        setModeDialogOpen(false)
        setToast('启动模式已被其他页面切换，已刷新当前状态')
        return
      }
      setModeSwitchBlockers(startupModeSwitchBlockers(error))
      setModeSwitchError(error instanceof Error ? error.message : '模式切换失败，请稍后重试')
      void refetch()
    } finally {
      setModeSwitchPending(false)
    }
  }, [refetch, snapshot.startupMode])

  return (
    <AppShell
      page={page}
      connection={connection}
      startupMode={connection === 'connected' ? snapshot.startupMode : undefined}
      activeTaskCount={snapshot.tasks.filter((task) => task.status === 'running' || task.status === 'canceling').length}
      onNavigate={navigate}
      onNotify={setToast}
      onStartupModeClick={connection === 'connected' ? openModeDialog : undefined}
    >
      {connection === 'error' ? (
        <div className="connection-alert" role="alert">
          <div><strong>Edge 数据暂不可用</strong><span>{error instanceof Error ? error.message : '请检查 Edge 服务与网络连接。'}</span></div>
          <button type="button" onClick={refresh}>重新连接</button>
        </div>
      ) : null}
      {connection === 'reconnecting' ? (
        <div className="connection-alert connection-alert-reconnecting" role="status">
          <div>
            <strong>Edge 连接短暂中断，当前为只读快照</strong>
            <span>
              {lastSuccessfulAt
                ? `显示 ${new Date(lastSuccessfulAt).toLocaleTimeString('zh-CN', { hour12: false })} 的最后一次成功数据；写操作已暂停。`
                : '正在恢复连接；写操作已暂停。'}
              {error instanceof Error ? ` ${error.message}` : ''}
            </span>
          </div>
          <button type="button" onClick={refresh}>立即重试</button>
        </div>
      ) : null}
      {connection === 'demo' ? (
        <div className="connection-alert connection-alert-demo" role="status">
          <div><strong>当前为显式演示模式</strong><span>以下内容不是 Edge 生产数据。</span></div>
        </div>
      ) : null}
      {page === 'overview' ? (
        <OverviewPage tasks={snapshot.tasks} materialTotal={snapshot.materialTotal} workflowLoaded={snapshot.workflowLoaded} connection={connection} onNavigate={navigate} onNotify={setToast} />
      ) : page === 'materials' ? (
        <MaterialsPage materials={snapshot.materials} total={snapshot.materialTotal} connected={connection === 'connected'} onNotify={setToast} onRefresh={refresh} />
      ) : page === 'operations' ? (
        <OperationsPage materials={snapshot.materials} connected={connection === 'connected'} onNotify={setToast} />
      ) : page === 'reagents' ? (
        <ReagentsPage materials={snapshot.materials} connected={connection === 'connected'} onNotify={setToast} onRefresh={refresh} />
      ) : page === 'workflows' ? (
        <WorkflowsPage
          workflows={snapshot.workflows}
          materials={snapshot.materials}
          connected={connection === 'connected'}
          onNavigate={navigate}
          onNotify={setToast}
          onSelectWorkflow={openWorkflow}
          targetWorkflow={workflowTarget}
          startupMode={snapshot.startupMode}
        />
      ) : (
        <TasksPage
          tasks={snapshot.tasks}
          workflows={snapshot.workflows}
          materials={snapshot.materials}
          connected={connection === 'connected'}
          onRefresh={refresh}
          onNotify={setToast}
          onOpenWorkflow={openWorkflow}
          startupMode={snapshot.startupMode}
        />
      )}
      <div className={`toast ${toast ? 'show' : ''}`} role="status" aria-live="polite">{toast}</div>
      {modeDialogOpen ? (
        <StartupModeDialog
          currentMode={snapshot.startupMode}
          pending={modeSwitchPending}
          blockers={modeSwitchBlockers}
          errorMessage={modeSwitchError || undefined}
          onClose={closeModeDialog}
          onConfirm={() => { void confirmModeSwitch() }}
        />
      ) : null}
    </AppShell>
  )
}
