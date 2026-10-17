import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { ApiError } from '../../api/client'
import { mcpApi } from '../../api/mcp'
import { closePendingRequest } from '../../api/requests'
import type { MCPSave, MCPView, MCPSnapshotView, RequestResult } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { plainJSON } from '../generated/validation'
import { MCPForm } from './MCPForm'
import './mcp.css'
type Pending = {
  kind: 'save' | 'discover'
  connectionId: string
  requestId: string
  expectedVersion: number
}
const pendingKey = (id: string) => `workbench.mcp-pending.${id}`
const draftKey = (id: string) => `workbench.mcp-new-id.${id}`
function readPending(id: string): Pending | null {
  try {
    const value = JSON.parse(localStorage.getItem(pendingKey(id)) ?? 'null') as Pending | null
    return value &&
      (value.kind === 'save' || value.kind === 'discover') &&
      typeof value.connectionId === 'string' &&
      typeof value.requestId === 'string' &&
      Number.isInteger(value.expectedVersion)
      ? value
      : null
  } catch {
    return null
  }
}
function isView(value: RequestResult): value is MCPView {
  return value !== null && 'configuration' in value && 'secret_names' in value
}
function isSnapshot(value: RequestResult): value is MCPSnapshotView {
  return value !== null && 'snapshot' in value
}
function Directory({
  view,
  connection,
  now,
}: {
  view: MCPSnapshotView
  connection: MCPView
  now: number
}) {
  const snapshot = view.snapshot
  const fresh =
    snapshot.connection_version === connection.version && Date.parse(snapshot.expires_at) > now
  return (
    <details className="mcp-directory">
      <summary>
        实际目录 · {snapshot.tools.length} 个工具 · {fresh ? '当前版本有效' : '已失效，需重新发现'}
      </summary>
      <dl className="run-meta">
        <dt>协议</dt>
        <dd>{snapshot.protocol_version}</dd>
        <dt>目录版本</dt>
        <dd>{snapshot.connection_version}</dd>
        <dt>发现时间</dt>
        <dd>{new Date(snapshot.discovered_at).toLocaleString()}</dd>
        <dt>有效至</dt>
        <dd>{new Date(snapshot.expires_at).toLocaleString()}</dd>
      </dl>
      {!fresh && <p className="form-error">此目录已过期或配置版本已改变，不能据此发起工具调用。</p>}
      <p className="page-note">
        远端 annotations
        仅是对方声明，不是工作台免批授权；调用仍受允许名单、精确授权与持久问题流程管理。
      </p>
      {snapshot.tools.length ? (
        snapshot.tools.map((tool) => (
          <details key={tool.name} className="mcp-tool">
            <summary>
              {tool.name}
              {connection.configuration.allowed_tools?.includes(tool.name)
                ? ' · 已在允许名单'
                : ' · 未允许'}
            </summary>
            {tool.description && <p>{tool.description}</p>}
            <p className="page-note">
              远端 requires_approval：{tool.requires_approval === false ? 'false' : 'true 或未声明'}
              。此值不取消宿主审批规则。
            </p>
            <details>
              <summary>输入 schema</summary>
              <pre>{plainJSON(tool.input_schema)}</pre>
            </details>
            {tool.output_schema && (
              <details>
                <summary>输出 schema</summary>
                <pre>{plainJSON(tool.output_schema)}</pre>
              </details>
            )}
            {tool.annotations && (
              <details>
                <summary>远端 annotations（非授权）</summary>
                <pre>{plainJSON(tool.annotations)}</pre>
              </details>
            )}
            <small>Schema digest：{tool.schema_digest}</small>
          </details>
        ))
      ) : (
        <p className="page-note">服务器实际返回空工具目录。</p>
      )}
      <details>
        <summary>服务身份、协商与能力</summary>
        <pre>
          {plainJSON({
            snapshot_id: snapshot.snapshot_id,
            identity_ref: snapshot.identity_ref,
            server_info: snapshot.server_info,
            server_capabilities: snapshot.server_capabilities,
            negotiation: snapshot.negotiation,
            sdk_version: snapshot.sdk_version,
            directory_digest: snapshot.directory_digest,
          })}
        </pre>
      </details>
    </details>
  )
}
export function MCPSection({ workspaceId, active }: { workspaceId: string; active: boolean }) {
  const client = useQueryClient()
  const listing = useQuery({
    queryKey: ['mcp-connections', workspaceId],
    queryFn: () => mcpApi.list(workspaceId),
    enabled: active,
  })
  const [editor, setEditor] = useState<{ id: string; initial?: MCPView } | null>(null)
  const [editorGeneration, setEditorGeneration] = useState(0)
  const [pending, setPending] = useState<Pending | null>(() => readPending(workspaceId))
  const savePayload = useRef<MCPSave | null>(null)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [message, setMessage] = useState('')
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [active])
  function store(value: Pending | null) {
    if (value) localStorage.setItem(pendingKey(workspaceId), JSON.stringify(value))
    else localStorage.removeItem(pendingKey(workspaceId))
    setPending(value)
  }
  async function refresh() {
    await client.invalidateQueries({ queryKey: ['mcp-connections', workspaceId] })
  }
  function added() {
    try {
      let id = localStorage.getItem(draftKey(workspaceId))
      if (!id) {
        id = crypto.randomUUID()
        localStorage.setItem(draftKey(workspaceId), id)
      }
      setEditor({ id, initial: listing.data?.connections.find((item) => item.id === id) })
      setConflict(false)
      setMessage('')
    } catch {
      setMessage('无法保存新连接的草稿标识，本次未创建连接。')
    }
  }
  function saved(view: MCPView) {
    client.setQueryData(
      ['mcp-connections', workspaceId],
      (old: Awaited<ReturnType<typeof mcpApi.list>> | undefined) => ({
        connections: [...(old?.connections ?? []).filter((item) => item.id !== view.id), view],
        snapshots: (old?.snapshots ?? []).filter((item) => item.snapshot.connection_id !== view.id),
      }),
    )
    if (localStorage.getItem(draftKey(workspaceId)) === view.id)
      localStorage.removeItem(draftKey(workspaceId))
    setEditor(null)
    setConflict(false)
    savePayload.current = null
    setMessage('MCP 配置已保存；旧目录失效，请明确点击发现以取得此版本目录。')
  }
  async function save(id: string, input: MCPSave) {
    if (busy) return
    if (pending) {
      setMessage('上次请求仍待确认，不能创建不同参数的新请求。')
      return
    }
    const command = structuredClone(input)
    try {
      store({
        kind: 'save',
        connectionId: id,
        requestId: command.request_id,
        expectedVersion: command.expected_version,
      })
    } catch {
      setMessage('无法保存待确认请求标识，本次未提交。')
      return
    }
    savePayload.current = command
    setBusy(true)
    setMessage('')
    try {
      const result = await mcpApi.save(workspaceId, id, command)
      store(null)
      saved(result)
      await refresh()
    } catch (error) {
      const uncertain = !(error instanceof ApiError && error.status < 500)
      if (!uncertain) {
        store(null)
        savePayload.current = null
        setConflict(error instanceof ApiError && error.status === 409)
      }
      setMessage(
        `${error instanceof Error ? error.message : '保存未完成'}${uncertain ? ' 结果未确认，保留原完整请求与标识，不能修改参数重发。' : ''}`,
      )
      throw error
    } finally {
      setBusy(false)
    }
  }
  async function retrySave() {
    if (!pending || pending.kind !== 'save' || !savePayload.current || busy) return
    setBusy(true)
    setMessage('')
    try {
      const result = await mcpApi.save(workspaceId, pending.connectionId, savePayload.current)
      store(null)
      saved(result)
      await refresh()
    } catch (error) {
      setMessage(
        `${error instanceof Error ? error.message : '保存结果未确认'} 原请求与字段继续保留，请先只读核对。`,
      )
    } finally {
      setBusy(false)
    }
  }
  async function discover(connection: MCPView) {
    if (busy || pending) return
    const command: Pending = {
      kind: 'discover',
      connectionId: connection.id,
      expectedVersion: connection.version,
      requestId: crypto.randomUUID(),
    }
    try {
      store(command)
    } catch {
      setMessage('无法保存发现请求标识，本次未启动程序或联网。')
      return
    }
    await replayDiscovery(command)
  }
  async function replayDiscovery(command: Pending) {
    if (busy) return
    setBusy(true)
    setMessage('')
    try {
      const result = await mcpApi.discover(workspaceId, command.connectionId, {
        expected_version: command.expectedVersion,
        request_id: command.requestId,
      })
      store(null)
      setMessage(
        `目录发现已收到完整回执：${result.snapshot.tools.length} 个工具。未自动调用业务工具。`,
      )
      await refresh()
    } catch (error) {
      setMessage(
        `${error instanceof Error ? error.message : '目录发现未确认'} 保留同一发现请求；不会因超时自动重开程序。请先读取服务端回执。`,
      )
    } finally {
      setBusy(false)
    }
  }
  async function reconcile() {
    if (!pending || busy) return
    const command = pending
    setBusy(true)
    setMessage('')
    try {
      const result = await mcpApi.receipt(command.requestId)
      if (command.kind === 'save' && isView(result) && result.id === command.connectionId) {
        store(null)
        saved(result)
        await refresh()
      } else if (
        command.kind === 'discover' &&
        isSnapshot(result) &&
        result.snapshot.connection_id === command.connectionId &&
        result.snapshot.connection_version === command.expectedVersion
      ) {
        store(null)
        setMessage('本次目录发现已确认；未发起新的发现或业务调用。')
        await refresh()
      } else if (
        result &&
        'status' in result &&
        result.status === 'failed' &&
        'message' in result
      ) {
        store(null)
        savePayload.current = null
        setMessage(`服务端已记录本次失败：${result.message}。新的发现需要你明确点击。`)
        await refresh()
      } else if (result && 'status' in result && result.status === 'in_progress') {
        setMessage('服务端仍记录本次发现执行中，结果尚未确认；未重新启动程序。')
      } else setMessage('回执尚不足以确认本次操作，保持原标识，未重新执行。')
    } catch (error) {
      setMessage(
        `${error instanceof Error ? error.message : '回执读取未完成'} 未改参重发，也未启动新的程序。`,
      )
    } finally {
      setBusy(false)
    }
  }
  async function closePending() {
    if (!pending || busy) return
    setBusy(true)
    try {
      const result = await closePendingRequest(pending.requestId)
      if (result.status === 'sealed') {
        store(null)
        savePayload.current = null
        setEditor(null)
        setMessage(
          '此请求已停止接收，迟到的原请求不能再执行。请读取当前配置后重新填写；此操作不回滚此前效果。',
        )
        await refresh()
      } else setMessage('服务端已有此请求的记录，请继续读取本次回执；未重新执行。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '请求关闭未确认，请继续核对。')
    } finally {
      setBusy(false)
    }
  }
  const editSnapshot = editor
    ? listing.data?.snapshots.find((item) => item.snapshot.connection_id === editor.id)
    : undefined
  return (
    <section className="section-card mcp-section">
      <div className="section-title">
        <h2>MCP 工具连接</h2>
        <div className="button-row">
          <Button
            disabled={!active || listing.isFetching || busy}
            onClick={() => void listing.refetch()}
          >
            <RefreshCw size={14} />
            读取配置
          </Button>
          <Button disabled={!listing.data || busy || !!pending || !!editor} onClick={added}>
            添加 MCP 连接
          </Button>
        </div>
      </div>
      <div className="mcp-section-body">
        <p className="page-note">
          工作区独立配置。stdio 会运行服务端原生程序，没有 OS 隔离；HTTP
          会访问你明确确认的端点。保存不触发连接，发现需要单独操作。
        </p>
        {pending && (
          <div className="memory-conflict">
            <strong>{pending.kind === 'save' ? '配置保存' : '目录发现'}请求待确认</strong>
            <p>
              请求 {pending.requestId} · 配置 {pending.connectionId} · 版本{' '}
              {pending.expectedVersion}
            </p>
            <p>
              只持久保留非秘密标识；完整保存内容与密钥仅在当前表单内存。刷新丢失后不能猜测原参数或自动重发。
            </p>
            <div className="button-row">
              <Button disabled={busy} onClick={() => void reconcile()}>
                只读核对本次结果
              </Button>
              <Button disabled={busy} onClick={() => void closePending()}>
                关闭未受理请求
              </Button>
              {pending.kind === 'save' && savePayload.current && (
                <Button disabled={busy} onClick={() => void retrySave()}>
                  原样重试保存（同标识）
                </Button>
              )}
              {pending.kind === 'discover' && (
                <Button disabled={busy} onClick={() => void replayDiscovery(pending)}>
                  核对发现（同标识）
                </Button>
              )}
            </div>
            {pending.kind === 'save' && !savePayload.current && (
              <p className="page-note">
                原密钥或配置内容已不在内存，不能再次发送保存；请读取服务端回执确认。
              </p>
            )}
          </div>
        )}
        {message && (
          <p role="status" className="mcp-message">
            {message}
          </p>
        )}
        {listing.isError ? (
          <div role="alert">
            <p className="form-error">{listing.error.message}</p>
            <Button onClick={() => void listing.refetch()}>重新读取 MCP</Button>
          </div>
        ) : listing.data ? (
          listing.data.connections.length ? (
            <div className="mcp-connection-list">
              {listing.data.connections.map((connection) => {
                const snapshot = listing.data.snapshots.find(
                  (item) => item.snapshot.connection_id === connection.id,
                )
                const valid =
                  snapshot &&
                  snapshot.snapshot.connection_version === connection.version &&
                  Date.parse(snapshot.snapshot.expires_at) > now
                return (
                  <article key={connection.id} className="mcp-connection">
                    <div className="run-line">
                      <div>
                        <h3>{connection.configuration.name}</h3>
                        <p>
                          {connection.configuration.transport === 'stdio'
                            ? connection.configuration.command
                            : connection.configuration.endpoint}
                        </p>
                      </div>
                      <Status>
                        {connection.configuration.enabled ? '已启用' : '已禁用'} ·{' '}
                        {valid ? '目录有效' : snapshot ? '目录已过期' : '尚无当前目录'}
                      </Status>
                    </div>
                    <p className="page-note">
                      版本 {connection.version} ·{' '}
                      {connection.execution_authorized ? '已确认精确连接目标' : '未授权建立连接'} ·
                      已保存凭据名称：
                      {connection.secret_names.length ? connection.secret_names.join('、') : '无'}
                    </p>
                    <div className="button-row">
                      <Button
                        disabled={busy || !!pending || !!editor}
                        onClick={() => {
                          setEditor({ id: connection.id, initial: connection })
                          setConflict(false)
                          setMessage('')
                        }}
                      >
                        编辑 MCP 配置
                      </Button>
                      <Button
                        disabled={
                          busy ||
                          !!pending ||
                          !!editor ||
                          !connection.configuration.enabled ||
                          !connection.execution_authorized
                        }
                        onClick={() => void discover(connection)}
                      >
                        {connection.configuration.transport === 'stdio'
                          ? '启动程序并发现工具'
                          : '连接端点并发现工具'}
                      </Button>
                    </div>
                    {snapshot ? (
                      <Directory view={snapshot} connection={connection} now={now} />
                    ) : (
                      <p className="page-note">
                        此配置版本尚无目录；修改保存会使旧目录失效，需明确重新发现。
                      </p>
                    )}
                  </article>
                )
              })}
            </div>
          ) : (
            <p className="page-note">尚无 MCP 连接。添加真实程序或网络服务后，再明确发现目录。</p>
          )
        ) : (
          <p className="page-note">正在读取 MCP 连接…</p>
        )}
        {editor && (
          <MCPForm
            key={`${editor.id}:${editorGeneration}`}
            connectionId={editor.id}
            initial={editor.initial}
            snapshot={editSnapshot}
            locked={busy || !!pending}
            conflict={conflict}
            onSave={(input) => save(editor.id, input)}
            onCancel={() => {
              if (!busy && !pending) {
                setEditor(null)
                savePayload.current = null
                setConflict(false)
              }
            }}
            onReload={async () => {
              if (busy || pending) return
              const result = await listing.refetch()
              if (result.data && !result.isError) {
                const current = result.data.connections.find((item) => item.id === editor.id)
                setEditor({ id: editor.id, initial: current })
                setConflict(false)
                setEditorGeneration((value) => value + 1)
              }
            }}
          />
        )}
        <p className="page-note">
          目录发现不等于业务工具已验证。调用若只收到部分响应（partial
          response）或结果未知，不能因断线当作普通失败自动重放；实际操作、问题与授权在任务面板核对。
        </p>
      </div>
    </section>
  )
}
