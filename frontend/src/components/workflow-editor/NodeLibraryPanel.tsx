import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search, Workflow } from 'lucide-react'
import type { NodeTemplateSummary } from '../../lib/edgeClient'

interface NodeLibraryPanelProps {
  templates: NodeTemplateSummary[]
  loading: boolean
  error?: string
  onAdd: (template: NodeTemplateSummary) => void
}

interface LibraryGroup {
  key: string
  label: string
  items: NodeTemplateSummary[]
}

function typeBadge(template: NodeTemplateSummary): string {
  if (template.nodeType === 'condition') return '条件'
  if (template.nodeType === 'repeat_until') return '循环'
  return '动作'
}

/**
 * 左侧节点库：按「设备 → 节点」两级浏览。
 *
 * 设备名直接取模板内嵌的资源模板显示名（机械手 / 离心机 / 固体加样…），
 * 与现有工作流页的设备口径一致，不需要前端再维护设备清单。
 */
export function NodeLibraryPanel({ templates, loading, error, onAdd }: NodeLibraryPanelProps) {
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const groups = useMemo<LibraryGroup[]>(() => {
    const keyword = query.trim().toLowerCase()
    const matched = keyword
      ? templates.filter((item) =>
        item.displayName.toLowerCase().includes(keyword)
        || item.name.toLowerCase().includes(keyword)
        || (item.resourceDisplayName || '').toLowerCase().includes(keyword))
      : templates
    const bucket = new Map<string, LibraryGroup>()
    for (const item of matched) {
      const key = item.resourceTemplateUuid || 'generic'
      const label = item.resourceDisplayName || '通用节点'
      const group = bucket.get(key) || { key, label, items: [] }
      group.items.push(item)
      bucket.set(key, group)
    }
    return [...bucket.values()]
      .map((group) => ({
        ...group,
        items: [...group.items].sort((left, right) => left.displayName.localeCompare(right.displayName, 'zh-CN')),
      }))
      .sort((left, right) => right.items.length - left.items.length || left.label.localeCompare(right.label, 'zh-CN'))
  }, [templates, query])

  return (
    <aside className="wf-lib" aria-label="节点库">
      <div className="wf-lib-head">
        <span className="wf-lib-title"><Workflow size={14} />节点库</span>
        <small>{templates.length} 个模板</small>
      </div>
      <label className="wf-lib-search">
        <Search size={13} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索设备 / 动作"
          aria-label="搜索节点模板"
        />
      </label>

      <div className="wf-lib-body">
        {loading ? <p className="wf-lib-hint">正在读取节点模板…</p> : null}
        {error ? <p className="wf-lib-hint wf-lib-error">{error}</p> : null}
        {!loading && !error && groups.length === 0 ? (
          <p className="wf-lib-hint">没有匹配的节点模板。</p>
        ) : null}
        {groups.map((group) => {
          const isCollapsed = Boolean(collapsed[group.key])
          return (
            <section className="wf-lib-group" key={group.key}>
              <button
                type="button"
                className="wf-lib-group-head"
                onClick={() => setCollapsed((current) => ({ ...current, [group.key]: !isCollapsed }))}
                aria-expanded={!isCollapsed}
              >
                {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                <strong>{group.label}</strong>
                <em>{group.items.length}</em>
              </button>
              {isCollapsed ? null : (
                <ul className="wf-lib-items">
                  {group.items.map((item) => (
                    <li key={item.uuid}>
                      <button
                        type="button"
                        className="wf-lib-item"
                        draggable
                        onDragStart={(event) => {
                          event.dataTransfer.setData('application/unilab-node-template', item.uuid)
                          event.dataTransfer.effectAllowed = 'move'
                        }}
                        onClick={() => onAdd(item)}
                        title={`${item.displayName}（${item.type}）`}
                      >
                        <span>{item.displayName}</span>
                        <em>{typeBadge(item)}</em>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
      <p className="wf-lib-tip">拖到画布放置，或点击直接追加到画布末尾。</p>
    </aside>
  )
}
