/**
 * 左侧工作流列表，带右键菜单。
 *
 * 交互参照 ChemWorldModel 的 `#wf-pick`（`app_workflow.js:3382-3450`）：
 * - 左键点一项 → 切换到该工作流（可编辑、可运行）；
 * - 右键一项 → 新建 / 重命名 / 复制 / 删除；
 * - 右键空白处 → 仅新建。
 *
 * 菜单做两件事：贴边自动翻转（不超出视口），以及点外部 / Esc 关闭。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Copy, FilePlus2, Pencil, Trash2, Workflow } from 'lucide-react'
import type { WorkflowDefinition } from '../../types'

type MenuAction = 'new' | 'rename' | 'duplicate' | 'delete'

interface MenuState {
  x: number
  y: number
  /** 右键目标；为空表示在空白处右键（只允许新建）。 */
  workflow?: WorkflowDefinition
}

export interface WorkflowPickerProps {
  workflows: WorkflowDefinition[]
  currentUuid: string
  connected: boolean
  onSelect: (uuid: string) => void
  onCreate: (name: string) => Promise<void>
  onRename: (workflow: WorkflowDefinition, name: string) => Promise<void>
  onDuplicate: (workflow: WorkflowDefinition) => Promise<void>
  onDelete: (workflow: WorkflowDefinition) => Promise<void>
}

export function WorkflowPicker({
  workflows,
  currentUuid,
  connected,
  onSelect,
  onCreate,
  onRename,
  onDuplicate,
  onDelete,
}: WorkflowPickerProps) {
  const [menu, setMenu] = useState<MenuState | null>(null)
  const [editing, setEditing] = useState<{ uuid: string; value: string } | null>(null)
  const [creating, setCreating] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)

  const closeMenu = useCallback(() => setMenu(null), [])

  useEffect(() => {
    if (!menu) return
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return
      closeMenu()
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeMenu()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [closeMenu, menu])

  const openMenu = useCallback((event: React.MouseEvent, workflow?: WorkflowDefinition) => {
    event.preventDefault()
    event.stopPropagation()
    setMenu({ x: event.clientX, y: event.clientY, workflow })
  }, [])

  const run = useCallback(async (action: MenuAction) => {
    const target = menu?.workflow
    closeMenu()
    if (action === 'new') {
      setCreating('')
      return
    }
    if (!target || busy) return
    if (action === 'rename') {
      setEditing({ uuid: target.uuid, value: target.name })
      return
    }
    setBusy(true)
    try {
      if (action === 'duplicate') await onDuplicate(target)
      else if (action === 'delete') await onDelete(target)
    } finally {
      setBusy(false)
    }
  }, [busy, closeMenu, menu?.workflow, onDelete, onDuplicate])

  const submitCreate = useCallback(async () => {
    const name = (creating || '').trim()
    if (!name || busy) return
    setBusy(true)
    try {
      await onCreate(name)
      setCreating(null)
    } finally {
      setBusy(false)
    }
  }, [busy, creating, onCreate])

  const submitRename = useCallback(async () => {
    if (!editing || busy) return
    const name = editing.value.trim()
    const target = workflows.find((item) => item.uuid === editing.uuid)
    if (!name || !target) {
      setEditing(null)
      return
    }
    setBusy(true)
    try {
      await onRename(target, name)
      setEditing(null)
    } finally {
      setBusy(false)
    }
  }, [busy, editing, onRename, workflows])

  return (
    <div className="wf-picker" onContextMenu={(event) => openMenu(event)}>
      <div className="wf-picker-head">
        <Workflow size={12} />
        <strong>工作流</strong>
        <small>{workflows.length}</small>
        <button
          type="button"
          className="wf-picker-new"
          title="新建工作流"
          aria-label="新建工作流"
          disabled={!connected}
          onClick={() => setCreating('')}
        >
          <FilePlus2 size={12} />
        </button>
      </div>

      <ul className="wf-picker-list">
        {workflows.map((workflow) => (
          <li key={workflow.uuid}>
            {editing?.uuid === workflow.uuid ? (
              <form
                className="wf-picker-rename"
                onSubmit={(event) => { event.preventDefault(); void submitRename() }}
              >
                <input
                  autoFocus
                  value={editing.value}
                  onChange={(event) => setEditing({ ...editing, value: event.target.value })}
                  onBlur={() => void submitRename()}
                  onKeyDown={(event) => { if (event.key === 'Escape') setEditing(null) }}
                  aria-label="工作流名称"
                />
              </form>
            ) : (
              <button
                type="button"
                className={`wf-picker-item${workflow.uuid === currentUuid ? ' is-current' : ''}`}
                title={`${workflow.name} · r${workflow.revision} ${workflow.status}\n右键：新建 / 重命名 / 复制 / 删除`}
                onClick={() => onSelect(workflow.uuid)}
                onContextMenu={(event) => openMenu(event, workflow)}
              >
                <span className="wf-picker-name">{workflow.name || workflow.uuid.slice(0, 8)}</span>
                <em>r{workflow.revision}</em>
              </button>
            )}
          </li>
        ))}
        {!workflows.length ? <li className="wf-picker-empty">右键这里新建工作流</li> : null}
      </ul>

      {creating !== null ? (
        <form
          className="wf-picker-create"
          onSubmit={(event) => { event.preventDefault(); void submitCreate() }}
        >
          <input
            autoFocus
            value={creating}
            onChange={(event) => setCreating(event.target.value)}
            placeholder="新工作流名称"
            aria-label="新工作流名称"
            onKeyDown={(event) => { if (event.key === 'Escape') setCreating(null) }}
          />
          <button type="submit" disabled={!creating.trim() || busy}>创建</button>
          <button type="button" onClick={() => setCreating(null)}>取消</button>
        </form>
      ) : null}

      {menu ? (
        <div
          className="wf-ctx-menu"
          ref={menuRef}
          style={{
            // 贴边翻转：菜单宽约 132px、每项 26px 高。
            left: Math.min(menu.x, window.innerWidth - 140),
            top: Math.min(menu.y, window.innerHeight - (menu.workflow ? 128 : 40)),
          }}
          role="menu"
        >
          <button type="button" role="menuitem" onClick={() => void run('new')}>
            <FilePlus2 size={12} />新建
          </button>
          {menu.workflow ? (
            <>
              <button type="button" role="menuitem" onClick={() => void run('rename')}>
                <Pencil size={12} />重命名
              </button>
              <button type="button" role="menuitem" onClick={() => void run('duplicate')} disabled={!connected}>
                <Copy size={12} />复制
              </button>
              <button type="button" role="menuitem" className="is-danger" onClick={() => void run('delete')} disabled={!connected}>
                <Trash2 size={12} />删除
              </button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
