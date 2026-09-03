import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { demoMaterials } from '../data/demo'
import { ReagentsPage, summariseDispense } from './ReagentsPage'

function row(id: number, materialUuid: string, quantity: string) {
  return { id, materialUuid, quantity }
}

describe('summariseDispense', () => {
  it('sums valid rows and reports the remaining source quantity', () => {
    const summary = summariseDispense([row(1, 'a', '100'), row(2, 'b', '150')], 500)
    expect(summary.total).toBe(250)
    expect(summary.remaining).toBe(250)
    expect(summary.duplicated).toBe(false)
    expect(summary.ready).toBe(true)
  })

  it('allows dispensing the whole bottle down to zero', () => {
    const summary = summariseDispense([row(1, 'a', '100')], 100)
    expect(summary.remaining).toBe(0)
    expect(summary.ready).toBe(true)
  })

  it('blocks submission when the total exceeds the source quantity', () => {
    const summary = summariseDispense([row(1, 'a', '60'), row(2, 'b', '50')], 100)
    expect(summary.total).toBe(110)
    expect(summary.remaining).toBeLessThan(0)
    expect(summary.ready).toBe(false)
  })

  it('blocks submission when the same container is chosen twice', () => {
    const summary = summariseDispense([row(1, 'a', '10'), row(2, 'a', '10')], 100)
    expect(summary.duplicated).toBe(true)
    expect(summary.ready).toBe(false)
  })

  it('blocks submission while any row is incomplete or non-positive', () => {
    expect(summariseDispense([row(1, '', '10')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '0')], 100).ready).toBe(false)
    expect(summariseDispense([row(1, 'a', '-5')], 100).ready).toBe(false)
    expect(summariseDispense([], 100).ready).toBe(false)
  })
})

describe('ReagentsPage dispense entry', () => {
  it('renders the reagent workspace without a connected edge', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ReagentsPage materials={demoMaterials} connected={false} onNotify={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(screen.getByRole('heading', { name: '试剂' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '录入试剂' })).toBeDisabled()
  })
})
