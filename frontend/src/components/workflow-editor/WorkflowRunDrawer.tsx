/**
 * 工作流「运行」抽屉。
 *
 * 对齐 ChemWorldModel 编辑器 `wfrun.js` 的信息结构：
 *   左：设备泳道甘特（同设备串行、异设备并行一眼可见）+ 并行度曲线
 *   右：状态汇总 + 运行事件
 * 顶部：运行档位、暂停/继续、取消、收起
 *
 * 数据来自 Edge 的 Task / Job 投影（`WorkflowTask`），不复用编辑器的本地草图：
 * 只有已经提交到 Edge 的冻结版本才有权威状态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  CircleSlash,
  Pause,
  Play,
  RefreshCw,
  Square,
  X,
} from 'lucide-react'
import { Button } from '../ui'
import type { WorkflowTask } from '../../types'
import {
  RUN_STATE_COLOR,
  RUN_STATE_ICON,
  RUN_STATE_LABEL,
  buildRunLanes,
  buildRunNodeViews,
  buildRunSummary,
  formatRunClock,
  formatRunDuration,
  isTaskTerminal,
  parallelismBuckets,
  type RunLane,
  type RunNodeView,
} from '../../lib/workflowRunModel'

/** 运行档位：仿真镜像跑一遍 / 真机直驱。真机档必须先过显式授权。 */
export type RunTier = 'simulate' | 'real'

export interface WorkflowRunDrawerProps {
  task?: WorkflowTask
  /** 当前展示的档位（由页面持有，抽屉只读）。 */
  tier: RunTier
  /** 任务是否仍在推进；用于决定轮询与控件可用性。 */
  active: boolean
  pending: boolean
  error?: string
  onClose: () => void
  onRefresh: () => void
  onPause: () => void
  onResume: () => void
  onCancel: () => void
  /** 点击泳道色块把画布选中切到该节点。 */
  onSelectNode: (nodeUuid: string) => void
}

const eventFeedLimit = 40

function stateOf(task: WorkflowTask | undefined): string {
  if (!task) return ''
  return String(task.executionStatus || task.status || '')
}

export function WorkflowRunDrawer({
  task,
  tier,
  active,
  pending,
  error,
  onClose,
  onRefresh,
  onPause,
  onResume,
  onCancel,
  onSelectNode,
}: WorkflowRunDrawerProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const events = useRef<string[]>([])
  // 事件数组是 ref（避免每次轮询都重建列表），用版本号触发重渲染。
  const [, setFeedVersion] = useState(0)

  // 墙钟只在有活动任务时走；结束后停表，避免「已运行 20 分钟」这类假读数。
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])

  const paused = Boolean(task && (task.dispatchGate === 'paused' || task.executionMode === 'step'))
  const views = useMemo(() => buildRunNodeViews(task, paused), [task, paused])
  const lanes = useMemo(() => buildRunLanes(views, now), [views, now])
  const summary = useMemo(() => buildRunSummary(views, now), [views, now])
  const buckets = useMemo(
    () => parallelismBuckets(
      views
        .filter((view) => view.startedMs !== undefined)
        .map((view) => [view.startedMs as number, view.finishedMs ?? now] as const),
    ),
    [views, now],
  )

  // 事件流只记录状态跃迁，不记录每次轮询：同一状态的重复快照对排查没有价值。
  // 放在 effect 里而不是 useMemo：渲染期不许有副作用，StrictMode 下 useMemo
  // 会重跑，重复入栈会让同一跃迁在列表里出现两次。
  const previousStates = useRef<Map<string, string>>(new Map())
  useEffect(() => {
    const lines: string[] = []
    for (const view of views) {
      const before = previousStates.current.get(view.uuid)
      if (before === view.state) continue
      lines.push(`${formatRunClock(view.startedMs || Date.now())} · ${view.name} → ${RUN_STATE_LABEL[view.state]}`)
    }
    previousStates.current = new Map(views.map((view) => [view.uuid, view.state]))
    if (!lines.length) return
    events.current = [...lines.reverse(), ...events.current].slice(0, eventFeedLimit)
    setFeedVersion((value) => value + 1)
  }, [views])
  const feed = events.current

  const laneStart = lanes.length
    ? Math.min(...lanes.flatMap((lane) => lane.blocks.map((block) => block.startedMs)))
    : now
  const laneEnd = lanes.length
    ? Math.max(now, ...lanes.flatMap((lane) => lane.blocks.map((block) => block.finishedMs)))
    : now
  const laneSpan = Math.max(1000, laneEnd - laneStart)

  const terminal = isTaskTerminal(task)
  const hasRunning = views.some((view) => view.state === 'running')

  const percent = useCallback(
    (value: number) => `${Math.max(0, Math.min(100, ((value - laneStart) / laneSpan) * 100)).toFixed(2)}%`,
    [laneStart, laneSpan],
  )

  return (
    <section className={`wf-run-drawer${collapsed ? ' is-collapsed' : ''}`} aria-label="工作流运行面板">
      <header className="wf-run-head">
        <span className={`wf-run-tier-badge is-${tier}`}>
          {tier === 'real' ? '真机运行' : '仿真镜像'}
        </span>
        <strong className="wf-run-title">
          {task ? task.workflowName : '尚未提交运行'}
        </strong>
        <span className="wf-run-sub">{describeSub(task, active, error, views, summary)}</span>
        <div className="wf-run-actions">
          <Button icon={<RefreshCw size={13} />} disabled={!task || pending} onClick={onRefresh} title="重新读取任务与作业状态">
            刷新
          </Button>
          {active && hasRunning ? (
            <Button icon={<Pause size={13} />} disabled={pending} onClick={onPause} title="停在当前节点，之后可继续">
              暂停
            </Button>
          ) : null}
          {active && paused ? (
            <Button icon={<Play size={13} />} disabled={pending} onClick={onResume} title="从停下的节点继续执行">
              继续
            </Button>
          ) : null}
          {active ? (
            <Button
              tone="danger"
              icon={<Square size={13} />}
              disabled={pending}
              onClick={onCancel}
              title="取消任务：未下发的节点不再执行"
            >
              取消
            </Button>
          ) : null}
          <button
            type="button"
            className="wf-run-corner"
            aria-label={collapsed ? '展开运行面板' : '收起运行面板'}
            onClick={() => setCollapsed((value) => !value)}
          >
            {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
          <button type="button" className="wf-run-corner" aria-label="关闭运行面板" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
      </header>

      {collapsed ? null : (
        <div className="wf-run-body">
          {error ? (
            <p className="wf-run-error" role="alert"><AlertTriangle size={13} />{error}</p>
          ) : null}

          <div className="wf-run-lanes">
            {!lanes.length ? (
              <p className="wf-run-empty">
                <CircleSlash size={13} />
                {task ? '还没有节点开始执行，等待调度器派发。' : '提交运行后，这里会按设备画出执行泳道。'}
              </p>
            ) : (
              <>
                <div className="wf-run-axis">
                  <span>{formatRunDuration(0)}</span>
                  <i />
                  <span>{formatRunDuration(laneSpan)}</span>
                </div>
                {lanes.map((lane) => (
                  <Lane key={lane.device} lane={lane} percent={percent} onSelectNode={onSelectNode} />
                ))}
                <ParallelCurve buckets={buckets} peak={summary.peakParallelism} />
              </>
            )}
          </div>

          <aside className="wf-run-side">
            <div className="wf-run-legend">
              {(['succeeded', 'running', 'paused', 'ready', 'pending', 'failed', 'skipped', 'canceled'] as const)
                .filter((state) => summary.counts[state])
                .map((state) => (
                  <div className="wf-run-kv" key={state}>
                    <span>
                      <i className="wf-run-dot" style={{ background: RUN_STATE_COLOR[state] }} />
                      {RUN_STATE_ICON[state]} {RUN_STATE_LABEL[state]}
                    </span>
                    <b>{summary.counts[state]}</b>
                  </div>
                ))}
              <div className="wf-run-kv"><span>峰值并行度</span><b>{summary.peakParallelism}</b></div>
              <div className="wf-run-kv"><span>墙钟耗时</span><b>{formatRunDuration(summary.wallClockMs)}</b></div>
              <div className="wf-run-kv"><span>节点耗时总和</span><b>{formatRunDuration(summary.sumNodeDurationMs)}</b></div>
              <div className="wf-run-kv">
                <span>并行加速比</span>
                <b>{summary.speedup > 0 ? `${summary.speedup.toFixed(2)}×` : '—'}</b>
              </div>
              <div className="wf-run-kv"><span>涉及设备</span><b>{summary.deviceCount}</b></div>
              <div className="wf-run-kv"><span>已开始 / 全部节点</span><b>{summary.startedCount} / {summary.totalCount}</b></div>
            </div>

            <div className="wf-run-events">
              <div className="wf-run-events-head">
                <strong>运行事件</strong>
                <small>仅记录状态跃迁</small>
              </div>
              {feed.length ? (
                <ul>
                  {feed.map((line, index) => <li key={`${line}-${index}`}>{line}</li>)}
                </ul>
              ) : (
                <p className="wf-run-empty">暂无状态变化。</p>
              )}
            </div>
          </aside>
        </div>
      )}
    </section>
  )
}

function describeSub(
  task: WorkflowTask | undefined,
  active: boolean,
  error: string | undefined,
  views: RunNodeView[],
  summary: ReturnType<typeof buildRunSummary>,
): string {
  if (error) return '读取失败'
  if (!task) return '等待提交'
  const finished = views.filter((view) => ['succeeded', 'failed', 'skipped', 'canceled'].includes(view.state)).length
  const prefix = active ? '运行中' : '已结束'
  const failure = summary.counts.failed || 0
  return [
    prefix,
    `${finished}/${views.length} 个节点已结束`,
    `成功 ${summary.counts.succeeded || 0}`,
    failure ? `失败 ${failure}` : '',
    `峰值并行 ${summary.peakParallelism}`,
    `墙钟 ${formatRunDuration(summary.wallClockMs)}`,
  ].filter(Boolean).join(' · ')
}

function Lane({
  lane,
  percent,
  onSelectNode,
}: {
  lane: RunLane
  percent: (value: number) => string
  onSelectNode: (nodeUuid: string) => void
}) {
  return (
    <div className="wf-run-lane">
      <div className="wf-run-lane-head">
        <i className="wf-run-dot" style={{ background: lane.color }} />
        <span title={lane.device}>{lane.device}</span>
        <small>×{lane.totalCount}</small>
        {lane.runningCount > 1 ? (
          <em className="wf-run-warn" title="同一设备上同时有多个节点在跑，请核对调度策略">
            并行 {lane.runningCount}
          </em>
        ) : null}
      </div>
      <div className="wf-run-track">
        {lane.blocks.map((block) => {
          const left = percent(block.startedMs)
          const right = percent(block.finishedMs)
          return (
            <button
              type="button"
              key={block.nodeUuid}
              className={`wf-run-block is-${block.state}${block.openEnded ? ' is-open' : ''}`}
              style={{
                left,
                width: `calc(${right} - ${left})`,
                minWidth: 8,
                ['--run-color' as string]: RUN_STATE_COLOR[block.state],
              }}
              title={`${block.name} · ${RUN_STATE_LABEL[block.state]}\n${formatRunClock(block.startedMs)} → ${block.openEnded ? '进行中' : formatRunClock(block.finishedMs)}（${formatRunDuration(block.finishedMs - block.startedMs)}）`}
              onClick={() => onSelectNode(block.nodeUuid)}
            >
              <span>{block.name}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function ParallelCurve({ buckets, peak }: { buckets: number[]; peak: number }) {
  const max = Math.max(1, ...buckets)
  return (
    <div className="wf-run-parallel">
      <div className="wf-run-parallel-head">
        <strong>并行度</strong>
        <small>同时执行的节点数</small>
        <em>峰值 {peak}</em>
      </div>
      <div className="wf-run-parallel-bars" role="img" aria-label={`并行度曲线，峰值 ${peak}`}>
        {buckets.map((value, index) => (
          <i
            key={index}
            style={{
              height: `${Math.max(2, (value / max) * 100)}%`,
              background: value > 1 ? '#0d9488' : '#94a3b8',
            }}
            title={`${value} 个节点并行`}
          />
        ))}
      </div>
    </div>
  )
}

/** 供页面复用的状态标签（任务级）。 */
export function runTaskStatusLabel(task: WorkflowTask | undefined): string {
  const status = stateOf(task)
  if (!status) return '未运行'
  const map: Record<string, string> = {
    pending: '待运行',
    running: '运行中',
    paused: '已暂停',
    succeeded: '已完成',
    failed: '失败',
    canceled: '已取消',
    timeout: '超时',
    intervention_required: '需要干预',
  }
  return map[status] || status
}
