import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { useEdgeData } from './hooks/useEdgeData'
import { pageFromSearch } from './lib/routes'
import { MaterialsPage } from './pages/MaterialsPage'
import { OverviewPage } from './pages/OverviewPage'
import { TasksPage } from './pages/TasksPage'
import { WorkflowsPage } from './pages/WorkflowsPage'
import type { PageId } from './types'

export default function App() {
  const [searchParams, setSearchParams] = useSearchParams()
  const page = pageFromSearch(`?${searchParams.toString()}`)
  const { snapshot, connection, error, refetch } = useEdgeData()
  const [toast, setToast] = useState('')

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(''), 3200)
    return () => window.clearTimeout(timer)
  }, [toast])

  const navigate = useCallback((nextPage: PageId) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('page', nextPage)
      return next
    })
  }, [setSearchParams])

  const refresh = useCallback(() => {
    void refetch().then((result) => {
      setToast(result.isError ? '刷新失败，已停止展示非权威业务数据' : 'Edge 状态已刷新')
    })
  }, [refetch])

  return (
    <AppShell
      page={page}
      connection={connection}
      activeTaskCount={snapshot.tasks.filter((task) => task.status === 'running' || task.status === 'canceling').length}
      onNavigate={navigate}
      onNotify={setToast}
    >
      {connection === 'error' ? (
        <div className="connection-alert" role="alert">
          <div><strong>Edge 数据暂不可用</strong><span>{error instanceof Error ? error.message : '请检查 Edge 服务与网络连接。'}</span></div>
          <button type="button" onClick={refresh}>重新连接</button>
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
        <MaterialsPage materials={snapshot.materials} total={snapshot.materialTotal} connected={connection === 'connected'} onNotify={setToast} />
      ) : page === 'workflows' ? (
        <WorkflowsPage workflows={snapshot.workflows} onNavigate={navigate} onNotify={setToast} />
      ) : (
        <TasksPage tasks={snapshot.tasks} workflows={snapshot.workflows} materials={snapshot.materials} connected={connection === 'connected'} onRefresh={refresh} onNotify={setToast} />
      )}
      <div className={`toast ${toast ? 'show' : ''}`} role="status" aria-live="polite">{toast}</div>
    </AppShell>
  )
}
