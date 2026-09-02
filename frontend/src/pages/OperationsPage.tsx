import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ChevronLeft, ChevronRight, Eye, FlaskConical, GripVertical, Pencil, Plus, Save, Search, Send, Sparkles, Trash2, Workflow } from 'lucide-react'
import { createExperimentOperation, deleteExperimentOperation, loadActionParameters, loadActionTemplates, loadExperimentOperations, loadOperationCategories, loadWorkflowGraph, publishExperimentOperation, updateExperimentOperation } from '../lib/edgeClient'
import type { ActionParameterRecord, ActionTemplateRecord, MaterialRecord, WorkflowDefinition } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'

interface DraftAction { id: string; nodeUuid?: string; templateUuid: string; materialUuid: string; deviceId: string; name: string; fields: ActionParameterRecord[]; param: Record<string, unknown>; inputBindings: Record<string, { parameter: string }> }

function parseParameterValue(raw: string, schema: Record<string, unknown>): unknown {
  if (!raw.trim()) return undefined
  if (schema.type === 'integer') return Number.parseInt(raw, 10)
  if (schema.type === 'number') return Number(raw)
  if (schema.type === 'boolean') return raw === 'true'
  if (schema.type === 'object' || schema.type === 'array' || schema.$slot) return JSON.parse(raw)
  return raw
}

function actionValue(value: unknown): string {
  if (value === undefined || value === null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value)
}

export function OperationsPage({ materials, connected, onNotify }: { materials: MaterialRecord[]; connected: boolean; onNotify: (message: string) => void }) {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  const [editingUuid, setEditingUuid] = useState('')
  const [selectedOperation, setSelectedOperation] = useState<WorkflowDefinition | null>(null)
  const [selectedOperationNodes, setSelectedOperationNodes] = useState<Array<{ uuid: string; name: string; materialUuid: string }>>([])
  const [selectedDeviceUuid, setSelectedDeviceUuid] = useState('')
  const [query, setQuery] = useState('')
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [categoryUuid, setCategoryUuid] = useState('')
  const [draft, setDraft] = useState<DraftAction[]>([])
  const [saving, setSaving] = useState(false)
  const [expandedActionId, setExpandedActionId] = useState('')
  const operationsQuery = useQuery({ queryKey: ['experiment-operations'], queryFn: ({ signal }) => loadExperimentOperations(signal), enabled: connected })
  const categoriesQuery = useQuery({ queryKey: ['operation-categories'], queryFn: ({ signal }) => loadOperationCategories(signal), enabled: connected })
  const actionsQuery = useQuery({ queryKey: ['action-templates'], queryFn: ({ signal }) => loadActionTemplates(signal), enabled: connected })
  const deviceGroups = useMemo(() => {
    const groups = new Map<string, { uuid: string; name: string; actions: ActionTemplateRecord[] }>()
    for (const action of actionsQuery.data || []) {
      const uuid = action.resourceTemplate.uuid
      if (!uuid) continue
      const group = groups.get(uuid) || { uuid, name: action.resourceTemplate.displayName || action.resourceTemplate.name, actions: [] }
      group.actions.push(action); groups.set(uuid, group)
    }
    return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  }, [actionsQuery.data])
  const selectedDevice = deviceGroups.find((device) => device.uuid === selectedDeviceUuid) || deviceGroups[0]
  const visibleActions = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (selectedDevice?.actions || []).filter((action) => !needle || [action.displayName, action.name].join(' ').toLowerCase().includes(needle))
  }, [query, selectedDevice])
  const missingBinding = draft.some((action) => !action.materialUuid || !action.deviceId)
  const missingParameter = draft.some((action) => action.fields.some((field) => field.required && action.param[field.key] === undefined && !action.inputBindings[field.handleUuid]?.parameter))
  const workflowParameters = [...new Set(draft.flatMap((action) => Object.values(action.inputBindings).map((binding) => binding.parameter).filter(Boolean)))]
  const saveProblem = !name.trim() ? '请填写操作名称' : !draft.length ? '请从左侧加入至少一个 Action' : missingBinding ? '存在未绑定设备实例的步骤' : missingParameter ? '存在未配置的必填节点参数' : ''

  function beginCreate() { setCreating(true); setEditingUuid(''); setSelectedOperation(null); setName(''); setDescription(''); setCategoryUuid(''); setDraft([]) }
  async function viewOperation(operation: WorkflowDefinition) {
    setSelectedOperation(operation)
    setSelectedOperationNodes([])
    try {
      const graph = await loadWorkflowGraph(operation.uuid)
      setSelectedOperation(graph.workflow)
      setSelectedOperationNodes(graph.nodes.map((node) => ({ uuid: String(node.uuid), name: String(node.name || node.uuid), materialUuid: String(node.material_uuid || '') })))
    } catch (error) { onNotify(`读取实验操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }
  async function beginEdit(operation: WorkflowDefinition) {
    try {
      const graph = await loadWorkflowGraph(operation.uuid)
      const actions = await Promise.all(graph.nodes.filter((node) => node.workflow_node_template_uuid).map(async (node) => ({
        id: crypto.randomUUID(), nodeUuid: String(node.uuid), templateUuid: String(node.workflow_node_template_uuid), materialUuid: String(node.material_uuid || ''), deviceId: String(node.meta_data?.unilab?.executor_binding?.device_id || ''), name: String(node.name || ''),
        fields: await loadActionParameters(String(node.workflow_node_template_uuid)), param: { ...(node.param || {}) }, inputBindings: { ...(node.meta_data?.unilab?.input_bindings || {}) },
      })))
      setEditingUuid(operation.uuid); setCreating(true); setSelectedOperation(null); setName(operation.name); setDescription(operation.description); setCategoryUuid(operation.operationCategoryUuid || ''); setDraft(actions)
    } catch (error) { onNotify(`读取实验操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }
  async function addAction(templateUuid: string) {
    const template = actionsQuery.data?.find((item) => item.uuid === templateUuid)
    if (!template) return
    const device = materials.find((material) => material.resourceTemplateUuid === template.resourceTemplate.uuid)
    try {
      const fields = await loadActionParameters(templateUuid)
      const id = crypto.randomUUID()
      const param = Object.fromEntries(fields.filter((field) => field.schema.default !== undefined).map((field) => [field.key, field.schema.default]))
      setDraft((current) => [...current, { id, templateUuid, materialUuid: device?.uuid || '', deviceId: device?.sourceNodeId || '', name: template.displayName, fields, param, inputBindings: {} }])
      setExpandedActionId(id)
    } catch (error) { onNotify(`读取 Action 参数失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }
  function moveAction(index: number, direction: -1 | 1) {
    const nextIndex = index + direction
    if (nextIndex < 0 || nextIndex >= draft.length) return
    setDraft((current) => { const next = [...current]; [next[index], next[nextIndex]] = [next[nextIndex], next[index]]; return next })
  }
  async function submit() {
    if (saveProblem) { onNotify(saveProblem); return }
    setSaving(true)
    try {
      if (editingUuid) {
        await updateExperimentOperation({ workflowUuid: editingUuid, name: name.trim(), description: description.trim(), categoryUuid: categoryUuid || undefined, actions: draft.filter((action) => action.nodeUuid).map(({ nodeUuid, materialUuid, deviceId, name: actionName, param, inputBindings }) => ({ nodeUuid: nodeUuid!, materialUuid, deviceId, name: actionName, param, inputBindings })) })
        onNotify(`实验操作“${name}”已保存，状态已退回 source`)
      } else {
        await createExperimentOperation({ name: name.trim(), description: description.trim(), categoryUuid: categoryUuid || undefined, actions: draft.map(({ templateUuid, materialUuid, deviceId, name: actionName, param, inputBindings }) => ({ templateUuid, materialUuid, deviceId, name: actionName, param, inputBindings })) })
        onNotify(`实验操作“${name}”已保存为 source，可确认后发布`)
      }
      setCreating(false); setDraft([])
      await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] })
      await queryClient.invalidateQueries({ queryKey: ['edge-snapshot'] })
    } catch (error) { onNotify(`保存失败：${error instanceof Error ? error.message : '未知错误'}`) } finally { setSaving(false) }
  }

  async function publish(operation: WorkflowDefinition) {
    try { await publishExperimentOperation(operation.uuid, operation.revision); onNotify(`实验操作“${operation.name}”已发布`); await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] }) }
    catch (error) { onNotify(`发布失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  async function remove(operation: WorkflowDefinition) {
    if (!window.confirm(`确认删除实验操作“${operation.name}”？该操作不可恢复。`)) return
    try { await deleteExperimentOperation(operation.uuid); setSelectedOperation(null); onNotify(`已删除实验操作“${operation.name}”`); await queryClient.invalidateQueries({ queryKey: ['experiment-operations'] }) }
    catch (error) { onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  const expandedAction = draft.find((action) => action.id === expandedActionId)

  return <div className="page operations-page">
    <PageHeader eyebrow="EXPERIMENT OPERATIONS" title="实验室操作" description="实验操作是由设备 Action 节点组成、可被普通工作流引用的子工作流。" actions={creating ? <Button icon={<ChevronLeft size={15} />} onClick={() => setCreating(false)}>返回实验操作</Button> : <Button tone="primary" icon={<Sparkles size={16} />} onClick={beginCreate}>创建实验操作</Button>} />
    <div className={`operation-authoring-layout ${creating ? 'is-creating' : ''}`}>
      <Panel className="device-action-browser"><PanelHeader title="设备模板" description={`${deviceGroups.length} 个设备 · ${actionsQuery.data?.length || 0} 个 Action`} />
        <label className="search-field"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索当前设备的 Action" /></label>
        <div className="device-template-list">{deviceGroups.map((device) => <button key={device.uuid} className={selectedDevice?.uuid === device.uuid ? 'active' : ''} onClick={() => { setSelectedDeviceUuid(device.uuid); setQuery('') }}><span><FlaskConical size={15} /></span><div><strong>{device.name}</strong><small>{device.actions.length} 个 Action</small></div><ChevronRight size={13} /></button>)}</div>
        {selectedDevice ? <section className="device-actions"><header><strong>{selectedDevice.name}</strong><span>{visibleActions.length} 个 Action</span></header>{visibleActions.map((action) => <button key={action.uuid} disabled={Boolean(editingUuid)} title={editingUuid ? '编辑模式当前只修改既有节点' : undefined} onClick={() => creating && !editingUuid ? void addAction(action.uuid) : undefined}><div><strong>{action.displayName}</strong><code>{action.name}</code></div>{creating && !editingUuid ? <Plus size={14} /> : null}</button>)}</section> : null}
      </Panel>
      {creating ? <Panel className="operation-builder"><div className="builder-heading"><div><span>EXPERIMENT OPERATION EDITOR</span><h2>{editingUuid ? '编辑实验操作' : '创建实验操作'}</h2>{editingUuid ? <code>{editingUuid}</code> : null}<p>{editingUuid ? '保存修改后状态退回 source，需要重新发布。' : '选择设备 Action 形成顺序节点；保存后发布为可复用子工作流。'}</p></div><div className="builder-heading-actions"><strong>{draft.length} 个节点</strong><Button tone="primary" icon={<Save size={15} />} disabled={Boolean(saveProblem) || saving} title={saveProblem} onClick={() => void submit()}>{saving ? '正在保存…' : '保存实验操作'}</Button></div></div>
        <div className="operation-meta"><label><span>操作名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：S09 移液并混匀" /></label><label><span>操作类别</span><select value={categoryUuid} onChange={(event) => setCategoryUuid(event.target.value)}><option value="">未分类</option>{categoriesQuery.data?.map((category) => <option key={category.uuid} value={category.uuid}>{category.name}</option>)}</select></label><label className="wide"><span>说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述该实验操作的目的和约束" /></label></div>
        <div className="save-requirements" data-ready={!saveProblem || undefined}>{saveProblem || `配置完整 · ${workflowParameters.length} 个工作流参数`}</div>
        {workflowParameters.length ? <div className="workflow-parameter-summary"><strong>工作流参数</strong>{workflowParameters.map((parameter) => <code key={parameter}>{parameter}</code>)}</div> : null}
        <div className="operation-canvas" aria-label="实验操作节点画布">{draft.map((item, index) => { const template = actionsQuery.data?.find((action) => action.uuid === item.templateUuid); const devices = materials.filter((material) => material.resourceTemplateUuid === template?.resourceTemplate.uuid && material.sourceNodeId); const requiredCount = item.fields.filter((field) => field.required).length; return <div className="operation-canvas-item" key={item.id}><article className={`operation-canvas-node ${expandedActionId === item.id ? 'selected' : ''}`}><header><span>{String(index + 1).padStart(2, '0')}</span><GripVertical size={13} /><div className="step-controls"><button aria-label={`上移 ${item.name}`} disabled={index === 0} onClick={() => moveAction(index, -1)}>←</button><button aria-label={`下移 ${item.name}`} disabled={index === draft.length - 1} onClick={() => moveAction(index, 1)}>→</button><button aria-label={`删除节点 ${item.name}`} onClick={() => setDraft((current) => current.filter((action) => action.id !== item.id))}>×</button></div></header><button className="node-body" onClick={() => setExpandedActionId(item.id)}><FlaskConical size={17} /><strong>{item.name}</strong><small>{template?.resourceTemplate.displayName || '未知设备模板'}</small><span>{requiredCount} 必填 · {item.fields.length - requiredCount} 选填</span></button><select aria-label={`设备实例 ${item.name}`} value={item.materialUuid} onChange={(event) => { const device = devices.find((candidate) => candidate.uuid === event.target.value); setDraft((current) => current.map((action) => action.id === item.id ? { ...action, materialUuid: device?.uuid || '', deviceId: device?.sourceNodeId || '' } : action)) }}><option value="">选择设备实例</option>{devices.map((device) => <option value={device.uuid} key={device.uuid}>{device.name}</option>)}</select><button className="node-port" aria-label={`配置参数 ${item.name}`} onClick={() => setExpandedActionId(item.id)}>参数</button></article>{index < draft.length - 1 ? <span className="canvas-edge"><ArrowRight size={16} /><small>ready</small></span> : null}</div> })}{!draft.length ? <EmptyState title="尚未添加工作流节点" description="从左侧选择设备，再点击设备下的 Action 加入实验操作。" /> : null}</div>
        {expandedAction ? <div className="node-parameter-editor"><header><div><strong>{expandedAction.name} · 节点参数</strong><span>固定值或绑定工作流参数</span></div><button aria-label="关闭节点参数" onClick={() => setExpandedActionId('')}>×</button></header>{expandedAction.fields.length ? expandedAction.fields.map((field) => { const binding = expandedAction.inputBindings[field.handleUuid]; const source = binding ? 'workflow' : 'literal'; return <div className="node-parameter-row" key={field.handleUuid}><label><span>{field.displayName}<em data-required={field.required}>{field.required ? '必填' : '选填'}</em></span><code>{field.key}</code></label><select value={source} onChange={(event) => setDraft((current) => current.map((action) => { if (action.id !== expandedAction.id) return action; const nextBindings = { ...action.inputBindings }; if (event.target.value === 'workflow') nextBindings[field.handleUuid] = { parameter: field.key }; else delete nextBindings[field.handleUuid]; return { ...action, inputBindings: nextBindings } }))}><option value="literal">固定值</option><option value="workflow">工作流参数</option></select>{source === 'workflow' ? <input aria-label={`工作流参数 ${field.key}`} value={binding?.parameter || ''} onChange={(event) => setDraft((current) => current.map((action) => action.id === expandedAction.id ? { ...action, inputBindings: { ...action.inputBindings, [field.handleUuid]: { parameter: event.target.value } } } : action))} placeholder={field.required ? '必填：参数名称' : '选填：参数名称'} /> : <input aria-label={`节点参数 ${field.key}`} value={actionValue(expandedAction.param[field.key])} onChange={(event) => { try { const value = parseParameterValue(event.target.value, field.schema); setDraft((current) => current.map((action) => action.id === expandedAction.id ? { ...action, param: { ...action.param, [field.key]: value } } : action)) } catch { /* 保留上一个有效 JSON */ } }} placeholder={`${field.required ? '必填' : '选填'} · ${String(field.schema.type || field.schema.$slot || 'string')}`} />}</div> }) : <EmptyState title="该 Action 没有输入参数" description="输出端口和 ready 控制依赖由模板定义。" />}</div> : null}
      </Panel> : <Panel className="operation-library operation-home"><PanelHeader title="实验操作" description={`${operationsQuery.data?.length || 0} 个可复用子工作流`} action={<Workflow size={17} />} /><div className="operation-list">{operationsQuery.data?.map((operation) => <article key={operation.uuid} className={selectedOperation?.uuid === operation.uuid ? 'selected' : ''}><span><Workflow size={16} /></span><div><strong>{operation.name}</strong><small>r{operation.revision} · {operation.status === 'published' ? '已发布' : 'source'}</small><code>{operation.uuid}</code></div><div className="operation-row-actions"><button title="查看" aria-label={`查看 ${operation.name} ${operation.uuid}`} onClick={() => void viewOperation(operation)}><Eye size={13} /></button><button title="编辑" aria-label={`编辑 ${operation.name} ${operation.uuid}`} onClick={() => void beginEdit(operation)}><Pencil size={13} /></button>{operation.status !== 'published' ? <button title="发布" aria-label={`发布 ${operation.name} ${operation.uuid}`} onClick={() => void publish(operation)}><Send size={13} /></button> : null}<button className="danger" title="删除" aria-label={`删除 ${operation.name} ${operation.uuid}`} onClick={() => void remove(operation)}><Trash2 size={13} /></button></div></article>)}{!operationsQuery.data?.length ? <EmptyState title="暂无实验操作" description="点击右上角“创建实验操作”，从设备 Action 开始编排。" /> : null}</div>{selectedOperation ? <aside className="operation-detail"><header><div><small>实验操作详情</small><h3>{selectedOperation.name}</h3><code>{selectedOperation.uuid}</code></div><button aria-label="关闭详情" onClick={() => setSelectedOperation(null)}>×</button></header><dl><div><dt>状态</dt><dd>{selectedOperation.status}</dd></div><div><dt>修订</dt><dd>r{selectedOperation.revision}</dd></div><div><dt>节点</dt><dd>{selectedOperation.nodeCount}</dd></div><div><dt>类别</dt><dd>{selectedOperation.operationCategoryUuid || '未分类'}</dd></div></dl><p>{selectedOperation.description || '暂无说明'}</p><section className="operation-detail-nodes"><strong>节点明细</strong>{selectedOperationNodes.map((node, index) => <div key={node.uuid}><span>{String(index + 1).padStart(2, '0')}</span><p><strong>{node.name}</strong><code>{node.uuid}</code><small>{node.materialUuid || '未绑定设备实例'}</small></p></div>)}</section><footer><Button icon={<Pencil size={13} />} onClick={() => void beginEdit(selectedOperation)}>编辑</Button>{selectedOperation.status !== 'published' ? <Button tone="primary" icon={<Send size={13} />} onClick={() => void publish(selectedOperation)}>发布</Button> : null}<Button icon={<Trash2 size={13} />} onClick={() => void remove(selectedOperation)}>删除</Button></footer></aside> : null}</Panel>}
    </div>
  </div>
}
