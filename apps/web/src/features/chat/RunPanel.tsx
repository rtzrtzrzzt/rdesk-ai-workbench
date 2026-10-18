import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { api } from '../../api/workbench'
import { conversationsApi } from '../../api/conversations'
import type { AgentView, Run, RunTrace } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { Dialog } from '../../components/Dialog'
import { SubagentTranscript } from './SubagentTranscript'
import { RunInteractions } from './RunInteractions'
import { runTerminal, runLabel as label, runStatusLabel, acceptsInput } from './runState'
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
}: {
  agent: AgentView
  workspaceId: string
  onRefresh: () => Promise<void>
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
      <button
        className="agent-entry"
        onClick={() => setOpen(true)}
        type="button"
        aria-label={`查看子任务：${agent.name}`}
      >
        <strong>{agent.name}</strong>
        <Status tone={agent.run.status === 'failed' ? 'warning' : 'neutral'}>
          {runStatusLabel(agent.run)}
        </Status>
        <span>
          查看对话 <ChevronDown size={13} />
        </span>
      </button>
      {open && (
        <Dialog
          title={`子任务 · ${agent.name}`}
          className="agent-dialog"
          onClose={() => setOpen(false)}
        >
          <SubagentTranscript agent={agent} workspaceId={workspaceId} />
          {agent.run.status === 'paused' && <PausedChild run={agent.run} onRefresh={onRefresh} />}
          <RunInteractions run={agent.run} onRefresh={onRefresh} />
          <details className="agent-technical">
            <summary>高级信息与执行记录</summary>
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
              <dd>
                {agent.allowed_tools.length ? agent.allowed_tools.join('、') : '无已授予工具'}
              </dd>
            </dl>
            {agent.run.status === 'waiting_resource' && (
              <p className="page-note">
                等待模型准入或其他资源，以实际运行状态为准；不推测队列位置。
              </p>
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
            <h4>已发布结果</h4>
            {agent.result !== null && (
              <MessageContent content={agent.result} workspaceId={workspaceId} />
            )}
          </details>
          <div className="button-row">
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
          <details className="agent-technical">
            <summary>调试追溯</summary>
            {trace.isError ? (
              <ReadError error={trace.error} retry={() => void trace.refetch()} />
            ) : trace.data ? (
              <TraceContent trace={trace.data} />
            ) : (
              <p className="page-note">正在读取 Agent 追溯…</p>
            )}
          </details>
        </Dialog>
      )}
    </section>
  )
}

function PausedChild({ run, onRefresh }: { run: Run; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<{ extend: boolean; id: string } | null>(null)
  async function resume(extend: boolean) {
    if (busy) return
    if (!pending.current || pending.current.extend !== extend)
      pending.current = { extend, id: crypto.randomUUID() }
    setBusy(true)
    setError('')
    try {
      await conversationsApi.resumeRun(run.id, pending.current.id, extend)
      pending.current = null
      await onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '子任务恢复未完成。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="run-visible-error">
      <strong>子任务已暂停</strong>
      <p>{run.paused_reason?.message}</p>
      <div className="button-row">
        <Button disabled={busy} onClick={() => void resume(false)}>
          {['repeated_tool_call', 'no_progress'].includes(run.paused_reason?.code ?? '')
            ? '重新规划并继续此 Agent'
            : '继续此 Agent'}
        </Button>
        {['round_limit', 'root_model_budget', 'model_output_limit'].includes(
          run.paused_reason?.code ?? '',
        ) && (
          <Button disabled={busy} onClick={() => void resume(true)}>
            {run.paused_reason?.code === 'model_output_limit'
              ? '使用剩余上下文并继续'
              : '扩展此 Agent 预算并继续'}
          </Button>
        )}
      </div>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  )
}
/** Technical controls live on the task page, away from the conversation timeline. */
export function RunDetails({ run, workspaceId }: { run: Run; workspaceId: string }) {
  const [open, setOpen] = useState(false)
  const trace = useQuery({
    queryKey: ['run-trace', run.id],
    queryFn: () => api.trace(run.id),
    enabled: open,
    refetchInterval: open && !runTerminal.has(run.status) ? 4000 : false,
  })
  return (
    <details className="task-details" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>高级信息、命令与日志</summary>
      {open && (
        <div className="run-panel-body">
          <p className="page-note">Run {run.id}</p>
          {run.state_artifact_id && (
            <StateArtifact workspaceId={workspaceId} artifactId={run.state_artifact_id} />
          )}
          <CommandsPanel
            workspaceId={workspaceId}
            runId={run.id}
            active={open}
            runActive={!runTerminal.has(run.status)}
          />
          <h3>执行追溯</h3>
          {trace.isError ? (
            <ReadError error={trace.error} retry={() => void trace.refetch()} />
          ) : trace.data ? (
            <TraceContent trace={trace.data} />
          ) : (
            <p className="page-note">正在读取真实执行记录…</p>
          )}
        </div>
      )}
    </details>
  )
}

export function RunPanel({ run, workspaceId }: { run: Run; workspaceId: string }) {
  const client = useQueryClient()
  const [resuming, setResuming] = useState(false)
  const [resumeError, setResumeError] = useState('')
  const resumeCommand = useRef<{ key: string; id: string } | null>(null)
  const rootId = run.root_run_id ?? run.id
  const tree = useQuery({
    queryKey: ['run-agents', rootId],
    queryFn: () => api.agents(rootId),
    refetchInterval: !runTerminal.has(run.status) && run.status !== 'held' ? 4000 : false,
  })
  const root = tree.data?.root ?? (rootId === run.id ? run : undefined)
  async function refresh() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['run-agents', rootId] }),
      client.invalidateQueries({ queryKey: ['run-trace'] }),
      client.invalidateQueries({ queryKey: ['run-surfaces'] }),
      client.invalidateQueries({ queryKey: ['runs'] }),
      client.invalidateQueries({ queryKey: ['messages'] }),
    ])
  }
  async function resume(extend: boolean) {
    if (!root || resuming) return
    const key = `${root.id}:${extend}`
    if (resumeCommand.current?.key !== key) resumeCommand.current = { key, id: crypto.randomUUID() }
    setResuming(true)
    setResumeError('')
    try {
      await conversationsApi.resumeRun(root.id, resumeCommand.current.id, extend)
      resumeCommand.current = null
      await refresh()
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : '恢复任务未完成。')
    } finally {
      setResuming(false)
    }
  }
  return (
    <section className="run-panel">
      {root?.status === 'paused' && (
        <div className="run-visible-error">
          <strong>任务已暂停</strong>
          <p>{root.paused_reason?.message ?? '请确认后继续任务。'}</p>
          <div className="button-row">
            <Button disabled={resuming} onClick={() => void resume(false)}>
              {['repeated_tool_call', 'no_progress'].includes(root.paused_reason?.code ?? '')
                ? '重新规划并继续'
                : '继续任务'}
            </Button>
            {['round_limit', 'root_model_budget', 'model_output_limit'].includes(
              root.paused_reason?.code ?? '',
            ) && (
              <Button disabled={resuming} onClick={() => void resume(true)}>
                {root.paused_reason?.code === 'model_output_limit'
                  ? '使用剩余上下文并继续'
                  : '扩展预算并继续'}
              </Button>
            )}
          </div>
          {resumeError && (
            <p role="alert" className="form-error">
              {resumeError}
            </p>
          )}
        </div>
      )}
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
      {!!tree.data?.agents.length && (
        <div className="agent-entries" aria-label="子任务">
          <p className="agent-entries-label">
            子任务 · {tree.data.agents.filter((agent) => agent.run.status === 'succeeded').length}/
            {tree.data.agents.length} 已完成
          </p>
          {tree.data.agents.map((agent) => (
            <ChildAgent
              key={agent.agent_id}
              agent={agent}
              workspaceId={workspaceId}
              onRefresh={refresh}
            />
          ))}
        </div>
      )}
      {root && <RunSurfaces run={root} workspaceId={workspaceId} active />}
      {root && <RunInteractions run={root} onRefresh={refresh} />}
      {tree.isError && <ReadError error={tree.error} retry={() => void tree.refetch()} />}
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
