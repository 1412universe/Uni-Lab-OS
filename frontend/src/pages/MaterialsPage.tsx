import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Barcode,
  Boxes,
  CircleCheck,
  Download,
  MapPin,
} from 'lucide-react'
import type { MaterialRecord } from '../types'
import { changeMaterialSite, instantiateMaterial, loadMaterialDetail, loadResourceTemplates, verifyMaterialBarcode } from '../lib/edgeClient'
import { Button, EmptyState, Panel } from '../components/ui'
import { LabObliqueOverview } from '../components/LabObliqueOverview'
import { MaterialHierarchyTree } from '../components/MaterialHierarchyTree'

export function MaterialsPage({
  materials,
  connected,
  onNotify,
  onRefresh,
}: {
  materials: MaterialRecord[]
  total: number
  connected: boolean
  onNotify: (message: string) => void
  onRefresh?: () => void
}) {
  const [selectedId, setSelectedId] = useState(materials[0]?.uuid || '')
  const [selectedSiteId, setSelectedSiteId] = useState('')
  const [dialog, setDialog] = useState<'barcode' | 'instantiate' | 'place' | 'remove' | null>(null)
  const [barcodeInput, setBarcodeInput] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [materialForm, setMaterialForm] = useState({ name: '', barcode: '', description: '', siteUuid: '' })
  const [placementMaterialId, setPlacementMaterialId] = useState('')

  useEffect(() => {
    if (!materials.some((material) => material.uuid === selectedId)) {
      setSelectedId(materials[0]?.uuid || '')
    }
  }, [materials, selectedId])

  const selected = materials.find((material) => material.uuid === selectedId) || materials[0]
  useEffect(() => {
    setSelectedSiteId(selected?.currentLocation.kind === 'site' ? selected.currentLocation.siteUuid : '')
  }, [selected?.uuid])
  const detailQuery = useQuery({
    queryKey: ['material-detail', selected?.uuid],
    queryFn: ({ signal }) => loadMaterialDetail(selected!.uuid, signal),
    enabled: connected && Boolean(selected),
    retry: 1,
  })
  const templatesQuery = useQuery({ queryKey: ['resource-templates'], queryFn: ({ signal }) => loadResourceTemplates(signal), enabled: connected, retry: 1 })
  const materialTemplates = useMemo(() => (templatesQuery.data || []).filter((template) => template.resourceType !== 'device'), [templatesQuery.data])
  const barcodeQuery = useQuery({ queryKey: ['barcode-verification', barcodeInput], queryFn: ({ signal }) => verifyMaterialBarcode(barcodeInput.trim(), signal), enabled: false, retry: false })
  const verifiedMaterials = barcodeQuery.data?.map((result) => materials.find((material) => material.uuid === result.uuid) || result)
  const detail = detailQuery.data
    ? {
        ...detailQuery.data,
        taskReferences: selected?.taskReferences || detailQuery.data.taskReferences,
        sites: selected?.sites?.length ? selected.sites : detailQuery.data.sites,
        isStructural: selected?.isStructural ?? detailQuery.data.isStructural,
        siteCount: selected?.siteCount ?? detailQuery.data.siteCount,
        currentLocation: selected?.currentLocation ?? detailQuery.data.currentLocation,
      }
    : selected
  const selectedOwnerUuid = detail?.currentLocation.kind === 'site'
    ? detail.currentLocation.ownerMaterialUuid
    : ''
  const siteContainers = detail
    ? (detail.isStructural
        ? [detail]
        : selectedOwnerUuid
          ? materials.filter((material) => material.uuid === selectedOwnerUuid)
          : [])
    : []
  const availableSites = siteContainers.flatMap((container) => container.sites)
  const selectedSite = availableSites.find((site) => site.uuid === selectedSiteId) || availableSites[0]
  const inventoryCandidates = materials.filter((material) => !material.isStructural && material.currentLocation.kind === 'unassigned')
  const emptySiteOptions = useMemo(() => materials.flatMap((owner) => owner.sites
    .filter((site) => !site.occupiedMaterialUuid)
    .map((site) => ({ ...site, ownerName: owner.name }))), [materials])
  const selectedTemplate = materialTemplates.find((template) => template.uuid === templateId)

  function exportInventory() {
    const header = ['UUID', '名称', '条码', '类型', '权威位置', '修订']
    const rows = materials.map((material) => [material.uuid, material.name, material.barcode, material.category, material.currentLocation.label, String(material.revision)])
    const csv = [header, ...rows].map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `unilab-inventory-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click(); URL.revokeObjectURL(url)
    onNotify(`已导出 ${materials.length} 条权威物料记录`)
  }

  async function submitInstantiation() {
    if (!templateId || !materialForm.name.trim() || !materialForm.barcode.trim()) return
    try {
      await instantiateMaterial({ resourceTemplateUuid: templateId, ...materialForm, siteUuid: materialForm.siteUuid || undefined })
      const targetSite = emptySiteOptions.find((site) => site.uuid === materialForm.siteUuid)
      onNotify(targetSite ? `已实例化“${materialForm.name}”并上料至 ${targetSite.ownerName} / ${targetSite.name}` : `已从模板实例化物料：${materialForm.name}`); setDialog(null); onRefresh?.()
    } catch (error) { onNotify(`实例化失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  async function submitPlacement(remove = false) {
    const material = remove ? materials.find((item) => item.uuid === selectedSite?.occupiedMaterialUuid) : materials.find((item) => item.uuid === placementMaterialId)
    if (!material || (!remove && !selectedSite)) return
    try {
      await changeMaterialSite(material.uuid, material.revision, remove ? undefined : selectedSite?.uuid)
      onNotify(remove ? `已从 ${selectedSite?.name} 下料` : `已上料至 ${selectedSite?.name}`); setDialog(null); onRefresh?.()
    } catch (error) { onNotify(`库位操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  return (
    <div className="page materials-page">
      <div className="materials-layout scene-layout">
        <MaterialHierarchyTree materials={materials} selectedId={selected?.uuid} onSelect={setSelectedId} />

        <Panel className="material-table-panel">
          <div className="table-toolbar">
            <div><strong>实验室 2.5D</strong><span>{materials.length} 个对象</span></div>
            <div className="material-toolbar-actions"><button onClick={exportInventory}><Download size={13} />导出盘点</button><button onClick={() => { setBarcodeInput(''); setDialog('barcode') }}><Barcode size={13} />扫码核验</button></div>
          </div>
          <div className="material-scene-view" aria-label="物料 2.5D 库位场景">
              <LabObliqueOverview materials={materials} templates={templatesQuery.data || []} selectedId={selected?.uuid} onSelect={setSelectedId} onSelectMaterialTemplate={(template) => { setTemplateId(template.uuid); setMaterialForm({ name: template.displayName, barcode: '', description: '', siteUuid: selectedSite && !selectedSite.occupiedMaterialUuid ? selectedSite.uuid : '' }); setDialog('instantiate') }} />
              {availableSites.length ? <section className="scene-site-strip"><header><div><strong>{detail?.name} · 详细库位</strong><small>{siteContainers.map((container) => container.name).join(' / ')} · 点击库位后可执行上下料</small></div><span>{availableSites.length} 个</span></header><div>{availableSites.map((site) => <button key={site.uuid} className={selectedSite?.uuid === site.uuid ? 'selected' : ''} onClick={() => setSelectedSiteId(site.uuid)}><MapPin size={13} /><strong>{site.name}</strong><small>{site.occupiedMaterialName || '空库位'}</small></button>)}</div>{selectedSite ? <footer><span>{selectedSite.occupiedMaterialUuid ? `已占用：${selectedSite.occupiedMaterialName}` : '当前库位空闲'}</span><Button disabled={Boolean(selectedSite.occupiedMaterialUuid)} onClick={() => setDialog('place')}>上料</Button><Button disabled={!selectedSite.occupiedMaterialUuid} onClick={() => setDialog('remove')}>下料</Button></footer> : null}</section> : null}
          </div>
          {!materials.length ? <EmptyState title="暂无物料" description="当前环境尚未加载物料资源。" /> : null}
        </Panel>
      </div>
      {dialog ? <div className="dialog-backdrop" role="presentation"><div className="material-write-dialog" role="dialog" aria-modal="true">
        <header><div><span>MATERIAL COMMAND</span><h2>{dialog === 'barcode' ? '扫码核验' : dialog === 'instantiate' ? '从模板实例化物料' : dialog === 'place' ? `上料至 ${selectedSite?.name}` : `从 ${selectedSite?.name} 下料`}</h2></div><button onClick={() => setDialog(null)}>×</button></header>
        {dialog === 'barcode' ? <div className="dialog-content"><label className="form-field"><span>扫描或输入条码</span><input autoFocus value={barcodeInput} onChange={(event) => setBarcodeInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && barcodeInput.trim()) void barcodeQuery.refetch() }} placeholder="扫描枪回车或手动输入" /></label><Button tone="primary" disabled={!barcodeInput.trim() || barcodeQuery.isFetching} onClick={() => void barcodeQuery.refetch()}>校验条码</Button>{verifiedMaterials ? <div className="barcode-result">{verifiedMaterials.length ? verifiedMaterials.map((item) => <div key={item.uuid}><CircleCheck size={18} /><p><strong>{item.name}</strong><small>{item.currentLocation.label}</small><code>{item.uuid}</code></p></div>) : <EmptyState title="未找到该条码" description="可检查条码后重试，或从物料模板实例化。" />}</div> : null}</div> : null}
        {dialog === 'instantiate' ? <div className="dialog-content"><div className="dialog-template-summary"><Boxes size={20} /><div><strong>{selectedTemplate?.displayName}</strong><code>{selectedTemplate?.uuid}</code></div></div><label className="form-field"><span>物料名称</span><input value={materialForm.name} onChange={(event) => setMaterialForm((current) => ({ ...current, name: event.target.value }))} /></label><label className="form-field"><span>唯一条码</span><input value={materialForm.barcode} onChange={(event) => setMaterialForm((current) => ({ ...current, barcode: event.target.value }))} /></label><label className="form-field"><span>初始库位</span><select value={materialForm.siteUuid} onChange={(event) => setMaterialForm((current) => ({ ...current, siteUuid: event.target.value }))}><option value="">暂不分配库位</option>{emptySiteOptions.map((site) => <option key={site.uuid} value={site.uuid}>{site.ownerName} / {site.name}</option>)}</select><small>选择后，实例化与上料将在同一事务中完成。</small></label><label className="form-field"><span>说明</span><input value={materialForm.description} onChange={(event) => setMaterialForm((current) => ({ ...current, description: event.target.value }))} /></label><Button tone="primary" disabled={!materialForm.name.trim() || !materialForm.barcode.trim()} onClick={() => void submitInstantiation()}>确认实例化</Button></div> : null}
        {dialog === 'place' ? <div className="dialog-content"><p className="write-warning">系统会通过 expected_revision 原子校验物料与库位，冲突时不会覆盖他人操作。</p><label className="form-field"><span>选择待上料物料</span><select value={placementMaterialId} onChange={(event) => setPlacementMaterialId(event.target.value)}><option value="">选择未分配库存</option>{inventoryCandidates.map((item) => <option value={item.uuid} key={item.uuid}>{item.name} · {item.barcode}</option>)}</select></label><Button tone="primary" disabled={!placementMaterialId} onClick={() => void submitPlacement()}>确认上料</Button></div> : null}
        {dialog === 'remove' ? <div className="dialog-content"><p className="write-warning">下料后物料进入“待分配库存”，不会删除物料实例。</p><Button tone="primary" onClick={() => void submitPlacement(true)}>确认下料</Button></div> : null}
      </div></div> : null}
    </div>
  )
}
