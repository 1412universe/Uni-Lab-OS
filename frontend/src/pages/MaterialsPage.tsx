import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Barcode,
  Boxes,
  CircleCheck,
  Download,
  MapPin,
  PackagePlus,
} from 'lucide-react'
import type { MaterialRecord } from '../types'
import { CapacityField, ReagentCapacityFields } from '../components/CapacityFields'
import { capacityBasisError, capacityText, capacityValue, configuredCapacity, maximumError, parseMaximum } from '../lib/capacity'
import { loadReagents, updateMaterialCapacity } from '../lib/edgeClient'
import { changeMaterialSite, instantiateMaterial, loadMaterialDetail, loadResourceTemplates, verifyMaterialBarcode } from '../lib/edgeClient'
import { Button, EmptyState, Panel } from '../components/ui'
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
  const [dialog, setDialog] = useState<'barcode' | 'instantiate' | 'place' | 'remove' | 'capacity' | null>(null)
  const [capacityForm, setCapacityForm] = useState({ maximum: '', unit: 'mL' })
  const [capacityError, setCapacityError] = useState('')
  const [capacitySaving, setCapacitySaving] = useState(false)
  const [barcodeInput, setBarcodeInput] = useState('')
  const [templateId, setTemplateId] = useState('')
  const [materialForm, setMaterialForm] = useState({ name: '', barcode: '', description: '', siteUuid: '' })
  const [placementMaterialId, setPlacementMaterialId] = useState('')
  const reagentsQuery = useQuery({ queryKey: ['reagents'], queryFn: ({ signal }) => loadReagents(signal), enabled: connected, retry: 1 })
  const containerReagent = reagentsQuery.data?.find((reagent) => reagent.materialUuid === selectedId)

  useEffect(() => {
    if (!materials.some((material) => material.uuid === selectedId)) {
      setSelectedId(materials[0]?.uuid || '')
    }
  }, [materials, selectedId])

  const selected = materials.find((material) => material.uuid === selectedId) || materials[0]
  useEffect(() => {
    setSelectedSiteId(selected?.sites[0]?.uuid || '')
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
  const availableSites = detail?.sites || []
  const selectedSite = availableSites.find((site) => site.uuid === selectedSiteId) || availableSites[0]
  const parentMaterial = detail?.parentUuid ? materials.find((material) => material.uuid === detail.parentUuid) : undefined
  const locationOwnerUuid = detail?.currentLocation.kind === 'site' ? detail.currentLocation.ownerMaterialUuid : undefined
  const locationOwner = locationOwnerUuid ? materials.find((material) => material.uuid === locationOwnerUuid) : undefined
  const childMaterials = detail ? materials.filter((material) => material.parentUuid === detail.uuid) : []
  const allowedTemplateIds = selectedSite?.allowedResourceTemplateUuids || []
  const inventoryCandidates = materials.filter((material) => {
    if (material.isStructural || material.currentLocation.kind !== 'unassigned') return false
    return !allowedTemplateIds.length || Boolean(material.resourceTemplateUuid && allowedTemplateIds.includes(material.resourceTemplateUuid))
  })
  const emptySiteOptions = useMemo(() => materials.flatMap((owner) => owner.sites
    .filter((site) => !site.occupiedMaterialUuid)
    .map((site) => ({ ...site, ownerName: owner.name }))), [materials])
  const selectedTemplate = materialTemplates.find((template) => template.uuid === templateId)
  const allowedTemplateNames = allowedTemplateIds.map((uuid) => ({
    uuid,
    name: materialTemplates.find((template) => template.uuid === uuid)?.displayName || uuid,
  }))
  const ratedCapacity = detail?.ratedCapacity ?? containerReagent?.ratedCapacity
  const currentMaximum = containerReagent?.maximumCapacity ?? detail?.capacity
  const emptyCapacityUnits = ['mL', 'g']
  const configuredMaximum = configuredCapacity(detail?.config?.capacity)
  const capacityUnit = containerReagent?.quantityUnit || (configuredMaximum?.max_mass_g != null || currentMaximum?.max_mass_g != null && currentMaximum.max_volume_ul == null ? 'g' : 'mL')
  const maximumInUnit = capacityValue(currentMaximum, capacityUnit, containerReagent)
  const maximumDisplay = containerReagent && maximumInUnit ? `${maximumInUnit} ${capacityUnit}` : capacityText(currentMaximum)
  const capacityReady = connected && !reagentsQuery.isPending && !reagentsQuery.isError && !detailQuery.isPending && !detailQuery.isError
  const canClearMaximum = !containerReagent && configuredMaximum != null && Object.keys(configuredMaximum).length > 0
  const capacityBasis = containerReagent
    ? capacityBasisError(capacityForm.unit, ratedCapacity, containerReagent)
    : ''
  const capacityValidation = validateCapacityForm()

  function validateCapacityForm() {
    if (containerReagent) {
      if (capacityForm.unit !== containerReagent.quantityUnit) return '最大装料量须沿用当前试剂的计量单位。'
      if (containerReagent.quantity == null || !Number.isFinite(containerReagent.quantity)) return '当前试剂余量尚未读取，请刷新后重试。'
      return maximumError(containerReagent.quantity, capacityForm.unit, currentMaximum, ratedCapacity, capacityForm.maximum, containerReagent)
    }
    if (capacityBasis) return capacityBasis
    try {
      const requested = parseMaximum(capacityForm.maximum, capacityForm.unit)
      const maximum = Number(capacityValue(requested, capacityForm.unit))
      const ratedValue = capacityValue(ratedCapacity, capacityForm.unit)
      const rated = Number(ratedValue)
      return ratedValue && maximum > rated + Math.max(1e-9, rated * 1e-12) ? `最大装料量不能超过容器额定容量 ${rated} ${capacityForm.unit}。` : ''
    } catch (error) { return error instanceof Error ? error.message : '最大装料量无效。' }
  }

  function openCapacity() {
    if (!capacityReady) return
    const unit = containerReagent ? containerReagent.quantityUnit || '' : capacityUnit
    setCapacityForm({ maximum: capacityValue(currentMaximum, unit, containerReagent) || capacityValue(currentMaximum, unit), unit })
    setCapacityError('')
    setDialog('capacity')
  }

  function openInstantiation() {
    if (!connected) return
    const template = selectedTemplate || materialTemplates[0]
    setTemplateId(template?.uuid || '')
    setMaterialForm({ name: template?.displayName || '', barcode: '', description: '', siteUuid: selectedSite && !selectedSite.occupiedMaterialUuid ? selectedSite.uuid : '' })
    setDialog('instantiate')
  }

  function exportInventory() {
    const header = ['UUID', '名称', '条码', '类型', '权威位置', '修订']
    const rows = materials.map((material) => [material.uuid, material.name, material.barcode, material.category, material.currentLocation.label, String(material.revision)])
    const csv = [header, ...rows].map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(',')).join('\n')
    const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `unilab-inventory-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click(); URL.revokeObjectURL(url)
    onNotify(`已导出 ${materials.length} 条权威物料记录`)
  }

  async function submitInstantiation() {
    if (!connected) return
    if (!templateId || !materialForm.name.trim() || !materialForm.barcode.trim()) return
    try {
      await instantiateMaterial({ resourceTemplateUuid: templateId, ...materialForm, siteUuid: materialForm.siteUuid || undefined })
      const targetSite = emptySiteOptions.find((site) => site.uuid === materialForm.siteUuid)
      onNotify(targetSite ? `已实例化“${materialForm.name}”并上料至 ${targetSite.ownerName} / ${targetSite.name}` : `已从模板实例化物料：${materialForm.name}`); setDialog(null); onRefresh?.()
    } catch (error) { onNotify(`实例化失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  async function submitPlacement(remove = false) {
    if (!connected) return
    const material = remove ? materials.find((item) => item.uuid === selectedSite?.occupiedMaterialUuid) : materials.find((item) => item.uuid === placementMaterialId)
    if (!material || (!remove && !selectedSite)) return
    try {
      await changeMaterialSite(material.uuid, material.revision, remove ? undefined : selectedSite?.uuid)
      onNotify(remove ? `已从 ${selectedSite?.name} 下料` : `已上料至 ${selectedSite?.name}`); setDialog(null); onRefresh?.()
    } catch (error) { onNotify(`库位操作失败：${error instanceof Error ? error.message : '未知错误'}`) }
  }

  function openPlacementDialog() {
    if (!connected) return
    setPlacementMaterialId('')
    setDialog('place')
  }

  function openRemovalDialog() {
    if (!connected) return
    if (!selectedSite?.occupiedMaterialUuid) {
      onNotify(`库位“${selectedSite?.name || '未选择'}”上没有物料，无法下料`)
      return
    }
    setDialog('remove')
  }

  async function saveCapacity(clear = false) {
    if (!capacityReady || !detail || capacitySaving) return
    if (clear ? !canClearMaximum : Boolean(capacityValidation)) {
      setCapacityError(clear ? '当前容器不能清除自定义上限。' : capacityValidation)
      return
    }
    setCapacitySaving(true)
    try {
      await updateMaterialCapacity(detail, clear ? {} : parseMaximum(capacityForm.maximum, capacityForm.unit))
      await Promise.all([detailQuery.refetch(), reagentsQuery.refetch()])
      setDialog(null); onRefresh?.(); onNotify('最大装料量已保存')
    } catch (error) { setCapacityError(error instanceof Error ? error.message : '保存失败') }
    finally { setCapacitySaving(false) }
  }

  return (
    <div className="page materials-page">
      <div className="materials-layout scene-layout">
        <MaterialHierarchyTree materials={materials} selectedId={selected?.uuid} onSelect={setSelectedId} />

        <Panel className="material-table-panel">
          <div className="table-toolbar">
            <div><strong>物料详情</strong><span>{materials.length} 个权威对象</span></div>
            <div className="material-toolbar-actions"><button disabled={!connected} onClick={openInstantiation}><PackagePlus size={13} />从模板实例化</button><button onClick={exportInventory}><Download size={13} />导出盘点</button><button onClick={() => { setBarcodeInput(''); setDialog('barcode') }}><Barcode size={13} />扫码核验</button></div>
          </div>
          {detail ? <div className="material-detail-workspace" aria-label="物料关系详情">
            <header className="material-detail-hero"><span><Boxes size={24} /></span><div><small>{detail.isStructural ? '结构资源' : '物料实例'}</small><h2>{detail.name}</h2><code>{detail.uuid}</code></div><em data-kind={detail.currentLocation.kind}>{detail.currentLocation.label}</em></header>
            <dl className="material-detail-facts"><div><dt>物料类型</dt><dd>{detail.category}</dd></div><div><dt>条码</dt><dd>{detail.barcode}</dd></div><div><dt>资源模板</dt><dd>{templatesQuery.data?.find((template) => template.uuid === detail.resourceTemplateUuid)?.displayName || detail.resourceTemplateUuid || '未绑定'}</dd></div><div><dt>修订</dt><dd>r{detail.revision}</dd></div><div><dt>父物料</dt><dd>{parentMaterial?.name || '无父物料'}</dd></div><div><dt>所在库位</dt><dd>{detail.currentLocation.kind === 'site' ? `${locationOwner?.name || detail.currentLocation.ownerMaterialUuid} / ${detail.currentLocation.label}` : detail.currentLocation.label}</dd></div></dl>
            {templatesQuery.data?.find((template) => template.uuid === detail.resourceTemplateUuid)?.tags?.includes('container') ? <section className="capacity-section"><strong>最大装料量：{maximumDisplay}</strong><small>容器额定容量：{capacityText(ratedCapacity)}</small><Button disabled={!capacityReady} onClick={openCapacity}>设置最大装料量</Button></section> : null}
            <section className="material-relation-section"><header><div><strong>物料关系</strong><small>parent 与库位占用关系</small></div></header><div className="material-relation-chain">{parentMaterial ? <button onClick={() => setSelectedId(parentMaterial.uuid)}><small>父物料</small><strong>{parentMaterial.name}</strong><code>{parentMaterial.uuid}</code></button> : <div className="relation-empty"><small>父物料</small><strong>无</strong></div>}<span>→</span><div className="relation-current"><small>当前物料</small><strong>{detail.name}</strong><code>{detail.uuid}</code></div><span>→</span><div><small>直接子物料</small><strong>{childMaterials.length} 个</strong></div></div>{childMaterials.length ? <div className="material-child-list">{childMaterials.map((child) => <button key={child.uuid} onClick={() => setSelectedId(child.uuid)}><Boxes size={14} /><span><strong>{child.name}</strong><small>{child.currentLocation.label}</small></span><code>{child.uuid}</code></button>)}</div> : null}</section>
            <section className="material-site-section"><header><div><strong>自身库位</strong><small>该物料直接提供的库位及当前占用</small></div><span>{availableSites.length} 个库位</span></header>{availableSites.length ? <><div className="material-site-grid">{availableSites.map((site) => <button key={site.uuid} className={selectedSite?.uuid === site.uuid ? 'selected' : ''} onClick={() => setSelectedSiteId(site.uuid)}><MapPin size={14} /><span><strong>{site.name}</strong><small>{site.occupiedMaterialName || '空库位'}</small></span><em data-occupied={Boolean(site.occupiedMaterialUuid)}>{site.occupiedMaterialUuid ? '已占用' : '空闲'}</em></button>)}</div>{selectedSite ? <><div className="material-site-policy"><div><strong>库位允许放置的物料</strong><small>{allowedTemplateIds.length ? '仅允许以下物料模板的实例' : '未限制物料模板，可放置任意非结构物料'}</small></div><div className="material-site-policy-tags">{allowedTemplateNames.length ? allowedTemplateNames.map((template) => <span key={template.uuid} title={template.uuid}>{template.name}</span>) : <span>全部物料模板</span>}</div></div><footer><div><strong>{selectedSite.name}</strong><small>{selectedSite.occupiedMaterialName ? `当前物料：${selectedSite.occupiedMaterialName}` : '当前没有物料'}</small></div><Button disabled={!connected || Boolean(selectedSite.occupiedMaterialUuid)} onClick={openPlacementDialog}>上料</Button><Button disabled={!connected} onClick={openRemovalDialog}>下料</Button></footer></> : null}</> : <EmptyState title="该物料没有库位" description="它是可被放置的物料实例，不是库位容器或结构资源。" />}</section>
          </div> : null}
          {!materials.length ? <EmptyState title="暂无物料" description="当前环境尚未加载物料资源。" /> : null}
        </Panel>
      </div>
      {dialog ? <div className="dialog-backdrop" role="presentation"><div className="material-write-dialog" role="dialog" aria-modal="true">
        <header><div><span>MATERIAL COMMAND</span><h2>{dialog === 'capacity' ? '设置最大装料量' : dialog === 'barcode' ? '扫码核验' : dialog === 'instantiate' ? '从模板实例化物料' : dialog === 'place' ? `上料至 ${selectedSite?.name}` : `从 ${selectedSite?.name} 下料`}</h2></div><button onClick={() => setDialog(null)}>×</button></header>
        {dialog === 'capacity' && detail ? <div className="dialog-content">
          <strong>{detail.name}</strong>
          <label className="form-field"><span>计量单位</span><select aria-label="最大装料量单位" value={capacityForm.unit} disabled={Boolean(containerReagent) || capacitySaving} onChange={(event) => {
            setCapacityForm({ maximum: capacityValue(currentMaximum, event.target.value), unit: event.target.value })
            setCapacityError('')
          }}>{containerReagent ? <option value={capacityForm.unit}>{capacityForm.unit || '未记录'}</option> : emptyCapacityUnits.map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select>{containerReagent ? <small>沿用当前试剂的计量单位，最大装料量不得低于现有余量。</small> : null}</label>
          {containerReagent ? <ReagentCapacityFields maximum={capacityForm.maximum} unit={capacityForm.unit} current={currentMaximum} rated={ratedCapacity} context={containerReagent} onChange={(maximum) => { setCapacityForm({ ...capacityForm, maximum }); setCapacityError('') }} error={capacityError || capacityValidation} disabled={!capacityReady || capacitySaving} /> : <section className="capacity-section reagent-capacity-section" aria-label="最大装料量设置">
            <div className="capacity-rated"><span>容器额定容量</span><strong>{capacityText(ratedCapacity)}</strong></div>
            <CapacityField value={capacityForm.maximum} unit={capacityForm.unit} defaultValue={capacityValue(ratedCapacity, capacityForm.unit)} onChange={(maximum) => { setCapacityForm({ ...capacityForm, maximum }); setCapacityError('') }} disabled={!capacityReady || capacitySaving} hint={!capacityValue(ratedCapacity, capacityForm.unit) ? '未提供此单位的额定规格，可手动设置最大装料量。' : undefined} />
            {capacityError || capacityValidation ? <small className="form-error" role="alert">{capacityError || capacityValidation}</small> : null}
          </section>}
          {capacityBasis && currentMaximum && Object.keys(currentMaximum).length ? <p>现有最大装料量：{capacityText(currentMaximum)}</p> : null}
          {canClearMaximum ? <Button disabled={!capacityReady || capacitySaving} onClick={() => void saveCapacity(true)}>清除自定义上限</Button> : null}
          <Button tone="primary" disabled={!capacityReady || capacitySaving || Boolean(capacityValidation)} onClick={() => void saveCapacity()}>{capacitySaving ? '正在保存…' : '保存'}</Button>
        </div> : null}
        {dialog === 'barcode' ? <div className="dialog-content"><label className="form-field"><span>扫描或输入条码</span><input autoFocus value={barcodeInput} onChange={(event) => setBarcodeInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && barcodeInput.trim()) void barcodeQuery.refetch() }} placeholder="扫描枪回车或手动输入" /></label><Button tone="primary" disabled={!barcodeInput.trim() || barcodeQuery.isFetching} onClick={() => void barcodeQuery.refetch()}>校验条码</Button>{verifiedMaterials ? <div className="barcode-result">{verifiedMaterials.length ? verifiedMaterials.map((item) => <div key={item.uuid}><CircleCheck size={18} /><p><strong>{item.name}</strong><small>{item.currentLocation.label}</small><code>{item.uuid}</code></p></div>) : <EmptyState title="未找到该条码" description="可检查条码后重试，或从物料模板实例化。" />}</div> : null}</div> : null}
        {dialog === 'instantiate' ? <div className="dialog-content"><label className="form-field"><span>物料模板</span><select value={templateId} onChange={(event) => { const template = materialTemplates.find((item) => item.uuid === event.target.value); setTemplateId(event.target.value); if (template) setMaterialForm((current) => ({ ...current, name: template.displayName })) }}><option value="">选择物料模板</option>{materialTemplates.map((template) => <option key={template.uuid} value={template.uuid}>{template.displayName}</option>)}</select></label><div className="dialog-template-summary"><Boxes size={20} /><div><strong>{selectedTemplate?.displayName || '尚未选择模板'}</strong><code>{selectedTemplate?.uuid || '—'}</code></div></div><label className="form-field"><span>物料名称</span><input value={materialForm.name} onChange={(event) => setMaterialForm((current) => ({ ...current, name: event.target.value }))} /></label><label className="form-field"><span>唯一条码</span><input value={materialForm.barcode} onChange={(event) => setMaterialForm((current) => ({ ...current, barcode: event.target.value }))} /></label><label className="form-field"><span>初始库位</span><select value={materialForm.siteUuid} onChange={(event) => setMaterialForm((current) => ({ ...current, siteUuid: event.target.value }))}><option value="">暂不分配库位</option>{emptySiteOptions.map((site) => <option key={site.uuid} value={site.uuid}>{site.ownerName} / {site.name}</option>)}</select><small>选择后，实例化与上料将在同一事务中完成。</small></label><label className="form-field"><span>说明</span><input value={materialForm.description} onChange={(event) => setMaterialForm((current) => ({ ...current, description: event.target.value }))} /></label><Button tone="primary" disabled={!connected || !templateId || !materialForm.name.trim() || !materialForm.barcode.trim()} onClick={() => void submitInstantiation()}>确认实例化</Button></div> : null}
        {dialog === 'place' ? <div className="dialog-content"><p className="write-warning">仅展示符合该库位模板定义且当前未分配的物料。系统还会通过 expected_revision 原子校验，冲突时不会覆盖他人操作。</p>{inventoryCandidates.length ? <label className="form-field"><span>选择待上料物料</span><select value={placementMaterialId} onChange={(event) => setPlacementMaterialId(event.target.value)}><option value="">选择未分配库存</option>{inventoryCandidates.map((item) => <option value={item.uuid} key={item.uuid}>{item.name} · {item.barcode}</option>)}</select></label> : <EmptyState title="没有符合库位定义的未分配物料" description={allowedTemplateIds.length ? '请先实例化允许的物料模板，或选择其他库位。' : '当前没有可上料的未分配物料。'} />}<Button tone="primary" disabled={!connected || !placementMaterialId} onClick={() => void submitPlacement()}>确认上料</Button></div> : null}
        {dialog === 'remove' ? <div className="dialog-content"><p className="write-warning">下料后物料进入“待分配库存”，不会删除物料实例。</p><Button tone="primary" disabled={!connected} onClick={() => void submitPlacement(true)}>确认下料</Button></div> : null}
      </div></div> : null}
    </div>
  )
}
