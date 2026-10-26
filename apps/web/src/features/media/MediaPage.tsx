import { useId, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Settings2, Sparkles } from 'lucide-react'
import { ApiError, authGeneration } from '../../api/client'
import {
  mediaApi,
  type MediaCreate,
  type MediaKind,
  type MediaReference,
  type MediaTaskView,
} from '../../api/media'
import { closePendingRequest } from '../../api/requests'
import type { Workspace } from '../../api/types'
import { Button, EmptyState, PageHeading } from '../../components/ui'
import { MediaConnectionsPanel } from './MediaConnectionsPanel'
import { MediaReferences } from './MediaReferences'
import { MediaTaskHistory } from './MediaTaskHistory'
import { updateTaskInHistory } from './history'
import { generationKey, readPendingGeneration } from './pending'
import { capabilities, describeError, record } from './shared'
import './media.css'

type Draft = {
  prompt: string
  connectionId: string
  width: string
  height: string
  seed: string
  steps: string
  duration: string
  voice: string
  format: string
  references: MediaReference[]
  consentTarget: string
}
function freshDraft(): Draft {
  return {
    prompt: '',
    connectionId: '',
    width: '1024',
    height: '1024',
    seed: '',
    steps: '',
    duration: '',
    voice: '',
    format: 'glb',
    references: [],
    consentTarget: '',
  }
}
function initialDrafts(pending: MediaCreate | null): Record<MediaKind, Draft> {
  const result = {
    image: freshDraft(),
    video: { ...freshDraft(), duration: '5' },
    audio: freshDraft(),
    '3d': freshDraft(),
  }
  if (pending?.capability)
    result[pending.capability] = {
      ...result[pending.capability],
      prompt: pending.prompt,
      connectionId: pending.connection_id ?? '',
      width: String(pending.width ?? 1024),
      height: String(pending.height ?? 1024),
      seed: pending.seed === null || pending.seed === undefined ? '' : String(pending.seed),
      steps: pending.steps === null || pending.steps === undefined ? '' : String(pending.steps),
      duration:
        pending.duration_seconds === null || pending.duration_seconds === undefined
          ? ''
          : String(pending.duration_seconds),
      voice: pending.voice ?? '',
      format: pending.format ?? 'glb',
      references: pending.references ?? [],
    }
  return result
}

export function MediaPage({
  workspace,
  authenticated,
  userId,
  active,
}: {
  workspace?: Workspace
  authenticated: boolean
  userId?: string
  active: boolean
}) {
  return (
    <div className="page-content media-page">
      <PageHeading title="生成">选择已接入模型，直接生成图像、视频、音频或 3D。</PageHeading>
      {!authenticated || !workspace || !userId ? (
        <EmptyState icon={Sparkles} title="先选择工作区">
          生成记录和实际产物保存在所选工作区中。
        </EmptyState>
      ) : (
        <MediaWorkspace
          key={`${userId}:${workspace.id}`}
          workspace={workspace}
          userId={userId}
          active={active}
        />
      )}
    </div>
  )
}

function MediaWorkspace({
  workspace,
  userId,
  active,
}: {
  workspace: Workspace
  userId: string
  active: boolean
}) {
  const client = useQueryClient()
  const key = generationKey(userId, workspace.id)
  const [restored] = useState(() => readPendingGeneration(key))
  const [pending, setPending] = useState<MediaCreate | null>(restored.input)
  const [capability, setCapability] = useState<MediaKind>(restored.input?.capability ?? 'image')
  const [drafts, setDrafts] = useState(() => initialDrafts(restored.input))
  const [showConnections, setShowConnections] = useState(false)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const tabId = useId()
  const connections = useQuery({
    queryKey: ['media-connections', workspace.id],
    queryFn: ({ signal }) => mediaApi.connections(workspace.id, signal),
    enabled: active,
    retry: false,
  })
  const available = (connections.data?.connections ?? []).filter(
    (connection) => connection.enabled && connection.capability === capability,
  )
  const draft = drafts[capability]
  const selected =
    available.find((connection) => connection.id === draft.connectionId) ??
    (!draft.connectionId
      ? (available.find(
          (connection) => connection.is_default && connection.scope === 'workspace',
        ) ??
        available.find((connection) => connection.is_default) ??
        available[0])
      : undefined)
  const invalidConnection = !!draft.connectionId && !selected
  const remoteReferences =
    !!selected &&
    selected.driver !== 'flux3_native' &&
    selected.allow_remote &&
    draft.references.length > 0
  const consentTarget = `${selected?.id ?? ''}:${selected?.version ?? ''}:${draft.references.map((reference) => `${reference.artifact_id}:${reference.version}`).join(',')}`
  const consent = draft.consentTarget === consentTarget
  const locked = busy || !!pending || !!restored.error
  function updateDraft(change: Partial<Draft>) {
    setDrafts((previous) => ({ ...previous, [capability]: { ...previous[capability], ...change } }))
  }
  function store(input: MediaCreate | null) {
    if (input) localStorage.setItem(key, JSON.stringify(input))
    else localStorage.removeItem(key)
    setPending(input)
  }
  async function accepted(task: MediaTaskView) {
    updateTaskInHistory(client, workspace.id, task)
    store(null)
    setDrafts((previous) => ({
      ...previous,
      [task.capability]: { ...previous[task.capability], consentTarget: '' },
    }))
    setFailure(false)
    setMessage('生成任务已接收，实际进度和产物见下方记录。')
    await client.invalidateQueries({ queryKey: ['media-tasks', workspace.id] })
  }
  async function submit(input: MediaCreate, original = false) {
    if (inFlight.current || restored.error || (!original && pending)) return
    try {
      store(input)
    } catch {
      setFailure(true)
      setMessage('无法保留本次生成请求标识，尚未提交。请检查浏览器存储后重试。')
      return
    }
    const generation = authGeneration()
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      const task = await mediaApi.create(workspace.id, input)
      if (generation !== authGeneration()) return
      await accepted(task)
    } catch (error) {
      if (generation !== authGeneration()) return
      let retain =
        original || !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      if (!retain) {
        try {
          store(null)
        } catch {
          retain = true
        }
      }
      setFailure(true)
      setMessage(
        `${describeError(error)}${retain ? ' 原生成参数和请求标识已保留，请先核对；不会自动重新生成。' : ''}`,
      )
      if (error instanceof ApiError && error.status === 409)
        void client.invalidateQueries({ queryKey: ['media-connections', workspace.id] })
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  function create() {
    if (locked || !selected || !draft.prompt.trim() || (remoteReferences && !consent)) return
    void submit({
      capability,
      prompt: draft.prompt.trim(),
      connection_id: selected.id,
      expected_connection_version: selected.version,
      request_id: crypto.randomUUID(),
      references:
        capability === 'audio'
          ? []
          : draft.references.map((reference) => ({
              ...reference,
              consent_remote: remoteReferences && consent,
            })),
      ...(capability !== 'audio'
        ? {
            width: Number(draft.width),
            height: Number(draft.height),
            ...(draft.seed !== '' ? { seed: Number(draft.seed) } : {}),
            ...(draft.steps !== '' ? { steps: Number(draft.steps) } : {}),
          }
        : {}),
      ...(capability === 'video' || capability === 'audio'
        ? { ...(draft.duration !== '' ? { duration_seconds: Number(draft.duration) } : {}) }
        : {}),
      ...(capability === 'audio' && draft.voice ? { voice: draft.voice.trim() } : {}),
      ...(capability === '3d' ? { format: draft.format } : {}),
    })
  }
  async function reconcile(close = false) {
    if (!pending || inFlight.current) return
    const input = pending
    const generation = authGeneration()
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      if (close) {
        const result = await closePendingRequest(input.request_id)
        if (generation !== authGeneration()) return
        if (result.status === 'sealed') {
          store(null)
          setMessage('已结束本次待核对请求。原标识不能再次提交，请先查看生成记录。')
          await client.invalidateQueries({ queryKey: ['media-tasks', workspace.id] })
          return
        }
      }
      const receipt = record(await mediaApi.receipt(input.request_id))
      const runId =
        typeof receipt?.run_id === 'string'
          ? receipt.run_id
          : typeof receipt?.id === 'string'
            ? receipt.id
            : ''
      if (!runId) throw new Error('未取得可核对的任务标识，原请求仍保留。')
      const task = await mediaApi.task(workspace.id, runId)
      if (task.connection_id !== input.connection_id || task.capability !== input.capability)
        throw new Error('任务与原请求不匹配，原请求仍保留。')
      if (generation !== authGeneration()) return
      await accepted(task)
    } catch (error) {
      if (generation !== authGeneration()) return
      setFailure(true)
      setMessage(
        error instanceof ApiError && error.status === 404
          ? '尚未查到完整回执，请求可能仍在处理。可稍后核对，或使用原请求标识重试。'
          : describeError(error),
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const kind = capabilities.find((item) => item.id === capability)!
  return (
    <>
      <div className="media-toolbar">
        <div className="media-tabs" role="tablist" aria-label="生成类型">
          {capabilities.map((item) => (
            <button
              type="button"
              role="tab"
              key={item.id}
              id={`${tabId}-${item.id}-tab`}
              aria-selected={capability === item.id}
              aria-controls={`${tabId}-panel`}
              onClick={() => setCapability(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <Button onClick={() => setShowConnections(!showConnections)}>
          <Settings2 size={15} />
          {showConnections ? '收起生成模型' : '管理生成模型'}
        </Button>
      </div>
      {showConnections && (
        <div className="media-inline-connections">
          <h2>生成模型</h2>
          <MediaConnectionsPanel workspace={workspace} active={active} />
        </div>
      )}
      {restored.error && (
        <p className="form-error" role="alert">
          {restored.error}
        </p>
      )}
      {pending && (
        <section className="media-pending" aria-label="待核对生成请求">
          <p role="status">
            {busy ? '正在提交或核对生成请求…' : '上次生成请求的结果待核对，原参数与标识已保留。'}
          </p>
          <p className="media-request-id">请求标识：{pending.request_id}</p>
          <div className="button-row">
            <Button disabled={busy} onClick={() => void reconcile()}>
              核对本次生成
            </Button>
            <Button disabled={busy} onClick={() => void submit(pending, true)}>
              使用原请求重试
            </Button>
            <Button disabled={busy} onClick={() => void reconcile(true)}>
              结束核对并解锁编辑
            </Button>
          </div>
        </section>
      )}
      {message && (
        <p className={failure ? 'form-error' : 'page-note'} role={failure ? 'alert' : 'status'}>
          {message}
        </p>
      )}
      <section
        role="tabpanel"
        id={`${tabId}-panel`}
        aria-labelledby={`${tabId}-${capability}-tab`}
        className="media-generation-panel"
      >
        {connections.error && (
          <p className="form-error" role="alert">
            {connections.error.message}{' '}
            <Button onClick={() => void connections.refetch()}>重新读取连接</Button>
          </p>
        )}
        {connections.isPending ? (
          <p className="page-note" role="status">
            正在读取生成模型…
          </p>
        ) : !available.length ? (
          !connections.error && (
            <EmptyState
              icon={Sparkles}
              title="没有模型接入"
              action={
                <Button variant="primary" onClick={() => setShowConnections(true)}>
                  接入生成模型
                </Button>
              }
            >
              当前没有已启用的{kind.noun}生成连接，可接入模型或在设置中启用已有连接。
            </EmptyState>
          )
        ) : (
          <form
            className="stacked-form media-generation-form"
            onSubmit={(event) => {
              event.preventDefault()
              create()
            }}
          >
            <label>
              生成模型
              <select
                value={selected?.id ?? draft.connectionId}
                disabled={locked}
                onChange={(event) =>
                  updateDraft({ connectionId: event.target.value, consentTarget: '' })
                }
              >
                {invalidConnection && (
                  <option value={draft.connectionId}>原连接已不可用，请重新选择</option>
                )}
                {available.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.name}
                    {connection.is_default ? ' · 默认' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label>
              生成描述
              <textarea
                required
                maxLength={32768}
                rows={5}
                value={draft.prompt}
                disabled={locked}
                placeholder={kind.placeholder}
                onChange={(event) => updateDraft({ prompt: event.target.value })}
              />
            </label>
            {capability !== 'audio' && (
              <MediaReferences
                workspaceId={workspace.id}
                references={draft.references}
                locked={locked}
                onChange={(references) => updateDraft({ references, consentTarget: '' })}
              />
            )}
            {remoteReferences && (
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={consent}
                  disabled={locked}
                  onChange={(event) =>
                    updateDraft({ consentTarget: event.target.checked ? consentTarget : '' })
                  }
                />
                <span>允许本次将已选参考图像发送给此生成连接</span>
              </label>
            )}
            <details className="settings-editor">
              <summary>生成参数（可选）</summary>
              <div className="settings-editor-content media-generation-options">
                {capability !== 'audio' && (
                  <>
                    <label>
                      宽度
                      <input
                        type="number"
                        required
                        min={64}
                        max={4096}
                        step={selected?.driver === 'flux3_native' ? 16 : 1}
                        value={draft.width}
                        disabled={locked}
                        onChange={(event) => updateDraft({ width: event.target.value })}
                      />
                    </label>
                    <label>
                      高度
                      <input
                        type="number"
                        required
                        min={64}
                        max={4096}
                        step={selected?.driver === 'flux3_native' ? 16 : 1}
                        value={draft.height}
                        disabled={locked}
                        onChange={(event) => updateDraft({ height: event.target.value })}
                      />
                    </label>
                    <label>
                      Seed（留空由服务决定）
                      <input
                        type="number"
                        min={0}
                        max={4294967295}
                        value={draft.seed}
                        disabled={locked}
                        onChange={(event) => updateDraft({ seed: event.target.value })}
                      />
                    </label>
                    <label>
                      采样步数（留空使用服务默认）
                      <input
                        type="number"
                        min={1}
                        max={256}
                        value={draft.steps}
                        disabled={locked}
                        onChange={(event) => updateDraft({ steps: event.target.value })}
                      />
                    </label>
                  </>
                )}
                {(capability === 'video' || capability === 'audio') && (
                  <label>
                    时长（秒{capability === 'audio' ? '，可选' : ''}）
                    <input
                      type="number"
                      min={0.01}
                      max={3600}
                      step="any"
                      value={draft.duration}
                      disabled={locked}
                      onChange={(event) => updateDraft({ duration: event.target.value })}
                    />
                  </label>
                )}
                {capability === 'audio' && (
                  <label>
                    音色标识（可选）
                    <input
                      maxLength={100}
                      value={draft.voice}
                      disabled={locked}
                      onChange={(event) => updateDraft({ voice: event.target.value })}
                    />
                  </label>
                )}
                {capability === '3d' && (
                  <label>
                    输出格式
                    <select
                      value={draft.format}
                      disabled={locked}
                      onChange={(event) => updateDraft({ format: event.target.value })}
                    >
                      {['glb', 'gltf', 'obj', 'ply'].map((format) => (
                        <option key={format} value={format}>
                          {format.toUpperCase()}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            </details>
            <Button
              type="submit"
              variant="primary"
              disabled={
                locked || !selected || !draft.prompt.trim() || (remoteReferences && !consent)
              }
            >
              <Sparkles size={15} />
              {busy ? '正在提交…' : `生成${kind.noun}`}
            </Button>
          </form>
        )}
      </section>
      <MediaTaskHistory
        workspaceId={workspace.id}
        userId={userId}
        capability={capability}
        active={active}
      />
    </>
  )
}
