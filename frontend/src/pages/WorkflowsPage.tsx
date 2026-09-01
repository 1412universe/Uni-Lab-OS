import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowRight,
  Braces,
  Check,
  ChevronRight,
  CircleDot,
  Code2,
  FileInput,
  GitBranch,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Workflow as WorkflowIcon,
} from 'lucide-react'
import { loadWorkflowGraph, loadWorkflowPreflight } from '../lib/edgeClient'
import type { PageId, WorkflowDefinition } from '../types'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'

function tagForWorkflow(workflow: WorkflowDefinition) {
  if (workflow.tags[0]) return workflow.tags[0]
  return workflow.status === 'source' ? '源码定义' : workflow.status
}

function WorkflowNode({ node, index }: { node: Record<string, any>; index: number }) {
  const label = node.name || node.action_name || node.kind || `节点 ${index + 1}`
  const device = node.meta_data?.unilab?.executor_binding?.device_id || node.device_id
  return (
    <div className="workflow-flow-item">
      {index ? <span className="flow-connector"><ArrowRight size={15} /></span> : null}
      <button type="button" className="workflow-node">
        <span>{node.kind === 'material_source' ? <CircleDot size={17} /> : <Braces size={17} />}</span>
        <strong>{label}</strong>
        <small>{device || node.type || node.kind || 'workflow node'}</small>
      </button>
    </div>
  )
}

type ReadinessTone = 'ready' | 'warning' | 'error' | 'neutral' | 'loading'

interface ReadinessRow {
  label: string
  value: string
  tone: ReadinessTone
}

function ReadinessIcon({ tone }: { tone: ReadinessTone }) {
  if (tone === 'loading') return <LoaderCircle className="spin" size={12} />
  if (tone === 'ready') return <Check size={12} />
  if (tone === 'neutral') return <CircleDot size={12} />
  return <AlertCircle size={12} />
}

export function WorkflowsPage({
  workflows,
  connected,
  onNavigate,
  onNotify,
}: {
  workflows: WorkflowDefinition[]
  connected: boolean
  onNavigate: (page: PageId) => void
  onNotify: (message: string) => void
}) {
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(workflows[0]?.uuid || '')

  useEffect(() => {
    if (!workflows.some((workflow) => workflow.uuid === selectedId)) {
      setSelectedId(workflows[0]?.uuid || '')
    }
  }, [workflows, selectedId])

  const visibleWorkflows = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return workflows
    return workflows.filter((workflow) => [workflow.name, workflow.uuid, workflow.description]
      .join(' ')
      .toLowerCase()
      .includes(needle))
  }, [query, workflows])

  const selected = visibleWorkflows.find((workflow) => workflow.uuid === selectedId) || visibleWorkflows[0]
  const graphQuery = useQuery({
    queryKey: ['workflow-graph', selected?.uuid],
    queryFn: ({ signal }) => loadWorkflowGraph(selected!.uuid, signal),
    enabled: Boolean(selected),
    retry: false,
  })
  const preflightQuery = useQuery({
    queryKey: ['workflow-run-preflight', selected?.uuid, selected?.revision],
    queryFn: ({ signal }) => loadWorkflowPreflight(selected!.uuid, signal),
    enabled: false,
    retry: false,
    staleTime: 0,
  })

  const detail = graphQuery.data?.workflow || selected
  const graphNodes = graphQuery.data?.nodes || []
  const preflight = preflightQuery.data
  const preflightTone: ReadinessTone = preflightQuery.isFetching
    ? 'loading'
    : preflightQuery.isError
      ? 'error'
      : preflight?.status === 'runnable_now'
        ? 'ready'
        : preflight?.status === 'invalid'
          ? 'error'
          : preflight
            ? 'warning'
            : 'neutral'
  const preflightStatusLabel = preflight?.status === 'runnable_now'
    ? '当前可提交，派发时仍会复核'
    : preflight?.status === 'temporarily_unavailable'
      ? '当前条件暂不可用'
      : preflight?.status === 'invalid'
        ? '执行计划无效'
        : preflightQuery.isError
          ? (preflightQuery.error instanceof Error ? preflightQuery.error.message : '检查失败')
          : preflightQuery.isFetching
            ? '正在读取 Edge 报告'
            : '尚未检查'
  const readinessRows: ReadinessRow[] = preflight
    ? [
        { label: '候选状态', value: preflightStatusLabel, tone: preflightTone },
        {
          label: '执行计划',
          value: `r${preflight.workflowRevision} · ${preflight.summary.executionNodeCount} 个节点`,
          tone: preflight.status === 'invalid' ? 'error' : 'ready',
        },
        {
          label: '阻塞检查',
          value: `${preflight.summary.blockingCheckCount} 项`,
          tone: preflight.summary.blockingCheckCount ? 'error' : 'ready',
        },
        {
          label: '延后与人工门禁',
          value: `${preflight.summary.deferredCheckCount} 项延后 · ${preflight.summary.confirmationRequiredCount} 项人工确认`,
          tone: preflight.summary.deferredCheckCount || preflight.summary.confirmationRequiredCount ? 'warning' : 'ready',
        },
      ]
    : [
        { label: '候选状态', value: preflightStatusLabel, tone: preflightTone },
        { label: '执行计划', value: '尚未检查', tone: 'neutral' },
        { label: '资源门禁', value: '尚未检查', tone: 'neutral' },
        { label: '人工确认', value: '尚未检查', tone: 'neutral' },
      ]

  return (
    <div className="page workflows-page">
      <PageHeader
        eyebrow="WORKFLOW DEFINITIONS"
        title="工作流"
        description="管理 Edge 已加载定义、发布修订、运行合同和工作流节点拓扑。"
        actions={
          <>
            <Button icon={<FileInput size={16} />} onClick={() => onNotify('Python 导入将调用工作流作者接口，当前保持只读')}>导入 Python</Button>
            <Button tone="primary" icon={<Plus size={17} />} onClick={() => onNotify('新建工作流将在作者工作台中开放')}>新建工作流</Button>
          </>
        }
      />

      <div className="workflow-layout">
        <Panel className="workflow-library">
          <PanelHeader title="工作流目录" description={`${workflows.length} 个 Edge 定义`} action={<WorkflowIcon size={18} />} />
          <label className="search-field">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索名称或 UUID" />
          </label>
          <div className="workflow-list">
            {visibleWorkflows.map((workflow) => (
              <button
                type="button"
                className={`workflow-list-item ${selected?.uuid === workflow.uuid ? 'active' : ''}`}
                key={workflow.uuid}
                onClick={() => setSelectedId(workflow.uuid)}
              >
                <span className="workflow-list-icon"><GitBranch size={17} /></span>
                <div><strong>{workflow.name}</strong><small>r{workflow.revision} · {workflow.nodeCount || '—'} 节点</small></div>
                <em>{tagForWorkflow(workflow)}</em>
              </button>
            ))}
            {!visibleWorkflows.length ? <EmptyState title="没有匹配的工作流" description="调整搜索词后重试。" /> : null}
          </div>
          <div className="library-summary">
            <div><span>已发布</span><strong>{workflows.filter((item) => item.status !== 'draft').length}</strong></div>
            <div><span>输入合同</span><strong>{workflows.reduce((sum, item) => sum + item.inputContract.length, 0)}</strong></div>
          </div>
        </Panel>

        {detail ? (
          <div className="workflow-main-column">
            <Panel className="workflow-definition">
              <div className="definition-hero">
                <span className="definition-icon"><WorkflowIcon size={22} /></span>
                <div><span className="definition-tag">{tagForWorkflow(detail)}</span><h2>{detail.name}</h2><p>{detail.description}</p></div>
                <div className="definition-actions">
                  <Button tone="ghost" icon={<Code2 size={15} />} onClick={() => onNotify(detail.sourcePath ? `源文件：${detail.sourcePath}` : '该定义没有可编辑源文件')}>查看源码</Button>
                  <Button tone="primary" icon={<Play size={15} />} onClick={() => onNavigate('tasks')}>运行</Button>
                </div>
              </div>
              <div className="definition-metrics">
                <div><span>工作流 UUID</span><code>{detail.uuid}</code></div>
                <div><span>应用修订</span><strong>r{detail.revision}</strong></div>
                <div><span>节点规模</span><strong>{graphNodes.length || detail.nodeCount || '—'} 个节点</strong></div>
                <div><span>来源</span><code>{detail.sourcePath || 'Edge runtime'}</code></div>
              </div>
              <div className="workflow-canvas">
                <div className="canvas-toolbar"><span><GitBranch size={15} />发布修订拓扑</span><small>{graphQuery.isFetching ? '正在读取图…' : graphQuery.isError ? '图接口不可用，显示定义摘要' : 'Edge 权威图'}</small></div>
                <div className="workflow-flow">
                  <div className="workflow-flow-item">
                    <button type="button" className="workflow-node boundary"><span><FileInput size={17} /></span><strong>运行输入</strong><small>{detail.inputContract.length} 个参数</small></button>
                  </div>
                  {graphNodes.slice(0, 8).map((node, index) => <WorkflowNode key={String(node.uuid || index)} node={node} index={index + 1} />)}
                  <div className="workflow-flow-item">
                    <span className="flow-connector"><ArrowRight size={15} /></span>
                    <button type="button" className="workflow-node boundary"><span><Check size={17} /></span><strong>结果汇总</strong><small>{detail.outputContract.length} 个输出</small></button>
                  </div>
                </div>
              </div>
            </Panel>

            <section className="workflow-bottom-grid">
              <Panel className="contract-panel">
                <PanelHeader title="输入合同" description="创建任务时允许提交的参数" action={<span className="count-badge">{detail.inputContract.length}</span>} />
                <div className="contract-list">
                  {detail.inputContract.length ? detail.inputContract.map((field) => (
                    <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>{field.required ? '必填' : `默认 ${field.defaultValue ?? '—'}`}</em></div>
                  )) : <EmptyState title="没有输入参数" description="该工作流可直接运行。" />}
                </div>
              </Panel>
              <Panel className="contract-panel">
                <PanelHeader title="输出合同" description="任务完成后的正式结果" action={<span className="count-badge">{detail.outputContract.length}</span>} />
                <div className="contract-list">
                  {detail.outputContract.length ? detail.outputContract.map((field) => (
                    <div key={field.name}><code>{field.name}</code><span>{field.type}</span><em>正式输出</em></div>
                  )) : <EmptyState title="没有正式输出" description="结果保留在 Job return_info。" />}
                </div>
              </Panel>
            </section>
          </div>
        ) : <Panel><EmptyState title="没有工作流定义" description="确认 Edge 已加载工作流运行时。" /></Panel>}

        <aside className="workflow-aside">
          <Panel className="readiness-panel">
            <PanelHeader title="运行准备" description="Edge 只读快照，不创建 Task 或取得资源" action={<span className={`ready-orb readiness-${preflightTone}`}><ShieldCheck size={15} /></span>} />
            {readinessRows.map(({ label, value, tone }) => (
              <div className={`readiness-row readiness-${tone}`} key={label}><span><ReadinessIcon tone={tone} /></span><strong>{label}</strong><small>{value}</small></div>
            ))}
            <Button
              className="full-button"
              icon={preflightQuery.isFetching ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}
              disabled={!connected || !selected || preflightQuery.isFetching}
              onClick={() => {
                void preflightQuery.refetch().then((result) => {
                  onNotify(result.isError ? 'Preflight 读取失败' : 'Preflight 报告已更新')
                })
              }}
            >{preflight ? '刷新 Preflight' : '运行 Preflight'}</Button>
          </Panel>

          <Panel className="revision-panel">
            <PanelHeader title="版本信息" description="当前加载修订" />
            <div className="revision-current"><span>r{detail?.revision || 1}</span><div><strong>{detail ? tagForWorkflow(detail) : '—'}</strong><small>Edge 当前版本</small></div><ChevronRight size={16} /></div>
            <dl className="property-list compact">
              <div><dt>状态</dt><dd>{detail?.status || '—'}</dd></div>
              <div><dt>源码</dt><dd>{detail?.sourcePath?.split('/').at(-1) || 'runtime'}</dd></div>
              <div><dt>节点</dt><dd>{graphNodes.length || detail?.nodeCount || 0}</dd></div>
            </dl>
          </Panel>
        </aside>
      </div>
    </div>
  )
}
