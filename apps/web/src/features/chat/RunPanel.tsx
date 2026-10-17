import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { api } from '../../api/workbench'
import type { AgentView, Run, RunTrace } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { RunInteractions } from './RunInteractions'
import { runTerminal, runLabel as label, acceptsInput } from './runState'
import { MessageContent } from './MessageContent'
import { StateArtifact } from './StateArtifact'
import { RunSurfaces } from '../generated/RunSurfaces'
import { CommandsPanel } from './CommandsPanel'
function ReadError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <div role="alert" className="run-read-error">
      <p className="form-error">{error instanceof Error ? error.message : '状态读取未完成'}</p>
      <Button onClick={retry}>
        <RefreshCw size={13} />
        重新读取
      </Button>
    </div>
  )
}
function JSONDetails({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="run-json">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}
function TraceContent({ trace }: { trace: RunTrace }) {
  return (
    <div className="run-trace">
      <h4>执行步骤</h4>
      {trace.steps.length ? (
        <ol>
          {trace.steps.map((step) => (
            <li key={step.id}>
              <div className="run-line">
                <strong>{step.logical_key}</strong>
                <Status tone={step.status === 'failed' ? 'warning' : 'neutral'}>
                  {label(step.status)}
                </Status>
              </div>
              <small>
                {step.kind} · Step {step.id}
              </small>
              {step.attempts.map((attempt) => (
                <details key={attempt.id} className="run-attempt">
                  <summary>
                    第 {attempt.number} 次尝试 · {label(attempt.status)}
                  </summary>
                  <p>Attempt {attempt.id}</p>
                  <p>
                    {new Date(attempt.created_at).toLocaleString()} →{' '}
                    {attempt.finished_at
                      ? new Date(attempt.finished_at).toLocaleString()
                      : '尚无结束时间'}
                  </p>
                  {attempt.error && (
                    <pre className="form-error">{JSON.stringify(attempt.error, null, 2)}</pre>
                  )}
                </details>
              ))}
            </li>
          ))}
        </ol>
      ) : (
        <p className="page-note">尚无执行步骤。</p>
      )}
      <h4>工具操作</h4>
      {trace.operations.length ? (
        trace.operations.map((operation) => (
          <details key={operation.id} className="run-operation">
            <summary>
              {operation.tool_name} · {label(operation.status)} · 效果 {operation.effect_status}
            </summary>
            <small>Operation {operation.id}</small>
            <JSONDetails title="实际参数" value={operation.arguments} />
            <JSONDetails title="实际结果" value={operation.result} />
            {operation.truncated && <p className="file-partial-warning">服务端结果已截断。</p>}
            {operation.effect_status === 'outcome_unknown' && (
              <p className="form-error">操作效果未知，需先核对，不能直接重复执行。</p>
            )}
          </details>
        ))
      ) : (
        <p className="page-note">尚无工具操作记录。</p>
      )}
      {trace.truncated && (
        <p className="file-partial-warning">追溯列表达到服务端上限，当前为部分记录。</p>
      )}
    </div>
  )
}
function ChildAgent({
  agent,
  workspaceId,
  onRefresh,
  onOpenThread,
}: {
  agent: AgentView
  workspaceId: string
  onRefresh: () => Promise<void>
  onOpenThread: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState('')
  const cancelId = useRef<string | null>(null)
  const trace = useQuery({
    queryKey: ['run-trace', agent.run.id],
    queryFn: () => api.trace(agent.run.id),
    enabled: open,
    refetchInterval: open && !runTerminal.has(agent.run.status) ? 4000 : false,
  })
  return (
    <section className="child-agent">
      <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>
          <strong>{agent.name}</strong>
          <Status tone={agent.run.status === 'failed' ? 'warning' : 'neutral'}>
            {label(agent.run.status)}
          </Status>
          <small>{agent.released_at ? '名额已释放' : '占用名额'}</small>
        </summary>
        <p>{agent.deliverable}</p>
        <dl className="run-meta">
          <dt>Agent</dt>
          <dd>{agent.agent_id}</dd>
          <dt>Run</dt>
          <dd>{agent.run.id}</dd>
          <dt>模式</dt>
          <dd>{agent.mode}</dd>
          <dt>路径范围</dt>
          <dd>{agent.paths.length ? agent.paths.join('\n') : '无已授予路径'}</dd>
          <dt>允许工具</dt>
          <dd>{agent.allowed_tools.length ? agent.allowed_tools.join('、') : '无已授予工具'}</dd>
        </dl>
        {agent.run.status === 'waiting_resource' && (
          <p className="page-note">等待模型准入或其他资源，以实际运行状态为准；不推测队列位置。</p>
        )}
        {agent.run.error && (
          <p role="alert" className="form-error">
            {agent.run.error.message}（{agent.run.error.code}）
          </p>
        )}
        {agent.run.state_artifact_id && (
          <StateArtifact workspaceId={workspaceId} artifactId={agent.run.state_artifact_id} />
        )}
        <RunSurfaces run={agent.run} workspaceId={workspaceId} active={open} />
        <CommandsPanel
          workspaceId={workspaceId}
          runId={agent.run.id}
          active={open}
          runActive={!runTerminal.has(agent.run.status)}
        />
        <h4>实际结果</h4>
        {agent.result !== null ? (
          <MessageContent content={agent.result} />
        ) : (
          <p className="page-note">尚无已发布结果。</p>
        )}
        <div className="button-row">
          {agent.run.thread_id && (
            <Button
              onClick={() => {
                if (agent.run.thread_id) onOpenThread(agent.run.thread_id)
              }}
            >
              打开 Agent 对话
            </Button>
          )}
          {!runTerminal.has(agent.run.status) && (
            <Button
              disabled={cancelling || agent.run.status === 'cancelling'}
              onClick={() => {
                setCancelling(true)
                setError('')
                cancelId.current ??= crypto.randomUUID()
                void api
                  .cancelRun(agent.run.id, cancelId.current)
                  .then(onRefresh)
                  .catch((e) => setError(e instanceof Error ? e.message : '停止请求未确认'))
                  .finally(() => setCancelling(false))
              }}
            >
              {cancelling ? '正在请求停止…' : '停止此 Agent'}
            </Button>
          )}
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <RunInteractions run={agent.run} onRefresh={onRefresh} historyOnly />
        {trace.isError ? (
          <ReadError error={trace.error} retry={() => void trace.refetch()} />
        ) : trace.data ? (
          <TraceContent trace={trace.data} />
        ) : (
          <p className="page-note">正在读取 Agent 追溯…</p>
        )}
      </details>
    </section>
  )
}
export function RunPanel({
  run,
  workspaceId,
  onOpenThread,
}: {
  run: Run
  workspaceId: string
  onOpenThread: (id: string) => void
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const rootId = run.root_run_id ?? run.id
  const tree = useQuery({
    queryKey: ['run-agents', rootId],
    queryFn: () => api.agents(rootId),
    refetchInterval: !runTerminal.has(run.status) && run.status !== 'held' ? 4000 : false,
  })
  const root = tree.data?.root ?? (rootId === run.id ? run : undefined)
  const trace = useQuery({
    queryKey: ['run-trace', rootId],
    queryFn: () => api.trace(rootId),
    enabled: open,
    refetchInterval: open && (!root || !runTerminal.has(root.status)) ? 4000 : false,
  })
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['run-agents', rootId] }),
      client.invalidateQueries({ queryKey: ['run-trace'] }),
      client.invalidateQueries({ queryKey: ['run-surfaces'] }),
      client.invalidateQueries({ queryKey: ['runs'] }),
      client.invalidateQueries({ queryKey: ['messages'] }),
    ])
  }
  return (
    <section className="run-panel">
      {root?.error && (
        <p className="run-visible-error form-error" role="alert">
          {root.error.message}
        </p>
      )}
      {root?.status === 'held' && (
        <p className="run-visible-error">
          恢复的任务已暂停，历史确认保持只读。可停止此任务后继续新消息。
        </p>
      )}
      <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>
          <span>任务 · {root ? label(root.status) : '读取根任务…'}</span>
          {!!tree.data?.agents.length && <small>{tree.data.agents.length} 个协作任务</small>}
          <ChevronDown size={15} />
        </summary>
        <div className="run-panel-body">
          <div className="run-line">
            <small>根 Run {rootId}</small>
            <Button disabled={tree.isFetching} onClick={() => void refresh()}>
              刷新任务
            </Button>
          </div>
          <p className="page-note">
            子 Agent 的完成与名额释放单独记录；整体是否完成以根任务状态为准。
          </p>
          {root?.error && (
            <p className="form-error" role="alert">
              {root.error.message}
            </p>
          )}
          {tree.isError && <ReadError error={tree.error} retry={() => void tree.refetch()} />}
          {root?.state_artifact_id && (
            <StateArtifact workspaceId={workspaceId} artifactId={root.state_artifact_id} />
          )}
          <CommandsPanel
            workspaceId={workspaceId}
            runId={rootId}
            active={open}
            runActive={!root || !runTerminal.has(root.status)}
          />
          <h3>根任务追溯</h3>
          {trace.isError ? (
            <ReadError error={trace.error} retry={() => void trace.refetch()} />
          ) : trace.data ? (
            <TraceContent trace={trace.data} />
          ) : (
            <p className="page-note">正在读取真实执行记录…</p>
          )}
          <h3>Agent 协作</h3>
          {tree.data?.agents.length ? (
            tree.data.agents.map((agent) => (
              <ChildAgent
                key={agent.agent_id}
                agent={agent}
                workspaceId={workspaceId}
                onRefresh={refresh}
                onOpenThread={onOpenThread}
              />
            ))
          ) : (
            <p className="page-note">
              {tree.data ? '本任务尚未派生 Agent。' : '正在读取 Agent 状态…'}
            </p>
          )}
        </div>
      </details>
      {root && <RunSurfaces run={root} workspaceId={workspaceId} active />}
      {root && <RunInteractions run={root} onRefresh={refresh} />}
      {tree.isError && !open && <ReadError error={tree.error} retry={() => void tree.refetch()} />}
      {tree.data?.agents
        .filter(
          (agent) =>
            acceptsInput(agent.run.status) &&
            (agent.run.questions.some((q) => q.status === 'pending') ||
              agent.run.approvals.some((a) => a.status === 'pending')),
        )
        .map((agent) => (
          <div className="child-decisions" key={agent.agent_id}>
            <p className="card-eyebrow">协作任务 · {agent.name}</p>
            <RunInteractions run={agent.run} onRefresh={refresh} pendingOnly />
          </div>
        ))}
    </section>
  )
}
