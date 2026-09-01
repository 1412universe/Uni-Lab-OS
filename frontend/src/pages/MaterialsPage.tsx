import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Archive,
  Barcode,
  Boxes,
  ChevronRight,
  CircleCheck,
  Download,
  FlaskConical,
  MapPin,
  PackageSearch,
  Search,
  ShieldCheck,
} from 'lucide-react'
import type { MaterialRecord } from '../types'
import { loadMaterialDetail } from '../lib/edgeClient'
import { Button, EmptyState, PageHeader, Panel, PanelHeader, StatusBadge } from '../components/ui'

const categoryLabels: Record<string, string> = {
  beaker: '烧杯',
  sample_vial: '样品瓶',
  powder: '粉体',
  liquid_reagent: '液体试剂',
  consumable: '耗材',
  material: '其他物料',
}

function materialTaskReferenceLabel(material: MaterialRecord): string {
  const count = material.taskReferences.length
  return count ? `${count} 个未结束任务` : '无未结束任务引用'
}

function MaterialIcon({ category }: { category: string }) {
  const Icon = category.includes('liquid') || category.includes('powder') ? FlaskConical : Boxes
  return <Icon size={18} />
}

export function MaterialsPage({
  materials,
  total,
  connected,
  onNotify,
}: {
  materials: MaterialRecord[]
  total: number
  connected: boolean
  onNotify: (message: string) => void
}) {
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('all')
  const [selectedId, setSelectedId] = useState(materials[0]?.uuid || '')

  useEffect(() => {
    if (!materials.some((material) => material.uuid === selectedId)) {
      setSelectedId(materials[0]?.uuid || '')
    }
  }, [materials, selectedId])

  const categories = useMemo(() => {
    const counts = new Map<string, number>()
    materials.forEach((material) => counts.set(material.category, (counts.get(material.category) || 0) + 1))
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [materials])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return materials.filter((material) => {
      const categoryMatch = category === 'all' || material.category === category
      const textMatch = !needle || [material.name, material.uuid, material.barcode, material.currentLocation.label]
        .join(' ')
        .toLowerCase()
        .includes(needle)
      return categoryMatch && textMatch
    })
  }, [materials, query, category])

  const selected = filtered.find((material) => material.uuid === selectedId) || filtered[0]
  const detailQuery = useQuery({
    queryKey: ['material-detail', selected?.uuid],
    queryFn: ({ signal }) => loadMaterialDetail(selected!.uuid, signal),
    enabled: connected && Boolean(selected),
    retry: 1,
  })
  const detail = detailQuery.data
    ? {
        ...detailQuery.data,
        taskReferences: selected?.taskReferences || detailQuery.data.taskReferences,
      }
    : selected
  const detailAuthoritative = Boolean(detailQuery.data)

  return (
    <div className="page materials-page">
      <PageHeader
        eyebrow="MATERIAL AUTHORITY"
        title="物料与库存"
        description="从统一物料账本查看身份、权威位置、当前任务引用与来源图，页面数据直接来自 Edge。"
        actions={
          <>
            <Button icon={<Download size={16} />} onClick={() => onNotify('盘点导出将在文件服务接入后开放')}>导出盘点</Button>
            <Button tone="primary" icon={<Barcode size={17} />} onClick={() => onNotify('扫码核验入口已就绪，等待扫码设备接入')}>扫码核验</Button>
          </>
        }
      />

      <section className="materials-stats" aria-label="物料概览">
        <div><span><Archive size={18} /></span><p><small>物料总量</small><strong>{total}</strong></p></div>
        <div><span><CircleCheck size={18} /></span><p><small>已分配权威库位</small><strong>{materials.filter((item) => item.currentLocation.kind === 'site').length}</strong></p></div>
        <div><span><ShieldCheck size={18} /></span><p><small>有未结束任务引用</small><strong>{materials.filter((item) => item.taskReferences.length > 0).length}</strong></p></div>
        <div><span><PackageSearch size={18} /></span><p><small>未结束任务引用</small><strong>{materials.reduce((sum, item) => sum + item.taskReferences.length, 0)}</strong></p></div>
      </section>

      <div className="materials-layout">
        <Panel className="material-filter-panel">
          <PanelHeader title="物料视图" description="按业务类别筛选" />
          <label className="search-field">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、UUID、条码或位置" />
          </label>
          <div className="filter-section">
            <span>物料类型</span>
            <button className={category === 'all' ? 'active' : ''} onClick={() => setCategory('all')}>
              <b>全部物料</b><em>{materials.length}</em>
            </button>
            {categories.map(([key, count]) => (
              <button key={key} className={category === key ? 'active' : ''} onClick={() => setCategory(key)}>
                <b>{categoryLabels[key] || key}</b><em>{count}</em>
              </button>
            ))}
          </div>
          <div className="filter-note">
            <ShieldCheck size={16} />
            <div><strong>权威来源</strong><small>Edge Material Aggregate</small></div>
          </div>
        </Panel>

        <Panel className="material-table-panel">
          <div className="table-toolbar">
            <div><strong>物料实例</strong><span>当前显示 {filtered.length} / {total}</span></div>
            <div className="segmented"><button className="active">列表</button><button onClick={() => onNotify('库位拓扑视图将在下一阶段接入')}>库位</button></div>
          </div>
          {filtered.length ? (
            <div className="table-scroll">
              <table className="data-table material-table">
                <thead><tr><th>物料</th><th>类型</th><th>权威当前位置</th><th>未结束任务引用</th><th>条码</th><th>更新时间</th></tr></thead>
                <tbody>
                  {filtered.map((material) => (
                    <tr
                      key={material.uuid}
                      className={selected?.uuid === material.uuid ? 'selected' : ''}
                      onClick={() => setSelectedId(material.uuid)}
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') setSelectedId(material.uuid)
                      }}
                    >
                      <td><span className="entity-icon"><MaterialIcon category={material.category} /></span><div><strong>{material.name}</strong><code>{material.uuid}</code></div></td>
                      <td>{categoryLabels[material.category] || material.category}</td>
                      <td><span className="location-cell"><MapPin size={13} />{material.currentLocation.label}</span></td>
                      <td><span className={`material-status ${material.taskReferences.length ? 'material-referenced' : 'material-not-referenced'}`}>{materialTaskReferenceLabel(material)}</span></td>
                      <td><code>{material.barcode}</code></td>
                      <td>{material.updatedAt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState title="没有匹配的物料" description="调整搜索词或选择其他物料类型。" />}
        </Panel>

        <Panel className="material-inspector">
          {detail ? (
            <>
              <div className="inspector-heading">
                <span className="entity-icon large"><MaterialIcon category={detail.category} /></span>
                <div><small>{categoryLabels[detail.category] || detail.category}</small><h2>{detail.name}</h2><code>{detail.uuid}</code></div>
                <span className={`material-status ${detail.taskReferences.length ? 'material-referenced' : 'material-not-referenced'}`}>{materialTaskReferenceLabel(detail)}</span>
              </div>
              <div className="inspector-block">
                <h3>
                  权威当前位置
                  {detailQuery.isFetching ? <small>更新中…</small> : null}
                  {detailQuery.isError ? <small className="detail-error">详情读取失败</small> : null}
                </h3>
                <div className="location-path"><span>{detailAuthoritative ? 'Edge Material Detail' : 'Edge Material Graph'}</span><ChevronRight size={15} /><strong>{detail.currentLocation.label}</strong></div>
                <dl className="property-list">
                  <div><dt>父物料 UUID</dt><dd>{detail.parentUuid || '根节点'}</dd></div>
                  <div><dt>物料类别</dt><dd>{detail.category}</dd></div>
                  <div><dt>资源类</dt><dd title={detail.className}>{detail.className.split('.').at(-1)}</dd></div>
                  <div><dt>来源图</dt><dd>{detail.sourceGraph || '—'}</dd></div>
                  <div><dt>配置来源位置</dt><dd>{detail.configuredSource}</dd></div>
                </dl>
              </div>
              <div className="inspector-block">
                <h3>未结束任务引用（调度投影）</h3>
                <p className="task-reference-note">这里只表示 Task/Job 载荷引用该 Material UUID，不证明任务物料预留、作业执行占用或库位占用。</p>
                {detail.taskReferences.length ? (
                  <div className="material-task-references">
                    {detail.taskReferences.map((reference) => (
                      <div key={reference.taskUuid}>
                        <div><strong>{reference.workflowName}</strong><code>{reference.taskUuid}</code><small>{reference.sample}</small></div>
                        <StatusBadge status={reference.taskStatus} />
                      </div>
                    ))}
                  </div>
                ) : <EmptyState title="无未结束任务引用" description="这不代表物料一定可用；正式准入仍由 Scheduler 与 Inventory 原子判断。" />}
              </div>
              <div className="inspector-block">
                <h3>资源谱系</h3>
                <div className="lineage">
                  <div className="done"><span><CircleCheck size={14} /></span><p><strong>资源图加载</strong><small>{detail.sourceGraph || 'runtime inventory'}</small></p></div>
                  <div className="done"><span><CircleCheck size={14} /></span><p><strong>物料实例化</strong><small>{detail.barcode}</small></p></div>
                  <div className="current"><span><MapPin size={14} /></span><p><strong>{detail.currentLocation.label}</strong><small>当前权威位置</small></p></div>
                </div>
              </div>
            </>
          ) : <EmptyState title="选择一条物料" description="物料详情将在这里显示。" />}
        </Panel>
      </div>
    </div>
  )
}
