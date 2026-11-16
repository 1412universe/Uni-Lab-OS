import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { ToastBar } from './ToastBar'

const writeText = vi.fn(async () => {})

beforeEach(() => {
  writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ToastBar', () => {
  it('错误提示渲染为常驻 alert，且内容可选中', () => {
    render(<ToastBar message="保存失败：工作流校验失败" onDismiss={() => {}} />)

    const alert = screen.getByRole('alert')
    expect(alert).toBeInTheDocument()
    // 常驻样式：用于阻止自动消失的视觉标记
    expect(alert.className).toContain('toast-sticky')

    const text = document.querySelector('[data-toast-text]')
    expect(text?.textContent).toBe('保存失败：工作流校验失败')
  })

  it('成功类提示用 status 而不是 alert', () => {
    render(<ToastBar message="已保存，工作流修订推进到 r3" onDismiss={() => {}} />)

    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('点击复制图标把完整提示写入剪贴板', async () => {
    const message = '保存失败：节点作者结果变量重复'
    render(<ToastBar message={message} onDismiss={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: '复制这条提示' }))

    expect(writeText).toHaveBeenCalledWith(message)
    // 复制成功后图标切换为「已复制」
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
    })
  })

  it('复制按钮没有可见文字，只有图标', () => {
    render(<ToastBar message="保存失败：X" onDismiss={() => {}} />)

    const button = screen.getByRole('button', { name: '复制这条提示' })
    expect(button.textContent?.trim()).toBe('')
  })

  it('剪贴板不可用时退回选中文本，不谎报成功', async () => {
    writeText.mockRejectedValueOnce(new Error('not allowed'))
    render(<ToastBar message="保存失败：Y" onDismiss={() => {}} />)

    await userEvent.click(screen.getByRole('button', { name: '复制这条提示' }))

    // 仍然显示「复制」而不是「已复制」
    expect(screen.getByRole('button', { name: '复制这条提示' })).toBeInTheDocument()
  })

  it('点击关闭按钮回调 onDismiss', async () => {
    const onDismiss = vi.fn()
    render(<ToastBar message="保存失败：Z" onDismiss={onDismiss} />)

    await userEvent.click(screen.getByRole('button', { name: '关闭提示' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
