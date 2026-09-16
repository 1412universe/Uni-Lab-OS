import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Clock3, Minus, Plus, RefreshCw } from 'lucide-react'
import type { WorkflowTask } from '../types'
import { currentTimeCandidate, loadTimeSchedule } from '../lib/timeScheduleClient'
import { buildTaskGantt, formatGanttDuration, formatGanttTime } from '../lib/taskGantt'
import type { GanttBar, GanttRow } from '../lib/taskGantt'
import { taskColor } from '../lib/resourceUsage'
import { Button, EmptyState, StatusBadge } from './ui'
import './TaskGantt.css'

type WindowRange = { start: number; end: number }
const MIN_WINDOW = 30_000
const terminalStatuses = new Set(['succeeded', 'failed', 'canceled', 'timeout'])
const kindLabels: Record<GanttBar['kind'], string> = {
  actual: '实际执行', running: '已运行，结束未确认', planned: '预计执行', remaining: '预计剩余',
}

function extent(bars: GanttBar[], fallback: number): WindowRange {
  const start = bars.length ? Math.min(...bars.map((bar) => bar.startMs)) : fallback - 60_000
  const end = bars.length ? Math.max(...bars.map((bar) => bar.endMs)) : fallback + 60_000
  const width = Math.max(MIN_WINDOW, end - start)
  return { start: start - width * 0.04, end: Math.max(end, start + MIN_WINDOW) + width * 0.08 }
}

/** 首屏聚焦正在运行的任务；没有在途任务时聚焦最近执行，避免旧历史挤扁新计划。 */
function recentRange(rows: GanttRow[], now: number): WindowRange {
  const active = rows.filter(({ task }) => !terminalStatuses.has(task.executionStatus || task.status)).flatMap((row) => row.bars)
  if (active.length) {
    const range = extent(active, now)
    return { start: Math.max(range.start, now - 15 * 60_000), end: Math.max(range.end, now + 15_000) }
  }
  const bars = rows.flatMap((row) => row.bars)
  const latest = bars.length ? Math.max(...bars.map((bar) => bar.endMs)) : now
  return extent(bars.filter((bar) => bar.endMs >= latest - 5 * 60_000), now)
}

function barSummary(bar: GanttBar): string {
  return `${bar.name} · ${kindLabels[bar.kind]}\n${formatGanttTime(bar.startMs, true)} → ${formatGanttTime(bar.endMs, true)}${bar.endIsOpen ? '（观测时刻，非完成时间）' : ''}\n${formatGanttDuration(bar.endMs - bar.startMs)} · ${bar.deviceId || '设备未指定'}`
}

export function TaskGantt({ connected, tasks, selectedId, selectedNode, onSelect, onSelectNode, onViewResources }: {
  connected: boolean
  tasks: WorkflowTask[]
  selectedId: string
  selectedNode?: { taskUuid: string; nodeUuid: string }
  onSelect: (taskUuid: string) => void
  onSelectNode: (taskUuid: string, nodeUuid: string) => void
  onViewResources?: (taskUuid: string) => void
}) {
  const query = useQuery({
    queryKey: ['scheduler-time-plan'], queryFn: ({ signal }) => loadTimeSchedule(signal),
    enabled: connected, refetchInterval: connected ? 2_000 : false, retry: false,
  })
  const [now, setNow] = useState(Date.now)
  const [rangeMode, setRangeMode] = useState<'recent' | 'all'>('recent')
  const [manualRange, setManualRange] = useState<WindowRange | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  useEffect(() => {
    if (!connected) return
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [connected])
  const candidate = currentTimeCandidate(query.data, connected, query.isError)
  const blocked = connected && !query.isError && !query.data?.input_error ? query.data?.input.blocked || [] : []
  const rows = useMemo(() => buildTaskGantt(tasks, candidate, blocked, now), [tasks, candidate, blocked, now])
  const bars = rows.flatMap((row) => row.bars)
  const range = manualRange || (rangeMode === 'all' ? extent(bars, now) : recentRange(rows, now))
  const span = range.end - range.start
  const inRange = (bar: GanttBar) => bar.endMs >= range.start && bar.startMs <= range.end
  const hovered = bars.find((bar) => bar.id === hoveredId && inRange(bar))
  const visibleRows = !manualRange && rangeMode === 'all' ? rows : rows.filter((row) => row.bars.some(inRange)
    || (!terminalStatuses.has(row.task.executionStatus || row.task.status) && (row.pending.length || !row.bars.length)))
  const ticks = Array.from({ length: 9 }, (_, index) => range.start + span * index / 8)
  const nowPercent = (now - range.start) / span * 100
  const planState = !connected ? '连接已断开 · 仅保留最近观测'
    : query.isError ? '计划读取失败 · 已隐藏预计时间'
      : !query.data ? '正在读取时间计划'
        : !query.data.enabled ? '时间排程未启用 · 仅显示实际执行'
          : query.data.input_error ? '规划输入待处理 · 已隐藏预计时间'
            : candidate ? '时间计划已同步'
              : query.data.latest?.status === 'INFEASIBLE' ? '当前无可行计划 · 等待重新排程'
                : '正在重排 · 暂不展示旧预测'
  const zoom = (factor: number) => {
    const nextSpan = Math.min(90 * 24 * 60 * 60_000, Math.max(MIN_WINDOW, span * factor))
    const center = (range.start + range.end) / 2
    setManualRange({ start: center - nextSpan / 2, end: center + nextSpan / 2 })
  }
  const shift = (direction: number) => setManualRange({ start: range.start + span * direction / 2, end: range.end + span * direction / 2 })

  return <section className="task-gantt" aria-label="多任务时间甘特图">
    <div className="gantt-tools">
      <div className="gantt-plan-state"><i className={candidate ? 'synced' : ''} /><span>{planState}</span><small>每 2 秒同步计划</small></div>
      <div className="gantt-range-tools">
        <div className="gantt-range-switch" role="group" aria-label="时间范围">
          <button type="button" aria-pressed={!manualRange && rangeMode === 'recent'} onClick={() => { setRangeMode('recent'); setManualRange(null) }}>当前 / 最近</button>
          <button type="button" aria-pressed={!manualRange && rangeMode === 'all'} onClick={() => { setRangeMode('all'); setManualRange(null) }}>全部时间</button>
        </div>
        <button type="button" className="gantt-icon-button" aria-label="向前查看时间" onClick={() => shift(-1)}><ChevronLeft size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="缩小甘特图" onClick={() => zoom(2)}><Minus size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="放大甘特图" disabled={span <= MIN_WINDOW} onClick={() => zoom(0.5)}><Plus size={15} /></button>
        <button type="button" className="gantt-icon-button" aria-label="向后查看时间" onClick={() => shift(1)}><ChevronRight size={15} /></button>
        <button type="button" className="gantt-now-button" onClick={() => setManualRange({ start: now - 60_000, end: now + 120_000 })}><Clock3 size={13} />现在</button>
        <Button icon={<RefreshCw size={13} />} disabled={!connected || query.isFetching} onClick={() => { void query.refetch() }}>刷新排程</Button>
      </div>
    </div>
    <div className="gantt-caption">
      <span><strong>{visibleRows.length}</strong> / {tasks.length} 个筛选任务 · {formatGanttTime(range.start, true)} — {formatGanttTime(range.end, true)}</span>
      <div className="gantt-legend" aria-label="甘特图图例"><span><i className="actual" />实际执行</span><span><i className="running" />正在运行</span><span><i className="planned" />预计执行 / 剩余</span><span><i className="unknown" />时间待定</span></div>
    </div>
    <div className="gantt-scroll" tabIndex={0} role="region" aria-label="可横向滚动的任务时间轴">
      <div className="gantt-canvas">
        <div className="gantt-axis-row">
          <div className="gantt-identity gantt-axis-label">任务 / 节点 <span>本地时间</span></div>
          <div className="gantt-axis">{ticks.map((tick, index) => <span key={index} style={{ left: `${index * 12.5}%` }}>
            {new Date(range.start).toDateString() !== new Date(range.end).toDateString() ? <small>{new Date(tick).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</small> : null}
            {formatGanttTime(tick)}
          </span>)}</div>
        </div>
        {visibleRows.map((row) => <div className={`gantt-row ${row.task.uuid === selectedId ? 'selected' : ''}`} key={row.task.uuid} style={{ '--task-color': taskColor(row.task.uuid) } as CSSProperties}>
          <div className="gantt-identity">
            <button type="button" className="gantt-task-name" aria-label={`选择任务 ${row.task.workflowName} ${row.task.uuid}`} onClick={() => onSelect(row.task.uuid)}><i /><strong>{row.task.workflowName}</strong></button>
            <code title={row.task.uuid}>{row.task.uuid.slice(0, 8)}</code><StatusBadge status={row.task.status} />
            <small className="gantt-task-sample">{row.task.sample} · {row.task.progress}%</small>
            {onViewResources ? <button type="button" className="gantt-resource-link" onClick={() => onViewResources(row.task.uuid)}>占用资源</button> : null}
          </div>
          <div className="gantt-row-content">
            <div className="gantt-tracks" style={{ height: Math.max(78, row.laneCount * 38 + 24) }}>
              {ticks.map((_, index) => <i className="gantt-grid-line" key={index} style={{ left: `${index * 12.5}%` }} />)}
              {nowPercent >= 0 && nowPercent <= 100 ? <i className="gantt-now-line" style={{ left: `${nowPercent}%` }}><span>现在</span></i> : null}
              {row.bars.filter(inRange).map((bar) => {
                const left = Math.max(0, (bar.startMs - range.start) / span * 100)
                const right = Math.min(100, (bar.endMs - range.start) / span * 100)
                const selected = selectedNode?.taskUuid === bar.taskUuid && selectedNode.nodeUuid === bar.nodeUuid
                const detailAvailable = row.task.nodes.some((node) => node.uuid === bar.nodeUuid)
                return <button type="button" key={bar.id}
                  className={`gantt-bar gantt-bar-${bar.kind} gantt-status-${bar.status} ${selected ? 'selected' : ''} ${bar.endIsOpen ? 'open-ended' : ''}`}
                  style={{ left: `${left}%`, width: `${Math.max(0, right - left)}%`, top: 15 + bar.lane * 38 }}
                  aria-label={`${row.task.workflowName} · ${barSummary(bar)}`} aria-pressed={selected} title={`${barSummary(bar)}${detailAvailable ? '' : '\n节点详情正在同步'}`} disabled={!detailAvailable}
                  onClick={() => onSelectNode(bar.taskUuid, bar.nodeUuid)}
                  onMouseEnter={() => setHoveredId(bar.id)} onMouseLeave={() => setHoveredId(null)}
                  onFocus={() => setHoveredId(bar.id)} onBlur={() => setHoveredId(null)}>
                  <strong>{bar.name}</strong><small>{formatGanttDuration(bar.endMs - bar.startMs)}</small>
                </button>
              })}
              {!row.bars.some(inRange) ? <span className="gantt-row-empty">{row.bars.length ? '执行时间不在当前窗口' : '暂无确定的执行时间'}</span> : null}
            </div>
            {row.pending.length ? <div className="gantt-pending"><span>时间待定</span>{row.pending.map((node) => <button type="button" key={node.nodeUuid} title={node.reason} disabled={!row.task.nodes.some((item) => item.uuid === node.nodeUuid)} onClick={() => onSelectNode(row.task.uuid, node.nodeUuid)}><Clock3 size={12} /><strong>{node.name}</strong><small>{node.reason}</small></button>)}</div> : null}
          </div>
        </div>)}
      </div>
    </div>
    {!visibleRows.length ? <EmptyState title="当前时间窗口没有任务" description="点击“全部时间”查看历史，或切回“当前 / 最近”查看最近执行。" /> : null}
    <div className="gantt-detail-strip">{hovered ? <><strong>{hovered.name}</strong><span>{kindLabels[hovered.kind]} · {formatGanttTime(hovered.startMs, true)} → {formatGanttTime(hovered.endMs, true)}{hovered.endIsOpen ? '（结束未确认）' : ''}</span><code>{hovered.deviceId || '设备未指定'}</code><span>点击查看节点详情</span></> : <span>点击时间条查看节点详情；虚线条会随重排更新，时间待定的节点不会被画成零耗时。</span>}</div>
    <p className="gantt-footnote">预计区间不是启动指令，也不代表资源已经释放。仅展示当前已加载任务；正常完成的数据输入/输出不占时间条，未确定分支和循环后续不推测完成时间。</p>
  </section>
}
