import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Pencil, Trash2 } from 'lucide-react'
import { conversationsApi } from '../../api/conversations'
import type { QueueItem, QueueView } from '../../api/types'
import { Button, IconButton } from '../../components/ui'

export function QueuePanel({
  threadId,
  archived = false,
}: {
  threadId: string
  archived?: boolean
}) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['queue', threadId],
    queryFn: () => conversationsApi.queue(threadId),
    refetchInterval: 3000,
  })
  const [editing, setEditing] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<{ key: string; id: string } | null>(null)
  const items = [...(query.data?.items ?? [])]
    .filter((item) => item.status !== 'dispatched')
    .sort((a, b) => a.position - b.position)
  async function mutate(key: string, operation: (id: string) => Promise<QueueView>) {
    if (busy) return
    setBusy(true)
    setError('')
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() }
    try {
      const result = await operation(pending.current.id)
      client.setQueryData(['queue', threadId], result)
      pending.current = null
      setEditing(null)
      await client.invalidateQueries({ queryKey: ['threads'] })
    } catch (err) {
      setError(err instanceof Error ? err.message : '队列更新未完成。')
      void query.refetch()
    } finally {
      setBusy(false)
    }
  }
  function update(
    item: QueueItem,
    changes: { input?: string; position?: number; remove?: boolean },
  ) {
    void mutate(`${item.id}:${item.version}:${JSON.stringify(changes)}`, (id) =>
      conversationsApi.updateQueue(threadId, item, changes, id),
    )
  }
  if (query.isError)
    return (
      <div className="queue-panel" role="alert">
        <span>{query.error.message}</span>
        <Button onClick={() => void query.refetch()}>重新读取队列</Button>
      </div>
    )
  if (!items.length && !query.data?.paused) return null
  return (
    <section className="queue-panel" aria-label="消息队列">
      <div className="run-line">
        <strong>消息队列 · {items.length}</strong>
        <span>{query.data?.paused ? '已暂停' : '成功完成后自动发送下一条'}</span>
        {query.data?.paused && (
          <Button
            disabled={busy || archived}
            onClick={() =>
              void mutate('resume', (id) => conversationsApi.resumeQueue(threadId, id))
            }
          >
            恢复队列
          </Button>
        )}
      </div>
      {query.data?.paused && (
        <p className="page-note">停止或失败后保留的消息不会自动运行，请明确恢复队列。</p>
      )}
      <ol>
        {items.map((item, index) => (
          <li key={item.id}>
            {editing === item.id ? (
              <div className="queue-edit">
                <textarea
                  aria-label="编辑排队消息"
                  readOnly={busy}
                  value={text}
                  maxLength={1048576}
                  onChange={(event) => setText(event.target.value)}
                />
                <div className="button-row">
                  <Button disabled={busy} onClick={() => setEditing(null)}>
                    取消
                  </Button>
                  <Button
                    disabled={busy || !text.trim()}
                    onClick={() => update(item, { input: text.trim() })}
                  >
                    保存
                  </Button>
                </div>
              </div>
            ) : (
              <>
                <div className="queue-item-content">
                  <p>{item.input}</p>
                  {item.error && (
                    <p role="status" className="form-error">
                      {item.error.message}
                    </p>
                  )}
                  <small>{item.status === 'paused' ? '已暂停' : '等待发送'}</small>
                </div>
                <div className="button-row">
                  <IconButton
                    icon={ArrowUp}
                    label="上移消息"
                    disabled={busy || archived || index === 0}
                    onClick={() => update(item, { position: index - 1 })}
                  />
                  <IconButton
                    icon={ArrowDown}
                    label="下移消息"
                    disabled={busy || archived || index === items.length - 1}
                    onClick={() => update(item, { position: index + 1 })}
                  />
                  <IconButton
                    icon={Pencil}
                    label="编辑排队消息"
                    disabled={busy || archived}
                    onClick={() => {
                      setEditing(item.id)
                      setText(item.input)
                    }}
                  />
                  <IconButton
                    icon={Trash2}
                    label="删除排队消息"
                    disabled={busy || archived}
                    onClick={() => update(item, { remove: true })}
                  />
                </div>
              </>
            )}
          </li>
        ))}
      </ol>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </section>
  )
}
