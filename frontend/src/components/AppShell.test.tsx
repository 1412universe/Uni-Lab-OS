import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell', () => {
  it('shows the Edge startup mode in the global header', () => {
    const onStartupModeClick = vi.fn()
    const props = {
      page: 'tasks' as const,
      connection: 'connected' as const,
      activeTaskCount: 0,
      onNavigate: vi.fn(),
      onNotify: vi.fn(),
      onStartupModeClick,
    }
    const { rerender } = render(
      <AppShell {...props} startupMode="develop"><div>content</div></AppShell>,
    )

    expect(screen.getByText('开发模式')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '当前为开发模式，切换至生产模式' }))
    expect(onStartupModeClick).toHaveBeenCalledOnce()

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

  it('在侧边栏提供 Swagger 和接口 JSON 两个已有文档入口', () => {
    render(
      <AppShell
        page="overview"
        connection="connected"
        activeTaskCount={0}
        onNavigate={vi.fn()}
        onNotify={vi.fn()}
      >
        <div>页面内容</div>
      </AppShell>,
    )

    expect(screen.getByRole('link', { name: '打开 Swagger 接口文档' })).toHaveAttribute('href', '/api/docs')
    expect(screen.getByRole('link', { name: '打开接口 JSON 文档' })).toHaveAttribute('href', '/api/openapi.json')
  })
})
