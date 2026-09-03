import type { PropsWithChildren } from 'react'
import {
  Bell,
  BookOpen,
  Boxes,
  ChevronDown,
  FileJson,
  FlaskConical,
  LayoutDashboard,
  ListChecks,
  Radio,
  Search,
  Workflow,
  FlaskRound,
  TestTubes,
} from 'lucide-react'
import type { ConnectionMode, PageId } from '../types'

const navigation: { page: PageId; label: string; icon: typeof LayoutDashboard }[] = [
  { page: 'overview', label: '总监控', icon: LayoutDashboard },
  { page: 'materials', label: '物料', icon: Boxes },
  { page: 'reagents', label: '试剂', icon: TestTubes },
  { page: 'operations', label: '实验室操作', icon: FlaskRound },
  { page: 'workflows', label: '工作流', icon: Workflow },
  { page: 'tasks', label: '任务', icon: ListChecks },
]

const pageLabels: Record<PageId, string> = {
  overview: '总监控',
  materials: '物料',
  reagents: '试剂',
  operations: '实验室操作',
  workflows: '工作流',
  tasks: '任务',
}

const connectionLabels: Record<ConnectionMode, string> = {
  loading: '正在连接 Edge',
  connected: 'Edge 已连接',
  demo: '演示数据',
  error: 'Edge 连接异常',
}

/**
 * 渲染 UniLabOS 控制台外壳、主导航和现有接口文档入口。
 * 参数由当前页面、连接状态、活动任务数和页面回调组成；返回完整页面框架。
 */
export function AppShell({
  page,
  connection,
  activeTaskCount,
  onNavigate,
  onNotify,
  children,
}: PropsWithChildren<{
  page: PageId
  connection: ConnectionMode
  activeTaskCount: number
  onNavigate: (page: PageId) => void
  onNotify: (message: string) => void
}>) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <span className="brand-mark"><FlaskConical size={22} /></span>
          <div>
            <strong>Uni-Lab OS</strong>
            <span>实验运营控制台</span>
          </div>
        </div>

        <button className="workspace-switcher" type="button" onClick={() => onNotify('当前仅配置深圳实验室工作区')}>
          <span className="workspace-icon">SZ</span>
          <span>
            <strong>深圳实验室</strong>
            <small>Edge Workspace</small>
          </span>
          <ChevronDown size={15} />
        </button>

        <nav aria-label="主导航">
          <span className="nav-label">运营工作台</span>
          {navigation.map(({ page: itemPage, label, icon: Icon }) => (
            <button
              type="button"
              key={itemPage}
              className={`nav-item ${page === itemPage ? 'active' : ''}`}
              aria-current={page === itemPage ? 'page' : undefined}
              onClick={() => onNavigate(itemPage)}
            >
              <Icon size={19} />
              <span>{label}</span>
              {itemPage === 'tasks' && activeTaskCount > 0 ? <em>{activeTaskCount}</em> : null}
            </button>
          ))}
          <a
            className="nav-item"
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            aria-label="打开 Swagger 接口文档"
          >
            <BookOpen size={19} />
            <span>Swagger</span>
          </a>
          <a
            className="nav-item"
            href="/api/openapi.json"
            target="_blank"
            rel="noreferrer"
            aria-label="打开接口 JSON 文档"
          >
            <FileJson size={19} />
            <span>接口 JSON</span>
          </a>
        </nav>

        <div className={`edge-card edge-${connection}`}>
          <div className="edge-card-head">
            <Radio size={17} />
            <span>{connectionLabels[connection]}</span>
          </div>
          <strong>SZLab Edge</strong>
          <small>同源 /api/v1</small>
          <div className="edge-meter"><span /></div>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>实验运营</span>
            <i>/</i>
            <strong>{pageLabels[page]}</strong>
          </div>
          <div className="topbar-actions">
            <label className="global-search">
              <Search size={16} />
              <input readOnly aria-label="全局搜索（待接入）" placeholder="全局搜索待接入" onFocus={() => onNotify('请使用物料和工作流页面内的搜索框')} />
              <kbd>⌘ K</kbd>
            </label>
            <span className={`live-chip live-chip-${connection}`}>
              <i />
              {connection === 'connected'
                ? '实验室在线'
                : connection === 'loading'
                  ? '正在连接'
                  : connection === 'demo'
                    ? '演示模式'
                    : '状态未知'}
            </span>
            <button className="icon-button" aria-label="通知" onClick={() => onNotify('通知中心尚未接入')}><Bell size={18} /></button>
            <button className="profile-button" type="button" onClick={() => onNotify('用户与权限菜单尚未接入')}>
              <span>XY</span>
              <div><strong>Yanfei Xiong</strong><small>实验室管理员</small></div>
              <ChevronDown size={14} />
            </button>
          </div>
        </header>
        <main className="page-content">{children}</main>
      </div>
    </div>
  )
}
