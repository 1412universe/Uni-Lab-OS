import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReagentsPage } from './ReagentsPage'

afterEach(() => vi.unstubAllGlobals())

function response(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as Response
}

describe('ReagentsPage', () => {
  it('disables reagent mutations while showing a stale read-only snapshot', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected={false} onNotify={vi.fn()} /></QueryClientProvider>)

    expect(screen.getByRole('button', { name: '新增试剂目录' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '录入试剂' })).toBeDisabled()
  })

  it('opens the immutable operation history for a reagent container', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/reagent-infos?')) return response({ code: 0, data: { items: [], total: 0 } })
      if (url.includes('/reagents?')) return response({ code: 0, data: { items: [{
        uuid: 'reagent-1', material_uuid: 'material-1', reagent_info_uuid: 'info-1', name: '乙醇',
        cas: '64-17-5', quantity: 500, quantity_unit: 'mL', revision: 1,
        container_name: '乙醇试剂瓶', container_barcode: 'R-001',
      }], total: 1 } })
      if (url.includes('/materials/material-1/reagent-history')) return response({ code: 0, data: { items: [{
        uuid: 'history-1', material_uuid: 'material-1', subject_uuid: 'reagent-1', event_type: 'add',
        operator_type: 'frontend', quantity_delta: 500, quantity_unit: 'mL', revision: 1,
        recorded_at: '2026-09-02T01:02:03.000Z', changes: { result: { quantity: 500, quantity_unit: 'mL' } },
        extension: { source: 'frontend:workbench' },
      }], has_more: false } })
      throw new Error(`Unexpected URL: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ReagentsPage materials={[]} connected onNotify={vi.fn()} /></QueryClientProvider>)

    const historyButton = await screen.findByRole('button', { name: '查看操作历史 乙醇 reagent-1' })
    fireEvent.click(historyButton)

    const drawer = await screen.findByRole('dialog', { name: '乙醇 操作历史' })
    expect(await within(drawer).findByText('录入 / 补充')).toBeInTheDocument()
    expect(within(drawer).getByText('+500 mL')).toBeInTheDocument()
    expect(within(drawer).getByText('frontend:workbench')).toBeInTheDocument()
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/materials/material-1/reagent-history'))).toBe(true)
  })
})
