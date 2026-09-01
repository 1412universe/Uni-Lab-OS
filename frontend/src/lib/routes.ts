import type { PageId } from '../types'

const supportedPages = new Set<PageId>(['overview', 'materials', 'reagents', 'operations', 'workflows', 'tasks'])

export function pageFromSearch(search: string): PageId {
  const page = new URLSearchParams(search).get('page')
  return page && supportedPages.has(page as PageId) ? (page as PageId) : 'overview'
}

export function searchWithPage(search: string, page: PageId): string {
  const params = new URLSearchParams(search)
  params.set('page', page)
  return `?${params.toString()}`
}
