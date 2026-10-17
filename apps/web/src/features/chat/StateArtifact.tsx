import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { filesApi } from '../../api/files'
import { Button } from '../../components/ui'
import { MessageContent } from './MessageContent'
import './StateArtifact.css'
export function StateArtifact({
  workspaceId,
  artifactId,
}: {
  workspaceId: string
  artifactId: string
}) {
  const [open, setOpen] = useState(false)
  const preview = useQuery({
    queryKey: ['artifact-preview', workspaceId, artifactId],
    queryFn: () => filesApi.preview(workspaceId, artifactId),
    enabled: open,
  })
  return (
    <details
      className="run-state-artifact"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>读取任务 STATE.md</summary>
      <p className="page-note">服务端持久状态产物 · {artifactId}</p>
      <a href={filesApi.downloadUrl(workspaceId, artifactId)} download>
        下载受管 STATE.md
      </a>
      {preview.isError ? (
        <div role="alert">
          <p className="form-error">{preview.error.message}</p>
          <Button onClick={() => void preview.refetch()}>重新读取</Button>
        </div>
      ) : preview.data ? (
        <>
          {preview.data.text !== null ? (
            <MessageContent content={preview.data.text} />
          ) : (
            <p className="page-note">此产物当前没有可读取文本，可下载原件。</p>
          )}
          {preview.data.truncated && (
            <p className="file-partial-warning">当前预览已截断，完整内容可下载读取。</p>
          )}
        </>
      ) : (
        <p className="page-note">正在读取状态产物…</p>
      )}
    </details>
  )
}
