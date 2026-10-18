import { useEffect, useRef, useState } from 'react'
import { authGeneration } from '../../api/client'
import type { ArtifactView } from '../../api/types'
import { uploadFile, UploadPaused } from '../files/upload'
import type { PendingUpload } from '../files/upload'

export type AttachmentUpload = {
  id: string
  draftKey: string
  filename: string
  total: number
  received: number
  status: 'queued' | 'uploading' | 'failed'
  error?: string
}
type Work = {
  item: AttachmentUpload
  workspaceId: string
  file: File
  record: PendingUpload
  generation: number
  cancelled: boolean
}

export function useAttachmentUploads(
  onComplete: (artifact: ArtifactView, draftKey: string) => void,
) {
  const [items, setItems] = useState<AttachmentUpload[]>([])
  const work = useRef(new Map<string, Work>())
  const chain = useRef(Promise.resolve())
  const complete = useRef(onComplete)
  complete.current = onComplete
  const generation = authGeneration()
  useEffect(() => {
    for (const item of work.current.values()) item.cancelled = true
    work.current.clear()
    setItems([])
    return () => {
      for (const item of work.current.values()) item.cancelled = true
    }
  }, [generation])

  function update(entry: Work, changes: Partial<AttachmentUpload>) {
    if (entry.cancelled || entry.generation !== authGeneration()) return
    entry.item = { ...entry.item, ...changes }
    setItems((old) => old.map((item) => (item.id === entry.item.id ? entry.item : item)))
  }
  function schedule(entry: Work) {
    chain.current = chain.current.then(async () => {
      if (entry.cancelled || entry.generation !== authGeneration()) return
      update(entry, { status: 'uploading', error: undefined })
      try {
        const artifact = await uploadFile(entry.workspaceId, entry.file, {
          record: entry.record,
          onRecord: (record) => {
            entry.record = record
          },
          onProgress: (status) => update(entry, { received: status.received_bytes }),
          shouldPause: () => entry.cancelled || entry.generation !== authGeneration(),
        })
        if (entry.cancelled || entry.generation !== authGeneration()) return
        complete.current(artifact, entry.item.draftKey)
        work.current.delete(entry.item.id)
        setItems((old) => old.filter((item) => item.id !== entry.item.id))
      } catch (error) {
        if (!(error instanceof UploadPaused))
          update(entry, {
            status: 'failed',
            error: error instanceof Error ? error.message : '上传未完成，请重试。',
          })
      }
    })
  }
  function add(files: File[], workspaceId: string, draftKey: string, existing: number) {
    const pending = [...work.current.values()].filter(
      (entry) => entry.item.draftKey === draftKey,
    ).length
    const accepted = files.slice(0, Math.max(0, 8 - existing - pending))
    for (const file of accepted) {
      const id = crypto.randomUUID()
      const item: AttachmentUpload = {
        id,
        draftKey,
        filename: file.name,
        total: file.size,
        received: 0,
        status: 'queued',
      }
      const entry: Work = {
        item,
        workspaceId,
        file,
        record: {
          requestId: id,
          filename: file.name,
          totalBytes: file.size,
          mimeType: file.type || null,
        },
        generation: authGeneration(),
        cancelled: false,
      }
      work.current.set(id, entry)
      setItems((old) => [...old, item])
      schedule(entry)
    }
    return files.length - accepted.length
  }
  return {
    items,
    add,
    remove(id: string) {
      const entry = work.current.get(id)
      if (entry) entry.cancelled = true
      work.current.delete(id)
      setItems((old) => old.filter((item) => item.id !== id))
    },
    retry(id: string) {
      const entry = work.current.get(id)
      if (entry && entry.item.status === 'failed') {
        update(entry, { status: 'queued', error: undefined })
        schedule(entry)
      }
    },
  }
}
