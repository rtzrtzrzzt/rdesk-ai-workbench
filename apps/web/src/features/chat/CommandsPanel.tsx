import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { commandsApi } from '../../api/commands'
import type { CommandView } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import { CommandLogs } from './CommandLogs'
import { commandLabel, commandsForRun, needsCommandObservation } from './commandState'
import './commands.css'
import { sandboxLabel } from '../settings/SandboxSelect'

const reason = (error: unknown) => (error instanceof Error ? error.message : '请求未完成。')
function Capability({ value }: { value: unknown }) {
  const record =
    value !== null && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null
  const status = typeof record?.status === 'string' ? record.status : null
  return (
    <div className="command-capability">
      <Status>
        {status === 'available'
          ? '原生执行器可用'
          : status === 'unsupported'
            ? '当前平台不支持'
            : (status ?? '未获得能力声明')}
      </Status>
      {typeof record?.reason === 'string' && <p className="page-note">{record.reason}</p>}
      <p className="page-note">
        平台：{typeof record?.platform === 'string' ? record.platform : '未知'} · 驱动：
        {typeof record?.driver === 'string' ? record.driver : '未知'}
      </p>
      <p className="page-note">
        执行隔离取决于每条命令保存的实际执行配置。命令启动须经 Agent 的 start_command
        工具与精确审批；此面板不会启动或重放命令。
      </p>
    </div>
  )
}
export function CommandsPanel({
  workspaceId,
  runId,
  active,
  runActive,
}: {
  workspaceId: string
  runId: string
  active: boolean
  runActive: boolean
}) {
  const [open, setOpen] = useState(false)
  const enabled = active && open
  const list = useQuery({
    queryKey: ['commands', workspaceId],
    queryFn: () => commandsApi.list(workspaceId),
    enabled,
    refetchInterval: (query) =>
      enabled &&
      (runActive ||
        !query.state.data ||
        needsCommandObservation(commandsForRun(query.state.data, runId)))
        ? 4000
        : false,
  })
  const capability = useQuery({
    queryKey: ['commands-capability', workspaceId],
    queryFn: () => commandsApi.capability(workspaceId),
    enabled,
  })
  const commands = commandsForRun(list.data ?? [], runId)
  return (
    <details
      className="run-commands"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>命令与日志{list.data ? ` · 当前返回 ${commands.length} 条` : ''}</summary>
      <p className="page-note">
        Run {runId}。从工作区最新 100 条命令中筛选本任务；当前返回列表不保证覆盖较早记录。
      </p>
      <div className="button-row">
        <Button disabled={list.isFetching} onClick={() => void list.refetch()}>
          刷新命令列表
        </Button>
        <Button disabled={capability.isFetching} onClick={() => void capability.refetch()}>
          读取执行器能力
        </Button>
      </div>
      {capability.data !== undefined && <Capability value={capability.data} />}
      {capability.error && (
        <p role="alert" className="form-error">
          {capability.error.message}
        </p>
      )}
      {list.error && (
        <p role="alert" className="form-error">
          {list.error.message}
        </p>
      )}
      {commands.length ? (
        <div className="command-list">
          {commands.map((command) => (
            <CommandCard
              key={command.id}
              workspaceId={workspaceId}
              command={command}
              active={enabled}
            />
          ))}
        </div>
      ) : (
        <p className="page-note">
          {list.data
            ? '当前返回列表中没有属于此 Run 的命令记录。'
            : enabled
              ? '正在读取真实命令记录…'
              : '展开后读取命令与日志。'}
        </p>
      )}
    </details>
  )
}
function CommandCard({
  workspaceId,
  command,
  active,
}: {
  workspaceId: string
  command: CommandView
  active: boolean
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [checking, setChecking] = useState(false)
  const [unknownStop, setUnknownStop] = useState(false)
  const [notice, setNotice] = useState('')
  const inspect = useQuery({
    queryKey: ['command', workspaceId, command.id],
    queryFn: () => commandsApi.inspect(workspaceId, command.id),
    initialData: command,
    enabled: active && open,
    refetchInterval: (query) =>
      active && open && !query.state.data?.owned_resources_closed ? 4000 : false,
  })
  const current = open ? inspect.data : command
  function received(value: CommandView) {
    client.setQueryData(['command', workspaceId, command.id], value)
    client.setQueryData<CommandView[]>(['commands', workspaceId], (old) =>
      old?.map((item) => (item.id === value.id ? value : item)),
    )
  }
  async function stop() {
    if (stopping || current.owned_resources_closed) return
    setStopping(true)
    setNotice('')
    try {
      const result = await commandsApi.stop(workspaceId, current.id)
      received(result)
      setUnknownStop(false)
      setNotice(
        result.owned_resources_closed
          ? '服务器已确认受管资源闭合；此前已产生的效果不会撤销。'
          : `服务器状态：${commandLabel(result.status)}。受管资源仍未闭合，继续等待核对。`,
      )
    } catch (error) {
      setUnknownStop(true)
      setNotice(
        `${reason(error)} 停止结果未确认；请只读查询当前命令。再次明确停止仅重复停止，不会重启命令。`,
      )
    } finally {
      setStopping(false)
    }
  }
  async function check() {
    if (checking) return
    setChecking(true)
    try {
      const result = await commandsApi.inspect(workspaceId, current.id)
      received(result)
      if (result.stop_requested || result.owned_resources_closed) setUnknownStop(false)
      setNotice(
        result.owned_resources_closed
          ? '已只读核对：受管资源已闭合。进程退出不等于业务结果已验证。'
          : `已只读核对：${commandLabel(result.status)}，受管资源未闭合，仍占用命令名额。`,
      )
    } catch (error) {
      setNotice(`${reason(error)} 未自动重发停止或启动命令。`)
    } finally {
      setChecking(false)
    }
  }
  return (
    <article className="command-card">
      <div className="run-line">
        <Status
          tone={
            current.status === 'outcome_unknown' ||
            (!current.owned_resources_closed &&
              ['failed', 'completed', 'cancelled', 'timed_out', 'output_limit'].includes(
                current.status,
              ))
              ? 'warning'
              : 'neutral'
          }
        >
          {commandLabel(current.status)}
        </Status>
        <small>{current.owned_resources_closed ? '受管资源已闭合' : '资源未闭合 · 仍占名额'}</small>
      </div>
      <p className="command-id">Command {current.id}</p>
      <pre className="command-source" tabIndex={0} aria-label="已登记的命令，只读">
        {current.command}
      </pre>
      <dl className="run-meta">
        <dt>实际执行配置</dt>
        <dd>{sandboxLabel(current.sandbox)}</dd>
        <dt>退出码</dt>
        <dd>
          {current.exit_code === null ? '未知 / 尚未退出' : current.exit_code} ·
          仅进程结果，业务结果未核实
        </dd>
        <dt>输出字节</dt>
        <dd>
          stdout {current.stdout_bytes === null ? '未知' : formatBytes(current.stdout_bytes)} ·
          stderr {current.stderr_bytes === null ? '未知' : formatBytes(current.stderr_bytes)}
        </dd>
        <dt>期限与输出预算</dt>
        <dd>
          {current.timeout_seconds} 秒 · {formatBytes(current.max_output_bytes)}
        </dd>
        <dt>创建 / 开始 / 结束</dt>
        <dd>
          {new Date(current.created_at).toLocaleString()} /{' '}
          {current.started_at ? new Date(current.started_at).toLocaleString() : '未确认'} /{' '}
          {current.finished_at ? new Date(current.finished_at).toLocaleString() : '未确认'}
        </dd>
        <dt>停止请求</dt>
        <dd>{current.stop_requested ? '服务器已记录' : '服务器未记录'}</dd>
      </dl>
      {!current.owned_resources_closed && (
        <p className="command-resource-warning">
          受管进程及后代尚未被确认全部闭合。即使 Run
          或主进程已结束，仍占用命令名额；结果未知时不会重新启动。
        </p>
      )}
      <div className="button-row">
        <Button disabled={stopping || current.owned_resources_closed} onClick={() => void stop()}>
          {stopping
            ? '正在请求停止…'
            : unknownStop || current.stop_requested
              ? '再次明确请求停止（不重启）'
              : '停止此命令'}
        </Button>
        <Button disabled={checking} onClick={() => void check()}>
          {checking ? '正在核对…' : '只读核对当前命令'}
        </Button>
      </div>
      {notice && (
        <p role="status" className="command-notice">
          {notice}
        </p>
      )}
      {current.error && (
        <details className="command-error">
          <summary>服务器错误详情</summary>
          <pre>{JSON.stringify(current.error, null, 2)}</pre>
        </details>
      )}
      <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>查看 stdout / stderr 分页与结束日志</summary>
        {inspect.error && (
          <p role="alert" className="form-error">
            {inspect.error.message}
          </p>
        )}
        <CommandLogs workspaceId={workspaceId} command={current} active={active && open} />
      </details>
    </article>
  )
}
