import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { request } from '../../api/client'
import { filesApi } from '../../api/files'
import type { ArtifactView } from '../../api/types'
import { Button } from '../../components/ui'
import { Dialog } from '../../components/Dialog'

/** Only registered same-origin artifact URLs enter the file viewer. */
function managedLink(href: string, workspaceId?: string) {
  try {
    const url = new URL(href, window.location.origin)
    if (url.origin !== window.location.origin) return null
    const current = url.pathname.match(/^\/api\/v1\/workspaces\/([^/]+)\/file-links\/([^/]+)$/)
    if (current)
      return {
        path: `${url.pathname.slice('/api/v1'.length)}${url.search}`,
        workspaceId: decodeURIComponent(current[1]),
        artifactId: decodeURIComponent(current[2]),
        version: url.searchParams.get('version'),
      }
    const legacy = url.pathname.match(/^\/api\/v1\/artifacts\/([^/]+)\/content$/)
    if (legacy && workspaceId)
      return {
        path: null,
        workspaceId,
        artifactId: decodeURIComponent(legacy[1]),
        version: url.searchParams.get('version'),
      }
    return null
  } catch {
    return null
  }
}
export function ManagedFileLink({
  href,
  workspaceId,
  children,
  ...props
}: {
  href: string
  workspaceId?: string
  children?: ReactNode
  className?: string
}) {
  const [selection, setSelection] = useState<{
    href: string
    link: NonNullable<ReturnType<typeof managedLink>>
  } | null>(null)
  const link = managedLink(href, workspaceId)
  const query = useQuery({
    queryKey: [
      'managed-artifact',
      selection?.link.workspaceId,
      selection?.link.artifactId,
      selection?.link.version,
    ],
    queryFn: async () => {
      if (!selection) throw new Error('文件链接不属于受管产物。')
      const target = selection.link
      const artifact = target.path
        ? await request<ArtifactView>(target.path)
        : await filesApi.artifact(target.workspaceId, target.artifactId)
      if (target.version && artifact.version !== target.version)
        throw new Error('文件版本已变化，未打开替代版本。')
      return artifact
    },
    enabled: !!selection,
    staleTime: 30000,
    retry: false,
  })
  if (!link)
    return (
      <a
        {...props}
        href={href}
        target={/^(?:https?:)?\/\//i.test(href) ? '_blank' : undefined}
        rel="noopener noreferrer"
      >
        {children}
      </a>
    )
  return (
    <>
      <a
        {...props}
        href={href}
        onClick={(event) => {
          event.preventDefault()
          setSelection({ href, link })
        }}
      >
        {children}
      </a>
      {selection &&
        (query.data ? (
          <FilePreview
            workspaceId={selection.link.workspaceId}
            artifact={query.data}
            onClose={() => setSelection(null)}
          />
        ) : (
          <Dialog title="文件预览" onClose={() => setSelection(null)}>
            {query.isError ? (
              <>
                <p role="alert" className="form-error">
                  {query.error.message}
                </p>
                <Button onClick={() => void query.refetch()}>重新读取文件</Button>
              </>
            ) : (
              <p role="status">正在读取真实文件…</p>
            )}
          </Dialog>
        ))}
    </>
  )
}
export function FilePreview({
  workspaceId,
  artifact,
  onClose,
}: {
  workspaceId: string
  artifact: ArtifactView
  onClose: () => void
}) {
  const preview = useQuery({
    queryKey: ['artifact-preview', workspaceId, artifact.id, artifact.version],
    queryFn: () => filesApi.preview(workspaceId, artifact.id, artifact.version),
  })
  const version = `&version=${encodeURIComponent(artifact.version)}`
  const linkedMetadata = useQuery({
    queryKey: ['managed-artifact', workspaceId, artifact.id, artifact.version],
    queryFn: () =>
      request<ArtifactView>(
        `/workspaces/${encodeURIComponent(workspaceId)}/file-links/${encodeURIComponent(artifact.id)}?version=${encodeURIComponent(artifact.version)}`,
      ),
    staleTime: 30000,
  })
  const metadata = (linkedMetadata.data?.metadata ?? artifact.metadata) as Record<string, unknown>
  const taskId = typeof metadata.preview_task_id === 'string' ? metadata.preview_task_id : undefined
  const previewId =
    typeof metadata.preview_artifact_id === 'string' ? metadata.preview_artifact_id : undefined
  const task = useQuery({
    queryKey: ['file-task', workspaceId, taskId],
    queryFn: () => filesApi.task(workspaceId, taskId!),
    enabled: !!taskId,
    refetchInterval: (query) =>
      query.state.data && ['succeeded', 'failed', 'cancelled'].includes(query.state.data.status)
        ? false
        : 3000,
  })
  const linked = useQuery({
    queryKey: ['artifact', workspaceId, previewId],
    queryFn: () => filesApi.artifact(workspaceId, previewId!),
    enabled: !!previewId,
  })
  const isSafePng = (item: ArtifactView) =>
    item.mime_type === 'image/png' && (item.kind === 'preview' || /^frame_\d{4}$/.test(item.kind))
  const image = isSafePng(artifact)
    ? artifact
    : linked.data && isSafePng(linked.data)
      ? linked.data
      : task.data?.results.find(isSafePng)
  return (
    <Dialog title={artifact.filename} onClose={onClose}>
      <div className="surface-source-preview">
        <p>
          {artifact.mime_type} · {artifact.byte_size} 字节
        </p>
        <a href={`${filesApi.downloadUrl(workspaceId, artifact.id)}${version}`} download>
          下载文件
        </a>
        {!image &&
          taskId &&
          !['succeeded', 'failed', 'cancelled'].includes(task.data?.status ?? '') &&
          !task.isError && <p role="status">正在准备安全图片预览…</p>}
        {(task.isError ||
          task.data?.error ||
          linked.isError ||
          (!image &&
            task.data &&
            ['succeeded', 'failed', 'cancelled'].includes(task.data.status))) && (
          <p role="status" className="page-note">
            安全图片预览暂时不可用，仍可下载原件。
          </p>
        )}
        {linkedMetadata.isError && (
          <p role="status" className="page-note">
            图片预览关联暂时无法读取，仍可下载文件。
          </p>
        )}
        {image && (
          <img
            className="file-safe-image"
            src={`${filesApi.previewImageUrl(workspaceId, image.id)}&version=${encodeURIComponent(image.version)}`}
            alt={artifact.filename}
          />
        )}
        {preview.isError ? (
          <>
            <p role="alert" className="form-error">
              {preview.error.message}
            </p>
            <Button onClick={() => void preview.refetch()}>重新读取预览</Button>
          </>
        ) : preview.data ? (
          <>
            {preview.data.text !== null ? (
              <pre>{preview.data.text}</pre>
            ) : (
              <p>当前没有可读取文本，可查看元数据或下载文件。</p>
            )}
            {preview.data.truncated && (
              <p className="file-partial-warning">预览已截断，仅显示服务端返回范围。</p>
            )}
            <details>
              <summary>文件来源、版本与元数据</summary>
              <p>文件：{artifact.id}</p>
              <p>版本：{artifact.version}</p>
              <pre>
                {JSON.stringify(
                  { file: artifact.metadata, preview: preview.data.metadata },
                  null,
                  2,
                )}
              </pre>
            </details>
          </>
        ) : (
          <p role="status">正在读取受管预览…</p>
        )}
      </div>
    </Dialog>
  )
}
