import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { filesApi } from '../../api/files'
import { surfacesApi } from '../../api/surfaces'
import type { ArtifactView, Run, SurfaceView } from '../../api/types'
import { Button } from '../../components/ui'
import { FilePreview } from '../chat/FilePreview'
import { TrustedSurface } from './TrustedSurface'
import type { FileLinkRequest } from './types'
import './surfaces.css'
const terminal = new Set(['succeeded', 'failed', 'cancelled'])
function SurfaceSnapshot({
  view,
  workspaceId,
  runId,
  onFile,
}: {
  view: SurfaceView
  workspaceId: string
  runId: string
  onFile: (artifact: ArtifactView) => void
}) {
  async function open(request: FileLinkRequest) {
    if (request.surface_artifact_id && request.surface_artifact_id !== view.artifact_id)
      throw new Error('界面产物追溯标识不同，文件入口已拒绝。')
    if (request.run_id && request.run_id !== runId)
      throw new Error('来源 Run 与当前产物不同，文件入口已拒绝。')
    const artifact = await filesApi.artifact(workspaceId, request.artifact_id)
    if (request.version && artifact.version !== request.version)
      throw new Error('来源文件版本已变化；未打开替代版本，请重新核对产物来源。')
    onFile(artifact)
  }
  return (
    <article className="run-surface-snapshot">
      <TrustedSurface surface={view.document} onFileLink={open} />
      <details className="run-surface-meta">
        <summary>来源与下载</summary>
        <span>界面快照 · {view.artifact_id}</span>
        <a href={filesApi.downloadUrl(workspaceId, view.artifact_id)} download>
          下载 JSON 原件
        </a>
        <small>版本：{view.version}</small>
      </details>
    </article>
  )
}
export function RunSurfaces({
  run,
  workspaceId,
  active,
}: {
  run: Run
  workspaceId: string
  active: boolean
}) {
  const [file, setFile] = useState<ArtifactView | null>(null)
  const query = useQuery({
    queryKey: ['run-surfaces', run.id],
    queryFn: () => surfacesApi.list(run.id),
    enabled: active,
    refetchInterval: active && !terminal.has(run.status) ? 4000 : false,
  })
  // A child may become terminal between polls; always read its final published snapshot.
  useEffect(() => {
    if (active) void query.refetch()
  }, [active, run.id, run.status, run.last_event_seq, query.refetch])
  if (!query.isError && !query.data?.surfaces.length && !file) return null
  return (
    <section className="run-surfaces">
      <div className="run-line">
        <h3>任务成果</h3>
        <Button disabled={!active || query.isFetching} onClick={() => void query.refetch()}>
          刷新
        </Button>
      </div>
      {query.isError ? (
        <div role="alert">
          <p className="form-error">{query.error.message}</p>
          <Button onClick={() => void query.refetch()}>重新读取界面产物</Button>
        </div>
      ) : query.data?.surfaces.length ? (
        query.data.surfaces.map((view) => (
          <SurfaceSnapshot
            key={`${view.artifact_id}:${view.version}`}
            view={view}
            workspaceId={workspaceId}
            runId={run.id}
            onFile={setFile}
          />
        ))
      ) : (
        <p className="page-note">
          {query.isPending ? '正在读取已发布的真实快照…' : '本任务尚无已发布的界面产物。'}
        </p>
      )}
      {file && (
        <FilePreview
          key={`${workspaceId}:${file.id}:${file.version}`}
          workspaceId={workspaceId}
          artifact={file}
          onClose={() => setFile(null)}
        />
      )}
    </section>
  )
}
