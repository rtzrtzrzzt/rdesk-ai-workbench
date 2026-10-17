import { useQuery } from '@tanstack/react-query'
import { filesApi } from '../../api/files'
import { formatBytes } from '../files/FileUpload'
import { number, text } from './data'

export function DownloadedArtifact({
  workspaceId,
  receipt,
}: {
  workspaceId: string
  receipt: Record<string, unknown>
}) {
  const artifactId = text(receipt.artifact_id),
    version = text(receipt.version),
    filename = text(receipt.filename),
    bytes = number(receipt.bytes)
  const artifact = useQuery({
    queryKey: ['artifact', workspaceId, artifactId],
    queryFn: () => filesApi.artifact(workspaceId, artifactId ?? ''),
    enabled: !!artifactId && !!version,
  })
  const verified =
    artifact.data &&
    artifact.data.id === artifactId &&
    artifact.data.version === version &&
    artifact.data.status === 'available'
  return (
    <div className="automation-downloaded-artifact">
      <h4>已保存的受管原件</h4>
      <p>
        {filename ?? '服务器未给出文件名'}
        {bytes !== null ? ` · ${formatBytes(bytes)}` : ''}
      </p>
      <p className="page-note">
        文件 ID：{artifactId ?? '缺失'} · 版本：{version ?? '缺失'}
      </p>
      {verified ? (
        <a
          className="button button-secondary"
          href={filesApi.downloadUrl(workspaceId, artifact.data.id)}
          download
        >
          下载已保存原件
        </a>
      ) : (
        <p
          role="status"
          className={
            artifact.error || artifact.data || !artifactId || !version ? 'form-error' : 'page-note'
          }
        >
          {!artifactId || !version
            ? '回执缺少原件 ID 或版本，未开放文件下载。'
            : (artifact.error?.message ??
              (artifact.data
                ? '原件版本或保存状态不符，未开放替代文件下载。'
                : '正在核对原件权限与版本…'))}
        </p>
      )}
      <p className="page-note">
        原件也会出现在当前工作区的文件库，可在那里明确选择后续处理；保存不代表已解析。
      </p>
    </div>
  )
}
