import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ImagePlus, X } from 'lucide-react'
import { filesApi } from '../../api/files'
import type { MediaReference } from '../../api/media'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'

export function MediaReferences({
  workspaceId,
  references,
  locked,
  onChange,
}: {
  workspaceId: string
  references: MediaReference[]
  locked: boolean
  onChange: (references: MediaReference[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const artifacts = useQuery({
    queryKey: ['artifacts', workspaceId],
    queryFn: () => filesApi.list(workspaceId),
    enabled: open || references.length > 0,
    retry: false,
  })
  const images = (artifacts.data ?? []).filter(
    (artifact) =>
      artifact.status === 'available' &&
      artifact.byte_size <= 16 * 1024 * 1024 &&
      ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(artifact.mime_type) &&
      artifact.kind !== 'screenshot' &&
      artifact.filename.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  )
  return (
    <div className="media-references">
      <Button disabled={locked || references.length >= 8} onClick={() => setOpen(true)}>
        <ImagePlus size={15} />
        从文件库添加参考图像
      </Button>
      {!!references.length && (
        <ul className="media-reference-list">
          {references.map((reference) => (
            <li key={reference.artifact_id}>
              <span>
                {artifacts.data?.find((artifact) => artifact.id === reference.artifact_id)
                  ?.filename ?? reference.artifact_id}
              </span>
              <Button
                variant="ghost"
                disabled={locked}
                aria-label={`移除参考图像 ${reference.artifact_id}`}
                onClick={() =>
                  onChange(references.filter((item) => item.artifact_id !== reference.artifact_id))
                }
              >
                <X size={14} />
              </Button>
            </li>
          ))}
        </ul>
      )}
      {open && (
        <Dialog title="选择参考图像" onClose={() => setOpen(false)}>
          <div className="stacked-form">
            <p className="page-note">
              从当前工作区已有文件中选择，最多 8 张、单张不超过 16 MiB。可先在「文件」页上传图像。
            </p>
            <label>
              搜索图像
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            {artifacts.isPending && <p role="status">正在读取真实文件列表…</p>}
            {artifacts.error && (
              <p className="form-error" role="alert">
                {artifacts.error.message}{' '}
                <Button onClick={() => void artifacts.refetch()}>重试</Button>
              </p>
            )}
            <div className="media-reference-library">
              {images.slice(0, 100).map((artifact) => {
                const added = references.some((item) => item.artifact_id === artifact.id)
                return (
                  <label className="checkbox-label" key={artifact.id}>
                    <input
                      type="checkbox"
                      disabled={locked || (!added && references.length >= 8)}
                      checked={added}
                      onChange={(event) =>
                        onChange(
                          event.target.checked
                            ? [
                                ...references,
                                {
                                  artifact_id: artifact.id,
                                  version: artifact.version,
                                  consent_remote: false,
                                },
                              ]
                            : references.filter((item) => item.artifact_id !== artifact.id),
                        )
                      }
                    />
                    <span>
                      {artifact.filename}
                      <small>{formatBytes(artifact.byte_size)}</small>
                    </span>
                  </label>
                )
              })}
            </div>
            {!artifacts.isPending && !artifacts.error && !images.length && (
              <p className="page-note">没有匹配的可用图像，可先到「文件」页上传。</p>
            )}
            {images.length > 100 && (
              <p className="page-note">当前显示前 100 张，请输入文件名缩小范围。</p>
            )}
            <Button onClick={() => setOpen(false)}>完成选择</Button>
          </div>
        </Dialog>
      )}
    </div>
  )
}
