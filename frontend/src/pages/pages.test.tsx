import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { demoMaterials, demoTasks, demoWorkflows } from '../data/demo'
import { MaterialsPage } from './MaterialsPage'
import { serialiseTaskInput, TasksPage } from './TasksPage'

function renderWithQuery(ui: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('MaterialsPage', () => {
  it('selects a material row and updates the inspector without rerendering HTML strings', () => {
    const { container } = renderWithQuery(
      <MaterialsPage materials={demoMaterials} total={demoMaterials.length} connected={false} onNotify={vi.fn()} />,
    )

    fireEvent.click(screen.getByRole('row', { name: /MAT-VIAL-088/ }))

    expect(container.querySelector('.inspector-heading h2')).toHaveTextContent('250 mL 样品瓶')
    expect(screen.getByRole('row', { name: /MAT-VIAL-088/ })).toHaveClass('selected')
  })
})

describe('TasksPage', () => {
  it('renders one matrix row per task and lights every running task node', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    expect(container.querySelectorAll('.matrix-row')).toHaveLength(demoTasks.length)
    expect(container.querySelectorAll('.matrix-node-running')).toHaveLength(3)
  })

  it('filters the matrix to failed tasks', () => {
    const { container } = renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /异常1/ }))
    expect(container.querySelectorAll('.matrix-row')).toHaveLength(1)
    expect(container.querySelector('.matrix-row')).toHaveTextContent('TK-240831-015')
  })

  it('separates different frozen execution plans even when workflow UUID is shared', () => {
    const tasks = [
      demoTasks[0],
      { ...demoTasks[1], planSignature: 'single-node-debug-plan', workflowRevision: 4 },
    ]
    const { container } = renderWithQuery(
      <TasksPage tasks={tasks} workflows={demoWorkflows} materials={demoMaterials} connected={false} onRefresh={vi.fn()} onNotify={vi.fn()} />,
    )

    expect(container.querySelectorAll('.matrix-group')).toHaveLength(2)
  })

  it('renders ResourceSlot inputs as Edge material selectors', () => {
    renderWithQuery(
      <TasksPage
        tasks={demoTasks}
        workflows={demoWorkflows}
        materials={demoMaterials}
        connected={false}
        onRefresh={vi.fn()}
        onNotify={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    fireEvent.change(screen.getByLabelText('工作流'), { target: { value: demoWorkflows[1].uuid } })

    expect(screen.getByLabelText(/resource必填/)).toHaveValue('')
    expect(screen.getAllByRole('option', { name: /500 mL 烧杯/ })).toHaveLength(3)
  })
})

describe('serialiseTaskInput', () => {
  it('omits blank optional values and rejects invalid numeric input', () => {
    const fields = [
      { name: 'volume', type: 'integer', required: true, schema: { type: 'integer' } },
      { name: 'note', type: 'string', required: false, schema: { type: 'string' } },
    ]
    expect(serialiseTaskInput(fields, { volume: '8', note: '' })).toEqual({ volume: 8 })
    expect(() => serialiseTaskInput(fields, { volume: 'abc', note: '' })).toThrow('必须是整数')
  })

  it('does not invent a false value for an unset optional boolean', () => {
    expect(serialiseTaskInput(
      [{ name: 'skip_robot', type: 'boolean', required: false, schema: { type: 'boolean' } }],
      { skip_robot: '' },
    )).toEqual({})
  })

  it('serialises scalar ResourceSlot values with the Edge uuid envelope', () => {
    expect(serialiseTaskInput(
      [{ name: 'resource', type: 'ResourceSlot', required: true, schema: { $slot: 'ResourceSlot' } }],
      { resource: 'mat-uuid' },
    )).toEqual({ resource: { uuid: 'mat-uuid' } })
  })

  it('parses structured JSON inputs and rejects a mismatched shape', () => {
    const fields = [
      { name: 'configuration', type: 'object', required: true, schema: { type: 'object' } },
      { name: 'replicates', type: 'array', required: true, schema: { type: 'array' } },
    ]

    expect(serialiseTaskInput(fields, {
      configuration: '{"mode":"fast"}',
      replicates: '[1,2]',
    })).toEqual({ configuration: { mode: 'fast' }, replicates: [1, 2] })
    expect(() => serialiseTaskInput(fields, {
      configuration: '[]',
      replicates: '[1,2]',
    })).toThrow('必须是 JSON 对象')
  })
})
