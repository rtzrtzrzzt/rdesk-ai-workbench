import { useEffect, useRef, useState } from 'react'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileText, FolderOpen, RefreshCw, Square } from 'lucide-react'
import { ApiError } from '../../api/client'
import { filesApi } from '../../api/files'
import type { ArtifactView, FileTask, ProcessorCapability, Workspace } from '../../api/types'
import { Button, EmptyState, PageHeading, Status } from '../../components/ui'
import { ExportDialog } from './ExportDialog'
import { operationLabel, pendingKey, taskKey, readPending, readTaskIds } from './shared'
import { ProcessControls } from './ProcessControls'
import type { ProcessSettings } from './ProcessControls'
import type { PendingProcess } from './shared'
import { FileUpload, formatBytes } from './FileUpload'

const terminal = new Set(['succeeded', 'failed', 'cancelled'])
const labels: Record<string, string> = {
  queued: '排队中',
  running: '处理中',
  waiting_resource: '等待资源',
  cancelling: '正在停止',
  succeeded: '已完成',
  failed: '未完成',
  cancelled: '已停止',
}
const describeError = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请重试。'
function Details({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="file-details">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}
function FileReadError({
  error,
  retry,
  service = false,
}: {
  error: unknown
  retry: () => void
  service?: boolean
}) {
  return (
    <div className="memory-read-error" role="alert">
      <p>
        {service && error instanceof ApiError && (error.status === 404 || error.status === 503)
          ? '文件服务尚未就绪，当前没有可读取的文件状态。'
          : describeError(error)}
      </p>
      <Button onClick={retry}>
        <RefreshCw size={14} />
        重新读取
      </Button>
    </div>
  )
}
export function FilesPage({
  workspace,
  authenticated,
  active,
}: {
  workspace?: Workspace
  authenticated: boolean
  active: boolean
}) {
  const [visited, setVisited] = useState<Record<string, Workspace>>({})
  useEffect(() => {
    if (workspace)
      setVisited((old) =>
        old[workspace.id] === workspace ? old : { ...old, [workspace.id]: workspace },
      )
  }, [workspace])
  const known = workspace ? { ...visited, [workspace.id]: workspace } : visited
  return (
    <div className="page-content files-page">
      <PageHeading title="文件与产物">
        保存原件，按需提取与预览，保留每份派生结果的来源。
      </PageHeading>
      {!authenticated || !workspace ? (
        <EmptyState icon={FolderOpen} title={authenticated ? '先选择工作区' : '文件服务尚未连接'}>
          文件与产物始终属于明确的工作区。连接后选择工作区即可读取真实列表。
        </EmptyState>
      ) : (
        Object.values(known).map((item) => (
          <div key={item.id} hidden={item.id !== workspace.id}>
            <FileWorkspace workspace={item} active={active && item.id === workspace.id} />
          </div>
        ))
      )}
    </div>
  )
}
function FileWorkspace({ workspace, active }: { workspace: Workspace; active: boolean }) {
  const client = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [exporting, setExporting] = useState(false)
  const [filter, setFilter] = useState('')
  const [taskIds, setTaskIds] = useState(() => readTaskIds(workspace.id))
  const [pending, setPending] = useState<PendingProcess | null>(() => readPending(workspace.id))
  useEffect(() => {
    if (active) setPending(readPending(workspace.id))
  }, [active, workspace.id])
  const [processing, setProcessing] = useState(false)
  const [processError, setProcessError] = useState('')
  const artifacts = useQuery({
    queryKey: ['artifacts', workspace.id],
    queryFn: () => filesApi.list(workspace.id),
    enabled: active,
  })
  const capabilities = useQuery({
    queryKey: ['file-processors', workspace.id],
    queryFn: () => filesApi.processors(workspace.id),
    enabled: active,
  })
  const artifact = useQuery({
    queryKey: ['artifact', workspace.id, selectedId],
    queryFn: () => filesApi.artifact(workspace.id, selectedId),
    enabled: active && !!selectedId,
  })
  const serverTasks = useQuery({
    queryKey: ['file-tasks', workspace.id],
    queryFn: () => filesApi.tasks(workspace.id),
    enabled: active,
    refetchInterval: active ? 4000 : false,
  })
  const discoveredTasks = new Map((serverTasks.data ?? []).map((task) => [task.id, task]))
  const discoveredIds = [...new Set([...discoveredTasks.keys(), ...taskIds])]
  const localQueries = useQueries({
    queries: discoveredIds.map((id) => ({
      queryKey: ['file-task', workspace.id, id],
      queryFn: () => filesApi.task(workspace.id, id),
      enabled: active && !discoveredTasks.has(id),
      refetchInterval: (query: { state: { data: FileTask | undefined; error: unknown } }) =>
        !active ||
        discoveredTasks.has(id) ||
        query.state.error ||
        terminal.has(query.state.data?.status ?? '')
          ? false
          : 2000,
    })),
  })
  const tasks = localQueries.map((query, index) => ({
    data: discoveredTasks.get(discoveredIds[index]) ?? query.data,
    isError: !discoveredTasks.has(discoveredIds[index]) && query.isError,
    error: query.error,
    refetch: query.refetch,
  }))
  const completedTasks = tasks
    .flatMap((task) =>
      task.data && terminal.has(task.data.status) ? [`${task.data.id}:${task.data.status}`] : [],
    )
    .join(',')
  useEffect(() => {
    if (completedTasks) void client.invalidateQueries({ queryKey: ['artifacts', workspace.id] })
  }, [completedTasks, client, workspace.id])
  useEffect(() => {
    if (!selectedId && artifacts.data?.length) setSelectedId(artifacts.data[0].id)
  }, [selectedId, artifacts.data])
  async function uploaded(item: ArtifactView) {
    client.setQueryData(['artifact', workspace.id, item.id], item)
    setSelectedId(item.id)
    await artifacts.refetch()
  }
  function storePending(value: PendingProcess | null) {
    setPending(value)
    if (value) localStorage.setItem(pendingKey(workspace.id), JSON.stringify(value))
    else localStorage.removeItem(pendingKey(workspace.id))
  }
  async function submitProcess(command: PendingProcess) {
    if (processing) return
    setProcessing(true)
    setProcessError('')
    storePending(command)
    try {
      const task = await filesApi.process(workspace.id, command.artifactId, command.input)
      client.setQueryData(['file-task', workspace.id, task.id], task)
      setTaskIds((old) => {
        const next = old.includes(task.id) ? old : [task.id, ...old]
        localStorage.setItem(taskKey(workspace.id), JSON.stringify(next))
        return next
      })
      storePending(null)
      await serverTasks.refetch()
    } catch (error) {
      const uncertain =
        error instanceof TypeError || (error instanceof ApiError && error.status >= 500)
      if (!uncertain) storePending(null)
      setProcessError(
        `${describeError(error)}${uncertain ? ' 请求结果尚未确认，请核对本次处理请求后再创建新任务。' : ''}`,
      )
    } finally {
      setProcessing(false)
    }
  }
  function processSelected(input: ProcessSettings) {
    const selected = artifact.data
    if (!selected) return
    void submitProcess({
      artifactId: selected.id,
      filename: selected.filename,
      input: { ...input, request_id: crypto.randomUUID() },
    })
  }
  const visible =
    artifacts.data?.filter((item) =>
      item.filename.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()),
    ) ?? []
  return (
    <>
      {exporting && (
        <ExportDialog
          workspaceId={workspace.id}
          artifacts={artifacts.data ?? []}
          onClose={() => setExporting(false)}
          onCreated={async (task) => {
            client.setQueryData(['file-task', workspace.id, task.id], task)
            setTaskIds((old) => {
              const next = [...new Set([task.id, ...old])]
              localStorage.setItem(taskKey(workspace.id), JSON.stringify(next))
              return next
            })
            await serverTasks.refetch()
          }}
        />
      )}
      <FileUpload workspaceId={workspace.id} active={active} onComplete={uploaded} />
      {pending && (
        <div className="memory-conflict" role="status">
          <strong>有文件处理请求需要确认</strong>
          <p>
            {pending.filename} · {operationLabel(pending.input.operation ?? 'extract')}
            ，核对将复用原请求标识。
          </p>
          <Button disabled={processing} onClick={() => void submitProcess(pending)}>
            {processing ? '正在核对…' : '核对本次处理请求'}
          </Button>
        </div>
      )}
      {processError && (
        <p className="form-error file-process-error" role="alert">
          {processError}
        </p>
      )}
      <div className="files-toolbar">
        <Button onClick={() => setExporting(true)}>创建导出</Button>
        <label>
          <span className="sr-only">筛选文件名</span>
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="按文件名筛选"
          />
        </label>
        <Button disabled={artifacts.isFetching} onClick={() => void artifacts.refetch()}>
          <RefreshCw size={14} />
          刷新文件
        </Button>
      </div>
      {artifacts.isError ? (
        <FileReadError error={artifacts.error} retry={() => void artifacts.refetch()} service />
      ) : artifacts.isPending ? (
        <p className="page-note">正在读取工作区文件…</p>
      ) : artifacts.data.length ? (
        <div className="files-workspace">
          <div className="file-list" aria-label="文件与产物列表">
            {visible.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-pressed={selectedId === item.id}
                onClick={() => setSelectedId(item.id)}
              >
                <FileText size={16} />
                <span>
                  <strong>{item.filename}</strong>
                  <small>
                    {item.parent_id ? '派生件' : '原件'} · {formatBytes(item.byte_size)}
                  </small>
                </span>
              </button>
            ))}
            {!visible.length && <p className="memory-list-note">没有匹配的文件名。</p>}
          </div>
          <div className="file-inspector">
            {artifact.isError ? (
              <FileReadError error={artifact.error} retry={() => void artifact.refetch()} />
            ) : artifact.data ? (
              <ArtifactDetail
                workspaceId={workspace.id}
                artifact={artifact.data}
                active={active}
                capabilities={capabilities.data ?? []}
                busy={processing}
                blocked={!!pending}
                onProcess={processSelected}
                onSelectParent={setSelectedId}
              />
            ) : (
              <EmptyState
                icon={FileText}
                title={artifact.isFetching ? '正在读取文件…' : '选择一份文件'}
              >
                查看原件、派生关系、文本预览和真实处理状态。
              </EmptyState>
            )}
          </div>
        </div>
      ) : (
        <EmptyState icon={FolderOpen} title="还没有文件">
          上传真实文件后，原件与生成的派生件会显示在这里。
        </EmptyState>
      )}
      {(discoveredIds.length > 0 || serverTasks.isError) && (
        <section className="file-tasks">
          <h2>文件处理任务</h2>
          {serverTasks.isError && (
            <FileReadError error={serverTasks.error} retry={() => void serverTasks.refetch()} />
          )}
          {tasks.map((query, index) =>
            query.isError ? (
              <FileReadError
                key={discoveredIds[index]}
                error={query.error}
                retry={() => void query.refetch()}
              />
            ) : query.data ? (
              <TaskCard
                key={query.data.id}
                workspaceId={workspace.id}
                task={query.data}
                filename={
                  artifacts.data?.find((item) => item.id === query.data?.artifact_id)?.filename
                }
                onSelect={setSelectedId}
              />
            ) : (
              <p key={discoveredIds[index]} className="page-note">
                正在读取处理任务…
              </p>
            ),
          )}
        </section>
      )}
      <details className="processor-details">
        <summary>可用处理器与格式边界</summary>
        {capabilities.isError ? (
          <FileReadError error={capabilities.error} retry={() => void capabilities.refetch()} />
        ) : (
          capabilities.data?.map((capability) => (
            <div className="processor-row" key={capability.id}>
              <div>
                <h3>{capability.name}</h3>
                <p>{capability.formats.join('、')}</p>
                <p>{capability.limitation}</p>
                {capability.version && <p>组件版本：{capability.version}</p>}
              </div>
              <Status tone={capability.status === 'available' ? 'good' : 'neutral'}>
                {capability.status === 'available'
                  ? '可调用'
                  : capability.status === 'not_installed'
                    ? '未安装'
                    : capability.status === 'not_configured'
                      ? '未配置'
                      : capability.status === 'adapter_pending'
                        ? '适配待完成'
                        : capability.status}
              </Status>
            </div>
          ))
        )}
      </details>
      <p className="page-note">
        原件保存、内容提取和模型原生输入是不同能力。提取结果保留来源与已处理范围，不代表模型原生支持此格式。
      </p>
    </>
  )
}
function ArtifactDetail({
  workspaceId,
  artifact,
  active,
  capabilities,
  busy,
  blocked,
  onProcess,
  onSelectParent,
}: {
  workspaceId: string
  artifact: ArtifactView
  active: boolean
  capabilities: ProcessorCapability[]
  busy: boolean
  blocked: boolean
  onProcess: (input: ProcessSettings) => void
  onSelectParent: (id: string) => void
}) {
  const [showPreview, setShowPreview] = useState(false)
  useEffect(() => setShowPreview(false), [artifact.id])
  const preview = useQuery({
    queryKey: ['file-preview', workspaceId, artifact.id],
    queryFn: () => filesApi.preview(workspaceId, artifact.id),
    enabled: active && showPreview,
  })
  const metadata = artifact.metadata as Record<string, unknown>
  const warnings = Array.isArray(metadata.warnings)
    ? metadata.warnings.filter((value): value is string => typeof value === 'string')
    : []
  return (
    <>
      <div className="file-inspector-heading">
        <div>
          <h2>{artifact.filename}</h2>
          <p>
            {artifact.parent_id ? '派生件' : '原件'} · {formatBytes(artifact.byte_size)} ·{' '}
            {artifact.mime_type}
          </p>
        </div>
        <a
          className="button button-secondary"
          href={filesApi.downloadUrl(workspaceId, artifact.id)}
          download
        >
          <Download size={14} />
          下载
        </a>
      </div>
      <dl className="file-metadata">
        <dt>版本</dt>
        <dd title={artifact.version}>{artifact.version.slice(0, 12)}</dd>
        <dt>保存时间</dt>
        <dd>{new Date(artifact.created_at).toLocaleString()}</dd>
        {artifact.parent_id && (
          <>
            <dt>来源原件</dt>
            <dd>
              <button onClick={() => onSelectParent(artifact.parent_id!)}>查看来源文件</button>
            </dd>
          </>
        )}
      </dl>
      {metadata.complete === false && (
        <p className="file-partial-warning" role="status">
          这是部分结果，仅覆盖实际处理的范围。不要据此视为整份文件已处理。
        </p>
      )}
      {warnings.map((warning, index) => (
        <p className="file-partial-warning" key={`${warning}:${index}`}>
          {warning}
        </p>
      ))}
      {(artifact.kind === 'preview' || /^frame_\d{4}$/.test(artifact.kind)) &&
        artifact.mime_type === 'image/png' && (
          <img
            className="file-safe-image"
            src={filesApi.previewImageUrl(workspaceId, artifact.id)}
            alt={`${artifact.filename}，服务端生成的${artifact.kind.startsWith('frame_') ? '视频帧' : '安全预览'}`}
          />
        )}
      {/^frame_\d{4}$/.test(artifact.kind) && (
        <p className="file-frame-caption">
          视频帧 · 实际时间戳{' '}
          {typeof metadata.timestamp_seconds === 'number'
            ? `${metadata.timestamp_seconds} 秒`
            : '见来源元数据'}
          。这只是所选范围的静态帧，不代表完整或原生视频理解。
        </p>
      )}
      <Details title="来源与处理详情" value={artifact.metadata} />
      <div className="file-actions">
        <Button onClick={() => setShowPreview(true)} disabled={preview.isFetching}>
          <FileText size={14} />
          {preview.isFetching ? '正在读取…' : '查看文本与预览元数据'}
        </Button>
      </div>
      {showPreview &&
        (preview.isError ? (
          <FileReadError error={preview.error} retry={() => void preview.refetch()} />
        ) : preview.data ? (
          <div className="file-preview">
            <h3>当前可读内容</h3>
            {preview.data.text !== null ? (
              <pre>{preview.data.text}</pre>
            ) : (
              <p>当前无可读取文本。可查看真实元数据，或选择可用处理器生成派生结果。</p>
            )}
            {preview.data.truncated && (
              <p className="file-partial-warning">预览已截断，仅显示服务端返回的范围。</p>
            )}
            <Details title="预览来源与范围" value={preview.data.metadata} />
          </div>
        ) : null)}
      <ProcessControls
        key={artifact.id}
        artifact={artifact}
        capabilities={capabilities}
        busy={busy}
        blocked={blocked}
        onProcess={onProcess}
      />
    </>
  )
}
function TaskCard({
  workspaceId,
  task,
  filename,
  onSelect,
}: {
  workspaceId: string
  task: FileTask
  filename?: string
  onSelect: (id: string) => void
}) {
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestId = useRef<string | null>(null)
  async function cancel() {
    if (busy) return
    setBusy(true)
    setError('')
    requestId.current ??= crypto.randomUUID()
    try {
      const updated = await filesApi.cancel(workspaceId, task.id, requestId.current)
      client.setQueryData(['file-task', workspaceId, task.id], updated)
      client.setQueryData<FileTask[]>(['file-tasks', workspaceId], (old) =>
        old?.map((item) => (item.id === updated.id ? updated : item)),
      )
    } catch (err) {
      setError(describeError(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="file-task-card">
      <div className="file-task-heading">
        <div>
          <h3>{filename ?? '文件处理任务'}</h3>
          <p>{operationLabel(task.operation)}</p>
        </div>
        <div className="button-row">
          <Status
            tone={
              task.status === 'failed' ? 'danger' : task.status === 'succeeded' ? 'good' : 'neutral'
            }
          >
            {labels[task.status] ?? task.status}
          </Status>
          {!terminal.has(task.status) && (
            <Button disabled={busy || task.status === 'cancelling'} onClick={() => void cancel()}>
              <Square size={12} />
              {busy || task.status === 'cancelling' ? '正在确认停止…' : '停止处理'}
            </Button>
          )}
        </div>
      </div>
      {task.results.some(
        (result) => (result.metadata as Record<string, unknown>).complete === false,
      ) && (
        <p className="file-partial-warning">
          仅返回部分范围的结果，未处理内容和限制请打开派生件查看。
        </p>
      )}
      {task.error && typeof task.error.message === 'string' && (
        <p className="form-error" role="status">
          {task.error.message}
        </p>
      )}
      {task.error && <Details title="处理未完成：查看真实原因" value={task.error} />}
      <div className="file-task-results">
        {task.results.map((result) => (
          <button key={result.id} onClick={() => onSelect(result.id)}>
            <FileText size={14} />
            <span>{result.filename}</span>
            <span>{formatBytes(result.byte_size)}</span>
          </button>
        ))}
      </div>
      {task.status === 'cancelled' && (
        <p className="page-note">处理已停止；以上仅列出服务端确认保存的结果。</p>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
    </div>
  )
}
