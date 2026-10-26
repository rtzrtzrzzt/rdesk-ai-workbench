import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock3, Download, Image, Square } from 'lucide-react'
import { filesApi } from '../../api/files'
import { mediaApi, type MediaTaskView } from '../../api/media'
import type { ArtifactView } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import { capabilities, describeError, record, taskPhase, taskStatus, taskTerminal } from './shared'
import { MediaReconcileForm } from './MediaReconcileForm'
import { updateTaskInHistory } from './history'
import './media.css'

type ImageSize = { width: number; height: number }
type WaitMotion = 'generating' | 'waiting' | 'none'
const waitingStatuses = new Set(['queued', 'running', 'waiting_resource', 'awaiting_approval'])

function requestedImageSize(task: MediaTaskView): ImageSize | null {
  const size = record(record(task.metadata)?.requested_image)
  const width = size?.width
  const height = size?.height
  return typeof width === 'number' &&
    Number.isFinite(width) &&
    width > 0 &&
    typeof height === 'number' &&
    Number.isFinite(height) &&
    height > 0
    ? { width, height }
    : null
}

function ImagePlaceholder({
  size,
  motion,
  title,
  description,
}: {
  size: ImageSize | null
  motion: WaitMotion
  title: string
  description: string
}) {
  const ratio = size ? size.width / size.height : 1
  // Bound the visual size while preserving the requested ratio, including portraits.
  const style: CSSProperties = { aspectRatio: ratio, width: `${Math.min(480, 380 * ratio)}px` }
  return (
    <div className={`media-image-placeholder is-${motion}`}>
      <div className="media-image-wait-visual" style={style} aria-hidden="true">
        <span className="media-image-wait-glow" />
        <span className="media-image-wait-sweep" />
        <span className="media-image-wait-icon">
          {motion === 'waiting' ? <Clock3 size={30} /> : <Image size={30} />}
        </span>
      </div>
      <div className="media-image-wait-caption">
        <span className="media-image-wait-label">
          {motion === 'none' ? '图像占位' : '等待动画'}
          {size && (
            <span>
              {size.width} × {size.height}
            </span>
          )}
        </span>
        <strong>{title}</strong>
        <span>{description}</span>
      </div>
    </div>
  )
}

function useWaitTime(createdAt: string, enabled: boolean) {
  const [currentTime, setCurrentTime] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    setCurrentTime(Date.now())
    const interval = window.setInterval(() => setCurrentTime(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [enabled])
  const started = Date.parse(createdAt)
  if (!Number.isFinite(started)) return null
  const elapsed = Math.max(0, Math.floor((currentTime - started) / 1000))
  const seconds = elapsed % 60
  const minutes = Math.floor(elapsed / 60) % 60
  const hours = Math.floor(elapsed / 3600)
  return hours ? `${hours} 小时 ${minutes} 分 ${seconds} 秒` : `${minutes} 分 ${seconds} 秒`
}

function MediaArtifact({
  workspaceId,
  artifact,
  active,
  imageSize,
  previewMotion,
}: {
  workspaceId: string
  artifact: ArtifactView
  active: boolean
  imageSize: ImageSize | null
  previewMotion: boolean
}) {
  const [loadedPreview, setLoadedPreview] = useState('')
  const [failedPreview, setFailedPreview] = useState('')
  const metadata = record(artifact.metadata)
  const previewTaskId =
    typeof metadata?.preview_task_id === 'string' ? metadata.preview_task_id : ''
  const previewId =
    typeof metadata?.preview_artifact_id === 'string' ? metadata.preview_artifact_id : ''
  const image = artifact.mime_type.startsWith('image/')
  const playableVideo = ['video/mp4', 'video/webm'].includes(artifact.mime_type)
  const playableAudio = ['audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/flac'].includes(
    artifact.mime_type,
  )
  const previewTask = useQuery({
    queryKey: ['file-task', workspaceId, previewTaskId],
    queryFn: () => filesApi.task(workspaceId, previewTaskId),
    enabled: active && image && !!previewTaskId && !previewId,
    retry: false,
    refetchInterval: (query) =>
      !active || taskTerminal.has(query.state.data?.status ?? '') || query.state.error
        ? false
        : 2000,
  })
  const safePreview =
    previewId ||
    previewTask.data?.results.find(
      (item) => item.kind === 'preview' && item.mime_type === 'image/png',
    )?.id ||
    (artifact.kind === 'preview' && artifact.mime_type === 'image/png' ? artifact.id : '')
  const previewLoaded = !!safePreview && loadedPreview === safePreview
  const previewFailed = !!safePreview && failedPreview === safePreview
  const previewUnavailable =
    previewFailed ||
    previewTask.isError ||
    (!!previewTask.data &&
      (taskTerminal.has(previewTask.data.status) ||
        ['paused', 'outcome_unknown'].includes(previewTask.data.status)) &&
      !safePreview)
  const previewPending = !previewUnavailable && (!!safePreview || !!previewTaskId)
  return (
    <figure className="media-artifact">
      {image && (
        <div className="media-image-preview">
          {active && safePreview && !previewFailed && (
            <img
              className={previewLoaded ? undefined : 'media-preview-loading'}
              loading="lazy"
              src={filesApi.previewImageUrl(workspaceId, safePreview)}
              alt={`${artifact.filename} 的安全预览`}
              onLoad={() => setLoadedPreview(safePreview)}
              onError={() => setFailedPreview(safePreview)}
            />
          )}
          {(!active || !previewLoaded || previewFailed) && (
            <ImagePlaceholder
              size={imageSize}
              motion={active && previewMotion && previewPending ? 'waiting' : 'none'}
              title={
                previewUnavailable
                  ? '安全预览未完成'
                  : previewPending
                    ? '正在准备安全预览'
                    : '尚未提供安全预览'
              }
              description="原文件已保存，可直接下载。"
            />
          )}
        </div>
      )}
      {previewTask.isError && !!previewTaskId && !safePreview && (
        <Button onClick={() => void previewTask.refetch()}>重新读取预览状态</Button>
      )}
      {active && playableVideo && (
        <video controls preload="metadata" src={filesApi.previewImageUrl(workspaceId, artifact.id)}>
          当前浏览器无法播放此视频，请下载文件。
        </video>
      )}
      {active && playableAudio && (
        <audio controls preload="metadata" src={filesApi.previewImageUrl(workspaceId, artifact.id)}>
          当前浏览器无法播放此音频，请下载文件。
        </audio>
      )}
      {/^(audio|video)\//.test(artifact.mime_type) && !playableVideo && !playableAudio && (
        <p className="page-note">此音视频格式可下载后查看。</p>
      )}
      <figcaption>
        <div>
          <strong>{artifact.filename}</strong>
          <span>
            {artifact.mime_type} · {formatBytes(artifact.byte_size)}
          </span>
        </div>
        <a
          className="button button-secondary"
          href={filesApi.downloadUrl(workspaceId, artifact.id)}
          download
        >
          <Download size={14} />
          下载
        </a>
      </figcaption>
      <details className="media-result-details">
        <summary>文件与来源元数据</summary>
        <pre>{JSON.stringify(artifact.metadata, null, 2)}</pre>
      </details>
    </figure>
  )
}

export function MediaTaskCard({
  workspaceId,
  task,
  active,
}: {
  workspaceId: string
  task: MediaTaskView
  active: boolean
}) {
  const client = useQueryClient()
  const [cancelRequested, setCancelling] = useState(false)
  const cancelling = cancelRequested && (!taskTerminal.has(task.status) || !task.resources_closed)
  const [error, setError] = useState('')
  const requestId = useRef<string | null>(null)
  const isWaiting = waitingStatuses.has(task.status) && !task.deletion_requested && !cancelling
  const waitTime = useWaitTime(task.created_at, active && isWaiting)
  const imageSize = task.capability === 'image' ? requestedImageSize(task) : null
  const phases = record(record(task.metadata)?.phases)
  const sampling = ['sample', 'sampling'].includes(task.phase)
  const sample = record(phases?.sample ?? phases?.sampling)
  const step = sample?.step
  const total = sample?.total
  const sampleSteps =
    sampling &&
    typeof step === 'number' &&
    Number.isInteger(step) &&
    step >= 0 &&
    typeof total === 'number' &&
    Number.isInteger(total) &&
    total > 0 &&
    step <= total
      ? `${step} / ${total} 步`
      : null
  const progress =
    task.status === 'running' &&
    isWaiting &&
    (!phases || sampling) &&
    typeof task.progress === 'number' &&
    task.progress >= 0 &&
    task.progress <= 1
      ? task.progress
      : null
  const phase =
    task.status === 'running' && isWaiting
      ? taskPhase(task.phase)
      : taskStatus(task.deletion_requested || cancelling ? 'cancelling' : task.status)
  const placeholderTitle = task.status === 'running' && isWaiting ? '图像生成中' : phase
  async function cancel() {
    if (cancelling) return
    setCancelling(true)
    setError('')
    requestId.current ??= crypto.randomUUID()
    try {
      const updated = await mediaApi.cancel(workspaceId, task.run_id, requestId.current)
      updateTaskInHistory(client, workspaceId, updated)
    } catch (error) {
      setError(`${describeError(error)} 停止结果尚未确认，可使用同一请求再次核对。`)
      setCancelling(false)
    } finally {
      setCancelling(false)
    }
  }
  const failure = record(task.error)
  return (
    <article className="media-task-card">
      <div className="media-task-heading">
        <div>
          <h3>{task.connection_name}</h3>
          <p>
            {capabilities.find((item) => item.id === task.capability)?.noun ?? task.capability} ·{' '}
            {new Date(task.created_at).toLocaleString()}
          </p>
        </div>
        <div className="button-row">
          <Status
            tone={
              task.status === 'succeeded' ? 'good' : task.status === 'failed' ? 'danger' : 'neutral'
            }
          >
            {taskStatus(task.status)}
          </Status>
          {(!taskTerminal.has(task.status) || !task.resources_closed) && (
            <Button
              disabled={cancelling || task.status === 'cancelling' || task.deletion_requested}
              onClick={() => void cancel()}
            >
              <Square size={12} />
              {cancelling || task.status === 'cancelling' || task.deletion_requested
                ? '正在确认停止…'
                : '停止生成'}
            </Button>
          )}
        </div>
      </div>
      {task.deletion_requested && (
        <p className="page-note media-deletion-status" role="status">
          正在停止并删除记录；待任务结束且资源确认关闭后移除，文件库产物保留。
        </p>
      )}
      <div className="media-task-progress">
        <p className="media-task-phase" role="status" aria-atomic="true">
          {phase}
        </p>
        <div className="media-task-metrics" aria-live="off">
          {task.status === 'running' && isWaiting && sampleSteps && <span>采样 {sampleSteps}</span>}
          {progress !== null && (
            <span>
              {sampling ? '采样' : '本阶段'} {Math.round(progress * 100)}%
            </span>
          )}
          {isWaiting && waitTime && <span>已等待 {waitTime}</span>}
        </div>
      </div>
      {progress !== null && (
        <progress
          max={1}
          value={progress}
          aria-label={sampling ? '采样阶段进度' : '当前阶段进度'}
        />
      )}
      {task.capability === 'image' && !task.artifacts.length && (
        <ImagePlaceholder
          size={imageSize}
          motion={
            active && isWaiting ? (task.status === 'running' ? 'generating' : 'waiting') : 'none'
          }
          title={placeholderTitle}
          description={
            task.status === 'succeeded'
              ? '生成已完成，正在等待可下载产物。'
              : isWaiting
                ? '完成后在此显示安全预览。'
                : task.status === 'outcome_unknown'
                  ? '等待核对生成结果。'
                  : '可在上方查看任务状态。'
          }
        />
      )}
      {!task.resources_closed && taskTerminal.has(task.status) && (
        <p className="page-note">生成状态已结束，正在确认资源回收。</p>
      )}
      {typeof failure?.message === 'string' && (
        <p className="form-error" role="status">
          {failure.message}
        </p>
      )}
      {task.status === 'outcome_unknown' && (
        <p className="page-note">提供方结果待核对，当前任务不会自动重新提交生成。</p>
      )}
      {(task.status === 'outcome_unknown' ||
        task.status === 'paused' ||
        (task.deletion_requested &&
          !task.resources_closed &&
          task.dispatch_state === 'submitting')) && (
        <MediaReconcileForm workspaceId={workspaceId} task={task} />
      )}
      {!!task.artifacts.length && (
        <div className="media-artifacts">
          {task.artifacts.map((artifact) => (
            <MediaArtifact
              key={artifact.id}
              workspaceId={workspaceId}
              artifact={artifact}
              active={active}
              imageSize={imageSize}
              previewMotion={task.status === 'succeeded' && !task.deletion_requested}
            />
          ))}
        </div>
      )}
      {task.capability === '3d' &&
        task.artifacts.length > 0 &&
        !task.artifacts.some((artifact) => /^(image|video)\//.test(artifact.mime_type)) && (
          <p className="page-note">
            提供方未提供可显示的预览，可下载真实 3D 文件并查看来源元数据。
          </p>
        )}
      {task.status === 'succeeded' && !task.artifacts.length && (
        <p className="page-note">服务端尚未返回可下载产物。</p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </article>
  )
}
