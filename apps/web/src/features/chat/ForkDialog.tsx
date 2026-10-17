import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import { api } from '../../api/workbench'
import type { Message, Thread, ThreadFork } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import './history.css'

export function ForkDialog({
  message,
  sourceThreadId,
  onCreated,
  onClose,
}: {
  message: Message
  sourceThreadId: string
  onCreated: (thread: Thread) => Promise<void>
  onClose: () => void
}) {
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [error, setError] = useState('')
  const submitted = useRef<ThreadFork | null>(null)
  async function create() {
    if (busy || message.status !== 'completed' || message.forkable !== true) return
    const input = submitted.current ?? {
      source_message_id: message.id,
      include_history: true as const,
      request_id: crypto.randomUUID(),
      ...(title.trim() ? { title: title.trim() } : {}),
    }
    submitted.current = input
    setBusy(true)
    setError('')
    try {
      const thread = await api.forkThread(sourceThreadId, input)
      await onCreated(thread)
      setUnknown(false)
      submitted.current = null
    } catch (error) {
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      setUnknown(uncertain)
      if (!uncertain) submitted.current = null
      setError(
        `${error instanceof Error ? error.message : '分支请求未完成。'}${uncertain ? ' 本次内容与请求标识已保留，核对不会创建不同内容的第二个分支。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      title="从此消息创建历史分支"
      onClose={() => {
        if (!busy && !unknown) onClose()
      }}
    >
      <div className="chat-fork-dialog">
        <p className="page-note">
          仅在你确认后，将原对话开始到此消息（含此条）的所选历史带入新对话。原对话不变，不发送模型请求；后续任务只在你选择的对话中启动。
        </p>
        <p className="page-note">来源消息：{message.id}</p>
        <pre>{message.content}</pre>
        <form
          className="stacked-form"
          onSubmit={(event) => {
            event.preventDefault()
            void create()
          }}
        >
          <label>
            新分支标题（可留空）
            <input
              value={title}
              maxLength={200}
              disabled={busy || unknown}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          {unknown && (
            <p className="memory-conflict">
              待核对请求：{submitted.current?.request_id}。核对前保留此窗口，不修改提交内容。
            </p>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <div className="button-row">
            <Button disabled={busy || unknown} onClick={onClose}>
              取消
            </Button>
            <Button variant="primary" type="submit" disabled={busy}>
              {busy ? '正在提交…' : unknown ? '核对本次分支请求' : '确认带入所选历史并创建分支'}
            </Button>
          </div>
        </form>
      </div>
    </Dialog>
  )
}
