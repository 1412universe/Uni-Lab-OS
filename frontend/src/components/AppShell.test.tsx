import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'

describe('AppShell', () => {
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
