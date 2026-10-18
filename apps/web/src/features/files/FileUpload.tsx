import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FileUp, Pause, Play, RefreshCw, X } from 'lucide-react'
import { filesApi } from '../../api/files'
import type { ArtifactView } from '../../api/types'
import { Button } from '../../components/ui'
import { Dialog } from '../../components/Dialog'
import { uploadFile, UploadPaused } from './upload'
import type { PendingUpload } from './upload'

export type { PendingUpload } from './upload'
const storageKey = (workspaceId: string) => `workbench.upload.${workspaceId}`
function readPending(workspaceId: string): PendingUpload | null {
  try {
    const value = JSON.parse(
      localStorage.getItem(storageKey(workspaceId)) ?? 'null',
    ) as PendingUpload | null
    return value &&
      typeof value.requestId === 'string' &&
      typeof value.filename === 'string' &&
      typeof value.totalBytes === 'number'
      ? value
      : null
  } catch {
    return null
  }
}
export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}
export function FileUpload({
  workspaceId,
  active,
  onComplete,
}: {
  workspaceId: string
  active: boolean
  onComplete: (artifact: ArtifactView) => Promise<void>
}) {
  const client = useQueryClient()
  const [record, setRecord] = useState<PendingUpload | null>(() => readPending(workspaceId))
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [abandon, setAbandon] = useState(false)
  const pause = useRef(false)
  const [pausing, setPausing] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const status = useQuery({
    queryKey: ['upload-status', workspaceId, record?.id],
    queryFn: () => filesApi.uploadStatus(workspaceId, record!.id!),
    enabled: active && !!record?.id,
    refetchInterval: (query) =>
      active && !busy && record?.id && !message && !query.state.error ? 4000 : false,
  })
  function persist(next: PendingUpload | null) {
    setRecord(next)
    if (next) localStorage.setItem(storageKey(workspaceId), JSON.stringify(next))
    else localStorage.removeItem(storageKey(workspaceId))
  }
  function choose(chosen: File) {
    setError('')
    setMessage('')
    if (record && (chosen.name !== record.filename || chosen.size !== record.totalBytes)) {
      setError('恢复上传需要重新选择原文件，文件名与大小必须一致。')
      return
    }
    setFile(chosen)
  }
  async function upload() {
    if (!file || busy) return
    setBusy(true)
    setPausing(false)
    pause.current = false
    setError('')
    setMessage('')
    let pending = record ?? {
      filename: file.name,
      totalBytes: file.size,
      mimeType: file.type || null,
      requestId: crypto.randomUUID(),
    }
    persist(pending)
    try {
      const artifact = await uploadFile(workspaceId, file, {
        record: pending,
        onRecord: (next) => {
          pending = next
          persist(next)
        },
        onProgress: (current) =>
          client.setQueryData(['upload-status', workspaceId, current.id], current),
        shouldPause: () => pause.current,
      })
      await onComplete(artifact)
      persist(null)
      setFile(null)
      setMessage('原件已保存，上传完成不等于已解析。')
    } catch (err) {
      if (err instanceof UploadPaused) {
        setMessage('传输已暂停，可从已确认的位置继续。')
        return
      }
      setError(
        `${err instanceof Error ? err.message : '上传未完成。'} 上传记录已保留，再次继续前会读取服务端确认的位置。`,
      )
      if (pending.id)
        void filesApi
          .uploadStatus(workspaceId, pending.id)
          .then((value) => client.setQueryData(['upload-status', workspaceId, value.id], value))
          .catch(() => undefined)
    } finally {
      setBusy(false)
      setPausing(false)
    }
  }
  const actual = status.data
  const received = actual?.received_bytes
  const total = actual?.total_bytes ?? record?.totalBytes ?? file?.size
  return (
    <section className="file-upload-card" aria-label="原件上传">
      <div className="file-upload-heading">
        <div>
          <h2>
            <FileUp size={16} />
            上传原件
          </h2>
          <p>文件先保存为原件，提取与预览单独运行。</p>
        </div>
        <Button disabled={busy} onClick={() => input.current?.click()}>
          {record ? '重新选择原文件' : '选择文件'}
        </Button>
      </div>
      <input
        ref={input}
        type="file"
        className="sr-only"
        aria-label="选择要上传的原文件"
        disabled={busy}
        onChange={(event) => {
          const chosen = event.target.files?.[0]
          if (chosen) choose(chosen)
          event.target.value = ''
        }}
      />
      {(record || file) && (
        <div className="file-upload-progress">
          <div>
            <strong>{record?.filename ?? file?.name}</strong>
            <span>
              {received !== undefined
                ? `${formatBytes(received)} / ${formatBytes(total ?? 0)} 已由服务端确认`
                : total !== undefined
                  ? `${formatBytes(total)} · 尚未确认上传位置`
                  : '尚未确认上传位置'}
            </span>
          </div>
          {received !== undefined && total !== undefined && (
            <progress value={received} max={Math.max(total, 1)} aria-label="原件已确认上传进度" />
          )}
          {!file && record && (
            <p>刷新后请重新选择原文件，以继续未完成的上传。上传记录仅保存标识和文件元信息。</p>
          )}
          <div className="button-row">
            {busy ? (
              <Button
                onClick={() => {
                  pause.current = true
                  setPausing(true)
                }}
                disabled={pausing}
              >
                <Pause size={14} />
                {pausing ? '等待当前分块确认…' : '暂停上传'}
              </Button>
            ) : (
              <>
                <Button
                  disabled={!file || actual?.status === 'completed'}
                  variant="primary"
                  onClick={() => void upload()}
                >
                  <Play size={14} />
                  {record ? '继续上传' : '上传原件'}
                </Button>
                {record?.id && (
                  <Button
                    disabled={status.isFetching}
                    onClick={() => {
                      setMessage('')
                      void status.refetch()
                    }}
                  >
                    <RefreshCw size={14} />
                    核对状态
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={() => {
                    setFile(null)
                    if (!record) setMessage('')
                    else setMessage('上传记录仍保留。选择原文件后可以继续。')
                  }}
                >
                  <X size={14} />
                  收起本地文件
                </Button>
                {record && (
                  <Button variant="ghost" onClick={() => setAbandon(true)}>
                    放弃此上传记录
                  </Button>
                )}
              </>
            )}
          </div>
        </div>
      )}
      {status.isError && (
        <p className="form-error" role="alert">
          {status.error.message}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="file-upload-message" role="status">
          {message}
        </p>
      )}
      {actual?.artifact_id && actual.status === 'completed' && record && (
        <Button
          onClick={() =>
            void filesApi
              .artifact(workspaceId, actual.artifact_id!)
              .then(async (artifact) => {
                await onComplete(artifact)
                persist(null)
                setFile(null)
              })
              .catch((err) => setError(err instanceof Error ? err.message : '无法读取原件'))
          }
        >
          查看已保存原件
        </Button>
      )}
      {abandon && (
        <Dialog title="放弃此上传恢复记录？" onClose={() => setAbandon(false)}>
          <p className="memory-confirm-description">
            这只清除此浏览器的恢复入口。未完成的临时块由服务端到期清理；不会删除已保存的原件。
          </p>
          <div className="button-row">
            <Button onClick={() => setAbandon(false)}>保留记录</Button>
            <Button
              variant="danger"
              onClick={() => {
                persist(null)
                setFile(null)
                setError('')
                setMessage('本地恢复记录已清除。')
                setAbandon(false)
              }}
            >
              清除本地记录
            </Button>
          </div>
        </Dialog>
      )}
    </section>
  )
}
