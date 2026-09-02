import { describe, expect, it } from 'vitest'
import { pageFromSearch, searchForWorkflow, searchWithPage, workflowTargetFromSearch } from './routes'

describe('pageFromSearch', () => {
  it('accepts the four supported legacy query routes', () => {
    expect(pageFromSearch('?page=overview')).toBe('overview')
    expect(pageFromSearch('?page=materials')).toBe('materials')
    expect(pageFromSearch('?page=workflows')).toBe('workflows')
    expect(pageFromSearch('?page=tasks&variant=C')).toBe('tasks')
  })

  it('falls back to overview for an unknown route', () => {
    expect(pageFromSearch('?page=devices')).toBe('overview')
    expect(pageFromSearch('')).toBe('overview')
  })
})

describe('searchWithPage', () => {
  it('changes only the page while retaining useful selection state', () => {
    expect(searchWithPage('?page=overview&task=task-1', 'tasks')).toBe('?page=tasks&task=task-1')
  })
})

describe('workflow task navigation', () => {
  it('stores the targeted workflow and revision in the same-page URL', () => {
    const search = searchForWorkflow('?page=tasks&deploy=latest', {
      workflowUuid: 'workflow-1',
      revision: 7,
      taskUuid: 'task-1',
    })
    expect(search).toBe('?page=workflows&deploy=latest&workflow=workflow-1&revision=7&task=task-1')
    expect(workflowTargetFromSearch(search)).toEqual({
      workflowUuid: 'workflow-1',
      revision: 7,
      taskUuid: 'task-1',
    })
  })

  it('ignores invalid or incomplete workflow targets', () => {
    expect(workflowTargetFromSearch('?page=workflows')).toBeUndefined()
    expect(workflowTargetFromSearch('?page=workflows&workflow=workflow-1&revision=nope')).toEqual({
      workflowUuid: 'workflow-1',
      revision: undefined,
      taskUuid: undefined,
    })
  })
})
