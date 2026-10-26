import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, authGeneration } from '../../api/client'
import { conversationHistoryApi } from '../../api/conversationHistory'
import type {
  ConversationHistorySave,
  ConversationHistoryScope,
  ConversationHistoryView,
} from '../../api/conversationHistory'
import { Button } from '../../components/ui'
import { useSettingsAttention } from './SettingsGroup'

const scopeValid = (value: unknown): value is ConversationHistoryScope =>
  value === 'disabled' || value === 'workspace' || value === 'user'

function restorePending(key: string): ConversationHistorySave | null {
  try {
    const raw = sessionStorage.getItem(key)
    if (!raw) return null
    const value = JSON.parse(raw) as ConversationHistorySave
    if (
      value &&
      scopeValid(value.scope) &&
      Number.isInteger(value.expected_version) &&
      value.expected_version >= 0 &&
      typeof value.request_id === 'string' &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.request_id)
    )
      return value
  } catch {
    // Recovery data never authorizes a submission without a user action.
  }
  return null
}

export function ConversationHistory({ userId }: { userId: string }) {
  const generation = authGeneration()
  return <ConversationHistorySetting key={`${userId}:${generation}`} userId={userId} />
}

function ConversationHistorySetting({ userId }: { userId: string }) {
  const client = useQueryClient()
  const generation = authGeneration()
  const key = ['conversation-history-settings', userId, generation] as const
  const storageKey = `workbench.conversation-history.pending:${userId}`
  const [pending, setPending] = useState(() => restorePending(storageKey))
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [needsReview, setNeedsReview] = useState(false)
  const [message, setMessage] = useState(() =>
    pending ? '上次保存结果待核对，请核对本次保存。' : '',
  )
  const inFlight = useRef(false)
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => conversationHistoryApi.settings(signal),
    retry: false,
    refetchInterval: 4000,
  })
  useSettingsAttention(
    'conversation-history',
    !!pending || conflict || needsReview || query.isError,
    '其他对话读取范围需要核对',
  )

  async function reload() {
    if (inFlight.current) return
    const result = await query.refetch()
    if (authGeneration() !== generation) return
    if (result.isError) {
      setNeedsReview(true)
      setMessage('读取失败，请稍后重新读取。')
      return
    }
    setConflict(false)
    setNeedsReview(false)
    setMessage('已读取最新设置，请确认范围后重新选择。')
  }

  async function save(command: ConversationHistorySave) {
    if (inFlight.current || authGeneration() !== generation) return
    // Retain the exact command before dispatch so a lost response can be replayed safely.
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(command))
    } catch {
      setNeedsReview(true)
      setMessage('无法保留本次保存请求，尚未提交。请检查浏览器存储后重试。')
      return
    }
    inFlight.current = true
    setPending(command)
    setBusy(true)
    setNeedsReview(false)
    setMessage('')
    try {
      const receipt = await conversationHistoryApi.save(command)
      if (authGeneration() !== generation) return
      await client.cancelQueries({ queryKey: key })
      if (authGeneration() !== generation) return
      client.setQueryData<ConversationHistoryView>(key, (old) =>
        old && old.version > receipt.version ? old : receipt,
      )
      sessionStorage.removeItem(storageKey)
      setPending(null)
      setConflict(false)
      const latest = await query.refetch()
      if (authGeneration() !== generation) return
      setMessage(
        latest.isError
          ? '保存已确认，暂未读取最新设置；稍后会继续同步。'
          : latest.data && latest.data.version > receipt.version
            ? '本次保存已核对，当前显示账户随后更新的最新范围。'
            : '已保存，其他浏览器和下一次工具调用会按此范围生效。',
      )
    } catch (error) {
      if (authGeneration() !== generation) return
      const certain = error instanceof ApiError && error.status < 500 && error.status !== 408
      if (certain) {
        setNeedsReview(true)
        try {
          sessionStorage.removeItem(storageKey)
        } catch {
          // A stale recovery record is safe to replay with its original request identity.
        }
        setPending(null)
        const changed = error.status === 409 || error.status === 410
        setConflict(changed)
        setMessage(
          changed
            ? '账户设置已变化，本次未覆盖。请先重新读取最新设置，再选择读取范围。'
            : error.message,
        )
      } else {
        setMessage('保存结果尚未确认。请核对本次保存，核对会复用原请求。')
      }
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const scope = query.data?.scope
  return (
    <section className="settings-preference conversation-history-setting">
      <div>
        <h3>其他对话读取范围</h3>
        <p>选择 AI 可以查找和读取的其他独立对话范围。保存到账户后，各浏览器同步生效。</p>
      </div>
      <label className="settings-follow-up">
        <span className="sr-only">其他对话读取范围</span>
        <select
          aria-label="其他对话读取范围"
          value={scope ?? 'workspace'}
          disabled={!query.data || query.isError || busy || !!pending || conflict}
          onChange={(event) => {
            if (!query.data || !scopeValid(event.target.value)) return
            void save({
              scope: event.target.value,
              expected_version: query.data.version,
              request_id: crypto.randomUUID(),
            })
          }}
        >
          <option value="disabled">关闭跨对话读取</option>
          <option value="workspace">当前工作区（默认）</option>
          <option value="user">当前账户的全部工作区</option>
        </select>
      </label>
      <p className="settings-preference-help">
        {scope === 'disabled'
          ? '已关闭查找和读取其他独立对话。当前对话上下文与原有任务历史读取不受影响。'
          : scope === 'user'
            ? '可查找和读取当前账户全部工作区的其他独立对话。'
            : scope === 'workspace'
              ? '仅可查找和读取当前任务所在工作区的其他独立对话。'
              : '正在读取账户当前设置…'}
        {' 子 Agent 始终只能读取当前任务树的历史。此设置只能由你在界面中更改。'}
      </p>
      <div className="settings-preference-help" role="status" aria-live="polite">
        {busy ? <p>正在保存或核对读取范围…</p> : message && <p>{message}</p>}
        {query.isError && <p>其他对话读取范围读取失败，请重新读取。</p>}
        {pending ? (
          <Button disabled={busy} onClick={() => void save(pending)}>
            核对本次保存
          </Button>
        ) : (
          (query.isError || conflict || message) && (
            <Button disabled={busy || query.isFetching} onClick={() => void reload()}>
              重新读取
            </Button>
          )
        )}
      </div>
    </section>
  )
}
