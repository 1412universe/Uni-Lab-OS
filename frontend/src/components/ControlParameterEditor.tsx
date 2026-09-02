import { Workflow } from 'lucide-react'

interface EditableControlNode {
  id: string
  nodeUuid?: string
  name: string
  templateUuid: string
  param: Record<string, unknown>
  parameterSchema: Record<string, unknown>
}

interface ControlParameterEditorProps {
  controls: EditableControlNode[]
  expandedId: string
  drafts: Record<string, string>
  onToggle: (id: string) => void
  onDraftChange: (id: string, value: string) => void
  onCommit: (control: EditableControlNode, value: string) => void
}

/** 展示并编辑结构控制节点的完整 param；保存时交给 OS 执行计划做最终校验。 */
export function ControlParameterEditor({ controls, expandedId, drafts, onToggle, onDraftChange, onCommit }: ControlParameterEditorProps) {
  if (!controls.length) return null
  return <section className="node-parameter-editor control-parameter-editor" aria-label="工作流控制节点参数">
    <header><div><strong>控制节点参数</strong><span>条件和循环参数由调度器从工作流图读取</span></div></header>
    {controls.map((control) => <article key={control.id} className={expandedId === control.id ? 'selected' : ''}>
      <button type="button" className="control-parameter-heading" onClick={() => onToggle(control.id)}>
        <Workflow size={15} /><strong>{control.name}</strong><code>{control.templateUuid}</code>
        <span>{String(control.parameterSchema.description || '结构化参数')}</span>
      </button>
      {expandedId === control.id ? <div className="control-parameter-body">
        <p>{String(control.parameterSchema.description || '参数会原样写入工作流节点的 param 字段，保存时由 OS 执行计划校验。')}</p>
        <textarea
          aria-label={`${control.name} 参数 JSON`}
          value={drafts[control.id] ?? JSON.stringify(control.param, null, 2)}
          onChange={(event) => onDraftChange(control.id, event.target.value)}
          onBlur={(event) => onCommit(control, event.target.value)}
        />
        <small>条件常用字段：variables、bindings、branches；循环常用字段：max_iterations、initial_carry、next_carry、until、bindings。</small>
      </div> : null}
    </article>)}
  </section>
}
