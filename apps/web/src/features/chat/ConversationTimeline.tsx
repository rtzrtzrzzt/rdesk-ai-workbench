import { Fragment, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Check, ChevronRight, CircleAlert, LoaderCircle, Wrench } from 'lucide-react'
import { api } from '../../api/workbench'
import type { ActivityItem, Message, Run } from '../../api/types'
import { Button } from '../../components/ui'
import { runLabel, runTerminal } from './runState'

function ResultPreview({ result }: { result: Record<string, unknown> }) {
  const sources = Array.isArray(result.results)
    ? result.results.filter((item): item is { title: string; url: string; snippet?: string } => {
        if (
          !item ||
          typeof item !== 'object' ||
          typeof item.title !== 'string' ||
          typeof item.url !== 'string'
        )
          return false
        try {
          return ['http:', 'https:'].includes(new URL(item.url).protocol)
        } catch {
          return false
        }
      })
    : []
  if (sources.length)
    return (
      <ul className="tool-sources">
        {sources.map((source, index) => (
          <li key={`${source.url}:${index}`}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.title}
            </a>
            {source.snippet && <p>{source.snippet.slice(0, 400)}</p>}
          </li>
        ))}
      </ul>
    )
  const text = [result.text, result.content, result.body, result.note, result.markdown].find(
    (value) => typeof value === 'string' && value.trim(),
  )
  if (typeof text === 'string')
    return (
      <p className="tool-output-preview">
        {text.slice(0, 1600)}
        {text.length > 1600 ? '…' : ''}
      </p>
    )
  if (Array.isArray(result.files))
    return (
      <p>
        找到 {result.files.length} 个文件{result.truncated ? '（部分结果）' : ''}。
      </p>
    )
  return null
}

function ToolActivity({
  item,
  threadId,
  live,
}: {
  item: ActivityItem
  threadId: string
  live: boolean
}) {
  const [open, setOpen] = useState(false)
  const ongoing = ['created', 'running', 'pending'].includes(item.status)
  const failed = item.status === 'failed' || item.effect_status === 'outcome_unknown'
  const details = useQuery({
    queryKey: ['tool-details', threadId, item.id],
    queryFn: ({ signal }) => api.activityDetails(threadId, item.id, signal),
    enabled: open,
    refetchInterval: open && live && ongoing ? 2500 : false,
  })
  const Icon = failed
    ? CircleAlert
    : ongoing && live
      ? LoaderCircle
      : item.status === 'succeeded'
        ? Check
        : Wrench
  return (
    <section className={`tool-activity ${failed ? 'tool-failed' : ''}`} data-operation-id={item.id}>
      <button
        className="tool-activity-heading"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon size={14} className={ongoing && live ? 'activity-spin' : ''} />
        <span className="tool-activity-title">{item.title}</span>
        {item.summary && (
          <span className="tool-activity-summary" title={item.summary}>
            {item.summary}
          </span>
        )}
        <small>
          {item.effect_status === 'outcome_unknown'
            ? '结果待核对'
            : ongoing && !live
              ? '尚未完成'
              : runLabel(item.status)}
        </small>
        <ChevronRight size={13} className={open ? 'expanded' : ''} />
      </button>
      {item.error && <p className="tool-activity-error">{item.error}</p>}
      {open && (
        <div className="tool-activity-details">
          {details.isError ? (
            <div role="alert">
              <p className="form-error">无法读取工具详情。</p>
              <Button onClick={() => void details.refetch()}>重新读取</Button>
            </div>
          ) : details.data ? (
            <>
              <p className="tool-detail-name">{details.data.tool_name}</p>
              {details.data.result !== null && (
                <>
                  <ResultPreview result={details.data.result} />
                  <details>
                    <summary>详细结果</summary>
                    <pre>{JSON.stringify(details.data.result, null, 2)}</pre>
                    {details.data.truncated && (
                      <p className="page-note">结果较长，此处展示部分内容。</p>
                    )}
                  </details>
                </>
              )}
              <details>
                <summary>调用参数</summary>
                <pre>{JSON.stringify(details.data.arguments, null, 2)}</pre>
              </details>
            </>
          ) : (
            <p className="page-note">正在读取详情…</p>
          )}
        </div>
      )}
    </section>
  )
}

/** Messages and tools share persisted timestamps, including branch-safe history. */
export function ConversationTimeline({
  threadId,
  messages,
  runs,
  renderMessage,
  renderRun,
}: {
  threadId: string
  messages: Message[]
  runs: Run[]
  renderMessage: (message: Message) => ReactNode
  renderRun?: (run: Run) => ReactNode
}) {
  const live = runs.some(
    (run) => !runTerminal.has(run.status) && !['paused', 'held'].includes(run.status),
  )
  const activity = useInfiniteQuery({
    queryKey: ['thread-activity', threadId],
    queryFn: ({ pageParam, signal }) => api.activity(threadId, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
    enabled: !!threadId,
    refetchInterval: live ? 2500 : false,
  })
  const items = useMemo(() => {
    const tools = [
      ...new Map(
        activity.data?.pages.flatMap((page) => page.items).map((item) => [item.id, item]) ?? [],
      ).values(),
    ]
    return [
      ...messages.map((message) => ({
        id: message.id,
        at: message.created_at,
        runId: message.run_id,
        message,
        tool: undefined as ActivityItem | undefined,
      })),
      ...tools.map((tool) => ({
        id: tool.id,
        at: tool.created_at,
        runId: tool.run_id,
        message: undefined as Message | undefined,
        tool,
      })),
    ].sort(
      (a, b) =>
        (Date.parse(a.at) || Number.MAX_SAFE_INTEGER) -
          (Date.parse(b.at) || Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id),
    )
  }, [messages, activity.data])
  const lastIndex = new Map<string, number>()
  items.forEach((item, index) => {
    if (item.runId) lastIndex.set(item.runId, index)
  })
  return (
    <>
      {activity.hasNextPage && (
        <Button
          variant="ghost"
          disabled={activity.isFetchingNextPage}
          onClick={() => void activity.fetchNextPage()}
        >
          {activity.isFetchingNextPage ? '正在读取…' : '查看更早的工具活动'}
        </Button>
      )}
      {activity.isError && (
        <div className="activity-read-error" role="alert">
          <p>执行进度暂时无法读取，消息正文仍可查看。</p>
          <Button onClick={() => void activity.refetch()}>重新读取进度</Button>
        </div>
      )}
      {items.map((item, index) => (
        <Fragment key={item.id}>
          {item.message ? (
            renderMessage(item.message)
          ) : item.tool ? (
            <ToolActivity
              item={item.tool}
              threadId={threadId}
              live={
                live &&
                !runTerminal.has(runs.find((run) => run.id === item.runId)?.status ?? 'succeeded')
              }
            />
          ) : null}
          {renderRun &&
            item.runId &&
            lastIndex.get(item.runId) === index &&
            (() => {
              const run = runs.find((run) => run.id === item.runId)
              return run ? renderRun(run) : null
            })()}
        </Fragment>
      ))}
      {renderRun &&
        runs
          .filter((run) => !lastIndex.has(run.id))
          .map((run) => <Fragment key={run.id}>{renderRun(run)}</Fragment>)}
    </>
  )
}
