import { useEffect, useRef, useState } from 'react'
import { Download } from 'lucide-react'
import {
  conversationExportUrl,
  fetchConversationExport,
  type ConversationExportFormat,
} from '../../api/conversationExport'
import type { Thread } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import './conversationExport.css'

export function ConversationExport({ thread }: { thread: Thread }) {
  const [selected, setSelected] = useState<Thread | null>(null)
  const [busy, setBusy] = useState<ConversationExportFormat | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pending = useRef<AbortController | null>(null)
  const savedUrl = useRef<string | null>(null)
  const revokeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function releaseUrl() {
    if (revokeTimer.current !== null) clearTimeout(revokeTimer.current)
    revokeTimer.current = null
    if (savedUrl.current) URL.revokeObjectURL(savedUrl.current)
    savedUrl.current = null
  }
  useEffect(
    () => () => {
      pending.current?.abort()
      releaseUrl()
    },
    [],
  )

  function close() {
    pending.current?.abort()
    pending.current = null
    setBusy(null)
    setSelected(null)
  }

  async function download(format: ConversationExportFormat) {
    if (!selected || pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(format)
    setError('')
    setNotice('')
    try {
      const { blob, filename } = await fetchConversationExport(
        selected.id,
        format,
        controller.signal,
      )
      if (controller.signal.aborted) return
      releaseUrl()
      const url = URL.createObjectURL(blob)
      savedUrl.current = url
      // Keep one bounded blob alive briefly so the browser can consume the download.
      revokeTimer.current = setTimeout(releaseUrl, 60_000)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.append(link)
      try {
        link.click()
      } finally {
        link.remove()
      }
      setNotice(`已交给浏览器保存：${filename}。保存位置与是否完成请在浏览器下载中查看。`)
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof Error ? error.message : '导出未完成，请重试。')
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(null)
      }
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        className="conversation-export-trigger"
        aria-label="导出对话"
        title="导出对话"
        onClick={() => {
          setSelected(thread)
          setError('')
          setNotice('')
        }}
      >
        <Download size={13} aria-hidden="true" />
      </Button>
      {selected && (
        <Dialog title="导出对话" onClose={close}>
          <div className="conversation-export-dialog" aria-busy={busy !== null}>
            <p className="conversation-export-title">{selected.title}</p>
            <p className="page-note">
              导出此对话已保存的用户与助手正文、所选分支历史、任务状态与工具名称，以及附件和产物引用。文件原件请在文件页另行下载。
            </p>
            <p className="page-note">
              若任务仍在运行，当前快照可能不完整，可在完成后再次导出。导出不包含工具参数、内部提示词或密钥；单份文件上限
              16 MiB。
            </p>
            <div className="conversation-export-options">
              {(['markdown', 'json'] as const).map((format) => (
                <a
                  key={format}
                  className="button button-secondary"
                  href={conversationExportUrl(selected.id, format)}
                  aria-disabled={busy !== null}
                  onClick={(event) => {
                    event.preventDefault()
                    if (!busy) void download(format)
                  }}
                >
                  <Download size={15} aria-hidden="true" />
                  {busy === format
                    ? '正在准备文件…'
                    : `下载 ${format === 'markdown' ? 'Markdown' : 'JSON'}`}
                </a>
              ))}
            </div>
            {error && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
            {notice && (
              <p role="status" className="page-note">
                {notice}
              </p>
            )}
            {busy && <p className="page-note">关闭此窗口可取消本次文件读取。</p>}
          </div>
        </Dialog>
      )}
    </>
  )
}
