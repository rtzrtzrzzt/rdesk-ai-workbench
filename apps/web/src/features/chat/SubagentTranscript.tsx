import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/workbench'
import type { AgentView, Message } from '../../api/types'
import { ConversationTimeline } from './ConversationTimeline'
import { MessageContent, shouldRenderMessage } from './MessageContent'
import { Thinking } from './Thinking'
import { useRunStream } from './useRunStream'
import { runStatusLabel, runTerminal } from './runState'
import { Button, Status } from '../../components/ui'

/** A child stays inside its parent chat and has no independent composer. */
export function SubagentTranscript({
  agent,
  workspaceId,
}: {
  agent: AgentView
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
  const messages = useQuery({
    queryKey: ['messages', threadId],
    queryFn: ({ signal }) => api.messages(threadId, signal),
    enabled: !!threadId,
  })
  const { streaming, reasoning } = useRunStream(run, threadId, !!threadId)
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  useEffect(() => {
    if (follow.current) scroll.current?.scrollTo({ top: scroll.current.scrollHeight })
  }, [messages.data, streaming, reasoning])
  const loaded = messages.data ?? []
  const visible: Message[] = [
    ...loaded,
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
          {runStatusLabel(run)}
        </Status>
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
      <div
        className="subagent-transcript"
        ref={scroll}
        onScroll={(event) => {
          const el = event.currentTarget
          follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
        }}
      >
        <ConversationTimeline
          threadId={threadId}
          messages={visible}
          runs={[run]}
          renderMessage={(message) => {
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
                  <Thinking
                    text={thought}
                    active={
                      message.status === 'streaming' && !content.trim() && run.status === 'running'
                    }
                    workspaceId={workspaceId}
                  />
                  <MessageContent content={content} workspaceId={workspaceId} />
                </div>
              </article>
            )
          }}
        />
        {!visible.length && (
          <p className="page-note">
            {runTerminal.has(run.status) ? '此子任务没有可显示的消息。' : '正在读取子任务对话…'}
          </p>
        )}
      </div>
    </>
  )
}
