import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import type { ReagentRecord } from '../types'
import { DispenseForm, EditReagentForm, ReagentsPage, RegisterForm, type RegisterFields } from './ReagentsPage'

const container = { ...demoMaterials[0], uuid: 'bottle', name: '100 mL 瓶', capacity: { max_volume_ul: 100000 }, ratedCapacity: { max_volume_ul: 100000 } }
const reagent: ReagentRecord = { uuid: 'reagent', materialUuid: 'bottle', reagentInfoUuid: 'info', name: '试剂', physicalState: 'liquid', quantity: 50, quantityUnit: 'mL', revision: 1, updatedAt: '', maximumCapacity: { max_volume_ul: 80000 }, ratedCapacity: container.ratedCapacity, materialRevision: 1 }
type EditFields = { quantity: string; concentrationValue: string; concentrationUnit: string; description: string; maximum?: string }
type TargetRow = { id: number; materialUuid: string; quantity: string; maximum?: string }

function Registration({ initial = {} }: { initial?: Partial<RegisterFields> }) {
  const [form, setForm] = useState<RegisterFields>({ materialUuid: 'bottle', reagentInfoUuid: 'info', quantity: '50', quantityUnit: 'mL', concentrationValue: '', concentrationUnit: '', description: '', ...initial })
  return <RegisterForm form={form} setForm={setForm} infos={[]} containers={[container]} templates={[]} filterTags={[]} saving={false} onSave={vi.fn()} />
}

afterEach(() => vi.unstubAllGlobals())

describe('最大装料量交互', () => {
  it('录入只显示一个最大装料量，默认带入容器容量并即时校验', () => {
    render(<Registration />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const save = screen.getByRole('button', { name: '确认录入' })
    expect(maximum).toHaveValue(100)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(1)
    expect(screen.queryByText(/本次装料上限|配置此容器的规格/)).not.toBeInTheDocument()
    expect(screen.queryByRole('spinbutton', { name: /容器规格/ })).not.toBeInTheDocument()
    fireEvent.change(maximum, { target: { value: '40' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 40 mL')
    fireEvent.change(maximum, { target: { value: '101' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent(/额定/)
    fireEvent.change(maximum, { target: { value: '60' } })
    expect(save).toBeEnabled()
    fireEvent.change(maximum, { target: { value: '' } })
    expect(save).toBeEnabled()
  })

  it('清空最大装料量后仍阻止数量超过领域包默认容量', () => {
    render(<Registration initial={{ quantity: '101', maximum: '' }} />)
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 100 mL')
  })

  it('切换单位时换算最大装料量，质量不从容积推算', () => {
    render(<Registration />)
    fireEvent.change(screen.getByRole('spinbutton', { name: '最大装料量（mL）' }), { target: { value: '80' } })
    fireEvent.change(screen.getByRole('combobox', { name: /^单位/ }), { target: { value: 'L' } })
    expect(screen.getByRole('spinbutton', { name: '最大装料量（L）' })).toHaveValue(0.08)
    fireEvent.change(screen.getByRole('combobox', { name: /^单位/ }), { target: { value: 'g' } })
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（g）' })
    expect(maximum).toHaveValue(null)
    fireEvent.change(maximum, { target: { value: '50' } })
    expect(screen.getByRole('button', { name: '确认录入' })).toBeEnabled()
    fireEvent.change(maximum, { target: { value: '49' } })
    expect(screen.getByRole('button', { name: '确认录入' })).toBeDisabled()
  })

  it('编辑默认带入现有最大装料量，清空恢复领域包容量并校验余量', () => {
    function Form() {
      const [form, setForm] = useState<EditFields>({ quantity: '50', concentrationValue: '', concentrationUnit: '', description: '' })
      return <EditReagentForm target={reagent} form={form} setForm={setForm} saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const maximum = screen.getByRole('spinbutton', { name: '最大装料量（mL）' })
    const save = screen.getByRole('button', { name: '保存修改' })
    expect(maximum).toHaveValue(80)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(1)
    fireEvent.change(maximum, { target: { value: '49' } })
    expect(save).toBeDisabled()
    fireEvent.change(maximum, { target: { value: '101' } })
    expect(save).toBeDisabled()
    fireEvent.change(maximum, { target: { value: '' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '90' } })
    expect(save).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '数量' }), { target: { value: '101' } })
    expect(save).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 100 mL')
  })

  it('分装每个目标只显示一个质量上限，并使用该目标的容量', () => {
    const targets = [
      { ...container, uuid: 'target-1', capacity: { max_volume_ul: 300000, max_mass_g: 10 }, ratedCapacity: { max_volume_ul: 300000, max_mass_g: 10 } },
      { ...container, uuid: 'target-2', capacity: { max_mass_g: 20 }, ratedCapacity: { max_mass_g: 20 } },
    ]
    function Form() {
      const [rows, setRows] = useState<TargetRow[]>([{ id: 1, materialUuid: 'target-1', quantity: '10' }, { id: 2, materialUuid: 'target-2', quantity: '20' }])
      return <DispenseForm source={{ ...reagent, physicalState: 'solid', quantity: 50, quantityUnit: 'g' }} rows={rows} setRows={setRows} containers={targets} templates={[]} filterTags={[]} preferredTag="" saving={false} onSave={vi.fn()} />
    }
    render(<Form />)
    const firstMaximum = screen.getByRole('spinbutton', { name: '目标 1 最大装料量（g）' })
    expect(firstMaximum).toHaveValue(10)
    expect(screen.getByRole('spinbutton', { name: '目标 2 最大装料量（g）' })).toHaveValue(20)
    expect(screen.getAllByRole('spinbutton', { name: /最大装料量/ })).toHaveLength(2)
    expect(screen.getByRole('button', { name: '确认分装' })).toBeEnabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '11' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('数量超过最大装料量 10 g')
    fireEvent.change(firstMaximum, { target: { value: '' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeDisabled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '分装量 1' }), { target: { value: '10' } })
    expect(screen.getByRole('button', { name: '确认分装' })).toBeEnabled()
  })

  it.each(['maximum_capacity', 'loading_limits'])('历史以一个最大装料量显示 %s 的前后值', async (capacityKey) => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      let items: unknown[] = []
      if (url.includes('/reagents?')) items = [{ uuid: 'reagent', material_uuid: 'bottle', reagent_info_uuid: 'info', name: '试剂', quantity: 50, quantity_unit: 'mL', revision: 2, maximum_capacity: { max_volume_ul: 60000 }, rated_capacity: { max_volume_ul: 100000 }, material_revision: 2 }]
      if (url.includes('/reagent-history')) items = [{ uuid: 'history', material_uuid: 'bottle', subject_uuid: 'reagent', event_type: 'adjust', quantity_delta: 0, quantity_unit: 'mL', revision: 2, recorded_at: '2026-09-07T01:00:00Z', changes: { previous: { [capacityKey]: { max_volume_ul: 80000 } }, result: { quantity: 50, [capacityKey]: { max_volume_ul: 60000 } } } }]
      return { ok: true, status: 200, json: async () => ({ code: 0, data: { items, total: items.length } }) } as Response
    }))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ReagentsPage materials={[container]} connected onNotify={vi.fn()} /></QueryClientProvider>)
    fireEvent.click(await screen.findByRole('button', { name: '查看操作历史 试剂 reagent' }))
    const history = await screen.findByRole('dialog', { name: '试剂 操作历史' })
    expect(await within(history).findByText('80 mL → 60 mL')).toBeInTheDocument()
    expect(within(history).getByText('最大装料量')).toBeInTheDocument()
    expect(within(history).queryByText('容器规格')).not.toBeInTheDocument()
    expect(within(history).getByText('0 mL')).toBeInTheDocument()
  })
})
