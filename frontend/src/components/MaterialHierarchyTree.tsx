import { useMemo, useState } from 'react'
import { ChevronRight, Search } from 'lucide-react'
import type { MaterialRecord } from '../types'

interface TreeNode { material: MaterialRecord; children: TreeNode[] }

function buildTree(materials: MaterialRecord[]): TreeNode[] {
  const byParent = new Map<string, MaterialRecord[]>()
  const byId = new Set(materials.map((material) => material.uuid))
  const siteOwnerByOccupant = new Map<string, string>()
  materials.forEach((owner) => owner.sites.forEach((site) => {
    if (site.occupiedMaterialUuid) siteOwnerByOccupant.set(site.occupiedMaterialUuid, owner.uuid)
  }))
  materials.forEach((material) => {
    const siteOwner = siteOwnerByOccupant.get(material.uuid)
    const parent = siteOwner && byId.has(siteOwner)
      ? siteOwner
      : material.parentUuid && byId.has(material.parentUuid)
        ? material.parentUuid
        : '__root__'
    byParent.set(parent, [...(byParent.get(parent) || []), material])
  })
  const build = (material: MaterialRecord): TreeNode => ({
    material,
    children: (byParent.get(material.uuid) || []).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')).map(build),
  })
  return (byParent.get('__root__') || []).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')).map(build)
}

function filterTree(nodes: TreeNode[], query: string): TreeNode[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return nodes
  return nodes.flatMap((node) => {
    const children = filterTree(node.children, query)
    const own = [node.material.name, node.material.uuid, node.material.barcode, ...node.material.sites.map((site) => site.name)].join(' ').toLowerCase().includes(needle)
    return own || children.length ? [{ ...node, children }] : []
  })
}

export function MaterialHierarchyTree({ materials, selectedId, onSelect }: { materials: MaterialRecord[]; selectedId?: string; onSelect: (uuid: string) => void }) {
  const roots = useMemo(() => buildTree(materials), [materials])
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(roots.map((node) => node.material.uuid)))
  const visible = useMemo(() => filterTree(roots, query), [roots, query])
  return <aside className="electron-material-tree" aria-label="物料目录">
    <header><div><strong>物料列表</strong><span>({materials.length})</span></div></header>
    <label><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="检索物料、设备或库位" /></label>
    <div className="electron-tree-legend" aria-label="库位状态说明"><span><i className="occupied" />已占用</span><span><i className="empty" />未占用</span></div>
    <div className="electron-tree-rows" role="tree">{visible.map((node) => <TreeRow key={node.material.uuid} node={node} depth={0} queryActive={Boolean(query.trim())} expanded={expanded} selectedId={selectedId} onSelect={onSelect} onToggle={(uuid) => setExpanded((current) => { const next = new Set(current); next.has(uuid) ? next.delete(uuid) : next.add(uuid); return next })} />)}</div>
  </aside>
}

function TreeRow({ node, depth, queryActive, expanded, selectedId, onSelect, onToggle }: { node: TreeNode; depth: number; queryActive: boolean; expanded: Set<string>; selectedId?: string; onSelect: (uuid: string) => void; onToggle: (uuid: string) => void }) {
  const hasContent = node.children.length > 0 || node.material.sites.length > 0
  const open = hasContent && (queryActive || expanded.has(node.material.uuid))
  const occupiedSite = node.material.currentLocation.kind === 'site'
  const occupiedIds = new Set(node.material.sites.map((site) => site.occupiedMaterialUuid).filter(Boolean))
  const unboundChildren = node.children.filter((child) => !occupiedIds.has(child.material.uuid))
  return <>
    <div className={`electron-tree-row ${selectedId === node.material.uuid ? 'selected' : ''}`} role="treeitem" aria-level={depth + 1} aria-expanded={hasContent ? open : undefined} style={{ '--tree-depth': depth } as React.CSSProperties}>
      <span className="tree-grip">⠿</span>{hasContent ? <button className={`tree-toggle ${open ? 'open' : ''}`} aria-label={`${open ? '收起' : '展开'} ${node.material.name}`} onClick={() => onToggle(node.material.uuid)}><ChevronRight size={14} /></button> : <span className="tree-toggle-spacer" />}
      <button className="tree-label" onClick={() => onSelect(node.material.uuid)} title={node.material.name}>{node.material.name}</button>{occupiedSite ? <i className="site-dot occupied" title="已占用库位" /> : null}
    </div>
    {open ? <>{node.material.sites.map((site) => {
      const occupant = node.children.find((child) => child.material.uuid === site.occupiedMaterialUuid)
      return occupant ? <TreeRow key={occupant.material.uuid} node={occupant} depth={depth + 1} queryActive={queryActive} expanded={expanded} selectedId={selectedId} onSelect={onSelect} onToggle={onToggle} /> : <div key={site.uuid} className="electron-tree-row site-row" role="treeitem" aria-level={depth + 2} aria-label={`${site.name}，未占用`} style={{ '--tree-depth': depth + 1 } as React.CSSProperties}><span className="tree-grip" /><span className="tree-toggle-spacer" /><span className="tree-label">{site.name}</span><i className="site-dot empty" title="未占用" /></div>
    })}{unboundChildren.map((child) => <TreeRow key={child.material.uuid} node={child} depth={depth + 1} queryActive={queryActive} expanded={expanded} selectedId={selectedId} onSelect={onSelect} onToggle={onToggle} />)}</> : null}
  </>
}
