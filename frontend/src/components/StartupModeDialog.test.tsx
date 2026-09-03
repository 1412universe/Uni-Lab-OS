import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { StartupModeDialog } from './StartupModeDialog'

describe('StartupModeDialog', () => {
  it('explains the session-only switch and requires explicit confirmation', () => {
    const onConfirm = vi.fn()
    render(
      <StartupModeDialog
        currentMode="develop"
        pending={false}
        blockers={[]}
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    )

    expect(screen.getByText('切换至生产模式')).toBeInTheDocument()
    expect(screen.getByText(/仅影响当前 Runtime 会话/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })

  it('renders every authoritative task blocker in the dialog', () => {
    render(
      <StartupModeDialog
        currentMode="develop"
        pending={false}
        errorMessage="存在未结束或未完成清理的任务，不能切换模式"
        blockers={[
          {
            taskUuid: 'task-running',
            workflowUuid: 'workflow-1',
            status: 'running',
            cleanupStatus: 'none',
            executionKind: 'workflow',
          },
          {
            taskUuid: 'task-cleanup',
            workflowUuid: 'workflow-2',
            status: 'failed',
            cleanupStatus: 'requires_attention',
            executionKind: 'workflow',
          },
        ]}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('不能切换模式')
    expect(screen.getByText('task-running')).toBeInTheDocument()
    expect(screen.getByText('运行中')).toBeInTheDocument()
    expect(screen.getByText('task-cleanup')).toBeInTheDocument()
    expect(screen.getByText('清理需人工处理')).toBeInTheDocument()
  })
})
