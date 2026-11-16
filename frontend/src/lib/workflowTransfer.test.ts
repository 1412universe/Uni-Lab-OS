import { describe, expect, it } from 'vitest'
import { buildBatchExportFile, buildExportFile, exportFilename, parseImportPayload } from './workflowTransfer'

const nodes = [{ uuid: 'n1', name: 'A' }]
const edges = [{ uuid: 'e1', source_node_uuid: 'n1', target_node_uuid: 'n1' }]

describe('workflowTransfer', () => {
  it('exports the exact shape the import endpoint accepts', () => {
    const file = buildExportFile('金四面体 · 三次离心', 'normal', nodes, edges, { workflowUuid: 'wf-1', revision: 3 })
    expect(file.name).toBe('金四面体 · 三次离心')
    expect(file.workflow_type).toBe('normal')
    expect(file.data.nodes).toBe(nodes)
    expect(file.data.edges).toBe(edges)
    expect(file.unilab_export.revision).toBe(3)
    expect(exportFilename('金四面体 · 三次离心', 3)).toBe('金四面体_·_三次离心-r3.json')
  })

  it('parses legacy CWM payloads wrapping nodes under data', () => {
    const parsed = parseImportPayload(JSON.stringify({
      target_lab_uuid: 'lab-1',
      name: 'CWM 导出',
      data: { nodes, edges },
    }), 'cwm.json')
    expect(parsed.failures).toHaveLength(0)
    expect(parsed.items[0].payload.name).toBe('CWM 导出')
    expect(parsed.items[0].payload.data.nodes).toHaveLength(1)
  })

  it('splits a batch bundle into one import task per workflow', () => {
    const bundle = buildBatchExportFile([
      buildExportFile('w1', 'normal', nodes, edges),
      buildExportFile('w2', 'experiment_operation', nodes, edges),
    ])
    const parsed = parseImportPayload(JSON.stringify(bundle), 'all.json')
    expect(parsed.items).toHaveLength(2)
    expect(parsed.items[1].payload.workflow_type).toBe('experiment_operation')
  })

  it('reports unreadable files instead of throwing', () => {
    const broken = parseImportPayload('{ not json', 'broken.json')
    expect(broken.items).toHaveLength(0)
    expect(broken.failures[0].reason).toContain('不是合法 JSON')

    const empty = parseImportPayload(JSON.stringify({ name: '空', data: { nodes: [], edges: [] } }), 'empty.json')
    expect(empty.failures[0].reason).toContain('节点列表为空')

    const shape = parseImportPayload(JSON.stringify({ hello: 'world' }), 'other.json')
    expect(shape.failures[0].reason).toContain('data.nodes')
  })
})
