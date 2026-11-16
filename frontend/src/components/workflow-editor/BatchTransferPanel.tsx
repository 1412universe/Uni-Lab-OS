/**
 * 批量导入 / 导出面板。
 *
 * 导出是纯前端落地（Blob 下载），导入走后端 `POST /workflows/import`——只有后端
 * 能判定模板 UUID 是否在本机目录里。批量包（`unilab_batch_export`）会被拆成多条
 * 逐个提交，某一条失败不影响其余条目，失败原因原样回显。
 */
import { useRef, useState } from 'react'
import { CheckCircle2, Download, FileJson, Loader2, Upload, X, XCircle } from 'lucide-react'
import { Button } from '../ui'
import {
  importWorkflowPayload,
  loadWorkflowGraphRaw,
  type RawWorkflowEdge,
  type RawWorkflowNode,
} from '../../lib/edgeClient'
import {
  buildBatchExportFile,
  buildExportFile,
  downloadJson,
  exportFilename,
  parseImportPayload,
  type WorkflowExportFile,
  type WorkflowImportPayload,
  type WorkflowType,
} from '../../lib/workflowTransfer'
import type { WorkflowDefinition } from '../../types'

type RowStatus = 'pending' | 'running' | 'ok' | 'fail'

interface ImportRow {
  key: string
  source: string
  payload: WorkflowImportPayload
  status: RowStatus
  message: string
}

interface BatchTransferPanelProps {
  workflowName: string
  workflowType: WorkflowType
  workflowUuid?: string
  revision: number
  workflows: WorkflowDefinition[]
  connected: boolean
  nodeCount: number
  edgeCount: number
  snapshot: () => { nodes: RawWorkflowNode[]; edges: RawWorkflowEdge[] }
  onClose: () => void
  onNotify: (message: string) => void
  onImported: () => void
}

export function BatchTransferPanel({
  workflowName,
  workflowType,
  workflowUuid,
  revision,
  workflows,
  connected,
  nodeCount,
  edgeCount,
  snapshot,
  onClose,
  onNotify,
  onImported,
}: BatchTransferPanelProps) {
  const [rows, setRows] = useState<ImportRow[]>([])
  const [busy, setBusy] = useState(false)
  const [exportingAll, setExportingAll] = useState(false)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const input = useRef<HTMLInputElement | null>(null)

  const currentSnapshot = () => snapshot()

  const exportCurrent = () => {
    const raws = currentSnapshot()
    if (!raws.nodes.length) {
      onNotify('画布为空，没有可导出的节点')
      return
    }
    const file = buildExportFile(workflowName || '未命名工作流', workflowType, raws.nodes, raws.edges, {
      workflowUuid,
      revision,
    })
    downloadJson(exportFilename(workflowName, revision), file)
    onNotify(`已导出 ${raws.nodes.length} 节点 / ${raws.edges.length} 连线`)
  }

  const exportAll = async () => {
    if (!workflows.length) return
    setExportingAll(true)
    setProgress({ done: 0, total: workflows.length })
    const files: WorkflowExportFile[] = []
    const failed: string[] = []
    for (const workflow of workflows) {
      try {
        const graph = await loadWorkflowGraphRaw(workflow.uuid)
        files.push(buildExportFile(
          workflow.name,
          (workflow.workflowType === 'experiment_operation' ? 'experiment_operation' : 'normal') as WorkflowType,
          graph.nodes,
          graph.edges,
          { workflowUuid: workflow.uuid, revision: graph.revision },
        ))
      } catch (error) {
        failed.push(`${workflow.name}：${error instanceof Error ? error.message : '读取失败'}`)
      }
      setProgress((current) => ({ ...current, done: current.done + 1 }))
    }
    setExportingAll(false)
    if (!files.length) {
      onNotify('全部工作流读取失败，未生成文件')
      return
    }
    const stamp = new Date().toISOString().slice(0, 10)
    downloadJson(`unilab-workflows-${stamp}.json`, buildBatchExportFile(files))
    onNotify(failed.length
      ? `已导出 ${files.length} 个工作流，${failed.length} 个失败：${failed[0]}`
      : `已导出 ${files.length} 个工作流`)
  }

  const readFiles = async (fileList: FileList | null) => {
    if (!fileList || !fileList.length) return
    const next: ImportRow[] = []
    for (const file of Array.from(fileList)) {
      const text = await file.text()
      const parsed = parseImportPayload(text, file.name)
      for (const failure of parsed.failures) {
        next.push({
          key: `${file.name}-err-${next.length}`,
          source: failure.source,
          payload: { name: '', workflow_type: 'normal', data: { nodes: [], edges: [] } },
          status: 'fail',
          message: failure.reason,
        })
      }
      for (const item of parsed.items) {
        next.push({
          key: `${item.source}-${next.length}`,
          source: item.source,
          payload: item.payload,
          status: 'pending',
          message: `${item.payload.data.nodes.length} 节点 / ${item.payload.data.edges.length} 连线`,
        })
      }
    }
    setRows((current) => [...current, ...next])
    if (input.current) input.current.value = ''
  }

  const runImport = async () => {
    const queue = rows.filter((row) => row.status === 'pending')
    if (!queue.length) return
    setBusy(true)
    setProgress({ done: 0, total: queue.length })
    let created = 0
    for (const row of queue) {
      setRows((current) => current.map((item) => (item.key === row.key ? { ...item, status: 'running' } : item)))
      try {
        const result = await importWorkflowPayload(row.payload as unknown as Record<string, unknown>)
        created += 1
        setRows((current) => current.map((item) => (item.key === row.key
          ? { ...item, status: 'ok', message: `已创建工作流 ${String(result.uuid || '').slice(0, 8)}…` }
          : item)))
      } catch (error) {
        setRows((current) => current.map((item) => (item.key === row.key
          ? { ...item, status: 'fail', message: error instanceof Error ? error.message : '导入失败' }
          : item)))
      }
      setProgress((current) => ({ ...current, done: current.done + 1 }))
    }
    setBusy(false)
    onNotify(created ? `导入完成：新增 ${created} 个工作流` : '导入完成，但没有成功条目')
    if (created) onImported()
  }

  const pending = rows.filter((row) => row.status === 'pending').length

  return (
    <div className="wf-drawer is-wide" role="dialog" aria-label="批量导入导出">
      <header>
        <strong>批量导入 / 导出 JSON</strong>
        <button type="button" aria-label="关闭" onClick={onClose}><X size={15} /></button>
      </header>
      <div className="wf-drawer-body">
        <section className="wf-io-section">
          <h4><Download size={13} />导出</h4>
          <div className="wf-io-actions">
            <Button icon={<FileJson size={13} />} onClick={exportCurrent} disabled={!nodeCount && !edgeCount}>
              导出当前草稿
            </Button>
            <Button
              icon={exportingAll ? <Loader2 size={13} className="wf-spin" /> : <Download size={13} />}
              onClick={() => void exportAll()}
              disabled={exportingAll || !workflows.length}
            >
              {exportingAll ? `导出中 ${progress.done}/${progress.total}` : `导出全部（${workflows.length} 个）`}
            </Button>
          </div>
          <p className="wf-io-hint">
            导出的是画布当前状态（未保存的改动也在内），格式为
            <code>{'{ name, workflow_type, data: { nodes, edges } }'}</code>，与后端导入接口一致。
          </p>
        </section>

        <section className="wf-io-section">
          <h4><Upload size={13} />导入</h4>
          <div className="wf-io-actions">
            <input
              ref={input}
              type="file"
              accept="application/json,.json"
              multiple
              style={{ display: 'none' }}
              onChange={(event) => void readFiles(event.target.files)}
            />
            <Button icon={<Upload size={13} />} onClick={() => input.current?.click()}>
              选择 JSON 文件（可多选）
            </Button>
            <Button
              tone="primary"
              icon={busy ? <Loader2 size={13} className="wf-spin" /> : <Upload size={13} />}
              disabled={!pending || busy || !connected}
              onClick={() => void runImport()}
            >
              {busy ? `导入中 ${progress.done}/${progress.total}` : `开始导入（${pending}）`}
            </Button>
          </div>
          <p className="wf-io-hint">
            支持编辑器导出格式、CWM 的 <code>{'{ target_lab_uuid, name, data }'}</code> 以及批量包。
            节点模板 UUID 不在本机目录时后端会逐条报错，不影响其它文件。
          </p>

          {rows.length ? (
            <>
              <ul className="wf-io-list">
                {rows.map((row) => (
                  <li key={row.key} className={`is-${row.status}`}>
                    <span className="wf-io-status">
                      {row.status === 'ok' ? <CheckCircle2 size={13} /> : null}
                      {row.status === 'fail' ? <XCircle size={13} /> : null}
                      {row.status === 'running' ? <Loader2 size={13} className="wf-spin" /> : null}
                      {row.status === 'pending' ? <FileJson size={13} /> : null}
                    </span>
                    <div>
                      <strong>{row.payload.name || row.source}</strong>
                      <small>{row.source} · {row.message}</small>
                    </div>
                  </li>
                ))}
              </ul>
              <button type="button" className="wf-io-clear" onClick={() => setRows([])}>清空列表</button>
            </>
          ) : null}
        </section>
      </div>
    </div>
  )
}
