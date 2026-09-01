import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, FlaskConical, PackagePlus, Plus, Search, Trash2 } from 'lucide-react'
import { Button, EmptyState, PageHeader, Panel, PanelHeader } from '../components/ui'
import { createReagent, createReagentInfo, loadReagentInfos, loadReagents, lookupCompoundByCas } from '../lib/edgeClient'
import type { CompoundLookupResult, MaterialRecord, ReagentInfoRecord } from '../types'

const physicalStateLabels: Record<ReagentInfoRecord['physicalState'], string> = {
  solid: '固体', liquid: '液体', gas: '气体', other: '其他', unknown: '未知',
}
type IdentityForm = {
  name: string; nameEn: string; cas: string; aliases: string; molecularFormula: string
  smiles: string; inchiKey: string; molecularWeight: string; density: string
  physicalState: ReagentInfoRecord['physicalState']; description: string
}
type CustomParameter = { id: number; name: string; value: string }
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
  const [dialog, setDialog] = useState<'catalog' | 'register' | null>(null)
  const [saving, setSaving] = useState(false)
  const [identityForm, setIdentityForm] = useState<IdentityForm>(emptyIdentity)
  const [customParameters, setCustomParameters] = useState<CustomParameter[]>([])
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [formError, setFormError] = useState('')
  const [lookup, setLookup] = useState<{ phase: 'idle' | 'loading' | 'success' | 'warning'; message: string; blocks: boolean }>({ phase: 'idle', message: '有效 CAS 会自动查询化学信息；自配物质可留空。', blocks: false })
  const previousCandidate = useRef<CompoundLookupResult['compound']>(undefined)
  const [registerForm, setRegisterForm] = useState({ materialUuid: '', reagentInfoUuid: '', quantity: '', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '%', description: '' })
  const infosQuery = useQuery({ queryKey: ['reagent-infos'], queryFn: ({ signal }) => loadReagentInfos(signal), enabled: connected, retry: 1 })
  const reagentsQuery = useQuery({ queryKey: ['reagents'], queryFn: ({ signal }) => loadReagents(signal), enabled: connected, retry: 1 })
  const reagentMaterialIds = new Set((reagentsQuery.data || []).map((item) => item.materialUuid))
  const containers = materials.filter((material) => !material.isStructural && !reagentMaterialIds.has(material.uuid))
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

  const loadingError = infosQuery.error || reagentsQuery.error
  return <div className="page reagents-page">
    <PageHeader eyebrow="REAGENT AUTHORITY" title="试剂" description="试剂目录定义化学品身份；试剂库存记录具体容器、数量与浓度。" actions={<><Button icon={<BookOpen size={15} />} onClick={openCatalogDialog}>新增试剂目录</Button><Button tone="primary" icon={<PackagePlus size={15} />} disabled={!infosQuery.data?.length || !containers.length} onClick={() => setDialog('register')}>录入试剂</Button></>} />
    <div className="reagent-summary"><div><FlaskConical size={19} /><span>试剂库存<strong>{reagentsQuery.data?.length || 0}</strong></span></div><div><BookOpen size={19} /><span>试剂目录<strong>{infosQuery.data?.length || 0}</strong></span></div><div><PackagePlus size={19} /><span>可录入容器<strong>{containers.length}</strong></span></div></div>
    <Panel className="reagent-workspace">
      <PanelHeader title={view === 'inventory' ? '试剂库存' : '试剂目录'} description={view === 'inventory' ? '容器级数量、浓度与化学身份' : 'CAS、分子式与基础理化信息'} action={<div className="segmented"><button className={view === 'inventory' ? 'active' : ''} onClick={() => setView('inventory')}>库存</button><button className={view === 'catalog' ? 'active' : ''} onClick={() => setView('catalog')}>目录</button></div>} />
      <label className="reagent-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={view === 'inventory' ? '搜索试剂、CAS、容器或条码' : '搜索名称、别名、CAS 或分子式'} /></label>
      {loadingError ? <div className="connection-alert" role="alert"><div><strong>试剂数据不可用</strong><span>{loadingError instanceof Error ? loadingError.message : '读取失败'}</span></div></div> : null}
      {view === 'inventory' ? <InventoryTable items={inventory} materials={materials} hasCatalog={Boolean(infosQuery.data?.length)} /> : <CatalogTable items={infos} />}
    </Panel>
    {dialog ? <div className="dialog-backdrop" role="presentation"><div className={`material-write-dialog reagent-dialog ${dialog === 'catalog' ? 'reagent-dialog-wide' : ''}`} role="dialog" aria-modal="true"><header><div><span>REAGENT COMMAND</span><h2>{dialog === 'catalog' ? '新增试剂目录' : '录入试剂'}</h2>{dialog === 'catalog' ? <p>输入 CAS 可自动补全化学信息；无 CAS 的自配物质可直接填写名称。</p> : null}</div><button aria-label="关闭" onClick={() => setDialog(null)}>×</button></header>
      {dialog === 'catalog' ? <CatalogForm form={identityForm} setForm={setIdentityForm} lookup={lookup} error={formError} customParameters={customParameters} setCustomParameters={setCustomParameters} advancedOpen={advancedOpen} setAdvancedOpen={setAdvancedOpen} saving={saving} onSave={() => void saveCatalogItem()} /> : <RegisterForm form={registerForm} setForm={setRegisterForm} infos={infosQuery.data || []} containers={containers} saving={saving} onSave={() => void saveRegistration()} />}
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

function RegisterForm({ form, setForm, infos, containers, saving, onSave }: { form: { materialUuid: string; reagentInfoUuid: string; quantity: string; quantityUnit: string; concentrationValue: string; concentrationUnit: string; description: string }; setForm: React.Dispatch<React.SetStateAction<typeof form>>; infos: ReagentInfoRecord[]; containers: MaterialRecord[]; saving: boolean; onSave: () => void }) {
  return <div className="dialog-content reagent-form"><label className="form-field wide"><span>试剂目录 *</span><select value={form.reagentInfoUuid} onChange={(e) => setForm({ ...form, reagentInfoUuid: e.target.value })}><option value="">选择试剂目录项</option>{infos.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · {item.cas || '无 CAS'}</option>)}</select></label><label className="form-field wide"><span>试剂容器 *</span><select value={form.materialUuid} onChange={(e) => setForm({ ...form, materialUuid: e.target.value })}><option value="">选择未登记试剂的容器</option>{containers.map((item) => <option key={item.uuid} value={item.uuid}>{item.name} · {item.barcode}</option>)}</select></label><label className="form-field"><span>数量 *</span><input type="number" min="0" step="any" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label><label className="form-field"><span>单位 *</span><select value={form.quantityUnit} onChange={(e) => setForm({ ...form, quantityUnit: e.target.value })}>{['mL', 'L', 'g', 'mg', 'μL', 'mmol'].map((unit) => <option key={unit}>{unit}</option>)}</select></label><label className="form-field"><span>浓度</span><input type="number" min="0" step="any" value={form.concentrationValue} onChange={(e) => setForm({ ...form, concentrationValue: e.target.value })} /></label><label className="form-field"><span>浓度单位</span><select value={form.concentrationUnit} onChange={(e) => setForm({ ...form, concentrationUnit: e.target.value })}>{['%', 'mol/L', 'mmol/L', 'mg/mL'].map((unit) => <option key={unit}>{unit}</option>)}</select></label><label className="form-field wide"><span>说明</span><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></label><Button tone="primary" icon={<PackagePlus size={15} />} disabled={saving || !form.materialUuid || !form.reagentInfoUuid || !(Number(form.quantity) > 0)} onClick={onSave}>确认录入</Button></div>
}

function InventoryTable({ items, materials, hasCatalog }: { items: Awaited<ReturnType<typeof loadReagents>>; materials: MaterialRecord[]; hasCatalog: boolean }) { return <div className="reagent-table"><header><span>试剂</span><span>容器</span><span>数量</span><span>浓度</span><span>修订</span></header>{items.map((item) => <article key={item.uuid}><span><strong>{item.name}</strong><small>{item.cas || '无 CAS'} · {item.molecularFormula || '无分子式'}</small><code>{item.uuid}</code></span><span><strong>{item.containerName || materials.find((m) => m.uuid === item.materialUuid)?.name || '未知容器'}</strong><small>{item.containerBarcode || materials.find((m) => m.uuid === item.materialUuid)?.barcode || item.materialUuid}</small></span><span><strong>{item.quantity ?? '—'} {item.quantityUnit || ''}</strong><small>{item.quantity != null && item.quantity > 0 ? '可用' : '已空'}</small></span><span>{item.concentrationValue == null ? '—' : `${item.concentrationValue} ${item.concentrationUnit || ''}`}</span><span>r{item.revision}</span></article>)}{!items.length ? <EmptyState title="暂无试剂库存" description={hasCatalog ? '点击“录入试剂”，把目录项登记到具体容器。' : '请先新增试剂目录，再录入容器库存。'} /> : null}</div> }
function CatalogTable({ items }: { items: ReagentInfoRecord[] }) { return <div className="reagent-table identity-table"><header><span>试剂目录</span><span>CAS</span><span>分子式</span><span>物态</span><span>密度</span></header>{items.map((item) => <article key={item.uuid}><span><strong>{item.name}</strong><small>{item.nameEn || item.aliases.join('、') || '无别名'}</small><code>{item.uuid}</code></span><span>{item.cas || '—'}</span><span>{item.molecularFormula || '—'}</span><span>{physicalStateLabels[item.physicalState]}</span><span>{item.densityGPerMl == null ? '—' : `${item.densityGPerMl} g/mL`}</span></article>)}{!items.length ? <EmptyState title="暂无试剂目录" description="创建化学品目录项后，才可以录入具体容器。" /> : null}</div> }

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
