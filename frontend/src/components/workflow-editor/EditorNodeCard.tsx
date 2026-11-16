import { Handle, Position, type NodeProps } from '@xyflow/react'
import { AlertTriangle, Ban, GitBranch, Repeat2, SplitSquareHorizontal, Waypoints } from 'lucide-react'
import { READY_KEY, type EditorNode } from '../../lib/workflowEditorModel'
import { describeParameter } from '../../lib/workflowParameterLabels'
import { RUN_STATE_COLOR, RUN_STATE_ICON, RUN_STATE_LABEL, type RunNodeState } from '../../lib/workflowRunModel'
import { useNodeActions } from './nodeActions'

const kindPresentation = {
  action: { label: '动作', icon: GitBranch },
  condition: { label: '条件', icon: SplitSquareHorizontal },
  repeat_until: { label: '循环', icon: Repeat2 },
  material_source: { label: '物料源', icon: GitBranch },
  group: { label: '分组', icon: GitBranch },
  composite: { label: '子流程', icon: Waypoints },
} as const

/** 端口栏最多展开多少个参数行；超出的引到右侧属性面板。 */
const RAIL_LIMIT = 10
const RAIL_SOURCE_LIMIT = 4

/**
 * 画布节点。
 *
 * 两种端口呈现：
 * - 常规：只画 `ready` 顺序端口 + 已连线占用的数据端口，画布保持清爽。
 * - 参数连线模式（`showDataPorts`）：底部展开成一条参数栏，每行左侧是入参点、
 *   右侧是出参点，直接从点上拖线即可把上游输出接到下游参数——数据绑定不再只
 *   存在于右侧属性面板。
 */
export function EditorNodeCard({ data, selected }: NodeProps<EditorNode>) {
  const {
    raw, template, subtitle, handles, linkedSourceHandles, linkedTargetHandles, showDataPorts, issue,
    runState, runDetail,
  } = data
  const { toggleDisabled, removeNode } = useNodeActions()
  const kind = (raw.kind || 'action') as keyof typeof kindPresentation
  const presentation = kindPresentation[kind] || kindPresentation.action
  const KindIcon = presentation.icon
  // 见 PropertyPanel：指令号表按设备 id 索引，显示名仅作兜底。
  const deviceLabel = template?.resourceName || template?.resourceDisplayName

  const readyTarget = handles.find((item) => item.ioType === 'target' && item.handleKey === READY_KEY)
  const readySource = handles.find((item) => item.ioType === 'source' && item.handleKey === READY_KEY)
  const dataTargets = handles.filter((item) => item.ioType === 'target' && item.handleKey !== READY_KEY)
  const dataSources = handles.filter((item) => item.ioType === 'source' && item.handleKey !== READY_KEY)

  const collapsedTargets = dataTargets.filter((item) => linkedTargetHandles.includes(item.uuid))
  const collapsedSources = dataSources.filter((item) => linkedSourceHandles.includes(item.uuid))

  const railTargets = showDataPorts ? dataTargets.slice(0, RAIL_LIMIT) : []
  const railSources = showDataPorts ? dataSources.slice(0, RAIL_SOURCE_LIMIT) : []
  const hiddenTargets = showDataPorts ? Math.max(0, dataTargets.length - railTargets.length) : 0
  const hiddenSources = showDataPorts ? Math.max(0, dataSources.length - railSources.length) : 0

  const disabled = Boolean(raw.disabled)
  const parameterCount = dataTargets.length
  const dataEdgeCount = linkedTargetHandles.length + linkedSourceHandles.length
  const run = (runState && runState !== 'idle') ? runState as RunNodeState : undefined

  return (
    <div
      className={[
        'wf-node',
        `wf-node-${kind}`,
        selected ? 'is-selected' : '',
        disabled ? 'is-disabled' : '',
        issue ? 'has-issue' : '',
        showDataPorts ? 'is-port-mode' : '',
        run ? `is-run-${run}` : '',
      ].filter(Boolean).join(' ')}
      title={String(raw.description || '')}
      style={run ? { ['--run-color' as string]: RUN_STATE_COLOR[run] } : undefined}
    >
      {readyTarget ? (
        <Handle
          id={readyTarget.uuid}
          type="target"
          position={Position.Left}
          className="wf-port wf-port-ready"
          title="执行顺序入口"
        />
      ) : null}
      {readySource ? (
        <Handle
          id={readySource.uuid}
          type="source"
          position={Position.Right}
          className="wf-port wf-port-ready"
          title="执行顺序出口"
        />
      ) : null}

      {/* 常规模式：只为已连线的数据端口留出可见锚点 */}
      {!showDataPorts
        ? collapsedTargets.map((handle, index) => (
          <Handle
            key={handle.uuid}
            id={handle.uuid}
            type="target"
            position={Position.Left}
            className="wf-port wf-port-data"
            style={{ top: 44 + index * 18 }}
            title={`${describeParameter(handle.dataKey, deviceLabel).label} 输入`}
          />
        ))
        : null}
      {!showDataPorts
        ? collapsedSources.map((handle, index) => (
          <Handle
            key={handle.uuid}
            id={handle.uuid}
            type="source"
            position={Position.Right}
            className="wf-port wf-port-data"
            style={{ top: 44 + index * 18 }}
            title={`${describeParameter(handle.dataKey, deviceLabel).label} 输出`}
          />
        ))
        : null}

      <header className="wf-node-head">
        <span className={`wf-node-kind wf-kind-${kind}`}>
          <KindIcon size={11} />
          {presentation.label}
        </span>
        {run ? (
          <span className={`wf-node-run is-${run}`} title={runDetail || RUN_STATE_LABEL[run]}>
            {RUN_STATE_ICON[run]} {RUN_STATE_LABEL[run]}
          </span>
        ) : null}
        {disabled ? <span className="wf-node-flag"><Ban size={11} />已停用</span> : null}
      </header>

      <strong className="wf-node-name">{String(raw.name || '未命名节点')}</strong>
      <span className="wf-node-sub">{subtitle || template?.displayName || '—'}</span>

      <footer className="wf-node-foot">
        <span className="wf-node-param">参数 {parameterCount}</span>
        {dataEdgeCount ? <span className="wf-node-link">数据线 {dataEdgeCount}</span> : null}
        {issue ? (
          <span className="wf-node-issue" title={issue}>
            <AlertTriangle size={11} />
            {issue}
          </span>
        ) : null}
      </footer>

      {/* 参数连线模式：每行自带句柄，行容器是定位上下文，端口天然对齐 */}
      {showDataPorts && (railTargets.length || railSources.length) ? (
        <div className="wf-port-rail">
          {railTargets.map((handle) => {
            const meta = describeParameter(handle.dataKey, deviceLabel)
            const linked = linkedTargetHandles.includes(handle.uuid)
            return (
              <div className={`wf-port-row is-target ${linked ? 'is-linked' : ''}`} key={handle.uuid}>
                <Handle
                  id={handle.uuid}
                  type="target"
                  position={Position.Left}
                  className="wf-port wf-port-data"
                  title={`${meta.label}（${handle.dataKey}）输入`}
                />
                <span className="wf-port-label">{meta.label}</span>
                {meta.unit ? <em className="wf-port-unit">{meta.unit}</em> : null}
              </div>
            )
          })}
          {railSources.map((handle) => {
            const meta = describeParameter(handle.dataKey, deviceLabel)
            const linked = linkedSourceHandles.includes(handle.uuid)
            return (
              <div className={`wf-port-row is-source ${linked ? 'is-linked' : ''}`} key={handle.uuid}>
                <span className="wf-port-label">{meta.label}</span>
                <em className="wf-port-unit">输出</em>
                <Handle
                  id={handle.uuid}
                  type="source"
                  position={Position.Right}
                  className="wf-port wf-port-data"
                  title={`${meta.label}（${handle.dataKey}）输出`}
                />
              </div>
            )
          })}
          {hiddenTargets || hiddenSources ? (
            <div className="wf-port-more">
              另有 {hiddenTargets + hiddenSources} 个参数，可在右侧属性面板绑定
            </div>
          ) : null}
        </div>
      ) : null}

      {/* 选中后浮在矩形右上角边框外的两个角标圆点（对齐 CWM 的 wfnode-dis / wfnode-del） */}
      {kind !== 'group' ? (
        <button
          type="button"
          className={`wf-node-dis nodrag${disabled ? ' is-on' : ''}`}
          aria-label={disabled ? `启用 ${String(raw.name || '')}` : `禁用 ${String(raw.name || '')}`}
          title={disabled ? '启用此节点' : '禁用此节点（运行时跳过）'}
          onClick={(event) => {
            event.stopPropagation()
            toggleDisabled(String(raw.uuid || ''))
          }}
        >
          {disabled ? '↻' : '⊘'}
        </button>
      ) : null}
      <button
        type="button"
        className="wf-node-del nodrag"
        aria-label={`删除 ${String(raw.name || '')}`}
        title="删除节点（可用撤销恢复）"
        onClick={(event) => {
          event.stopPropagation()
          removeNode(String(raw.uuid || ''))
        }}
      >
        ✕
      </button>
    </div>
  )
}
