import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { File as FileIcon, LoaderCircle, X } from 'lucide-react'
import { filesApi } from '../../api/files'
import type { AttachmentInput, Message, PreparedAttachment } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import type { AttachmentUpload } from './useAttachmentUploads'

export type DraftAttachment = {
  artifact_id: string
  filename?: string
  consent_remote: boolean
  consent_model?: string
  consent_version?: string
}
export const modeLabel: Record<AttachmentInput['mode'], string> = {
  file: '原文件',
  text: '文本',
  extracted_text: '文件内容',
  native_image: '图像',
  native_document: '文档',
  native_audio: '音频',
}
export function inputMode(value: string): value is AttachmentInput['mode'] {
  return Object.hasOwn(modeLabel, value)
}

type AttachmentListProps = {
  modelId: string
  items: DraftAttachment[]
  preparations: PreparedAttachment[]
  error: unknown
  locked: boolean
  onChange: (
    items: DraftAttachment[] | ((previous: DraftAttachment[]) => DraftAttachment[]),
  ) => void
  onRetry: () => void
}
export function AttachmentList({
  modelId,
  items,
  preparations,
  error,
  locked,
  onChange,
  onRetry,
}: AttachmentListProps) {
  const remote = preparations.some((item) => item.remote)
  const allReady = items.every((item) =>
    preparations.some((p) => p.artifact_id === item.artifact_id && p.attachment),
  )
  const consent =
    allReady &&
    items.every((item) => {
      const prepared = preparations.find((p) => p.artifact_id === item.artifact_id)
      return (
        !prepared?.remote ||
        (item.consent_remote &&
          item.consent_model === modelId &&
          item.consent_version === prepared.attachment?.version)
      )
    })
  return (
    <div className="attachment-list">
      {items.map((item) => {
        const prepared = preparations.find((p) => p.artifact_id === item.artifact_id)
        const filename = prepared?.filename ?? item.filename ?? '附件'
        const preparing = !prepared || prepared.status === 'preparing'
        return (
          <div className="attachment-chip" key={item.artifact_id}>
            {preparing ? (
              <LoaderCircle size={17} className="attachment-spinner" />
            ) : (
              <FileIcon size={17} />
            )}
            <div className="attachment-chip-content">
              <strong>{filename}</strong>
              <span>{prepared?.note ?? (modelId ? '正在准备附件…' : '选择模型后自动准备')}</span>
            </div>
            <button
              type="button"
              className="icon-button"
              aria-label={`移除附件 ${filename}`}
              disabled={locked}
              onClick={() =>
                onChange((previous) =>
                  previous.filter((other) => other.artifact_id !== item.artifact_id),
                )
              }
            >
              <X size={15} />
            </button>
          </div>
        )
      })}
      {error instanceof Error && (
        <div className="form-error" role="alert">
          {error.message}{' '}
          <Button disabled={locked} onClick={onRetry}>
            重试准备
          </Button>
        </div>
      )}
      {remote && items.length > 0 && (
        <label className="checkbox-label attachment-consent">
          <input
            type="checkbox"
            disabled={locked || !allReady}
            checked={consent}
            onChange={(event) =>
              onChange((previous: DraftAttachment[]) =>
                previous.map((item) => ({
                  ...item,
                  consent_remote: event.target.checked,
                  consent_model: modelId,
                  consent_version: preparations.find((p) => p.artifact_id === item.artifact_id)
                    ?.attachment?.version,
                })),
              )
            }
          />
          <span>允许将这些附件发送给当前远程模型</span>
        </label>
      )}
    </div>
  )
}

export function AttachmentUploads({
  items,
  onRetry,
  onRemove,
}: {
  items: AttachmentUpload[]
  onRetry: (id: string) => void
  onRemove: (id: string) => void
}) {
  return (
    <div className="attachment-list">
      {items.map((item) => (
        <div className="attachment-chip" key={item.id}>
          {item.status !== 'failed' && <LoaderCircle size={17} className="attachment-spinner" />}
          <div
            className="attachment-chip-content"
            role={item.status === 'failed' ? 'alert' : 'status'}
          >
            <strong>{item.filename}</strong>
            <span>
              {item.status === 'failed'
                ? item.error
                : item.status === 'queued'
                  ? '等待上传…'
                  : `上传中 ${formatBytes(item.received)} / ${formatBytes(item.total)}`}
            </span>
            {item.status === 'uploading' && (
              <progress
                value={item.received}
                max={Math.max(1, item.total)}
                aria-label={`${item.filename} 上传进度`}
              />
            )}
          </div>
          {item.status === 'failed' && <Button onClick={() => onRetry(item.id)}>重试</Button>}
          <button
            type="button"
            className="icon-button"
            aria-label={`移除上传 ${item.filename}`}
            onClick={() => onRemove(item.id)}
          >
            <X size={15} />
          </button>
        </div>
      ))}
    </div>
  )
}

export function AttachmentPicker({
  workspaceId,
  onFiles,
  onClose,
  maxItems = 8,
  ...list
}: AttachmentListProps & {
  workspaceId: string
  maxItems?: number
  onFiles: (files: File[]) => void
  onClose: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [search, setSearch] = useState('')
  const artifacts = useQuery({
    queryKey: ['artifacts', workspaceId],
    queryFn: () => filesApi.list(workspaceId),
  })
  const visible = (artifacts.data ?? []).filter(
    (artifact) =>
      !['task_state', 'ui_surface', 'command_log', 'screenshot', 'manifest'].includes(
        artifact.kind,
      ) &&
      (!artifact.parent_id || artifact.kind === 'export' || artifact.kind === 'asr_text') &&
      artifact.filename.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  )
  return (
    <Dialog title="添加附件" onClose={onClose}>
      <div className="stacked-form">
        <Button variant="primary" disabled={list.locked} onClick={() => input.current?.click()}>
          选择文件
        </Button>
        <input
          ref={input}
          type="file"
          multiple
          className="sr-only"
          aria-label="选择上传附件"
          onChange={(event) => {
            onFiles(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
        <p className="page-note">
          支持所有文件格式，单文件最多 1 GB。上传后自动添加并准备内容，原文件始终保留。
        </p>
        {list.items.length > 0 && <AttachmentList {...list} />}
        <label>
          从文件库添加
          <input
            type="search"
            placeholder="搜索已上传文件"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        {artifacts.isPending && <p role="status">正在读取文件库…</p>}
        {artifacts.error && (
          <p role="alert" className="form-error">
            {artifacts.error.message}
          </p>
        )}
        <div className="attachment-library">
          {visible.slice(0, 30).map((artifact) => {
            const added = list.items.some((item) => item.artifact_id === artifact.id)
            return (
              <button
                type="button"
                key={artifact.id}
                disabled={list.locked || added || list.items.length >= maxItems}
                onClick={() =>
                  list.onChange((previous) =>
                    previous.length >= maxItems ||
                    previous.some((item) => item.artifact_id === artifact.id)
                      ? previous
                      : [
                          ...previous,
                          {
                            artifact_id: artifact.id,
                            filename: artifact.filename,
                            consent_remote: false,
                          },
                        ],
                  )
                }
              >
                <FileIcon size={17} />
                <span>
                  {artifact.filename}
                  <small>{formatBytes(artifact.byte_size)}</small>
                </span>
                <small>{added ? '已添加' : '添加'}</small>
              </button>
            )
          })}
          {!artifacts.isPending && !visible.length && (
            <p className="page-note">暂无匹配文件，可直接选择文件上传。</p>
          )}
        </div>
        <Button onClick={onClose}>关闭</Button>
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
          href={filesApi.downloadUrl(workspaceId, item.source_artifact_id ?? item.artifact_id)}
          download
        >
          {item.filename} · {modeLabel[item.mode]} ↓
        </a>
      ))}
    </div>
  )
}
