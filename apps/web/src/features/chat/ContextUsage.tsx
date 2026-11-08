import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { contextApi } from '../../api/context'
import { Button } from '../../components/ui'

const tokens = (value: number | null | undefined) =>
  value == null ? '未知' : value.toLocaleString()
const sources = {
  measured: '提供方实测',
  tokenizer: '分词器计数',
  estimated: '估计',
  unavailable: '未知',
}

export type ContextUsageActions = { compact: () => void; show: () => void }

export function ContextUsage({
  threadId,
  modelId,
  contextWindow,
  active,
  disabledReason,
  actionsRef,
}: {
  threadId?: string
  modelId?: string
  contextWindow?: number
  active: boolean
  disabledReason?: string
  actionsRef?: Ref<ContextUsageActions>
}) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const command = useRef<string | null>(null)
  const panel = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      if (panel.current?.open && !panel.current.contains(event.target as Node)) {
        panel.current.open = false
      }
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [])
  const key = ['thread-context', threadId, modelId]
  const context = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => contextApi.view(threadId!, modelId, signal),
    enabled: !!threadId,
    refetchInterval: (query) => (active || query.state.data?.compaction_pending ? 2500 : false),
  })
  const view = context.data
  const capacity = view?.context_window ?? contextWindow ?? null
  const percentage =
    view?.used_tokens != null && capacity
      ? Math.min(100, Math.round((view.used_tokens / capacity) * 100))
      : null
  async function compact() {
    if (panel.current) panel.current.open = true
    if (busy || view?.compaction_pending) return
    const blocked =
      disabledReason ??
      (!threadId
        ? '发送第一条消息后可压缩当前对话。'
        : context.isPending
          ? '正在读取上下文，请稍后重试。'
          : context.isError
            ? '上下文读取失败，请先重新读取。'
            : undefined)
    if (blocked) {
      setError(blocked)
      return
    }
    command.current ??= crypto.randomUUID()
    setBusy(true)
    setError('')
    try {
      const result = await contextApi.compact(threadId!, command.current, modelId)
      client.setQueryData(key, result)
      command.current = null
    } catch (err) {
      setError(err instanceof Error ? err.message : '压缩请求未确认，请重试核对。')
    } finally {
      setBusy(false)
    }
  }
  useImperativeHandle(actionsRef, () => ({
    compact: () => void compact(),
    show: () => {
      if (panel.current) panel.current.open = true
    },
  }))
  return (
    <details
      ref={panel}
      name="composer-menu"
      className="context-usage"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && panel.current?.open) {
          event.preventDefault()
          event.stopPropagation()
          panel.current.open = false
          panel.current.querySelector('summary')?.focus()
        }
      }}
    >
      <summary
        aria-label="查看上下文占用"
        title={`上下文已用 ${percentage === null ? '未知' : `${percentage}%`}${view?.compaction_pending ? ' · 正在安排压缩' : ''}`}
      >
        <span
          className={`context-ring${percentage !== null && percentage >= 90 ? ' context-ring-warning' : ''}${view?.compaction_pending ? ' context-ring-pending' : ''}`}
          role="meter"
          aria-label="上下文占用比例"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percentage ?? undefined}
          aria-valuetext={percentage === null ? '暂未取得计数' : `${percentage}%`}
        >
          <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
            <circle className="context-ring-track" cx="14" cy="14" r="10" />
            <circle
              className="context-ring-fill"
              cx="14"
              cy="14"
              r="10"
              pathLength={100}
              strokeDasharray={`${percentage ?? 0} 100`}
              transform="rotate(-90 14 14)"
            />
          </svg>
          {percentage === null && <span className="context-ring-unknown">?</span>}
        </span>
      </summary>
      <div className="context-usage-details">
        <h3>上下文 {percentage === null ? '未知' : `${percentage}%`}</h3>
        {view?.compaction_pending && (
          <p role="status">
            {view.compaction_status === 'running' ? '正在压缩…' : '将在任务安全边界压缩。'}
          </p>
        )}
        <dl>
          <dt>已用 tokens</dt>
          <dd>{tokens(view?.used_tokens)}</dd>
          <dt>上下文窗口</dt>
          <dd>{tokens(capacity)}</dd>
          <dt>剩余 tokens</dt>
          <dd>{tokens(view?.remaining_tokens)}</dd>
          <dt>计数来源</dt>
          <dd>{sources[view?.source ?? 'unavailable']}</dd>
          <dt>最近更新</dt>
          <dd>{view?.updated_at ? new Date(view.updated_at).toLocaleString() : '暂无记录'}</dd>
          <dt>最近压缩</dt>
          <dd>
            {view?.last_compaction_at
              ? new Date(view.last_compaction_at).toLocaleString()
              : '暂无记录'}
          </dd>
          {view?.last_compaction_at && (
            <>
              <dt>上次压缩前后</dt>
              <dd>
                {tokens(view.last_compaction_before_tokens)} →{' '}
                {tokens(view.last_compaction_after_tokens)}
              </dd>
            </>
          )}
        </dl>
        {view?.source === 'estimated' && <p>估计值可能与模型实际输入不同。</p>}
        {view?.used_tokens != null && <p>按最近模型请求计算，尚未发送的草稿不计入。</p>}
        {!threadId && <p>创建对话后可读取实际上下文并请求压缩。</p>}
        <Button
          disabled={
            !threadId ||
            !!disabledReason ||
            busy ||
            view?.compaction_pending ||
            context.isPending ||
            context.isError
          }
          onClick={() => void compact()}
        >
          {busy
            ? '正在提交…'
            : view?.compaction_pending
              ? '压缩请求已接收'
              : error
                ? '重试压缩请求'
                : '手动压缩'}
        </Button>
        <p>
          {disabledReason ??
            (active ? '任务会在安全边界处理压缩请求。' : '压缩会保留摘要，历史消息仍可查看。')}
        </p>
        {context.isError && (
          <div role="alert">
            <p>上下文读取失败。</p>
            <Button onClick={() => void context.refetch()}>重新读取</Button>
          </div>
        )}
        {(error || view?.compaction_error) && (
          <p role="alert" className="form-error">
            {error || view?.compaction_error}
          </p>
        )}
      </div>
    </details>
  )
}
