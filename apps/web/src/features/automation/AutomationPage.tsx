import { useEffect, useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Monitor, RefreshCw } from 'lucide-react'
import { automationApi } from '../../api/automation'
import { closePendingRequest } from '../../api/requests'
import type { AutomationSessionCreate } from '../../api/automation'
import type { Workspace } from '../../api/types'
import { Button, EmptyState, PageHeading, Status } from '../../components/ui'
import { SessionCard } from './SessionCard'
import { errorText, json, record, statusLabel, text, uncertain } from './data'
import './automation.css'

export function AutomationPage({
  workspace,
  authenticated,
  active,
}: {
  workspace?: Workspace
  authenticated: boolean
  active: boolean
}) {
  const [visited, setVisited] = useState<Record<string, Workspace>>({})
  useEffect(() => {
    if (workspace)
      setVisited((old) =>
        old[workspace.id] === workspace ? old : { ...old, [workspace.id]: workspace },
      )
  }, [workspace])
  const known = workspace ? { ...visited, [workspace.id]: workspace } : visited
  return (
    <div className="page-content automation-page">
      <PageHeading title="电脑操作">查看浏览器与桌面能力、当前会话，或随时停止操作。</PageHeading>
      {!authenticated || !workspace ? (
        <EmptyState icon={Monitor} title={authenticated ? '先选择工作区' : '电脑操作服务尚未连接'}>
          连接工作台并选择工作区后，读取真实能力与授权状态。
        </EmptyState>
      ) : (
        Object.values(known).map((item) => (
          <div key={item.id} hidden={item.id !== workspace.id}>
            <AutomationWorkspace
              workspaceId={item.id}
              active={active && item.id === workspace.id}
            />
          </div>
        ))
      )}
    </div>
  )
}
function AutomationWorkspace({ workspaceId, active }: { workspaceId: string; active: boolean }) {
  const id = useId()
  const settings = useQuery({
    queryKey: ['automation-settings'],
    queryFn: automationApi.settings,
    enabled: active,
  })
  const capabilities = useQuery({
    queryKey: ['automation-capabilities', workspaceId],
    queryFn: () => automationApi.capabilities(workspaceId),
    enabled: active,
    refetchInterval: active ? 10000 : false,
  })
  const sessions = useQuery({
    queryKey: ['automation-sessions', workspaceId],
    queryFn: () => automationApi.sessions(workspaceId),
    enabled: active,
    refetchInterval: active ? 4000 : false,
  })
  const [kind, setKind] = useState<AutomationSessionCreate['kind']>('browser')
  const [mode, setMode] = useState<'observe' | 'control'>('observe')
  const [ttl, setTtl] = useState('')
  const [creating, setCreating] = useState(false)
  const [createPending, setCreatePending] = useState<string | null>(() => {
    try {
      return localStorage.getItem(`workbench.automation.create.${workspaceId}`)
    } catch {
      return null
    }
  })
  const [startingRuntime, setStartingRuntime] = useState(false)
  const [stoppingRuntime, setStoppingRuntime] = useState(false)
  const [runtimeUnknown, setRuntimeUnknown] = useState<{
    action: 'start' | 'stop'
    id: string
  } | null>(() => {
    try {
      const value = record(
        JSON.parse(localStorage.getItem(`workbench.automation.runtime.${workspaceId}`) ?? 'null'),
      )
      return value &&
        (value.action === 'start' || value.action === 'stop') &&
        typeof value.id === 'string'
        ? { action: value.action, id: value.id }
        : null
    } catch {
      return null
    }
  })
  const [message, setMessage] = useState('')
  const runtimePendingRef = useRef(runtimeUnknown)
  const runtime = capabilities.data?.runtime
  useEffect(() => {
    if (createPending && sessions.data?.some((session) => session.id === createPending)) {
      setCreatePending(null)
      try {
        localStorage.removeItem(`workbench.automation.create.${workspaceId}`)
      } catch {
        /* Server remains the source of truth. */
      }
    }
  }, [createPending, sessions.data, workspaceId])
  async function refreshSessions() {
    const result = await sessions.refetch()
    if (result.error) throw result.error
    return result.data
  }
  async function closeCreate() {
    if (!createPending || creating) return
    setCreating(true)
    try {
      const result = await closePendingRequest(createPending)
      if (result.status === 'sealed') {
        localStorage.removeItem(`workbench.automation.create.${workspaceId}`)
        setCreatePending(null)
        setMessage('原创建请求已停止接收，迟到请求不能再创建会话。可重新填写并明确提交。')
      }
      await refreshSessions()
    } catch (error) {
      setMessage(errorText(error))
    } finally {
      setCreating(false)
    }
  }
  async function refreshCapabilities() {
    const result = await capabilities.refetch()
    if (result.error || !result.data) throw result.error ?? new Error('能力状态尚未返回。')
    return result.data
  }
  async function runtimeCommand(action: 'start' | 'stop') {
    if (
      (action === 'start' && (startingRuntime || runtimeUnknown)) ||
      (action === 'stop' && stoppingRuntime)
    )
      return
    const requestId = crypto.randomUUID()
    const setWorking = action === 'start' ? setStartingRuntime : setStoppingRuntime
    setWorking(true)
    rememberRuntime({ action, id: requestId })
    setMessage('')
    try {
      const result = await automationApi.runtime(workspaceId, action, requestId)
      setMessage(`运行时：${statusLabel(result.status)}。${result.reason}`)
      await refreshCapabilities()
      if (runtimePendingRef.current?.id === requestId) rememberRuntime(null)
    } catch (error) {
      if (runtimePendingRef.current?.id === requestId) rememberRuntime({ action, id: requestId })
      setMessage(`${errorText(error)} 运行时操作结果待核对，不会自动重发或重启。`)
    } finally {
      setWorking(false)
    }
  }
  function rememberRuntime(value: typeof runtimeUnknown) {
    runtimePendingRef.current = value
    setRuntimeUnknown(value)
    try {
      const key = `workbench.automation.runtime.${workspaceId}`
      if (value) localStorage.setItem(key, JSON.stringify(value))
      else localStorage.removeItem(key)
    } catch {
      setMessage('浏览器无法保留运行时请求标识；离开前请记录本次 ID，不会自动重启。')
    }
  }
  async function create() {
    if (creating || createPending || !settings.data?.enabled) return
    let input: AutomationSessionCreate
    try {
      const seconds = ttl.trim() ? Number(ttl) : undefined
      if (seconds !== undefined && (!Number.isInteger(seconds) || seconds < 30 || seconds > 900))
        throw new Error('会话期限须为 30–900 秒。')
      input = {
        kind,
        mode: kind === 'browser' ? 'observe' : mode,
        ...(seconds !== undefined ? { ttl_seconds: seconds } : {}),
        request_id: crypto.randomUUID(),
      }
    } catch (error) {
      setMessage(errorText(error))
      return
    }
    setCreating(true)
    setCreatePending(input.request_id)
    try {
      localStorage.setItem(`workbench.automation.create.${workspaceId}`, input.request_id)
    } catch {
      setMessage(`浏览器无法保留恢复标识，请记录本次请求：${input.request_id}`)
    }
    try {
      const result = await automationApi.create(workspaceId, input)
      await refreshSessions()
      setMessage(`会话状态：${statusLabel(result.status)}。没有自动读取网页或捕获窗口。`)
    } catch (error) {
      if (uncertain(error))
        setMessage(
          `${errorText(error)} 已保留会话请求标识；请只读刷新会话列表核对，不自动新建第二个会话。`,
        )
      else {
        setCreatePending(null)
        try {
          localStorage.removeItem(`workbench.automation.create.${workspaceId}`)
        } catch {
          /* Only a nonsecret recovery identifier is stored. */
        }
        setMessage(`会话请求被拒绝：${errorText(error)}。没有自动重试。`)
        await refreshSessions().catch(() => undefined)
      }
    } finally {
      setCreating(false)
    }
  }
  return (
    <>
      <section className="automation-section">
        <div className="run-line">
          <h2>原生运行时</h2>
          <Status tone={runtime?.status === 'unknown' ? 'warning' : 'neutral'}>
            {runtime ? statusLabel(runtime.status) : '未读取'}
          </Status>
        </div>
        <p className="page-note">
          {runtime?.reason ?? '尚未读取原生执行器状态。打开此页不会启动执行器。'}
        </p>
        <div className="button-row">
          <Button
            variant="primary"
            disabled={
              runtime?.status !== 'stopped' ||
              startingRuntime ||
              stoppingRuntime ||
              !!runtimeUnknown
            }
            onClick={() => void runtimeCommand('start')}
          >
            明确启动运行时
          </Button>
          <Button disabled={stoppingRuntime} onClick={() => void runtimeCommand('stop')}>
            停止运行时
          </Button>
          <Button
            onClick={() =>
              void refreshCapabilities().catch((error) => setMessage(errorText(error)))
            }
          >
            <RefreshCw size={14} />
            只读刷新能力
          </Button>
        </div>
        {runtimeUnknown && (
          <div className="automation-pending">
            <p>
              待核对运行时请求：{runtimeUnknown.id}。只读刷新后的当前状态：
              {runtime ? statusLabel(runtime.status) : '未读取'}。
            </p>
            <Button
              onClick={async () => {
                try {
                  const latest = await refreshCapabilities()
                  if (
                    (runtimeUnknown.action === 'start' && latest.runtime.status === 'running') ||
                    (runtimeUnknown.action === 'stop' && latest.runtime.status === 'stopped')
                  ) {
                    rememberRuntime(null)
                    setMessage(
                      `已重新读取当前运行时：${statusLabel(latest.runtime.status)}。原操作未被重发。`,
                    )
                  } else
                    setMessage(
                      `尚未观察到本次请求的目标状态，不会重发启动。可明确停止运行时后再核对。${latest.runtime.reason}`,
                    )
                } catch (error) {
                  setMessage(errorText(error))
                }
              }}
            >
              只读核对当前运行时
            </Button>
          </div>
        )}
        {capabilities.error && (
          <p role="alert" className="form-error">
            {capabilities.error.message}
          </p>
        )}
        {capabilities.data && (
          <details className="automation-capabilities">
            <summary>实际能力与限制</summary>
            <div className="automation-grid">
              {(['browser', 'desktop'] as const).map((kind) => (
                <div key={kind}>
                  <h3>{kind === 'browser' ? '浏览器' : '桌面'}</h3>
                  {capabilities.data[kind] ? (
                    <Capability value={capabilities.data[kind]} />
                  ) : (
                    <p className="page-note">运行时尚未提供此能力信息。</p>
                  )}
                </div>
              ))}
            </div>
            {capabilities.data.limitations.map((note) => (
              <p className="page-note" key={note}>
                {note}
              </p>
            ))}
          </details>
        )}
      </section>
      <p className="page-note">
        {settings.data
          ? settings.data.enabled
            ? '电脑操作已开启；可在设置中关闭。首次使用时自动启动执行器，AI 操作由智能审核自动判断。'
            : '电脑操作已关闭。请在设置中开启后使用。'
          : '正在读取电脑操作设置…'}
      </p>
      {settings.isError && (
        <p className="form-error" role="alert">
          电脑操作设置读取失败。
        </p>
      )}
      <section className="automation-section">
        <h2>会话</h2>
        <details open>
          <summary>新建独立会话</summary>
          <fieldset
            className="automation-fields"
            disabled={creating || !!createPending || !settings.data?.enabled}
          >
            <div className="automation-grid">
              <label htmlFor={`${id}-kind`}>
                会话类型
                <select
                  id={`${id}-kind`}
                  value={kind}
                  onChange={(e) => {
                    setKind(e.target.value as AutomationSessionCreate['kind'])
                    setMode('observe')
                  }}
                >
                  <option value="browser">浏览器</option>
                  <option value="desktop">桌面</option>
                </select>
              </label>
              <label htmlFor={`${id}-mode`}>
                权限模式
                <select
                  id={`${id}-mode`}
                  value={kind === 'browser' ? 'observe' : mode}
                  disabled={kind === 'browser'}
                  onChange={(e) => setMode(e.target.value as 'observe' | 'control')}
                >
                  <option value="observe">观察</option>
                  {kind === 'desktop' && <option value="control">控制 · 独立会话</option>}
                </select>
              </label>
            </div>
            <label htmlFor={`${id}-ttl`}>
              会话期限（30–900 秒，留空由服务端采用默认值）
              <input
                id={`${id}-ttl`}
                type="number"
                min={30}
                max={900}
                value={ttl}
                onChange={(e) => setTtl(e.target.value)}
              />
            </label>
            <p className="page-note">
              {kind === 'browser'
                ? '独立浏览器可访问公网网站，不继承日常浏览器的登录。'
                : mode === 'control'
                  ? '控制会话可操作不同桌面窗口；切换窗口后需要重新观察。'
                  : '桌面观察会话只捕获桌面，不允许输入。'}
            </p>
          </fieldset>
          <Button
            disabled={
              !settings.data?.enabled ||
              creating ||
              !!createPending ||
              runtime?.status === 'unknown'
            }
            onClick={() => void create()}
          >
            创建会话
          </Button>
        </details>
        {createPending && (
          <div className="automation-pending">
            <p>待核对会话请求：{createPending}。创建命令未自动重发。</p>
            <Button
              disabled={creating}
              onClick={() => void refreshSessions().catch((error) => setMessage(errorText(error)))}
            >
              只读读取会话列表
            </Button>
            <Button disabled={creating} onClick={() => void closeCreate()}>
              关闭未受理请求
            </Button>
            <p className="page-note">
              若列表中始终没有此 ID，须核对服务端处理结果；此页不会凭超时自动重新创建。
            </p>
          </div>
        )}
        {sessions.error && (
          <p role="alert" className="form-error">
            {sessions.error.message}
          </p>
        )}
        {sessions.data?.length ? (
          <div className="automation-session-list">
            {sessions.data.map((session) => (
              <SessionCard
                key={session.id}
                workspaceId={workspaceId}
                session={session}
                runtimeRunning={runtime?.status === 'running' && !!settings.data?.enabled}
                onRefresh={refreshSessions}
              />
            ))}
          </div>
        ) : (
          <p className="page-note">
            {sessions.data
              ? '当前没有会话。电脑操作开启后，AI 会在需要时创建，也可以在此手动创建。'
              : '正在读取会话列表…'}
          </p>
        )}
      </section>
      {message && (
        <p role="status" className="automation-message">
          {message}
        </p>
      )}
    </>
  )
}
function Capability({ value }: { value: Record<string, unknown> }) {
  return (
    <>
      <dl className="run-meta">
        {Object.entries(value).flatMap(([key, raw]) => {
          const item = record(raw)
          const status = text(item?.status)
          const reason = text(item?.reason)
          return status
            ? [
                <div className="automation-capability-row" key={key}>
                  <dt>
                    {(
                      {
                        capture: '截屏',
                        input: '输入',
                        window_identity: '窗口标识',
                        human_input_detection: '人工输入检测',
                      } as Record<string, string>
                    )[key] ?? key}
                  </dt>
                  <dd>
                    {statusLabel(status)}
                    {reason ? ` · ${reason}` : ''}
                  </dd>
                </div>,
              ]
            : []
        })}
      </dl>
      {text(value.status) && <p>{statusLabel(String(value.status))}</p>}
      {text(value.reason) && <p className="page-note">{text(value.reason)}</p>}
      <details>
        <summary>能力原始数据</summary>
        <pre>{json(value)}</pre>
      </details>
    </>
  )
}
