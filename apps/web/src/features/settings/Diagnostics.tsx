import { useSettingsAttention } from './SettingsGroup'
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { maintenanceApi } from '../../api/maintenance'
import { Button } from '../../components/ui'
import { DiagnosticsSnapshotView } from './DiagnosticsSnapshot'
import './diagnostics.css'

export function Diagnostics({ initiallyOpen = true }: { initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const [visited, setVisited] = useState(initiallyOpen)
  return (
    <details
      className="settings-diagnostics"
      open={open}
      onToggle={(event) => {
        setOpen(event.currentTarget.open)
        if (event.currentTarget.open) setVisited(true)
      }}
    >
      <summary>诊断快照与脱敏导出</summary>
      {visited && <DiagnosticsContent />}
    </details>
  )
}

function DiagnosticsContent() {
  const query = useQuery({
    queryKey: ['maintenance-diagnostics'],
    queryFn: maintenanceApi.diagnostics,
  })
  const [downloading, setDownloading] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  useSettingsAttention('diagnostics-error', query.isError || !!error, '诊断读取或导出未完成')
  const pending = useRef<AbortController | null>(null)
  const savedUrl = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  function releaseUrl() {
    if (timer.current !== null) clearTimeout(timer.current)
    timer.current = null
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
  async function download() {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setDownloading(true)
    setError('')
    setNotice('')
    try {
      const { blob, filename } = await maintenanceApi.exportDiagnostics(controller.signal)
      if (controller.signal.aborted) return
      releaseUrl()
      savedUrl.current = URL.createObjectURL(blob)
      timer.current = setTimeout(releaseUrl, 60_000)
      const link = document.createElement('a')
      link.href = savedUrl.current
      link.download = filename
      document.body.append(link)
      try {
        link.click()
      } finally {
        link.remove()
      }
      setNotice(
        'workbench-diagnostics.zip 已交给浏览器保存。保存位置与是否完成请在浏览器下载中查看。',
      )
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof Error ? error.message : '诊断下载未完成。')
    } finally {
      if (!controller.signal.aborted) setDownloading(false)
      if (pending.current === controller) pending.current = null
    }
  }
  return (
    <div className="diagnostics-content">
      <p className="page-note">
        只读采集真实版本、队列心跳与存储观测。默认按结构化白名单脱敏：不包含原始日志、对话正文、用户或任务
        ID、路径、参数、截图与凭据。
      </p>
      <div className="button-row">
        <Button disabled={query.isFetching} onClick={() => void query.refetch()}>
          <RefreshCw size={14} className={query.isFetching ? 'spinning' : ''} />
          刷新诊断快照
        </Button>
        <Button disabled={downloading} onClick={() => void download()}>
          <Download size={14} />
          {downloading ? '正在准备诊断 ZIP…' : '下载脱敏诊断 ZIP'}
        </Button>
      </div>
      <p className="page-note">
        ZIP 上限 1 MiB，下载时重新生成只读快照；展开或刷新不会发送模型请求。
      </p>
      {query.error && (
        <p className="form-error" role="alert">
          {query.error.message}
          {query.data ? ' 下方保留的是上次成功快照。' : ''}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="page-note" role="status">
          {notice}
        </p>
      )}
      {query.data ? (
        <DiagnosticsSnapshotView data={query.data} />
      ) : (
        !query.error && <p className="page-note">正在读取真实诊断快照…</p>
      )}
    </div>
  )
}
