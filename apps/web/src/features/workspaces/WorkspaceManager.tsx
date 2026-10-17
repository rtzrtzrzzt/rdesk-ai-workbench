import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { workspacesApi } from '../../api/workspaces'
import type { Workspace, WorkspaceLifecycle, WorkspaceRename } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import './workspaces.css'

type Command =
  | { action: 'rename'; input: WorkspaceRename }
  | { action: 'archive' | 'restore' | 'remove'; input: WorkspaceLifecycle }
const actionLabels = { rename: '重命名', archive: '归档', restore: '恢复', remove: '移除登记' }
const describe = (error: unknown) => (error instanceof Error ? error.message : '请求未完成。')
export function WorkspaceManager({
  currentId,
  onChanged,
  onSelect,
  onClose,
}: {
  currentId: string
  onChanged: () => Promise<unknown>
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const list = useQuery({ queryKey: ['workspaces-management'], queryFn: workspacesApi.all })
  async function refresh() {
    const result = await list.refetch()
    if (result.error) throw result.error
    await onChanged()
    return result.data ?? []
  }
  return (
    <Dialog title="管理工作区" onClose={onClose}>
      <div className="workspace-management">
        <p className="page-note">
          归档后不出现在当前工作区选择中，可在此恢复。移除仅取消登记，不删除磁盘上的项目目录；活动任务或电脑会话会阻止归档和移除。
        </p>
        <Button onClick={() => void refresh().catch(() => undefined)}>读取服务器列表</Button>
        {list.error && (
          <p role="alert" className="form-error">
            {list.error.message}
          </p>
        )}
        {list.data ? (
          list.data.length ? (
            list.data.map((workspace) => (
              <WorkspaceRow
                key={workspace.id}
                workspace={workspace}
                current={workspace.id === currentId}
                onRefresh={refresh}
                onSelect={() => {
                  onSelect(workspace.id)
                  onClose()
                }}
              />
            ))
          ) : (
            <p className="page-note">当前没有已登记的工作区。</p>
          )
        ) : (
          <p className="page-note">正在读取工作区…</p>
        )}
      </div>
    </Dialog>
  )
}
function WorkspaceRow({
  workspace,
  current,
  onRefresh,
  onSelect,
}: {
  workspace: Workspace
  current: boolean
  onRefresh: () => Promise<Workspace[]>
  onSelect: () => void
}) {
  const [name, setName] = useState(workspace.name)
  const [version, setVersion] = useState(workspace.version)
  const [pending, setPending] = useState<Command | null>(null)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [message, setMessage] = useState('')
  const [confirm, setConfirm] = useState<'archive' | 'restore' | 'remove' | null>(null)
  async function execute(command: Command) {
    if (busy) return
    setBusy(true)
    setPending(command)
    setMessage('')
    try {
      const result =
        command.action === 'rename'
          ? await workspacesApi.rename(workspace.id, command.input)
          : await workspacesApi.lifecycle(workspace.id, command.action, command.input)
      setPending(null)
      setName(result.name)
      setVersion(result.version)
      setConfirm(null)
      setConflict(false)
      setMessage(`${actionLabels[command.action]}已完成。`)
      await onRefresh().catch((error) =>
        setMessage(`操作已确认，但列表刷新失败：${describe(error)}`),
      )
    } catch (error) {
      const unknown = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      if (!unknown) setPending(null)
      setConflict(error instanceof ApiError && error.status === 409)
      setMessage(
        `${describe(error)}${unknown ? ' 结果未确认；核对将复用同一操作、版本与请求标识。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  function lifecycle(action: 'archive' | 'restore' | 'remove') {
    void execute({ action, input: { expected_version: version, request_id: crypto.randomUUID() } })
  }
  return (
    <section className="workspace-manager-row">
      <div className="run-line">
        <h3>{workspace.name}</h3>
        <Status>{workspace.archived ? '已归档' : current ? '当前工作区' : '未归档'}</Status>
      </div>
      <p className="page-note">{workspace.root_path}</p>
      <p className="page-note">
        服务器版本 {workspace.version} · 草稿基于 {version}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (!pending && !conflict)
            void execute({
              action: 'rename',
              input: {
                name: name.trim(),
                expected_version: version,
                request_id: crypto.randomUUID(),
              },
            })
        }}
      >
        <label>
          名称
          <input
            required
            maxLength={160}
            value={name}
            disabled={busy || !!pending}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <Button
          type="submit"
          disabled={busy || !!pending || conflict || !name.trim() || name.trim() === workspace.name}
        >
          重命名
        </Button>
      </form>
      <div className="button-row">
        {!workspace.archived && <Button onClick={onSelect}>选择工作区</Button>}
        <Button
          disabled={busy || !!pending || conflict}
          onClick={() => setConfirm(workspace.archived ? 'restore' : 'archive')}
        >
          {workspace.archived ? '恢复工作区' : '归档工作区'}
        </Button>
        <Button
          variant="danger"
          disabled={busy || !!pending || conflict}
          onClick={() => setConfirm('remove')}
        >
          移除登记
        </Button>
      </div>
      {confirm && (
        <div className="workspace-action-confirm">
          <p>
            {confirm === 'remove'
              ? `确认移除「${workspace.name}」的登记？磁盘项目目录不会被删除；此操作不会在归档列表中保留恢复入口。`
              : confirm === 'archive'
                ? '确认归档此工作区？将从默认选择中隐藏，项目目录和历史保留，可在此恢复。'
                : '确认恢复此工作区，使其重新出现在默认选择中？'}
          </p>
          <div className="button-row">
            <Button disabled={busy || !!pending} onClick={() => setConfirm(null)}>
              取消
            </Button>
            <Button
              variant={confirm === 'remove' ? 'danger' : 'primary'}
              disabled={busy || !!pending || conflict}
              onClick={() => lifecycle(confirm)}
            >
              确认{actionLabels[confirm]}
            </Button>
          </div>
        </div>
      )}
      {pending && (
        <div className="memory-conflict">
          <p>
            待核对：{actionLabels[pending.action]} · {pending.input.request_id}
            。不会采用新的表单值。
          </p>
          <Button disabled={busy} onClick={() => void execute(pending)}>
            核对本次提交
          </Button>
        </div>
      )}
      {conflict && (
        <div className="memory-conflict">
          <p>服务器状态或版本有变化。名称草稿仍保留，不会自动重试，也不会自动停止活动任务。</p>
          <Button
            disabled={busy}
            onClick={async () => {
              try {
                const latest = (await onRefresh()).find((item) => item.id === workspace.id)
                if (!latest) return
                setVersion(latest.version)
                setConflict(false)
                setConfirm(null)
              } catch (error) {
                setMessage(describe(error))
              }
            }}
          >
            重新读取，保留名称草稿
          </Button>
        </div>
      )}
      {message && (
        <p role="status" className="workspace-manager-message">
          {message}
        </p>
      )}
    </section>
  )
}
