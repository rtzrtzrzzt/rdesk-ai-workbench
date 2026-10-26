import { useId, useRef, useState } from 'react'
import { automationApi } from '../../api/automation'
import { ApiError } from '../../api/client'
import type { AutomationAction, AutomationSession, BrowserDownload } from '../../api/automation'
import { Button, Status } from '../../components/ui'
import { ActionForm } from './ActionForm'
import { Observation } from './Observation'
import { errorText, json, record, statusLabel, text, uncertain } from './data'
import { observedDownloadLinks } from './downloads'
import type { ObservedDownloadLink } from './downloads'
import { DownloadedArtifact } from './DownloadedArtifact'

type Pending =
  | { id: string; kind: 'observe' | 'action' }
  | { id: string; kind: 'download'; sessionId: string; input: BrowserDownload }
function readPending(key: string): Pending | null {
  try {
    const value = record(JSON.parse(localStorage.getItem(key) ?? 'null'))
    if (value && value.kind === 'download') {
      const input = record(value.input)
      return typeof value.id === 'string' &&
        typeof value.sessionId === 'string' &&
        input?.request_id === value.id &&
        typeof input.last_observation === 'string' &&
        typeof input.target_ref === 'string' &&
        /^e[0-9]{1,4}$/.test(input.target_ref)
        ? {
            id: value.id,
            kind: 'download',
            sessionId: value.sessionId,
            input: {
              request_id: value.id,
              last_observation: input.last_observation,
              target_ref: input.target_ref,
            },
          }
        : null
    }
    return value &&
      typeof value.id === 'string' &&
      (value.kind === 'observe' || value.kind === 'action')
      ? { id: value.id, kind: value.kind }
      : null
  } catch {
    return null
  }
}
export function SessionCard({
  workspaceId,
  session,
  runtimeRunning,
  onRefresh,
}: {
  workspaceId: string
  session: AutomationSession
  runtimeRunning: boolean
  onRefresh: () => Promise<unknown>
}) {
  const id = useId()
  const storageKey = `workbench.automation.pending.${workspaceId}.${session.id}`
  const [expanded, setExpanded] = useState(false)
  const [pending, setPending] = useState<Pending | null>(() => readPending(storageKey))
  const [result, setResult] = useState<AutomationAction | null>(null)
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [takingOver, setTakingOver] = useState(false)
  const [checking, setChecking] = useState(false)
  const [screenshot, setScreenshot] = useState(false)
  const [freshDesktop, setFreshDesktop] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [downloadTarget, setDownloadTarget] = useState<ObservedDownloadLink | null>(null)
  const [staleDownloadObservation, setStaleDownloadObservation] = useState<string | null>(null)
  // Kept in memory only: uncertain native input must never be regenerated or replayed.
  const submitted = useRef<unknown>(null)
  const observation = session.observation
  const observationId = text(observation?.observation_id)
  const usable =
    session.status === 'active' && runtimeRunning && Date.parse(session.expires_at) > Date.now()
  const downloadSupported =
    session.kind === 'browser' && session.native_view.explicit_download_supported === true
  const targetStillCurrent =
    !!downloadTarget &&
    downloadTarget.observationId !== staleDownloadObservation &&
    observedDownloadLinks(observation).some(
      (item) =>
        item.observationId === downloadTarget.observationId &&
        item.targetRef === downloadTarget.targetRef &&
        item.url === downloadTarget.url,
    )
  const downloadedArtifact = result?.status === 'succeeded' ? record(result.result?.artifact) : null
  function remember(value: Pending | null) {
    setPending(value)
    try {
      if (value) localStorage.setItem(storageKey, JSON.stringify(value))
      else localStorage.removeItem(storageKey)
    } catch {
      setMessage('浏览器无法保存核对标识。离开前请记录本次请求 ID，未确认的原生动作不会自动重发。')
    }
  }
  async function receive(value: AutomationAction, kind: Pending['kind']) {
    setResult(value)
    if (['succeeded', 'failed'].includes(value.status)) {
      remember(null)
      submitted.current = null
      if (kind === 'observe' && value.status === 'succeeded' && session.kind === 'desktop')
        setFreshDesktop(text(value.result?.observation_id))
    }
    await onRefresh()
  }
  async function perform(command: Pending, input: unknown, call: () => Promise<AutomationAction>) {
    if (busy || pending) return
    const { id: requestId, kind } = command
    setBusy(true)
    setMessage('')
    if (session.kind === 'desktop') setFreshDesktop(null)
    submitted.current = input
    remember(command)
    try {
      await receive(await call(), kind)
    } catch (error) {
      setMessage(
        `${errorText(error)}${kind === 'download' && error instanceof ApiError && error.status === 409 ? ' 链接观察或下载状态有冲突，请重新观察并核对本次记录；未重新拉取。' : ' 本次请求未被重发；请只读核对动作记录。'}`,
      )
      if (kind === 'download' && error instanceof ApiError && error.status === 409) {
        setDownloadTarget(null)
        if (command.kind === 'download') setStaleDownloadObservation(command.input.last_observation)
      }
      try {
        await receive(await automationApi.action(workspaceId, requestId), kind)
      } catch (readError) {
        if (!uncertain(error) && readError instanceof ApiError && readError.status === 404) {
          remember(null)
          submitted.current = null
          setMessage(
            `请求被拒绝且无动作记录：${errorText(error)}。${kind === 'download' && error instanceof ApiError && error.status === 409 ? '请重新观察后选择链接。' : ''}未自动重试。`,
          )
        }
      }
    } finally {
      setBusy(false)
    }
  }
  async function check() {
    if (!pending || checking) return
    setChecking(true)
    try {
      await receive(await automationApi.action(workspaceId, pending.id), pending.kind)
    } catch (error) {
      setMessage(`${errorText(error)} 没有可确认结果，未重发原动作。`)
    } finally {
      setChecking(false)
    }
  }
  async function download() {
    if (!downloadTarget || !targetStillCurrent || !usable || !downloadSupported || busy || pending)
      return
    const input: BrowserDownload = {
      request_id: crypto.randomUUID(),
      last_observation: downloadTarget.observationId,
      target_ref: downloadTarget.targetRef,
    }
    await perform(
      { id: input.request_id, kind: 'download', sessionId: session.id, input },
      { sessionId: session.id, input, url: downloadTarget.url },
      () => automationApi.download(workspaceId, session.id, input),
    )
    setDownloadTarget(null)
  }
  async function stop(action: 'stop' | 'takeover') {
    const setWorking = action === 'stop' ? setStopping : setTakingOver
    setWorking(true)
    try {
      const updated = await automationApi.stop(workspaceId, session.id, action, crypto.randomUUID())
      setFreshDesktop(null)
      setMessage(`服务器会话状态：${statusLabel(updated.status)}。已发出的动作不能撤回。`)
      await onRefresh()
    } catch (error) {
      setMessage(`${errorText(error)} 停止或接管结果尚需核对服务器会话状态。`)
      await onRefresh().catch(() => undefined)
    } finally {
      setWorking(false)
    }
  }
  return (
    <article className="automation-session">
      <div className="run-line">
        <h3>
          {session.kind === 'browser'
            ? '浏览器'
            : session.kind === 'desktop'
              ? '桌面'
              : session.kind}{' '}
          ·{' '}
          {session.mode === 'control'
            ? '控制会话'
            : session.mode === 'observe'
              ? '观察会话'
              : session.mode}
        </h3>
        <Status tone={session.status === 'outcome_unknown' ? 'warning' : 'neutral'}>
          {statusLabel(session.status)}
        </Status>
      </div>
      <p className="automation-wrap page-note">
        {session.id} · 授权版本 {session.policy_version} · 到期{' '}
        {new Date(session.expires_at).toLocaleString()}
      </p>
      <div className="button-row">
        <Button disabled={stopping} onClick={() => void stop('stop')}>
          {stopping ? '正在停止…' : '停止会话'}
        </Button>
        <Button disabled={takingOver} onClick={() => void stop('takeover')}>
          {takingOver ? '正在接管…' : '人工接管'}
        </Button>
      </div>
      <details open={expanded} onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>观察、动作与实际结果</summary>
        {expanded && (
          <>
            {session.kind === 'browser' && (
              <label className="automation-check" htmlFor={`${id}-capture`}>
                <input
                  id={`${id}-capture`}
                  type="checkbox"
                  checked={screenshot}
                  onChange={(e) => setScreenshot(e.target.checked)}
                  disabled={busy || !!pending}
                />
                本次观察同时截图
              </label>
            )}
            <Button
              disabled={!usable || busy || !!pending}
              onClick={() => {
                const requestId = crypto.randomUUID()
                void perform(
                  { kind: 'observe', id: requestId },
                  { screenshot: session.kind === 'desktop' || screenshot, request_id: requestId },
                  () =>
                    automationApi.observe(
                      workspaceId,
                      session.id,
                      session.kind === 'desktop' || screenshot,
                      requestId,
                    ),
                )
              }}
            >
              {session.kind === 'desktop' ? '捕获当前桌面' : '读取当前 DOM 与网页纯文本'}
            </Button>
            {!usable && (
              <p className="page-note">
                当前会话未处于可操作状态、已到期或运行时未运行。停止与人工接管仍可请求。
              </p>
            )}
            <Observation
              workspaceId={workspaceId}
              value={observation}
              downloadDisabled={
                !usable ||
                !downloadSupported ||
                busy ||
                !!pending ||
                observationId === staleDownloadObservation
              }
              onDownload={
                session.kind === 'browser' && session.status === 'active' && downloadSupported
                  ? (link) => {
                      setDownloadTarget(link)
                    }
                  : undefined
              }
            />
            {session.kind === 'browser' && !downloadSupported && (
              <p className="page-note">
                此会话尚未声明支持明确链接下载，入口不可用；浏览器自动下载保持关闭。
              </p>
            )}
            {session.kind === 'browser' &&
              observationId &&
              observationId === staleDownloadObservation && (
                <p className="form-error">
                  此观察的下载引用已发生冲突。请重新读取当前 DOM，再明确选择下载链接。
                </p>
              )}
            {downloadTarget && (
              <div className="automation-download-confirm">
                <h4>确认受管链接下载</h4>
                <p className="automation-wrap">{downloadTarget.url}</p>
                <p className="page-note">
                  绑定观察 {downloadTarget.observationId} · 元素 {downloadTarget.targetRef}。最多 16
                  MiB，不继承浏览器登录 Cookie；仅下载明确链接，Chrome
                  自动下载仍关闭。需要登录的链接可能被拒绝。
                </p>
                {!targetStillCurrent && (
                  <p className="form-error">
                    当前观察已变化，请重新观察并选择链接，不会使用旧引用下载。
                  </p>
                )}
                <div className="button-row">
                  <Button
                    disabled={busy}
                    onClick={() => {
                      setDownloadTarget(null)
                    }}
                  >
                    取消选择
                  </Button>
                  <Button
                    variant="primary"
                    disabled={
                      !usable || !targetStillCurrent || busy || !!pending || !downloadSupported
                    }
                    onClick={() => void download()}
                  >
                    确认下载此链接
                  </Button>
                </div>
              </div>
            )}
            {session.kind === 'desktop' && freshDesktop !== observationId && (
              <p className="page-note">
                桌面输入前请在本页重新捕获；已有截图不会自动视为仍有效。
              </p>
            )}
            {(session.kind === 'browser' ||
              (session.kind === 'desktop' && session.mode === 'control')) && (
              <ActionForm
                key={observationId ?? 'unobserved'}
                session={session}
                observation={observation}
                disabled={
                  !usable ||
                  busy ||
                  !!pending ||
                  (session.kind === 'desktop' && freshDesktop !== observationId)
                }
                onSubmit={async (command) => {
                  await perform({ kind: 'action', id: command.action.request_id }, command, () =>
                    command.kind === 'browser'
                      ? automationApi.browser(workspaceId, session.id, command.action)
                      : automationApi.desktop(workspaceId, session.id, command.action),
                  )
                }}
              />
            )}
            {session.kind === 'desktop' && session.mode !== 'control' && (
              <p className="page-note">
                观察会话只捕获桌面，不能升级为控制；需要单独新建控制会话。
              </p>
            )}
            {pending && (
              <div className="automation-pending">
                <p>
                  待核对请求：{pending.id}。
                  {pending.kind === 'download'
                    ? `下载快照：会话 ${pending.sessionId} · 观察 ${pending.input.last_observation} · 元素 ${pending.input.target_ref}。只读核对，不重新拉取。`
                    : '原参数保留于当前页面内存；重新载入后仅保留请求标识，不重建或重发输入。'}
                </p>
                <Button disabled={busy || checking} onClick={() => void check()}>
                  {checking ? '读取中…' : '只读核对此动作'}
                </Button>
              </div>
            )}
            {result && (
              <div className="automation-result">
                <div className="run-line">
                  <h4>最近读取的动作</h4>
                  <Status tone={result.status === 'outcome_unknown' ? 'warning' : 'neutral'}>
                    {statusLabel(result.status)}
                  </Status>
                </div>
                <p>{result.id}</p>
                {text(result.result?.message) && <p>{text(result.result?.message)}</p>}
                {result.result?.execution_status === 'executed' && (
                  <p className="page-note">原生调用已返回；应用业务结果尚未核实。</p>
                )}
                {downloadedArtifact && (
                  <DownloadedArtifact workspaceId={workspaceId} receipt={downloadedArtifact} />
                )}
                <details>
                  <summary>动作结果原始数据</summary>
                  <pre>{json(result)}</pre>
                </details>
              </div>
            )}
            {session.error && (
              <details>
                <summary>服务器会话错误</summary>
                <pre>{json(session.error)}</pre>
              </details>
            )}
            <details>
              <summary>原生会话元数据</summary>
              <pre>{json(session.native_view)}</pre>
            </details>
          </>
        )}
      </details>
      {message && (
        <p role="status" className="automation-message">
          {message}
        </p>
      )}
    </article>
  )
}
