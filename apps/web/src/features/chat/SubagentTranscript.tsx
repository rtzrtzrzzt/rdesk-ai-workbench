import { useEffect, useRef } from 'react'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { api } from '../../api/workbench'
import { collaborationApi, type AgentCommunication } from '../../api/collaboration'
import type { AgentView, Message } from '../../api/types'
import { ConversationTimeline } from './ConversationTimeline'
import { MessageContent, shouldRenderMessage } from './MessageContent'
import { Thinking } from './Thinking'
import { useRunStream } from './useRunStream'
import { runStatusLabel, runTerminal } from './runState'
import { Button, Status } from '../../components/ui'

const communicationKinds: Record<AgentCommunication['kind'], string> = {
  note: '消息',
  question: '提问',
  reply: '回复',
  instruction: '要求',
  result: '结果通知',
}
const communicationStatuses: Record<AgentCommunication['status'], string> = {
  queued: '待送达',
  applying: '正在送达',
  delivered: '已送达',
}

/** A child's conversation and collaboration history stay inside its parent chat. */
export function SubagentTranscript({
  agent,
  rootId,
  workspaceId,
}: {
  agent: AgentView
  rootId: string
  workspaceId: string
}) {
  const threadId = agent.run.thread_id ?? ''
  const runs = useQuery({
    queryKey: ['runs', threadId],
    queryFn: () => api.runs(threadId),
    enabled: !!threadId,
    refetchInterval: 2500,
  })
  const run = runs.data?.find((item) => item.id === agent.run.id) ?? agent.run
  const turnIds = new Set(agent.turn_run_ids)
  const turns = (runs.data ?? []).filter((item) => turnIds.has(item.id) && item.id !== run.id)
  turns.push(run)
  const messages = useQuery({
    queryKey: ['messages', threadId],
    queryFn: ({ signal }) => api.messages(threadId, signal),
    enabled: !!threadId,
  })
  const { streaming, reasoning } = useRunStream(run, threadId, !!threadId)
  const communications = useInfiniteQuery({
    queryKey: ['agent-messages', rootId, agent.agent_id],
    queryFn: ({ pageParam, signal }) =>
      collaborationApi.messages(rootId, agent.agent_id, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: agent.state !== 'closed' ? 2500 : false,
  })
  const communicationById = new Map<string, AgentCommunication>(
    communications.data?.pages.flatMap((page) =>
      page.items.map((message) => [`agent-message:${message.id}`, message] as const),
    ),
  )
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  useEffect(() => {
    if (follow.current) scroll.current?.scrollTo({ top: scroll.current.scrollHeight })
  }, [messages.data, streaming, reasoning, communications.data])
  const loaded = messages.data ?? []
  const visible: Message[] = [
    // A new turn persists its instruction in both histories with the same ID.
    ...loaded.filter(
      (message) => message.role !== 'user' || !communicationById.has(`agent-message:${message.id}`),
    ),
    ...[...new Set([...Object.keys(streaming), ...Object.keys(reasoning)])]
      .filter((id) => !loaded.some((message) => message.id === id))
      .map((id) => ({
        id,
        thread_id: threadId,
        run_id: run.id,
        role: 'assistant',
        content: streaming[id] ?? '',
        reasoning: reasoning[id] ?? '',
        status: 'streaming',
        created_at: '',
        attachments: [],
      })),
  ]
  // Persisted timestamps let the existing timeline interleave messages, tools,
  // and communication without adding a separate conversation surface.
  const timelineMessages: Message[] = [
    ...visible,
    ...[...communicationById.entries()].map(([id, message]) => ({
      id,
      thread_id: threadId,
      run_id: turnIds.has(message.to_run_id) ? message.to_run_id : message.from_run_id,
      role: 'assistant' as const,
      content: message.content,
      status: 'completed' as const,
      created_at: message.created_at,
      attachments: [],
    })),
  ]
  return (
    <>
      <div className="agent-transcript-status">
        <Status
          tone={
            run.status === 'failed' || run.status === 'paused'
              ? 'warning'
              : run.status === 'succeeded'
                ? 'good'
                : 'neutral'
          }
        >
          {agent.state === 'closed'
            ? '已关闭'
            : agent.state === 'stopping'
              ? '正在停止'
              : runStatusLabel(run)}
        </Status>
        <span>第 {agent.turn_count} 轮</span>
        <span>{agent.deliverable}</span>
      </div>
      {run.status === 'waiting_resource' && (
        <p className="page-note">正在等待共享模型；其它子任务可继续处理工具和资料。</p>
      )}
      {messages.isError && (
        <div role="alert">
          <p className="form-error">子任务对话读取失败。</p>
          <Button onClick={() => void messages.refetch()}>重新读取</Button>
        </div>
      )}
      {runs.isError && (
        <div role="alert">
          <p className="form-error">子任务轮次状态暂时无法读取。</p>
          <Button onClick={() => void runs.refetch()}>重新读取轮次</Button>
        </div>
      )}
      <div
        className="subagent-transcript"
        ref={scroll}
        onScroll={(event) => {
          const el = event.currentTarget
          follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
        }}
      >
        {communications.hasNextPage && (
          <Button
            variant="ghost"
            disabled={communications.isFetchingNextPage}
            onClick={() => {
              follow.current = false
              void communications.fetchNextPage()
            }}
          >
            {communications.isFetchingNextPage ? '正在读取…' : '查看更早的协作消息'}
          </Button>
        )}
        {communications.isError && (
          <div role="alert" className="activity-read-error">
            <p>协作消息暂时无法读取。</p>
            <Button onClick={() => void communications.refetch()}>重新读取协作消息</Button>
          </div>
        )}
        <ConversationTimeline
          threadId={threadId}
          workspaceId={workspaceId}
          messages={timelineMessages}
          runs={turns}
          renderReasoning={(message) => {
            if (communicationById.has(message.id)) return null
            const thought = reasoning[message.id] ?? message.reasoning ?? ''
            return thought.trim() ? (
              <Thinking
                text={thought}
                active={
                  message.status === 'streaming' &&
                  !(streaming[message.id] ?? message.content).trim() &&
                  run.status === 'running'
                }
                workspaceId={workspaceId}
              />
            ) : null
          }}
          renderMessage={(message) => {
            const communication = communicationById.get(message.id)
            if (communication) {
              const participant = (id: string, name: string) =>
                id === rootId ? '主任务' : `子任务 · ${name}`
              const reply = communication.reply_to_id
                ? communicationById.get(`agent-message:${communication.reply_to_id}`)
                : undefined
              return (
                <details className="agent-communication" id={`agent-message-${communication.id}`}>
                  <summary>
                    <span>
                      {participant(communication.from_run_id, communication.from_name)} →{' '}
                      {participant(communication.to_run_id, communication.to_name)}
                    </span>
                    <span>{communicationKinds[communication.kind]}</span>
                    <Status tone="neutral">{communicationStatuses[communication.status]}</Status>
                    <time dateTime={communication.created_at}>
                      {new Date(communication.created_at).toLocaleString()}
                    </time>
                  </summary>
                  {communication.reply_to_id && (
                    <p className="page-note">
                      {reply ? (
                        <a href={`#agent-message-${reply.id}`}>
                          回复：{reply.content.slice(0, 100)}
                        </a>
                      ) : (
                        '回复先前的协作消息'
                      )}
                    </p>
                  )}
                  <MessageContent content={communication.content} workspaceId={workspaceId} />
                </details>
              )
            }
            if (message.role === 'user')
              return (
                <details className="agent-task-input">
                  <summary>任务要求</summary>
                  <MessageContent content={message.content} role="user" workspaceId={workspaceId} />
                </details>
              )
            const content = streaming[message.id] ?? message.content
            const thought = reasoning[message.id] ?? message.reasoning ?? ''
            if (!shouldRenderMessage(message, content, thought)) return null
            return (
              <article className="chat-message message-assistant">
                {['superseded', 'interrupted'].includes(message.status) && (
                  <div className="message-author">已保留部分输出</div>
                )}
                <div className="message-body">
                  <MessageContent content={content} workspaceId={workspaceId} />
                </div>
              </article>
            )
          }}
        />
        {!timelineMessages.length && (
          <p className="page-note">
            {runTerminal.has(run.status) ? '此子任务没有可显示的消息。' : '正在读取子任务对话…'}
          </p>
        )}
      </div>
    </>
  )
}
