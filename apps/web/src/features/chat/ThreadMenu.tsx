import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MoreHorizontal } from 'lucide-react'
import { conversationsApi } from '../../api/conversations'
import type { DeletionPreview, DeletionState, Thread } from '../../api/types'
import { Button, IconButton } from '../../components/ui'
import { Dialog } from '../../components/Dialog'

export function ThreadMenu({
  thread,
  onChanged,
  onDeleted,
}: {
  thread: Thread
  onChanged: () => Promise<void>
  onDeleted: (scope: DeletionPreview) => Promise<void>
}) {
  const client = useQueryClient()
  const completedDeletion = useRef<string | null>(null)
  const [mode, setMode] = useState<'menu' | 'rename' | 'archive' | 'delete' | null>(null)
  const [title, setTitle] = useState(thread.title)
  const [preview, setPreview] = useState<DeletionPreview | null>(null)
  const [deletion, setDeletion] = useState<DeletionState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const command = useRef<{ key: string; id: string } | null>(null)
  const pendingDeletion = useQuery({
    queryKey: ['thread-deletion', thread.id],
    queryFn: () => conversationsApi.deletion(thread.id),
    enabled: !!thread.deleting || deletion?.status === 'pending',
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
  })
  const state = pendingDeletion.data ?? deletion
  async function finishDeletion(result: DeletionState, scope: DeletionPreview) {
    if (result.status !== 'completed' || completedDeletion.current === result.id) return
    completedDeletion.current = result.id
    await onDeleted(scope)
  }
  useEffect(() => {
    if (state?.status === 'completed' && preview && completedDeletion.current !== state.id)
      void finishDeletion(state, preview)
        .then(onChanged)
        .catch((err) => setError(err instanceof Error ? err.message : '删除后状态刷新未完成。'))
  }, [state, preview, onDeleted, onChanged])
  async function action(key: string, fn: (id: string) => Promise<unknown>) {
    if (busy) return
    setBusy(true)
    setError('')
    if (command.current?.key !== key) command.current = { key, id: crypto.randomUUID() }
    try {
      await fn(command.current.id)
      command.current = null
      await onChanged()
      if (!key.startsWith('delete:')) setMode(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作未完成。')
    } finally {
      setBusy(false)
    }
  }
  async function showDelete() {
    setMode('delete')
    setPreview(null)
    setError('')
    setBusy(true)
    try {
      if (state?.status === 'blocked') {
        await client.cancelQueries({ queryKey: ['thread-deletion', thread.id], exact: true })
        client.setQueryData(['thread-deletion', thread.id], null)
        setDeletion(null)
      }
      setPreview(await conversationsApi.deletionPreview(thread.id))
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除范围暂时无法读取。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="thread-menu">
      <IconButton
        icon={MoreHorizontal}
        label={`管理对话：${thread.title}`}
        onClick={() => {
          setError('')
          setMode('menu')
        }}
      />
      {mode && (
        <Dialog
          title={
            mode === 'rename'
              ? '重命名对话'
              : mode === 'archive'
                ? '归档对话'
                : mode === 'delete'
                  ? '永久删除对话树'
                  : '管理对话'
          }
          onClose={() => {
            if (!busy) setMode(null)
          }}
        >
          <div className="stacked-form">
            {mode === 'menu' && (
              <>
                <Button
                  disabled={thread.deleting}
                  onClick={() => {
                    setTitle(thread.title)
                    setMode('rename')
                  }}
                >
                  重命名
                </Button>
                <Button
                  disabled={busy || thread.deleting}
                  onClick={() =>
                    thread.archived
                      ? void action(`restore:${thread.version}`, (id) =>
                          conversationsApi.lifecycle(thread, 'restore', false, id),
                        )
                      : setMode('archive')
                  }
                >
                  {thread.archived ? '恢复对话' : '归档对话'}
                </Button>
                <Button disabled={busy} onClick={() => void showDelete()}>
                  永久删除…
                </Button>
              </>
            )}
            {mode === 'rename' && (
              <form
                className="stacked-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  void action(`rename:${thread.version}:${title.trim()}`, (id) =>
                    conversationsApi.rename(thread, title.trim(), id),
                  )
                }}
              >
                <label>
                  对话名称
                  <input
                    autoFocus
                    readOnly={busy}
                    required
                    maxLength={200}
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </label>
                <Button type="submit" disabled={busy || !title.trim()}>
                  保存名称
                </Button>
              </form>
            )}
            {mode === 'archive' && (
              <>
                <p>
                  归档将停止此对话树中仍在运行的任务，并暂停消息队列。归档后可在归档列表找回。
                  恢复对话不会自动运行任务。
                </p>
                <Button
                  disabled={busy}
                  onClick={() =>
                    void action(`archive:${thread.version}`, (id) =>
                      conversationsApi.lifecycle(thread, 'archive', true, id),
                    )
                  }
                >
                  确认停止并归档
                </Button>
              </>
            )}
            {mode === 'delete' && (
              <>
                {state && (
                  <p role="status">
                    {state.status === 'pending'
                      ? '删除处理中'
                      : state.status === 'blocked'
                        ? '删除暂未完成'
                        : '已永久删除'}
                    ：{state.message}
                  </p>
                )}
                {preview && state?.status !== 'pending' && state?.status !== 'completed' && (
                  <>
                    <p>
                      将永久删除此对话及全部后代、子 Agent 对话，共 {preview.thread_count} 个对话、
                      {preview.run_count} 个任务（其中 {preview.active_run_count}{' '}
                      个正在运行）。此操作无法恢复。
                    </p>
                    <details>
                      <summary>查看服务端确认的删除范围</summary>
                      <p>对话</p>
                      <ul>
                        {preview.thread_ids.map((id) => (
                          <li key={id}>{id}</li>
                        ))}
                      </ul>
                      <p>任务</p>
                      <ul>
                        {preview.run_ids.map((id) => (
                          <li key={id}>{id}</li>
                        ))}
                      </ul>
                    </details>
                    <Button
                      disabled={busy}
                      onClick={() =>
                        void action(`delete:${preview.preview_token}`, async (id) => {
                          await client.cancelQueries({
                            queryKey: ['thread-deletion', thread.id],
                            exact: true,
                          })
                          const result = await conversationsApi.delete(
                            thread.id,
                            preview.preview_token,
                            id,
                          )
                          client.setQueryData(['thread-deletion', thread.id], result)
                          setDeletion(result)
                          await finishDeletion(result, preview)
                        })
                      }
                    >
                      确认永久删除全部范围
                    </Button>
                  </>
                )}
                {preview && state?.status !== 'pending' && state?.status !== 'completed' && (
                  <Button disabled={busy} onClick={() => void showDelete()}>
                    重新读取删除范围
                  </Button>
                )}
                {!preview && !busy && !state && (
                  <Button onClick={() => void showDelete()}>重新读取删除范围</Button>
                )}
                {state?.status === 'pending' && (
                  <Button
                    disabled={pendingDeletion.isFetching}
                    onClick={() => void pendingDeletion.refetch()}
                  >
                    重新读取删除状态
                  </Button>
                )}
                {state?.status === 'blocked' && (
                  <Button
                    disabled={busy}
                    onClick={() => {
                      void showDelete()
                    }}
                  >
                    重新核对删除范围
                  </Button>
                )}
                {busy && <p role="status">正在读取或提交…</p>}
              </>
            )}
            {pendingDeletion.error && (
              <p role="alert" className="form-error">
                删除状态读取未完成：{pendingDeletion.error.message}
              </p>
            )}
            {error && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
          </div>
        </Dialog>
      )}
    </div>
  )
}
