import { filesApi } from '../../api/files'
import type { ArtifactView, UploadStatus } from '../../api/types'

export type PendingUpload = {
  requestId: string
  filename: string
  totalBytes: number
  mimeType: string | null
  id?: string
}

export class UploadPaused extends Error {}

/** Shared resumable transport for the file library and composer. */
export async function uploadFile(
  workspaceId: string,
  file: File,
  options: {
    record: PendingUpload
    onRecord: (record: PendingUpload) => void
    onProgress: (status: UploadStatus) => void
    shouldPause?: () => boolean
  },
): Promise<ArtifactView> {
  const check = () => {
    if (options.shouldPause?.()) throw new UploadPaused()
  }
  check()
  options.onRecord(options.record)
  let current = options.record.id
    ? await filesApi.uploadStatus(workspaceId, options.record.id)
    : await filesApi.upload(workspaceId, {
        filename: options.record.filename,
        total_bytes: options.record.totalBytes,
        mime_type: options.record.mimeType,
        request_id: options.record.requestId,
      })
  options.onRecord({ ...options.record, id: current.id })
  options.onProgress(current)
  check()
  if (current.artifact_id && current.status === 'completed')
    return filesApi.artifact(workspaceId, current.artifact_id)
  if (
    current.total_bytes !== file.size ||
    current.received_bytes > file.size ||
    current.chunk_size < 1
  )
    throw new Error('服务端上传状态与原文件不一致，请核对后再继续。')
  while (current.received_bytes < file.size) {
    check()
    const offset = current.received_bytes
    const size = Math.min(current.chunk_size, 4 * 1024 * 1024)
    const next = await filesApi.chunk(
      workspaceId,
      current.id,
      offset,
      file.slice(offset, Math.min(offset + size, file.size)),
    )
    if (next.received_bytes <= offset) throw new Error('服务端未确认新的上传内容，请重试。')
    current = next
    options.onProgress(current)
  }
  check()
  return filesApi.complete(workspaceId, current.id)
}
