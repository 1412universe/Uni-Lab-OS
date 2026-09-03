import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, BookOpen, FlaskConical, History, PackagePlus, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { createReagent, createReagentInfo, deleteReagent, deleteReagentInfo, dispenseReagent, updateReagent, loadReagentHistory, loadReagentInfos, loadReagents, loadResourceTemplates, lookupCompoundByCas } from '../lib/edgeClient'
import type { CompoundLookupResult, MaterialRecord, ReagentHistoryRecord, ReagentInfoRecord, ReagentRecord, ResourceTemplateRecord } from '../types'

const physicalStateLabels: Record<ReagentInfoRecord['physicalState'], string> = {
  solid: '固体', liquid: '液体', gas: '气体', other: '其他', unknown: '未知',
}
type IdentityForm = {
  name: string; nameEn: string; cas: string; aliases: string; molecularFormula: string
  smiles: string; inchiKey: string; molecularWeight: string; density: string
  physicalState: ReagentInfoRecord['physicalState']; description: string
}
type CustomParameter = { id: number; name: string; value: string }
type DispenseRow = { id: number; materialUuid: string; quantity: string }
type EditForm = { quantity: string; concentrationValue: string; concentrationUnit: string; description: string }

const containerTagLabels: Record<string, string> = {
  liquid_reagent: '液体试剂瓶',
  powder_reagent: '粉末试剂瓶',
  sample_vial: '样品瓶',
  beaker: '烧杯',
}

/** 从领域包容器模板中提取有区分度的标签，排除 container 和所有模板共有的包级标签。 */
export function deriveContainerFilterTags(templates: ResourceTemplateRecord[]) {
  const containerTemplates = templates.filter((template) => (template.tags || []).includes('container'))
  const frequency = new Map<string, number>()
  for (const template of containerTemplates) {
    for (const tag of new Set((template.tags || []).map((value) => value.trim()).filter((value) => value && value !== 'container'))) {
      frequency.set(tag, (frequency.get(tag) || 0) + 1)
    }
  }
  return [...frequency]
    .filter(([, count]) => containerTemplates.length <= 1 || count < containerTemplates.length)
    .map(([tag]) => tag)
    .sort((left, right) => containerTagLabel(left).localeCompare(containerTagLabel(right), 'zh-CN'))
}

function containerTagLabel(tag: string) {
  return containerTagLabels[tag] || tag.replaceAll('_', ' ')
}

function filterContainersByTag(containers: MaterialRecord[], templateTags: Map<string, Set<string>>, selectedTag: string) {
  return selectedTag ? containers.filter((item) => item.resourceTemplateUuid && templateTags.get(item.resourceTemplateUuid)?.has(selectedTag)) : containers
}

function ContainerTagFilter({ tags, selectedTag, containers, templateTags, onChange }: { tags: string[]; selectedTag: string; containers: MaterialRecord[]; templateTags: Map<string, Set<string>>; onChange: (tag: string) => void }) {
  if (!tags.length) return null
  const count = (tag: string) => filterContainersByTag(containers, templateTags, tag).length
  return <div className="container-tag-filter" role="group" aria-label="按容器标签筛选">
    <span>容器类型</span>
    <button type="button" aria-pressed={!selectedTag} onClick={() => onChange('')}>全部<em>{containers.length}</em></button>
    {tags.map((tag) => <button key={tag} type="button" aria-pressed={selectedTag === tag} onClick={() => onChange(tag)}>{containerTagLabel(tag)}<em>{count(tag)}</em></button>)}
  </div>
}

/** 生成分装命令 ID；同一次弹窗内重试复用，服务端按它幂等重放。 */
function newCommandId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `dispense-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

/** 汇总分装行：合计、分装后剩余，以及每行是否合法。 */
export function summariseDispense(rows: DispenseRow[], available: number) {
  const parsed = rows.map((row) => ({ ...row, value: Number(row.quantity) }))
  const validRows = parsed.filter((row) => row.materialUuid && Number.isFinite(row.value) && row.value > 0)
  const total = validRows.reduce((sum, row) => sum + row.value, 0)
  const duplicated = new Set(validRows.map((row) => row.materialUuid)).size !== validRows.length
  const remaining = available - total
  const ready = validRows.length === rows.length && rows.length > 0 && !duplicated && total > 0 && remaining >= -1e-9
  return { total, remaining, duplicated, ready }
}
const emptyIdentity: IdentityForm = { name: '', nameEn: '', cas: '', aliases: '', molecularFormula: '', smiles: '', inchiKey: '', molecularWeight: '', density: '', physicalState: 'unknown', description: '' }

export function isValidCas(value: string) {
  if (!/^\d{2,7}-\d{2}-\d$/.test(value)) return false
  const digits = value.replaceAll('-', '')
  const body = digits.slice(0, -1)
  let sum = 0
  for (let index = body.length - 1, weight = 1; index >= 0; index -= 1, weight += 1) sum += Number(body[index]) * weight
  return sum % 10 === Number(digits.at(-1))
}

export function ReagentsPage({ materials, connected, onNotify }: { materials: MaterialRecord[]; connected: boolean; onNotify: (message: string) => void }) {
  const [view, setView] = useState<'inventory' | 'catalog'>('inventory')
  const [query, setQuery] = useState('')
  const [dialog, setDialog] = useState<'catalog' | 'register' | 'dispense' | 'edit' | null>(null)
  const [editTarget, setEditTarget] = useState<ReagentRecord | null>(null)
  const [editForm, setEditForm] = useState<EditForm>({ quantity: '', concentrationValue: '', concentrationUnit: '', description: '' })
  const [saving, setSaving] = useState(false)
  const [dispenseSource, setDispenseSource] = useState<ReagentRecord | null>(null)
  const [dispenseRows, setDispenseRows] = useState<DispenseRow[]>([])
  const dispenseCommandId = useRef('')
  const [identityForm, setIdentityForm] = useState<IdentityForm>(emptyIdentity)
  const [customParameters, setCustomParameters] = useState<CustomParameter[]>([])
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [formError, setFormError] = useState('')
  const [lookup, setLookup] = useState<{ phase: 'idle' | 'loading' | 'success' | 'warning'; message: string; blocks: boolean }>({ phase: 'idle', message: '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false })
  const previousCandidate = useRef<CompoundLookupResult['compound']>(undefined)
  const [registerForm, setRegisterForm] = useState({ materialUuid: '', reagentInfoUuid: '', quantity: '', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '%', description: '' })
  const [historyReagent, setHistoryReagent] = useState<ReagentRecord | null>(null)
  const infosQuery = useQuery({ queryKey: ['reagent-infos'], queryFn: ({ signal }) => loadReagentInfos(signal), enabled: connected, retry: 1 })
  const reagentsQuery = useQuery({ queryKey: ['reagents'], queryFn: ({ signal }) => loadReagents(signal), enabled: connected, retry: 1 })
  const historyQuery = useQuery({ queryKey: ['reagent-history', historyReagent?.materialUuid], queryFn: ({ signal }) => loadReagentHistory(historyReagent?.materialUuid || '', signal), enabled: connected && Boolean(historyReagent), retry: 1 })
  const templatesQuery = useQuery({ queryKey: ['resource-templates'], queryFn: ({ signal }) => loadResourceTemplates(signal), enabled: connected, retry: 1 })
  const reagentMaterialIds = new Set((reagentsQuery.data || []).map((item) => item.materialUuid))
  // 只有模板带 container 标签的物料才能承载试剂（与后端 require_container 同一规则）；
  // 模板尚未加载时不放行任何容器，避免列表先宽后窄闪动。
  const containerTemplateIds = useMemo(() => new Set((templatesQuery.data || []).filter((template) => (template.tags || []).includes('container')).map((template) => template.uuid)), [templatesQuery.data])
  const containers = materials.filter((material) => !material.isStructural && !reagentMaterialIds.has(material.uuid) && Boolean(material.resourceTemplateUuid) && containerTemplateIds.has(material.resourceTemplateUuid as string))
  const containerFilterTags = useMemo(() => deriveContainerFilterTags(templatesQuery.data || []), [templatesQuery.data])
  const sourceMaterial = dispenseSource ? materials.find((material) => material.uuid === dispenseSource.materialUuid) : undefined
  const sourceTemplateTags = (templatesQuery.data || []).find((template) => template.uuid === sourceMaterial?.resourceTemplateUuid)?.tags || []
  const preferredContainerTag = containerFilterTags.find((tag) => sourceTemplateTags.includes(tag)) || ''
  const keyword = query.trim().toLowerCase()
  const infos = useMemo(() => (infosQuery.data || []).filter((item) => !keyword || [item.name, item.nameEn, item.cas, item.molecularFormula, ...item.aliases].some((value) => value?.toLowerCase().includes(keyword))), [infosQuery.data, keyword])
  const inventory = useMemo(() => (reagentsQuery.data || []).filter((item) => !keyword || [item.name, item.cas, item.containerName, item.containerBarcode].some((value) => value?.toLowerCase().includes(keyword))), [reagentsQuery.data, keyword])

  useEffect(() => {
    if (dialog !== 'catalog') return
    const cas = identityForm.cas.trim()
    if (!cas || !isValidCas(cas)) {
      setLookup({ phase: 'idle', message: cas ? 'CAS 号校验位不正确；请修正或留空。' : '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false })
      return
    }
    const controller = new AbortController()
    setLookup({ phase: 'loading', message: '正在通过 Backend 查询化合物信息…', blocks: true })
    const timer = window.setTimeout(() => void lookupCompoundByCas(cas, controller.signal).then((result) => {
      if (result.status === 'ok' && result.compound) {
        const next = result.compound
        const previous = previousCandidate.current
        setIdentityForm((current) => ({
          ...current,
          nameEn: replaceAutoValue(current.nameEn, previous?.name, next.name),
          molecularFormula: replaceAutoValue(current.molecularFormula, previous?.molecularFormula, next.molecularFormula),
          smiles: replaceAutoValue(current.smiles, previous?.smiles, next.smiles),
          inchiKey: next.inchiKey || '',
          molecularWeight: replaceAutoValue(current.molecularWeight, numberText(previous?.molecularWeight), numberText(next.molecularWeight)),
        }))
        previousCandidate.current = next
        setLookup({ phase: 'success', message: '已补全可用字段，请核对后保存。', blocks: false })
      } else {
        previousCandidate.current = undefined
        setLookup({ phase: 'warning', message: result.message || lookupFallback(result.status), blocks: result.status === 'registered' })
      }
    }).catch((error) => {
      if (controller.signal.aborted) return
      setLookup({ phase: 'warning', message: error instanceof Error ? `${error.message}；仍可手工填写。` : '查询失败，仍可手工填写。', blocks: false })
    }), 500)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [dialog, identityForm.cas])

  function openCatalogDialog() {
    setIdentityForm(emptyIdentity); setCustomParameters([]); setAdvancedOpen(false); setFormError(''); previousCandidate.current = undefined
    setLookup({ phase: 'idle', message: '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false }); setDialog('catalog')
  }

  async function saveCatalogItem() {
    const error = validateIdentity(identityForm, customParameters)
    if (error) { setFormError(error); return }
    if (lookup.blocks || lookup.phase === 'loading') { setFormError(lookup.message); return }
    setSaving(true); setFormError('')
    try {
      await createReagentInfo({
        name: identityForm.name.trim(), nameEn: textOrUndefined(identityForm.nameEn), cas: textOrUndefined(identityForm.cas),
        aliases: [...new Set(identityForm.aliases.split(/[,，;；\n]/).map((value) => value.trim()).filter(Boolean))],
        molecularFormula: textOrUndefined(identityForm.molecularFormula), smiles: textOrUndefined(identityForm.smiles), inchiKey: textOrUndefined(identityForm.inchiKey),
        molecularWeight: numberOrUndefined(identityForm.molecularWeight), densityGPerMl: numberOrUndefined(identityForm.density),
        physicalState: identityForm.physicalState, description: textOrUndefined(identityForm.description),
        metadata: customParameters.length ? { custom_parameters: customParameters.map(({ name, value }) => ({ name: name.trim(), value: value.trim() })) } : undefined,
      })
      await infosQuery.refetch(); setDialog(null); onNotify(`试剂目录“${identityForm.name}”已创建`)
    } catch (error) { setFormError(error instanceof Error ? error.message : '保存失败') } finally { setSaving(false) }
  }

  async function saveRegistration() {
    const quantity = Number(registerForm.quantity)
    if (!registerForm.materialUuid || !registerForm.reagentInfoUuid || !Number.isFinite(quantity) || quantity <= 0 || !registerForm.quantityUnit) return
    setSaving(true)
    try {
      await createReagent({ materialUuid: registerForm.materialUuid, reagentInfoUuid: registerForm.reagentInfoUuid, quantity, quantityUnit: registerForm.quantityUnit, concentrationValue: registerForm.concentrationValue ? Number(registerForm.concentrationValue) : undefined, concentrationUnit: registerForm.concentrationValue ? registerForm.concentrationUnit : undefined, description: textOrUndefined(registerForm.description) })
      await reagentsQuery.refetch(); setDialog(null); onNotify('试剂已录入容器库存')
    } catch (error) { onNotify(`录入失败：${error instanceof Error ? error.message : '未知错误'}`) } finally { setSaving(false) }
  }

  function openEditDialog(item: ReagentRecord) {
    setEditTarget(item)
    setEditForm({ quantity: numberText(item.quantity) || '', concentrationValue: numberText(item.concentrationValue) || '', concentrationUnit: item.concentrationUnit || '', description: item.description || '' })
    setDialog('edit')
  }

  async function saveEdit() {
    if (!editTarget) return
    const quantity = Number(editForm.quantity)
    if (!Number.isFinite(quantity) || quantity < 0) return
    setSaving(true)
    try {
      await updateReagent({
        uuid: editTarget.uuid, quantity, quantityUnit: editTarget.quantityUnit || 'mL', expectedRevision: editTarget.revision,
        concentrationValue: numberOrUndefined(editForm.concentrationValue), concentrationUnit: textOrUndefined(editForm.concentrationUnit),
        description: textOrUndefined(editForm.description), metaData: editTarget.metaData,
      })
      await reagentsQuery.refetch()
      setDialog(null); setEditTarget(null)
      onNotify(`已更新 ${editTarget.containerName || editTarget.name}：${quantity} ${editTarget.quantityUnit || ''}`)
    } catch (error) {
      onNotify(`更新失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  async function removeReagent(item: ReagentRecord) {
    if (!window.confirm(`确认删除 ${item.containerName || item.name} 里的 ${item.name} 记录？\n\n容器会变回空容器，操作历史保留一条“移除”。`)) return
    setSaving(true)
    try {
      await deleteReagent(item.uuid)
      await reagentsQuery.refetch()
      onNotify(`已移除 ${item.containerName || item.name} 里的 ${item.name}`)
    } catch (error) {
      onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  function openDispenseDialog(item: ReagentRecord) {
    setDispenseSource(item)
    setDispenseRows([{ id: 1, materialUuid: '', quantity: '' }])
    dispenseCommandId.current = newCommandId()
    setDialog('dispense')
  }

  async function saveDispense() {
    if (!dispenseSource) return
    const summary = summariseDispense(dispenseRows, dispenseSource.quantity ?? 0)
    if (!summary.ready) return
    setSaving(true)
    try {
      const result = await dispenseReagent({
        commandId: dispenseCommandId.current,
        sourceReagentUuid: dispenseSource.uuid,
        expectedRevision: dispenseSource.revision,
        quantityUnit: dispenseSource.quantityUnit || 'mL',
        targets: dispenseRows.map((row) => ({ materialUuid: row.materialUuid, quantity: Number(row.quantity) })),
      })
      await reagentsQuery.refetch()
      setDialog(null); setDispenseSource(null)
      onNotify(`已分装到 ${result.targets.length} 个容器，源瓶剩余 ${result.source.quantity} ${result.source.quantityUnit}`)
    } catch (error) {
      // 保持弹窗与 command_id 不变，用户修正后重试即幂等重放或重新校验。
      onNotify(`分装失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  async function removeCatalogItem(item: ReagentInfoRecord) {
    if (!window.confirm(`确认删除试剂目录“${item.name}”？\n\n已被试剂库存引用的目录项会由后端拒绝删除。`)) return
    setSaving(true)
    try {
      await deleteReagentInfo(item.uuid)
      await infosQuery.refetch()
      onNotify(`已删除试剂目录“${item.name}”`)
    } catch (error) {
      onNotify(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally { setSaving(false) }
  }

  const loadingError = infosQuery.error || reagentsQuery.error
  return <div className="page reagents-page">
    <PageHeader eyebrow="REAGENT AUTHORITY" title="试剂" description="试剂目录定义化学品身份；试剂库存记录具体容器、数量与浓度。" actions={<><Button icon={<BookOpen size={15} />} onClick={openCatalogDialog}>新增试剂目录</Button><Button tone="primary" icon={<PackagePlus size={15} />} disabled={!infosQuery.data?.length || !containers.length} onClick={() => setDialog('register')}>录入试剂</Button></>} />
    <div className="reagent-summary"><div><FlaskConical size={19} /><span>试剂库存<strong>{reagentsQuery.data?.length || 0}</strong></span></div><div><BookOpen size={19} /><span>试剂目录<strong>{infosQuery.data?.length || 0}</strong></span></div><div><PackagePlus size={19} /><span>可录入容器<strong>{containers.length}</strong></span></div></div>
    <Panel className="reagent-workspace">
      <PanelHeader title={view === 'inventory' ? '试剂库存' : '试剂目录'} description={view === 'inventory' ? '容器级数量、浓度与化学身份' : 'CAS、分子式与基础理化信息'} action={<div className="segmented"><button className={view === 'inventory' ? 'active' : ''} onClick={() => setView('inventory')}>库存</button><button className={view === 'catalog' ? 'active' : ''} onClick={() => { setView('catalog'); setHistoryReagent(null) }}>目录</button></div>} />
      <label className="reagent-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={view === 'inventory' ? '搜索试剂、CAS、容器或条码' : '搜索名称、别名、CAS 或分子式'} /></label>
      {loadingError ? <div className="connection-alert" role="alert"><div><strong>试剂数据不可用</strong><span>{loadingError instanceof Error ? loadingError.message : '读取失败'}</span></div></div> : null}
      {view === 'inventory' ? <InventoryTable items={inventory} materials={materials} hasCatalog={Boolean(infosQuery.data?.length)} onHistory={setHistoryReagent} onDispense={openDispenseDialog}  onEdit={openEditDialog} onDelete={removeReagent} busy={saving} /> :<CatalogTable items={infos} deleting={saving} onDelete={(item) => void removeCatalogItem(item)} />}
    </Panel>
    {historyReagent ? <ReagentHistoryDrawer reagent={historyReagent} items={historyQuery.data || []} loading={historyQuery.isLoading || historyQuery.isFetching} error={historyQuery.error} onClose={() => setHistoryReagent(null)} /> : null}
    {dialog ? <div className="dialog-backdrop" role="presentation"><div className={`material-write-dialog reagent-dialog ${dialog === 'catalog' ? 'reagent-dialog-wide' : ''} ${dialog === 'dispense' ? 'reagent-dialog-dispense' : ''}`} role="dialog" aria-modal="true"><header><div><span>REAGENT COMMAND</span><h2>{dialog === 'catalog' ? '新增试剂目录' : dialog === 'dispense' ? '分装' : dialog === 'edit' ? '编辑试剂' : '录入试剂'}</h2>{dialog === 'dispense' && dispenseSource ? <p>把源瓶里的试剂分到若干空容器；同一化学身份与浓度，数量守恒，一次提交。</p> : null}{dialog === 'catalog' ? <p>输入 CAS 可自动补全化学信息；无 CAS 的自配物质可直接填写名称。</p> : null}</div><button aria-label="关闭" onClick={() => setDialog(null)}>×</button></header>
      {dialog === 'edit' && editTarget ? <EditReagentForm target={editTarget} form={editForm} setForm={setEditForm} saving={saving} onSave={saveEdit} /> : dialog === 'catalog' ? <CatalogForm form={identityForm} setForm={setIdentityForm} lookup={lookup} error={formError} customParameters={customParameters} setCustomParameters={setCustomParameters} advancedOpen={advancedOpen} setAdvancedOpen={setAdvancedOpen} saving={saving} onSave={() => void saveCatalogItem()} /> : dialog === 'dispense' && dispenseSource ? <DispenseForm source={dispenseSource} rows={dispenseRows} setRows={setDispenseRows} containers={containers} templates={templatesQuery.data || []} filterTags={containerFilterTags} preferredTag={preferredContainerTag} saving={saving} onSave={() => void saveDispense()} /> : <RegisterForm form={registerForm} setForm={setRegisterForm} infos={infosQuery.data || []} containers={containers} templates={templatesQuery.data || []} filterTags={containerFilterTags} saving={saving} onSave={() => void saveRegistration()} />}
    </div></div> : null}
  </div>
}

function CatalogForm({ form, setForm, lookup, error, customParameters, setCustomParameters, advancedOpen, setAdvancedOpen, saving, onSave }: { form: IdentityForm; setForm: React.Dispatch<React.SetStateAction<IdentityForm>>; lookup: { phase: string; message: string; blocks: boolean }; error: string; customParameters: CustomParameter[]; setCustomParameters: React.Dispatch<React.SetStateAction<CustomParameter[]>>; advancedOpen: boolean; setAdvancedOpen: (value: boolean) => void; saving: boolean; onSave: () => void }) {
  const field = (key: keyof IdentityForm, value: string) => setForm((current) => ({ ...current, [key]: value }))
  return <div className="dialog-content reagent-catalog-form">
    {error ? <div className="reagent-form-error" role="alert">{error}</div> : null}
    <fieldset><legend>试剂信息</legend><div className="reagent-field-grid">
      <label className="form-field"><span>CAS 号</span><input autoFocus value={form.cas} onChange={(e) => field('cas', e.target.value)} placeholder="例如 64-17-5" /><small className="reagent-lookup" data-phase={lookup.phase}>{lookup.message}</small></label>
      <label className="form-field"><span>试剂名称 *</span><input value={form.name} onChange={(e) => field('name', e.target.value)} /></label>
      <label className="form-field"><span>英文名称</span><input value={form.nameEn} onChange={(e) => field('nameEn', e.target.value)} /></label>
      <label className="form-field"><span>别名</span><input value={form.aliases} onChange={(e) => field('aliases', e.target.value)} placeholder="多个别名用逗号分隔" /></label>
    </div></fieldset>
    <fieldset><legend>化学信息 {lookup.phase === 'success' ? <small>已自动补全</small> : null}</legend><div className="reagent-field-grid">
      <label className="form-field"><span>分子式</span><input value={form.molecularFormula} onChange={(e) => field('molecularFormula', e.target.value)} /></label>
      <label className="form-field"><span>分子量（g/mol）</span><input type="number" min="0" step="any" value={form.molecularWeight} onChange={(e) => field('molecularWeight', e.target.value)} /></label>
      <label className="form-field wide"><span>SMILES</span><input className="mono-input" value={form.smiles} onChange={(e) => field('smiles', e.target.value)} /></label>
      <div className="reagent-structure wide"><span>2D 结构</span><div>{form.smiles ? <><FlaskConical size={24} /><code>{form.smiles}</code><small>结构式将在支持 SMILES 绘制的 Workbench 中渲染</small></> : <small>输入或查询到 SMILES 后显示结构信息</small>}</div></div>
    </div></fieldset>
    <fieldset><legend>物理属性</legend><div className="reagent-field-grid">
      <label className="form-field"><span>常温物态</span><select value={form.physicalState} onChange={(e) => field('physicalState', e.target.value)}>{Object.entries(physicalStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="form-field"><span>参考密度（g/mL）</span><input type="number" min="0" step="any" value={form.density} onChange={(e) => field('density', e.target.value)} /></label>
    </div></fieldset>
    <details open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}><summary>更多信息</summary><div className="reagent-field-grid advanced-fields">
      <label className="form-field wide"><span>说明</span><textarea rows={3} value={form.description} onChange={(e) => field('description', e.target.value)} /></label>
      <div className="custom-parameters wide"><header><strong>自定义参数</strong><button type="button" onClick={() => setCustomParameters((items) => [...items, { id: Date.now(), name: '', value: '' }])}><Plus size={13} />添加参数</button></header>{customParameters.map((parameter) => <div key={parameter.id}><input aria-label="参数名称" placeholder="参数名称" value={parameter.name} onChange={(e) => setCustomParameters((items) => items.map((item) => item.id === parameter.id ? { ...item, name: e.target.value } : item))} /><input aria-label="参数值" placeholder="参数值" value={parameter.value} onChange={(e) => setCustomParameters((items) => items.map((item) => item.id === parameter.id ? { ...item, value: e.target.value } : item))} /><button aria-label="删除参数" onClick={() => setCustomParameters((items) => items.filter((item) => item.id !== parameter.id))}><Trash2 size={14} /></button></div>)}</div>
    </div></details>
    <Button tone="primary" icon={<Plus size={15} />} disabled={saving || lookup.phase === 'loading' || lookup.blocks} onClick={onSave}>{saving ? '正在创建…' : '创建'}</Button>
  </div>
}

export function RegisterForm({ form, setForm, infos, containers, templates, filterTags, saving, onSave }: { form: { materialUuid: string; reagentInfoUuid: string; quantity: string; quantityUnit: string; concentrationValue: string; concentrationUnit: string; description: string }; setForm: React.Dispatch<React.SetStateAction<typeof form>>; infos: ReagentInfoRecord[]; containers: MaterialRecord[]; templates: ResourceTemplateRecord[]; filterTags: string[]; saving: boolean; onSave: () => void }) {
  const [selectedTag, setSelectedTag] = useState('')
  const templateTags = useMemo(() => new Map(templates.map((template) => [template.uuid, new Set(template.tags || [])])), [templates])
  const taggedContainers = filterContainersByTag(containers, templateTags, selectedTag)
  const changeTag = (tag: string) => {
    setSelectedTag(tag)
    if (form.materialUuid && !filterContainersByTag(containers, templateTags, tag).some((item) => item.uuid === form.materialUuid)) setForm({ ...form, materialUuid: '' })
  }
  return <div className="dialog-content reagent-form">
    <label className="form-field wide"><span>试剂目录 *</span><select value={form.reagentInfoUuid} onChange={(e) => setForm({ ...form, reagentInfoUuid: e.target.value })}><option value="">选择试剂目录项</option>{infos.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · {item.cas || '无 CAS'}</option>)}</select></label>
    <div className="reagent-container-picker wide">
      <ContainerTagFilter tags={filterTags} selectedTag={selectedTag} containers={containers} templateTags={templateTags} onChange={changeTag} />
      <label className="form-field"><span>试剂容器 *</span><select aria-label="试剂容器" value={form.materialUuid} onChange={(e) => setForm({ ...form, materialUuid: e.target.value })}><option value="">{taggedContainers.length ? '选择未登记试剂的容器' : '该类型暂无可用容器'}</option>{taggedContainers.map((item) => <option key={item.uuid} value={item.uuid}>{item.name}</option>)}</select></label>
    </div>
    <label className="form-field"><span>数量 *</span><input type="number" min="0" step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
    <label className="form-field"><span>单位 *</span><select value={form.quantityUnit} onChange={(e) => setForm({ ...form, quantityUnit: e.target.value })}>{['mL', 'L', 'g', 'mg', 'μL', 'mmol'].map((unit) => <option key={unit}>{unit}</option>)}</select></label>
    <label className="form-field"><span>浓度</span><input type="number" min="0" step="any" value={form.concentrationValue} onChange={(e) => setForm({ ...form, concentrationValue: e.target.value })} /></label>
    <label className="form-field"><span>浓度单位</span><select value={form.concentrationUnit} onChange={(e) => setForm({ ...form, concentrationUnit: e.target.value })}>{['%', 'mol/L', 'mmol/L', 'mg/mL'].map((unit) => <option key={unit}>{unit}</option>)}</select></label>
    <label className="form-field wide"><span>说明</span><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label>
    <Button tone="primary" icon={<PackagePlus size={15} />} disabled={saving || !form.materialUuid || !form.reagentInfoUuid || !(Number(form.quantity) > 0)} onClick={onSave}>确认录入</Button>
  </div>
}

export function EditReagentForm({ target, form, setForm, saving, onSave }: { target: ReagentRecord; form: EditForm; setForm: (form: EditForm) => void; saving: boolean; onSave: () => void }) {
  const reserved = target.activeWorkflowReservedQuantity ?? 0
  const quantity = Number(form.quantity)
  const quantityValid = form.quantity.trim() !== '' && Number.isFinite(quantity) && quantity >= 0
  const belowReserved = quantityValid && quantity < reserved
  return <div className="reagent-edit-form">
    <div className="reagent-edit-target"><strong>{target.name}</strong><small>{target.containerName || target.materialUuid} · 当前 {target.quantity ?? '—'} {target.quantityUnit || ''}{reserved > 0 ? ` · 预留中 ${reserved} ${target.quantityUnit || ''}` : ''}</small></div>
    <div className="form-grid">
      <label className="form-field"><span>数量（{target.quantityUnit || '单位不变'}）*</span><input aria-label="数量" type="number" min={0} step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
      <label className="form-field"><span>浓度值</span><input aria-label="浓度值" type="number" step="any" value={form.concentrationValue} onChange={(e) => setForm({ ...form, concentrationValue: e.target.value })} /></label>
      <label className="form-field"><span>浓度单位</span><input aria-label="浓度单位" value={form.concentrationUnit} onChange={(e) => setForm({ ...form, concentrationUnit: e.target.value })} placeholder="如 %、mol/L" /></label>
      <label className="form-field wide"><span>说明</span><input aria-label="说明" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="为什么改：盘点、称重复核、录错更正…" /></label>
    </div>
    {belowReserved ? <small className="form-error">数量不能低于任务预留量 {reserved} {target.quantityUnit || ''}。</small> : null}
    <small className="reagent-edit-hint">单位不可改；数量增减会记入操作历史（增加记“录入 / 补充”，减少记“调整”）。分装血缘保持不变。</small>
    <Button tone="primary" icon={<Pencil size={15} />} disabled={saving || !quantityValid || belowReserved} onClick={onSave}>保存修改</Button>
  </div>
}

export function DispenseForm({ source, rows, setRows, containers, templates, filterTags, preferredTag, saving, onSave }: { source: ReagentRecord; rows: DispenseRow[]; setRows: React.Dispatch<React.SetStateAction<DispenseRow[]>>; containers: MaterialRecord[]; templates: ResourceTemplateRecord[]; filterTags: string[]; preferredTag: string; saving: boolean; onSave: () => void }) {
  const [selectedTag, setSelectedTag] = useState(preferredTag)
  const available = source.quantity ?? 0
  const unit = source.quantityUnit || ''
  const summary = summariseDispense(rows, available)
  const chosen = new Set(rows.map((row) => row.materialUuid).filter(Boolean))
  const templateTags = useMemo(() => new Map(templates.map((template) => [template.uuid, new Set(template.tags || [])])), [templates])
  const taggedContainers = filterContainersByTag(containers, templateTags, selectedTag)
  const unchosenContainerCount = taggedContainers.filter((item) => !chosen.has(item.uuid)).length
  const hasAnotherContainer = unchosenContainerCount > rows.filter((row) => !row.materialUuid).length
  const addRow = () => setRows((current) => [...current, { id: (current.at(-1)?.id || 0) + 1, materialUuid: '', quantity: '' }])
  const update = (id: number, patch: Partial<DispenseRow>) => setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)))
  const remove = (id: number) => setRows((current) => (current.length > 1 ? current.filter((row) => row.id !== id) : current))
  return <form className="dialog-content dispense-form" onSubmit={(event) => { event.preventDefault(); onSave() }}>
    <section className="dispense-source" aria-label="分装源瓶">
      <span className="dispense-source-icon"><FlaskConical size={20} /></span>
      <div><small>源瓶</small><strong>{source.name}</strong><span>{source.containerName || source.materialUuid}</span></div>
      <div className="dispense-source-balance"><small>当前可分装</small><strong>{available}<em>{unit}</em></strong></div>
    </section>
    <section className="dispense-targets">
      <header>
        <div><strong>目标容器</strong><small>选择空容器并填写本次转移数量</small></div>
        <Button type="button" className="dispense-add" icon={<Plus size={15} />} onClick={addRow} disabled={!hasAnotherContainer}>添加容器</Button>
      </header>
      <ContainerTagFilter tags={filterTags} selectedTag={selectedTag} containers={containers} templateTags={templateTags} onChange={setSelectedTag} />
      <div className="dispense-target-list">
        {rows.map((row, index) => {
          const duplicate = row.materialUuid !== '' && rows.some((other) => other.id !== row.id && other.materialUuid === row.materialUuid)
          const options = containers.filter((item) => item.uuid === row.materialUuid || (taggedContainers.includes(item) && !chosen.has(item.uuid)))
          return <article key={row.id} className={`dispense-row${duplicate ? ' dispense-row-invalid' : ''}`}>
            <span className="dispense-row-index">{String(index + 1).padStart(2, '0')}</span>
            <label className="form-field dispense-container-field"><span>目标容器 *</span><select aria-label={`目标容器 ${index + 1}`} value={row.materialUuid} onChange={(e) => update(row.id, { materialUuid: e.target.value })}><option value="">{taggedContainers.length ? '选择空容器' : '该类型暂无空容器'}</option>{options.map((item) => <option key={item.uuid} value={item.uuid}>{item.name}</option>)}</select></label>
            <label className="form-field dispense-quantity-field"><span>分装量 *</span><div><input aria-label={`分装量 ${index + 1}`} type="number" min="0" step="any" value={row.quantity} onChange={(e) => update(row.id, { quantity: e.target.value })} /><em>{unit}</em></div></label>
            <button type="button" className="dispense-remove" aria-label={`移除目标 ${index + 1}`} title="移除目标容器" disabled={rows.length <= 1} onClick={() => remove(row.id)}><Trash2 size={16} /></button>
            {duplicate ? <small className="form-error">该容器已被选择，请更换一个空容器。</small> : null}
          </article>
        })}
      </div>
    </section>
    <footer className={`dispense-summary${summary.remaining < -1e-9 ? ' dispense-summary-over' : ''}`} aria-live="polite">
      <div className="dispense-summary-flow">
        <span><small>本次分装</small><strong>{summary.total} <em>{unit}</em></strong></span>
        <ArrowRight size={18} aria-hidden="true" />
        <span><small>源瓶剩余</small><strong>{summary.remaining} <em>{unit}</em></strong></span>
      </div>
      {summary.remaining < -1e-9 ? <small className="form-error">分装合计超过源瓶现有余量 {available} {unit}</small> : null}
      <Button type="submit" tone="primary" icon={<PackagePlus size={16} />} disabled={saving || !summary.ready}>{saving ? '正在分装…' : '确认分装'}</Button>
    </footer>
  </form>
}

/** 一瓶试剂在分组列表里的位置：``depth`` 为 0 是源瓶或独立瓶，大于 0 表示挂在上一级分装源瓶之下。 */
export type InventoryBottle = { item: ReagentRecord; depth: number; source?: ReagentRecord; sourceLocation?: string; orphanSource: boolean; location?: string }
export type InventoryGroup = { key: string; name: string; cas?: string; molecularFormula?: string; bottles: InventoryBottle[]; totals: Array<{ unit: string; available: number; reserved: number }>; emptyCount: number }

function inventoryGroupKey(item: ReagentRecord) { return item.reagentInfoUuid || `${item.name}|${item.cas || ''}` }
function formatQuantity(value: number) { return Number(value.toFixed(3)).toString() }
/** 图引导生成的物料把 UNILAB-GRAPH-… 写进条码字段，那是系统身份不是实物条码，列表不展示。 */
export function displayBarcode(barcode?: string) { return barcode && !barcode.startsWith('UNILAB-GRAPH-') ? barcode : undefined }
/** 库位标签形如“试剂瓶堆栈 / R3C2”，血缘标签只需要最后一段代号。 */
export function shortLocation(location?: string) { const last = location?.split('/').map((part) => part.trim()).filter(Boolean).pop(); return last || undefined }

/**
 * 把平铺的试剂记录整理成"按化学身份分组、组内按分装血缘成树"的展示结构。
 * 源瓶（有分装子瓶的）排在组内最前，子瓶紧随其源瓶并缩进；源瓶已不在库的子瓶按独立瓶处理并标记。
 * 汇总按单位分别相加，避免 mL 与 g 混算。
 */
export function groupInventory(items: ReagentRecord[], materials: MaterialRecord[]): InventoryGroup[] {
  const byUuid = new Map(items.map((item) => [item.uuid, item]))
  const locationOf = new Map(materials.map((material) => [material.uuid, material.currentLocation?.label]))
  const groups = new Map<string, InventoryGroup>()
  const members = new Map<string, ReagentRecord[]>()
  for (const item of items) {
    const key = inventoryGroupKey(item)
    if (!groups.has(key)) groups.set(key, { key, name: item.name, cas: item.cas, molecularFormula: item.molecularFormula, bottles: [], totals: [], emptyCount: 0 })
    members.set(key, [...(members.get(key) || []), item])
  }
  const byContainer = (left: ReagentRecord, right: ReagentRecord) => (left.containerName || '').localeCompare(right.containerName || '', 'zh-CN')
  for (const group of groups.values()) {
    const own = members.get(group.key) || []
    const children = new Map<string, ReagentRecord[]>()
    const roots: ReagentRecord[] = []
    for (const item of own) {
      const source = item.sourceReagentUuid ? byUuid.get(item.sourceReagentUuid) : undefined
      if (source && inventoryGroupKey(source) === group.key && source.uuid !== item.uuid) children.set(source.uuid, [...(children.get(source.uuid) || []), item])
      else roots.push(item)
    }
    roots.sort((left, right) => Number(children.has(right.uuid)) - Number(children.has(left.uuid)) || byContainer(left, right))
    const visited = new Set<string>()
    const walk = (item: ReagentRecord, depth: number) => {
      if (visited.has(item.uuid)) return
      visited.add(item.uuid)
      const source = item.sourceReagentUuid ? byUuid.get(item.sourceReagentUuid) : undefined
      group.bottles.push({ item, depth, source, sourceLocation: source ? locationOf.get(source.materialUuid) : undefined, orphanSource: Boolean(item.sourceReagentUuid) && !source, location: locationOf.get(item.materialUuid) })
      for (const child of [...(children.get(item.uuid) || [])].sort(byContainer)) walk(child, depth + 1)
    }
    roots.forEach((root) => walk(root, 0))
    // 数据成环时 walk 不会重复进入；这里兜底把没走到的瓶按独立瓶补上，保证一瓶不漏。
    own.filter((item) => !visited.has(item.uuid)).sort(byContainer).forEach((item) => walk(item, 0))
    const totals = new Map<string, { available: number; reserved: number }>()
    for (const item of own) {
      const unit = item.quantityUnit || ''
      const total = totals.get(unit) || { available: 0, reserved: 0 }
      total.available += item.quantity ?? 0
      total.reserved += item.activeWorkflowReservedQuantity ?? 0
      totals.set(unit, total)
      if (!((item.quantity ?? 0) > 0)) group.emptyCount += 1
    }
    group.totals = [...totals].map(([unit, total]) => ({ unit, ...total }))
  }
  return [...groups.values()].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'))
}

function InventoryTable({ items, materials, hasCatalog, onHistory, onDispense, onEdit, onDelete, busy }: { items: Awaited<ReturnType<typeof loadReagents>>; materials: MaterialRecord[]; hasCatalog: boolean; onHistory: (item: ReagentRecord) => void; onDispense: (item: ReagentRecord) => void; onEdit: (item: ReagentRecord) => void; onDelete: (item: ReagentRecord) => void; busy?: boolean }) {
  const groups = groupInventory(items, materials)
  return <div className="reagent-table reagent-inventory-table">
    <header><span>容器 · 来源</span><span>可用量</span><span>浓度</span><span>更新时间</span><span>操作</span></header>
    {groups.map((group) => {
      const reserved = group.totals.filter((total) => total.reserved > 0).map((total) => `${formatQuantity(total.reserved)} ${total.unit}`).join(' + ')
      return <section className="reagent-group" key={group.key} aria-label={`${group.name} 库存`}>
        <div className="reagent-group-header">
          <span className="reagent-group-icon"><FlaskConical size={15} /></span>
          <div><strong>{group.name}</strong><small>{group.cas || '无 CAS'} · {group.molecularFormula || '无分子式'}</small></div>
          <div className="reagent-group-totals">
            <strong>{group.totals.map((total) => `${formatQuantity(total.available)} ${total.unit}`).join(' + ') || '—'}</strong>
            <small>{group.bottles.length} 瓶{group.emptyCount ? ` · ${group.emptyCount} 瓶已空` : ''}{reserved ? ` · 预留中 ${reserved}` : ''}</small>
          </div>
        </div>
        {group.bottles.map(({ item, depth, source, sourceLocation, orphanSource, location }) => {
          const barcode = displayBarcode(item.containerBarcode)
          const available = item.quantity ?? 0
          const reservedHere = item.activeWorkflowReservedQuantity ?? 0
          const containerName = item.containerName || materials.find((material) => material.uuid === item.materialUuid)?.name || '未知容器'
          return <article key={item.uuid} className={depth ? 'reagent-bottle reagent-bottle-child' : 'reagent-bottle'} data-depth={depth}>
            <span style={{ paddingLeft: depth * 22 }}>
              <strong>{depth ? <i className="reagent-branch" aria-hidden="true">↳</i> : null}{containerName}</strong>
              <small>{location || barcode || '库位未知'}{location && barcode ? ` · ${barcode}` : ''}</small>
              {source ? <small className="reagent-lineage-chip" title={`分装自 ${source.containerName || '源瓶'}${item.dispenseCommandId ? `（命令 ${item.dispenseCommandId}）` : ''}`}>分装自 {shortLocation(sourceLocation) || source.containerName || '源瓶'}</small> : orphanSource ? <small className="reagent-lineage-chip muted">分装自已不在库的源瓶</small> : null}
            </span>
            <span>
              <strong>{item.quantity ?? '—'} {item.quantityUnit || ''}</strong>
              <small className={reservedHere > 0 ? 'reagent-reserved' : undefined}>{available > 0 ? (reservedHere > 0 ? `预留中 ${formatQuantity(reservedHere)} ${item.quantityUnit || ''}` : '可用') : '已空'}</small>
            </span>
            <span>{item.concentrationValue == null ? '—' : `${item.concentrationValue} ${item.concentrationUnit || ''}`}</span>
            <span className="reagent-updated">{formatHistoryTime(item.updatedAt)}</span>
            <span className="reagent-row-actions history-action">
              <button aria-label={`分装 ${item.name} ${item.uuid}`} title={available > 0 ? '分装到其他容器' : '源瓶已空，无法分装'} disabled={!(available > 0)} onClick={() => onDispense(item)}><PackagePlus size={14} /></button>
              <button aria-label={`查看操作历史 ${item.name} ${item.uuid}`} title="查看操作历史" onClick={() => onHistory(item)}><History size={14} /></button>
              <button aria-label={`编辑 ${item.name} ${item.uuid}`} title="修正数量、浓度或说明" disabled={busy} onClick={() => onEdit(item)}><Pencil size={14} /></button>
              <button className="danger" aria-label={`删除 ${item.name} ${item.uuid}`} title={reservedHere > 0 ? `有任务预留 ${formatQuantity(reservedHere)} ${item.quantityUnit || ''}，不能删除` : '移除这条试剂记录，容器变回空容器'} disabled={busy || reservedHere > 0} onClick={() => onDelete(item)}><Trash2 size={14} /></button>
            </span>
          </article>
        })}
      </section>
    })}
    {!items.length ? <EmptyState title="暂无试剂库存" description={hasCatalog ? '点击“录入试剂”，把目录项登记到具体容器。' : '请先新增试剂目录，再录入容器库存。'} /> : null}
  </div>
}

function CatalogTable({ items, deleting, onDelete }: { items: ReagentInfoRecord[]; deleting: boolean; onDelete: (item: ReagentInfoRecord) => void }) { return <div className="reagent-table identity-table"><header><span>试剂目录</span><span>CAS</span><span>分子式</span><span>物态</span><span>操作</span></header>{items.map((item) => <article key={item.uuid}><span><strong>{item.name}</strong><small>{item.nameEn || item.aliases.join('、') || '无别名'}</small><code>{item.uuid}</code></span><span>{item.cas || '—'}</span><span>{item.molecularFormula || '—'}</span><span>{physicalStateLabels[item.physicalState]}</span><span className="reagent-row-actions"><button disabled={deleting} aria-label={`删除试剂目录 ${item.name}`} title="删除试剂目录" onClick={() => onDelete(item)}><Trash2 size={14} /></button></span></article>)}{!items.length ? <EmptyState title="暂无试剂目录" description="创建化学品目录项后，才可以录入具体容器。" /> : null}</div> }

function ReagentHistoryDrawer({ reagent, items, loading, error, onClose }: { reagent: ReagentRecord; items: ReagentHistoryRecord[]; loading: boolean; error: Error | null; onClose: () => void }) {
  return <div className="reagent-history-backdrop" role="presentation"><aside className="reagent-history-drawer" role="dialog" aria-modal="true" aria-label={`${reagent.name} 操作历史`}><header><div><span>REAGENT LEDGER</span><h2>操作历史</h2><p>{reagent.name} · {reagent.containerName || reagent.materialUuid}</p><code className="reagent-uuid" title="试剂记录 UUID">{reagent.uuid}</code></div><button aria-label="关闭操作历史" onClick={onClose}>×</button></header><section className="reagent-history-summary"><div><small>当前余量</small><strong>{reagent.quantity ?? '—'} {reagent.quantityUnit || ''}</strong></div><div><small>当前修订</small><strong>r{reagent.revision}</strong></div><div><small>历史记录</small><strong>{items.length}</strong></div></section><div className="reagent-history-identities"><span>试剂 UUID<code>{reagent.uuid}</code></span><span>容器物料 UUID<code>{reagent.materialUuid}</code></span></div><section className="reagent-history-timeline">{loading ? <EmptyState title="正在读取操作历史" description="正在从本地不可变台账读取记录。" /> : error ? <EmptyState title="操作历史读取失败" description={error.message} /> : items.length ? items.map((item) => <ReagentHistoryItem key={item.uuid} item={item} />) : <EmptyState title="暂无操作历史" description="该试剂尚未产生台账记录。" />}</section></aside></div>
}

function ReagentHistoryItem({ item }: { item: ReagentHistoryRecord }) {
  const event = historyEvent(item.eventType)
  const delta = `${item.quantityDelta > 0 ? '+' : ''}${item.quantityDelta} ${item.quantityUnit}`
  return <article data-event={item.eventType}><span className="history-marker">{event.short}</span><div><header><div><strong>{event.label}</strong><time>{formatHistoryTime(item.recordedAt)}</time></div><em data-positive={item.quantityDelta > 0}>{delta}</em></header><dl><div><dt>变更后余量</dt><dd>{item.resultQuantity ?? '—'} {item.resultQuantityUnit || item.quantityUnit}</dd></div><div><dt>操作方</dt><dd>{item.operatorType}</dd></div><div><dt>修订</dt><dd>r{item.revision}</dd></div>{item.source ? <div><dt>来源</dt><dd>{item.source}</dd></div> : null}{item.eventType === 'dispense_target' && item.sourceReagentUuid ? <div><dt>分装自</dt><dd><code>{item.sourceReagentUuid}</code></dd></div> : null}{item.eventType === 'dispense_source' && item.targetReagentUuids?.length ? <div><dt>分装到</dt><dd>{item.targetReagentUuids.length} 个容器</dd></div> : null}{item.causationId ? <div><dt>分装批次</dt><dd><code>{item.causationId}</code></dd></div> : null}</dl>{item.workflowTaskUuid || item.workflowNodeJobUuid ? <p>{item.workflowTaskUuid ? <>任务 <code>{item.workflowTaskUuid}</code></> : null}{item.workflowNodeJobUuid ? <>节点作业 <code>{item.workflowNodeJobUuid}</code></> : null}</p> : null}<code className="history-uuid">{item.uuid}</code></div></article>
}

function historyEvent(type: string) {
  if (type === 'add') return { label: '录入 / 补充', short: '+' }
  if (type === 'consume') return { label: '工作流消耗', short: '−' }
  if (type === 'remove') return { label: '移除试剂', short: '×' }
  if (type === 'dispense_source') return { label: '分装出库', short: '⇢' }
  if (type === 'dispense_target') return { label: '分装入库', short: '⇠' }
  return { label: '调整余量', short: '↕' }
}

function formatHistoryTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function validateIdentity(form: IdentityForm, parameters: CustomParameter[]) {
  if (!form.name.trim()) return '试剂名称不能为空'
  if (form.cas.trim() && !isValidCas(form.cas.trim())) return 'CAS 号校验位不正确，请修正或留空'
  for (const [label, value] of [['分子量', form.molecularWeight], ['参考密度', form.density]]) if (value && (!Number.isFinite(Number(value)) || Number(value) <= 0)) return `${label}必须是大于零的有限数`
  const names = new Set<string>()
  for (const parameter of parameters) { const name = parameter.name.trim().toLowerCase(); if (!name || !parameter.value.trim()) return '自定义参数的名称和值都不能为空'; if (names.has(name)) return `自定义参数名称“${parameter.name.trim()}”重复`; names.add(name) }
  return null
}
function replaceAutoValue(current: string, previous?: string, next?: string) { return !current.trim() || current === previous ? next || '' : current }
function numberText(value?: number) { return value == null ? undefined : String(value) }
function numberOrUndefined(value: string) { return value.trim() ? Number(value) : undefined }
function textOrUndefined(value: string) { return value.trim() || undefined }
function lookupFallback(status: CompoundLookupResult['status']) { return status === 'registered' ? '该 CAS 已登记，请直接选择现有试剂目录项。' : status === 'not_found' ? '未查到该 CAS，请手工填写化学信息。' : '外部化合物数据源不可用，可手工填写化学信息。' }
