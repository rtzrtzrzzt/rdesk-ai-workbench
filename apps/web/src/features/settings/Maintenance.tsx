import { useSettingsAttention } from './SettingsGroup'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { maintenanceApi } from '../../api/maintenance'
import type {
  BackupCreate,
  BackupManifest,
  BackupView,
  RestoreCreate,
  RestoreView,
} from '../../api/types'
import { Button, Status } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import { Cleanup } from './Cleanup'

type PendingMaintenance =
  { kind: 'backup'; command: BackupCreate } | { kind: 'restore'; command: RestoreCreate }
const storageKey = (userId: string) => `workbench.maintenance.${userId}`
const statusLabels: Record<string, string> = {
  complete: '完整',
  incomplete: '未完成',
  aborted: '已中止',
  failed: '失败',
  isolated: '已建立隔离副本',
}
function readPending(userId: string): PendingMaintenance | null {
  try {
    const value = JSON.parse(
      localStorage.getItem(storageKey(userId)) ?? 'null',
    ) as PendingMaintenance | null
    if (!value || !value.command || typeof value.command.request_id !== 'string') return null
    if (
      value.kind === 'backup' &&
      (value.command.label === undefined || typeof value.command.label === 'string')
    )
      return value
    if (value.kind === 'restore' && typeof value.command.backup_id === 'string')
      return {
        kind: 'restore',
        command: {
          request_id: value.command.request_id,
          backup_id: value.command.backup_id,
          ...(value.command.timeout_seconds !== undefined
            ? { timeout_seconds: value.command.timeout_seconds }
            : {}),
        },
      }
    return null
  } catch {
    return null
  }
}
function errorCode(error: unknown) {
  if (
    !(error instanceof ApiError) ||
    !error.body ||
    typeof error.body !== 'object' ||
    !('error' in error.body)
  )
    return ''
  const detail = error.body.error
  return detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
    ? detail.code
    : ''
}
function Manifest({ manifest }: { manifest: BackupManifest }) {
  return (
    <div className="backup-manifest">
      <dl className="run-meta">
        <dt>备份</dt>
        <dd>{manifest.backup_id}</dd>
        <dt>状态</dt>
        <dd>{statusLabels[manifest.status]}</dd>
        <dt>应用版本</dt>
        <dd>{manifest.app_version}</dd>
        <dt>数据库版本</dt>
        <dd>PostgreSQL {manifest.database_major_version}</dd>
        <dt>文件</dt>
        <dd>
          {manifest.file_count} 份 · {formatBytes(manifest.total_bytes)}
        </dd>
        <dt>凭据策略</dt>
        <dd>{manifest.credentials_policy}</dd>
      </dl>
      <h4>外部工作区</h4>
      {manifest.workspaces.length ? (
        <ul>
          {manifest.workspaces.map((workspace) => (
            <li key={workspace.id}>
              {workspace.name} · {workspace.external_root_path}
              <p className="page-note">原目录内容未纳入备份，隔离恢复后需要重新映射并授权。</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="page-note">清单中没有外部工作区。</p>
      )}
      {manifest.excluded.length > 0 && (
        <>
          <h4>排除内容</h4>
          <ul>
            {manifest.excluded.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </>
      )}
      {manifest.notes.map((text, index) => (
        <p className="page-note" key={index}>
          {text}
        </p>
      ))}
      <details className="run-json">
        <summary>完整服务端清单与校验值</summary>
        <pre>{JSON.stringify(manifest, null, 2)}</pre>
      </details>
    </div>
  )
}
function RestoreResult({ result }: { result: RestoreView }) {
  return (
    <section className="restore-result">
      <h3>隔离恢复结果</h3>
      <Status tone={result.status === 'isolated' ? 'good' : 'warning'}>
        {statusLabels[result.status]}
      </Status>
      <dl className="run-meta">
        <dt>恢复标识</dt>
        <dd>{result.restore_id}</dd>
        <dt>隔离数据库</dt>
        <dd>{result.database_name}</dd>
        <dt>隔离目录</dt>
        <dd>{result.data_dir}</dd>
        <dt>连接配置</dt>
        <dd>{result.connection_file}</dd>
        <dt>保留任务</dt>
        <dd>{result.held_runs} 个任务处于 held，未自动恢复执行</dd>
        <dt>界面验收</dt>
        <dd>{result.user_interface_verified === false ? '尚未实际验收' : ''}</dd>
      </dl>
      <p className="page-note">
        当前实例未切换。外部工作区、模型凭据与搜索连接需重新授权；会话和凭据未恢复，远程模型与搜索未启用。
      </p>
      {result.notes.map((note, index) => (
        <p className="page-note" key={index}>
          {note}
        </p>
      ))}
    </section>
  )
}
export function Maintenance({ userId }: { userId: string }) {
  const client = useQueryClient()
  const backups = useQuery({
    queryKey: ['maintenance-backups', userId],
    queryFn: maintenanceApi.backups,
    refetchInterval: 5000,
  })
  const [selected, setSelected] = useState('')
  const [label, setLabel] = useState('')
  const [timeout, setTimeout] = useState('')
  const [pending, setPending] = useState<PendingMaintenance | null>(() => readPending(userId))
  const [busy, setBusy] = useState(false)
  const [aborting, setAborting] = useState(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const [abortMessage, setAbortMessage] = useState('')
  const [restoreResult, setRestoreResult] = useState<RestoreView | null>(null)
  const manifest = useQuery({
    queryKey: ['backup-manifest', selected],
    queryFn: () => maintenanceApi.manifest(selected),
    enabled: !!selected,
    refetchInterval: (query) => (query.state.data?.status === 'incomplete' ? 5000 : false),
  })
  useSettingsAttention('maintenance', !!pending, '备份或隔离恢复请求待核对')
  useSettingsAttention(
    'maintenance-error',
    backups.isError || manifest.isError || failure,
    '维护信息或操作需要处理',
  )
  const actualBackup = backups.data?.find((item) => item.backup_id === selected)
  function store(value: PendingMaintenance | null) {
    if (value) localStorage.setItem(storageKey(userId), JSON.stringify(value))
    else localStorage.removeItem(storageKey(userId))
    setPending(value)
  }
  async function execute(kind: 'backup' | 'restore') {
    if (busy) return
    setMessage('')
    setFailure(false)
    setAbortMessage('')
    const original = pending
    let command: PendingMaintenance
    try {
      const seconds = timeout === '' ? undefined : Number(timeout)
      if (
        !original &&
        seconds !== undefined &&
        (!Number.isInteger(seconds) || seconds < 60 || seconds > 86400)
      )
        throw new Error('超时须为 60–86400 秒的整数，或留空使用服务端默认。')
      command =
        original ??
        (kind === 'backup'
          ? {
              kind: 'backup',
              command: {
                request_id: crypto.randomUUID(),
                label,
                ...(seconds !== undefined ? { timeout_seconds: seconds } : {}),
              },
            }
          : {
              kind: 'restore',
              command: {
                request_id: crypto.randomUUID(),
                backup_id: selected,
                ...(seconds !== undefined ? { timeout_seconds: seconds } : {}),
              },
            })
      if (command.kind === 'restore') {
        if (!original && actualBackup?.status !== 'complete')
          throw new Error('只允许从已完整发布的备份建立隔离副本。')
      }
      store(command)
    } catch (error) {
      setFailure(true)
      setMessage(error instanceof Error ? error.message : '无法保存待确认请求，本次未提交')
      return
    }
    setBusy(true)
    try {
      if (command.kind === 'backup') {
        const result = await maintenanceApi.create(command.command)
        client.setQueryData<BackupView[]>(['maintenance-backups', userId], (old) => [
          result,
          ...(old ?? []).filter((item) => item.backup_id !== result.backup_id),
        ])
        setSelected(result.backup_id)
        setMessage(`备份状态：${statusLabels[result.status]}。`)
      } else {
        const result = await maintenanceApi.restore(command.command)
        setRestoreResult(result)
        setMessage(`恢复状态：${statusLabels[result.status]}。`)
      }
      store(null)
      await backups.refetch()
    } catch (error) {
      setFailure(true)
      const code = errorCode(error)
      const uncertain =
        error instanceof TypeError || (error instanceof ApiError && error.status >= 500)
      if (!uncertain && !original) {
        store(null)
      }
      setMessage(
        `${error instanceof Error ? error.message : '维护请求未完成'}${code === 'maintenance_busy' ? ' 请等待安全停点后手动重试；不会自动停止用户任务。' : uncertain ? ' 结果未确认，请保留本次标识并手动核对。' : original ? ' 原请求标识保留；请先核对实际结果，不会自动创建新的副本。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  async function abort() {
    setAborting(true)
    setAbortMessage('')
    setFailure(false)
    try {
      const result = await maintenanceApi.abort()
      setAbortMessage(
        result.status === 'stop_requested'
          ? '中止请求已提交，正在等待安全检查点确认；不代表任务已停止。'
          : `中止请求状态：${result.status ?? '未知'}`,
      )
      await backups.refetch()
    } catch (error) {
      setFailure(true)
      setAbortMessage(error instanceof Error ? error.message : '中止请求未确认')
    } finally {
      setAborting(false)
    }
  }
  return (
    <section className="settings-maintenance">
      <div className="run-line">
        <h2>备份与隔离恢复</h2>
        <Button disabled={backups.isFetching} onClick={() => void backups.refetch()}>
          刷新备份
        </Button>
      </div>
      <p className="page-note">
        备份保存到服务端固定私有维护目录。仅在无活动写入、取得独占边界时执行；繁忙时请等待安全停点，不会自动停止任务。隔离恢复会创建全新数据库与目录，不切换当前实例。
      </p>
      {pending && (
        <div className="memory-conflict">
          <strong>{pending.kind === 'backup' ? '备份' : '隔离恢复'}请求待确认</strong>
          <p>请求标识：{pending.command.request_id}。核对复用原参数。</p>
          {pending.kind === 'restore' && <p>备份：{pending.command.backup_id}</p>}
        </div>
      )}
      <div className="maintenance-controls">
        <label>
          备份标签（可选）
          <input
            maxLength={120}
            disabled={busy || !!pending}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <label>
          维护超时（秒，留空用服务默认）
          <input
            type="number"
            min={60}
            max={86400}
            step={1}
            disabled={busy || !!pending}
            value={timeout}
            onChange={(event) => setTimeout(event.target.value)}
          />
        </label>
        <div className="button-row">
          <Button
            variant="primary"
            disabled={busy || !!pending}
            onClick={() => void execute('backup')}
          >
            创建备份
          </Button>
          {pending?.kind === 'backup' && (
            <Button disabled={busy} onClick={() => void execute('backup')}>
              核对本次备份
            </Button>
          )}
          {(busy || pending) && (
            <Button disabled={aborting} onClick={() => void abort()}>
              {aborting ? '正在请求中止…' : '请求中止当前维护'}
            </Button>
          )}
        </div>
      </div>
      {message && (
        <p role="status" className="maintenance-message">
          {message}
        </p>
      )}
      {abortMessage && (
        <p role="status" className="page-note">
          {abortMessage}
        </p>
      )}
      {backups.isError ? (
        <p role="alert" className="form-error">
          {backups.error.message}
        </p>
      ) : backups.data?.length ? (
        <div className="backup-list">
          {backups.data.map((item) => (
            <button
              key={item.backup_id}
              aria-pressed={selected === item.backup_id}
              onClick={() => setSelected(item.backup_id)}
            >
              <span>
                <strong>{item.label || '无标签备份'}</strong>
                <small>
                  {new Date(item.created_at).toLocaleString()} · {item.file_count} 份 ·{' '}
                  {formatBytes(item.total_bytes)}
                </small>
              </span>
              <Status tone={item.status === 'complete' ? 'good' : 'neutral'}>
                {statusLabels[item.status]}
              </Status>
            </button>
          ))}
        </div>
      ) : (
        <p className="page-note">{backups.isPending ? '正在读取真实备份…' : '尚无已保存备份。'}</p>
      )}
      {selected && (
        <div className="backup-inspector">
          {manifest.isError ? (
            <div role="alert">
              <p className="form-error">{manifest.error.message}</p>
              <Button onClick={() => void manifest.refetch()}>重新读取清单</Button>
            </div>
          ) : manifest.data ? (
            <Manifest manifest={manifest.data} />
          ) : (
            <p className="page-note">正在读取备份清单…</p>
          )}
        </div>
      )}
      {(actualBackup?.status === 'complete' || pending?.kind === 'restore') && (
        <form
          className="stacked-form restore-form"
          onSubmit={(event) => {
            event.preventDefault()
            void execute('restore')
          }}
        >
          <h3>从备份建立隔离副本</h3>
          <p className="page-note">
            恢复后任务保持
            held，模型权重和外部工作区原文件不会被复制；凭据、远程模型及搜索需重新授权。此操作不会启动隔离实例。
          </p>
          <Button
            type="submit"
            variant="primary"
            disabled={busy || (pending !== null && pending.kind !== 'restore')}
          >
            {busy
              ? '正在执行维护…'
              : pending?.kind === 'restore'
                ? '核对本次隔离恢复'
                : '创建全新隔离副本'}
          </Button>
        </form>
      )}
      {restoreResult && <RestoreResult result={restoreResult} />}
      <Cleanup userId={userId} />
    </section>
  )
}
