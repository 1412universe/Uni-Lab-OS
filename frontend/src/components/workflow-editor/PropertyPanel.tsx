import { useMemo } from 'react'
import { Ban, Info, Trash2 } from 'lucide-react'
import type { NodeTemplateHandle } from '../../lib/edgeClient'
import { describeParameter, sortHandlesForPanel } from '../../lib/workflowParameterLabels'
import { READY_KEY, type EditorEdge, type EditorNode } from '../../lib/workflowEditorModel'

interface PropertyPanelProps {
  node?: EditorNode
  edge?: EditorEdge
  onPatchNode: (patch: { name?: string; description?: string; param?: Record<string, unknown>; disabled?: boolean }) => void
  onDeleteNode: () => void
  onDeleteEdge: () => void
}

type ParamValue = unknown

function parseValue(raw: string, handle: NodeTemplateHandle): ParamValue {
  const type = handle.type || 'string'
  if (raw === '') return null
  if (type === 'integer') {
    const value = Number.parseInt(raw, 10)
    return Number.isFinite(value) ? value : raw
  }
  if (type === 'number') {
    const value = Number(raw)
    return Number.isFinite(value) ? value : raw
  }
  if (type === 'boolean') return raw === 'true'
  return raw
}

function displayValue(value: ParamValue): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function ParameterField({
  handle,
  deviceLabel,
  value,
  onChange,
}: {
  handle: NodeTemplateHandle
  deviceLabel?: string
  value: ParamValue
  onChange: (next: ParamValue) => void
}) {
  const meta = useMemo(() => describeParameter(handle.dataKey, deviceLabel), [handle.dataKey, deviceLabel])
  const numeric = handle.type === 'integer' || handle.type === 'number'
  const options = meta.options && numeric ? meta.options : undefined

  return (
    <label className="wf-field">
      <span className="wf-field-label">
        <strong>{meta.label}</strong>
        <code>{handle.dataKey}</code>
        {handle.required ? <em className="wf-field-required">必填</em> : null}
      </span>
      {options ? (
        <select
          value={displayValue(value)}
          onChange={(event) => onChange(parseValue(event.target.value, handle))}
        >
          <option value="">未设置</option>
          {Object.entries(options).map(([optionValue, label]) => (
            <option key={optionValue} value={optionValue}>{optionValue} · {label}</option>
          ))}
        </select>
      ) : handle.type === 'boolean' ? (
        <select
          value={displayValue(value)}
          onChange={(event) => onChange(parseValue(event.target.value, handle))}
        >
          <option value="">未设置</option>
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
      ) : (
        <input
          type={numeric ? 'number' : 'text'}
          value={displayValue(value)}
          placeholder={handle.type || 'string'}
          onChange={(event) => onChange(parseValue(event.target.value, handle))}
        />
      )}
      <span className="wf-field-meta">
        {handle.type || 'string'}
        {meta.unit ? ` · ${meta.unit}` : ''}
        {meta.min !== undefined || meta.max !== undefined
          ? ` · 范围 ${meta.min ?? '−∞'}~${meta.max ?? '+∞'}`
          : ''}
      </span>
      {meta.hint ? <span className="wf-field-hint"><Info size={11} />{meta.hint}</span> : null}
    </label>
  )
}

/**
 * 右侧属性面板。
 *
 * 参数清单来自模板句柄而不是节点的 `param` 键：这样即便参数还没赋值，用户
 * 也能看到该动作完整可填的字段，中文名与单位由 `workflowParameterLabels`
 * 补齐。
 */
export function PropertyPanel({ node, edge, onPatchNode, onDeleteNode, onDeleteEdge }: PropertyPanelProps) {
  if (edge) {
    const raw = edge.data?.raw || {}
    return (
      <aside className="wf-props" aria-label="连线属性">
        <div className="wf-props-head"><span>连线</span><small>执行顺序依赖</small></div>
        <div className="wf-props-body">
          <dl className="wf-props-dl">
            <div><dt>源节点</dt><dd title={edge.source}>{edge.source}</dd></div>
            <div><dt>目标节点</dt><dd title={edge.target}>{edge.target}</dd></div>
            <div><dt>出口句柄</dt><dd>{String(raw.source_handle_uuid || edge.sourceHandle || '—')}</dd></div>
            <div><dt>入口句柄</dt><dd>{String(raw.target_handle_uuid || edge.targetHandle || '—')}</dd></div>
            <div><dt>连线 UUID</dt><dd title={edge.id}>{edge.id}</dd></div>
          </dl>
          <button type="button" className="wf-danger-btn" onClick={onDeleteEdge}>
            <Trash2 size={13} />删除这条连线
          </button>
        </div>
      </aside>
    )
  }

  if (!node) {
    return (
      <aside className="wf-props" aria-label="属性">
        <div className="wf-props-head"><span>属性</span><small>未选中</small></div>
        <div className="wf-props-body">
          <p className="wf-props-empty">
            点击画布中的节点编辑其名称、步骤说明与真实参数；点击连线可删除依赖。
          </p>
        </div>
      </aside>
    )
  }

  const raw = node.data.raw
  const param = (raw.param && typeof raw.param === 'object' ? raw.param : {}) as Record<string, unknown>
  // 指令号表按设备 **id** 索引（`community.gn_lab.gn_robotic_arm`），中文名只
  // 用于展示。这里取 id；旧模板没有 id 时由 commandDeviceKey 回落到显示名。
  const deviceLabel = node.data.template?.resourceName || node.data.template?.resourceDisplayName
  const editableHandles = sortHandlesForPanel(
    node.data.handles.filter((handle) => handle.ioType === 'target' && handle.handleKey !== READY_KEY),
  )

  return (
    <aside className="wf-props" aria-label="节点属性">
      <div className="wf-props-head">
        <span>节点属性</span>
        <small>{node.data.template?.displayName || '自定义节点'}</small>
      </div>
      <div className="wf-props-body">
        <label className="wf-field">
          <span className="wf-field-label"><strong>步骤名称</strong><code>name</code></span>
          <input
            value={String(raw.name || '')}
            onChange={(event) => onPatchNode({ name: event.target.value })}
          />
        </label>

        <label className="wf-field">
          <span className="wf-field-label"><strong>步骤说明</strong><code>description</code></span>
          <textarea
            rows={2}
            value={String(raw.description || '')}
            onChange={(event) => onPatchNode({ description: event.target.value })}
            placeholder="必填：后端要求每个节点都有可读说明"
          />
          <span className="wf-field-hint"><Info size={11} />整图保存时后端用它做节点展示，留空会被拒绝</span>
        </label>

        <label className="wf-field wf-field-inline">
          <input
            type="checkbox"
            checked={Boolean(raw.disabled)}
            onChange={(event) => onPatchNode({ disabled: event.target.checked })}
          />
          <span><Ban size={12} />停用该节点（保留在图中但不参与执行）</span>
        </label>

        <div className="wf-props-section">
          <span className="wf-props-section-title">参数（{editableHandles.length}）</span>
          {editableHandles.length === 0 ? (
            <p className="wf-props-empty">该动作没有可填参数。</p>
          ) : editableHandles.map((handle) => (
            <ParameterField
              key={handle.uuid}
              handle={handle}
              deviceLabel={deviceLabel}
              value={param[handle.dataKey]}
              onChange={(next) => {
                const nextParam = { ...param }
                if (next === null) delete nextParam[handle.dataKey]
                else nextParam[handle.dataKey] = next
                onPatchNode({ param: nextParam })
              }}
            />
          ))}
        </div>

        <dl className="wf-props-dl wf-props-meta">
          <div><dt>类型</dt><dd>{String(raw.type || '—')}</dd></div>
          <div><dt>设备</dt><dd>{deviceLabel || '—'}</dd></div>
          <div><dt>动作</dt><dd>{String(raw.action_name || '—')}</dd></div>
          <div><dt>模板 UUID</dt><dd title={String(raw.workflow_node_template_uuid || '')}>{String(raw.workflow_node_template_uuid || '—')}</dd></div>
          <div><dt>节点 UUID</dt><dd title={node.id}>{node.id}</dd></div>
        </dl>

        <button type="button" className="wf-danger-btn" onClick={onDeleteNode}>
          <Trash2 size={13} />删除该节点
        </button>
      </div>
    </aside>
  )
}
