import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('shows the Edge startup mode in the global header', () => {
    const props = {
      page: 'tasks' as const,
      connection: 'connected' as const,
      activeTaskCount: 0,
      onNavigate: vi.fn(),
      onNotify: vi.fn(),
    }
    const { rerender } = render(
      <AppShell {...props} startupMode="develop"><div>content</div></AppShell>,
    )

    expect(screen.getByText('开发模式')).toBeInTheDocument()

    rerender(
      <AppShell {...props} startupMode="product"><div>content</div></AppShell>,
    )
    expect(screen.getByText('生产模式')).toBeInTheDocument()
    expect(screen.queryByText('开发模式')).not.toBeInTheDocument()
  })

  it('does not infer an Edge startup mode before readiness is authoritative', () => {
    render(
      <AppShell
        page="tasks"
        connection="loading"
        activeTaskCount={0}
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      >
        <div>content</div>
      </AppShell>,
    )

    expect(screen.getByText('模式未知')).toBeInTheDocument()
    expect(screen.queryByText('生产模式')).not.toBeInTheDocument()
  })
})
