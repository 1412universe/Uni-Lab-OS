import { describe, expect, it } from 'vitest'
import { pageFromSearch, searchWithPage } from './routes'

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
