import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { filesApi } from '../../api/files'
import { ApiError } from '../../api/client'
import type { ArtifactInputPlan, AttachmentInput, Message } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { FileUpload } from '../files/FileUpload'
import { matchingProcessors, pendingKey, readPending } from '../files/shared'
import type { PendingProcess } from '../files/shared'
export type DraftAttachment = {
  artifact_id: string
  mode?: AttachmentInput['mode']
  consent_remote: boolean
  consent_model?: string
  consent_version?: string
}
export const modeLabel: Record<AttachmentInput['mode'], string> = {
  text: '文本片段',
  extracted_text: '已提取文字',
  native_image: '安全图像预览',
  native_document: '原生 PDF 文档',
  native_audio: '原生 MP3 / WAV 音频',
}
export function inputMode(value: string): value is AttachmentInput['mode'] {
  return Object.hasOwn(modeLabel, value)
}
export function AttachmentPicker({
  workspaceId,
  modelId,
  items,
  plans,
  error,
  loading,
  locked,
  onChange,
  onClose,
}: {
  workspaceId: string
  modelId: string
  items: DraftAttachment[]
  plans: ArtifactInputPlan[]
  error: unknown
  loading: boolean
  locked: boolean
  onChange: (items: DraftAttachment[]) => void
  onClose: () => void
}) {
  const client = useQueryClient()
  const [selected, setSelected] = useState('')
  const [processError, setProcessError] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<PendingProcess | null>(() => readPending(workspaceId))
  const artifacts = useQuery({
    queryKey: ['artifacts', workspaceId],
    queryFn: () => filesApi.list(workspaceId),
    refetchInterval: 3000,
  })
  const processors = useQuery({
    queryKey: ['file-processors', workspaceId],
    queryFn: () => filesApi.processors(workspaceId),
  })
  const tasks = useQuery({
    queryKey: ['file-tasks', workspaceId],
    queryFn: () => filesApi.tasks(workspaceId),
    refetchInterval: 2000,
  })
  const completedTasks =
    tasks.data
      ?.filter((task) => ['succeeded', 'failed', 'cancelled'].includes(task.status))
      .map(
        (task) => `${task.id}:${task.status}:${task.results.map((result) => result.id).join(',')}`,
      )
      .join('|') ?? ''
  useEffect(() => {
    if (completedTasks) void client.invalidateQueries({ queryKey: ['input-plan', workspaceId] })
  }, [completedTasks, client, workspaceId])
  const add = (id: string) => {
    if (!locked && items.length < 8 && !items.some((i) => i.artifact_id === id))
      onChange([...items, { artifact_id: id, consent_remote: false }])
  }
  function process(id: string, operation: 'extract' | 'preview') {
    const command = pending ?? {
      artifactId: id,
      filename: artifacts.data?.find((a) => a.id === id)?.filename ?? id,
      input: { operation, start: 1, limit: 50, request_id: crypto.randomUUID() },
    }
    return submitProcess(command)
  }
  async function submitProcess(command: PendingProcess) {
    if (busy || locked) return
    try {
      localStorage.setItem(pendingKey(workspaceId), JSON.stringify(command))
    } catch {
      setProcessError('无法保存待确认处理请求；本次尚未提交，请检查浏览器存储后重试。')
      return
    }
    setPending(command)
    setBusy(true)
    setProcessError('')
    try {
      await filesApi.process(workspaceId, command.artifactId, command.input)
      setPending(null)
      localStorage.removeItem(pendingKey(workspaceId))
      await tasks.refetch()
      await client.invalidateQueries({ queryKey: ['artifacts', workspaceId] })
    } catch (e) {
      const uncertain = e instanceof TypeError || (e instanceof ApiError && e.status >= 500)
      if (!uncertain) {
        setPending(null)
        localStorage.removeItem(pendingKey(workspaceId))
      }
      setProcessError(
        (e instanceof Error ? e.message : '处理未完成') +
          (uncertain ? ' 请求结果未确认，核对将复用同一请求。' : ''),
      )
    } finally {
      setBusy(false)
    }
  }
  const inspected = artifacts.data?.find((a) => a.id === selected)
  const available =
    inspected?.status === 'available'
      ? new Set(matchingProcessors(inspected, processors.data ?? []).flatMap((p) => p.operations))
      : new Set<string>()
  return (
    <Dialog title="本次消息的附件" onClose={onClose}>
      <div className="stacked-form">
        <p className="page-note">
          上传仅保存原件。输入路线由你明确选择；不会自动转换、换路线或向模型发送。最多 8 份附件、2
          张安全预览。
        </p>
        {!locked && (
          <FileUpload
            workspaceId={workspaceId}
            active
            onComplete={async (a) => {
              setSelected(a.id)
              await artifacts.refetch()
            }}
          />
        )}
        <label>
          文件库
          <select
            aria-label="文件库"
            value={selected}
            disabled={locked}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">选择真实原件或派生件</option>
            {artifacts.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.filename} · {a.parent_id ? '派生件' : '原件'}
              </option>
            ))}
          </select>
        </label>
        {artifacts.error && (
          <p role="alert" className="form-error">
            {artifacts.error.message}
          </p>
        )}
        {inspected && (
          <div className="stacked-form">
            <a href={filesApi.downloadUrl(workspaceId, inspected.id)}>下载 {inspected.filename}</a>
            <div className="button-row">
              <Button
                disabled={
                  locked || items.length >= 8 || items.some((i) => i.artifact_id === inspected.id)
                }
                onClick={() => add(inspected.id)}
              >
                添加到本次消息
              </Button>
              {(['preview', 'extract'] as const)
                .filter((op) => available.has(op))
                .map((op) => (
                  <Button
                    key={op}
                    disabled={locked || busy || !!pending}
                    onClick={() => void process(inspected.id, op)}
                  >
                    {op === 'preview' ? '生成安全预览' : '提取内容 / 元数据'}
                  </Button>
                ))}
            </div>
            <p className="page-note">
              预览可能缩小图像或仅含首帧；提取默认从起始位置读取最多 50
              个单位（页/块/行等），音频提取只核对媒体元数据。精确范围可在文件页设置。发送提取文字时请明确选择派生件；选择原生文档或音频路线时仍选原件。
            </p>
          </div>
        )}
        {pending && (
          <Button disabled={busy} onClick={() => void submitProcess(pending)}>
            核对本次处理请求（{pending.filename}）
          </Button>
        )}
        {processError && (
          <p role="alert" className="form-error">
            {processError}
          </p>
        )}
        {tasks.data?.some((t) => !['succeeded', 'failed', 'cancelled'].includes(t.status)) && (
          <p className="page-note">
            文件处理任务正在继续，派生件发布后会显示在文件库。可在文件页查看状态与取消。
          </p>
        )}
        {loading && <p role="status">正在按当前模型预检…</p>}
        {error instanceof Error && (
          <p role="alert" className="form-error">
            {error.message}
          </p>
        )}
        {items.map((item) => {
          const plan = plans.find((p) => p.artifact_id === item.artifact_id)
          return (
            <section className="attachment-plan" key={item.artifact_id}>
              <strong>
                {plan?.filename ??
                  artifacts.data?.find((a) => a.id === item.artifact_id)?.filename ??
                  item.artifact_id}
              </strong>
              <Button disabled={locked} onClick={() => onChange(items.filter((i) => i !== item))}>
                移除
              </Button>
              {plan && (
                <>
                  <p className="page-note">{plan.note}</p>
                  <details>
                    <summary>附件版本</summary>
                    <small>{plan.version}</small>
                  </details>
                  {plan.routes.map((route) => (
                    <label className="attachment-route" key={route.mode}>
                      <input
                        type="radio"
                        name={`route-${item.artifact_id}`}
                        disabled={locked || !route.available || !inputMode(route.mode)}
                        checked={item.mode === route.mode}
                        onChange={() => {
                          const mode = route.mode
                          if (inputMode(mode))
                            onChange(
                              items.map((i) =>
                                i === item
                                  ? {
                                      ...i,
                                      mode,
                                      consent_remote: false,
                                    }
                                  : i,
                              ),
                            )
                        }}
                      />
                      <span>
                        {inputMode(route.mode) ? modeLabel[route.mode] : route.mode}
                        {!route.available ? ' · 不可用' : ''}
                        <small>{route.explanation}</small>
                      </span>
                    </label>
                  ))}
                  {item.mode === 'native_document' && (
                    <p className="page-note">
                      原生 PDF 需先提取并核对真实页数，最多 10 页、8
                      MiB；模型支持、能力启用及协议路线均须成立。不会自动改为提取文字。
                    </p>
                  )}
                  {item.mode === 'native_audio' && (
                    <p className="page-note">
                      原生音频需先提取并核对媒体元数据，仅 MP3 / WAV、纯音频、120 秒内、4
                      MiB，且使用支持音频的 Chat 协议连接。转录文字是另一条路线，不会自动转换。
                    </p>
                  )}
                  {plan.remote && (
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        disabled={locked}
                        checked={
                          item.consent_remote &&
                          item.consent_model === modelId &&
                          item.consent_version === plan.version
                        }
                        onChange={(e) =>
                          onChange(
                            items.map((i) =>
                              i === item
                                ? {
                                    ...i,
                                    consent_remote: e.target.checked,
                                    consent_model: modelId,
                                    consent_version: plan.version,
                                  }
                                : i,
                            ),
                          )
                        }
                      />
                      <span>我同意本次向当前远程模型发送这份附件</span>
                    </label>
                  )}
                </>
              )}
            </section>
          )
        })}
        {!items.length && <p className="page-note">尚未添加附件。</p>}
        <Button onClick={onClose}>完成选择</Button>
      </div>
    </Dialog>
  )
}
export function MessageAttachments({
  message,
  workspaceId,
}: {
  message: Message
  workspaceId: string
}) {
  return (
    <div className="message-attachments">
      {message.attachments.map((item, index) => (
        <a
          key={`${item.artifact_id}:${index}`}
          href={filesApi.downloadUrl(workspaceId, item.artifact_id)}
          download
        >
          {item.filename} · {modeLabel[item.mode]} ↓
        </a>
      ))}
    </div>
  )
}
