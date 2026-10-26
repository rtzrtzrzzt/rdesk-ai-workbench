import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import { mediaApi, type MediaConnectionView, type MediaConnections } from '../../api/media'
import type { Workspace } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import { MediaConnectionForm } from './MediaConnectionForm'
import { capabilities, describeError, drivers } from './shared'
import './media.css'

export function MediaConnectionsPanel({
  workspace,
  active = true,
}: {
  workspace: Workspace
  active?: boolean
}) {
  const client = useQueryClient()
  const [editor, setEditor] = useState<{ initial?: MediaConnectionView } | null>(null)
  const [editorLocked, setEditorLocked] = useState(false)
  const [checking, setChecking] = useState<string | null>(null)
  const checkInFlight = useRef(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const queryKey = ['media-connections', workspace.id]
  const connections = useQuery({
    queryKey,
    queryFn: ({ signal }) => mediaApi.connections(workspace.id, signal),
    enabled: active,
    retry: false,
  })
  async function check(connection: MediaConnectionView) {
    if (checkInFlight.current) return
    checkInFlight.current = true
    setChecking(connection.id)
    setMessage('')
    setFailure(false)
    try {
      const result = await mediaApi.checkConnection(workspace.id, connection.id)
      client.setQueryData<MediaConnections>(queryKey, (previous) =>
        previous
          ? {
              ...previous,
              connections: previous.connections.map((item) =>
                item.id === result.id ? result : item,
              ),
            }
          : previous,
      )
      setMessage('配置检查已返回，检查结果显示在对应连接下；尚未发起生成。')
    } catch (error) {
      setFailure(true)
      setMessage(describeError(error))
    } finally {
      checkInFlight.current = false
      setChecking(null)
    }
  }
  return (
    <section className="media-connections-panel">
      <div className="button-row media-panel-actions">
        <Button disabled={connections.isFetching} onClick={() => void connections.refetch()}>
          <RefreshCw size={14} />
          刷新
        </Button>
        <Button variant="primary" onClick={() => setEditor({})}>
          <Plus size={14} />
          接入生成模型
        </Button>
      </div>
      <p className="page-note">
        为图像、视频、音频或 3D 选择对应的生成服务；可以在独立生成页直接使用。
      </p>
      {connections.error && (
        <p className="form-error" role="alert">
          {connections.error.message}
        </p>
      )}
      {message && (
        <p className={failure ? 'form-error' : 'page-note'} role={failure ? 'alert' : 'status'}>
          {message}
        </p>
      )}
      {connections.isPending ? (
        <p className="page-note">正在读取生成连接…</p>
      ) : connections.data?.connections.length ? (
        <div className="media-connection-list">
          {connections.data.connections.map((connection) => (
            <article className="media-connection" key={connection.id}>
              <div>
                <h3>{connection.name}</h3>
                <p>
                  {capabilities.find((item) => item.id === connection.capability)?.noun ??
                    connection.capability}{' '}
                  ·{' '}
                  {drivers.find((item) => item.id === connection.driver)?.label ??
                    connection.driver}{' '}
                  · {connection.scope === 'workspace' ? '当前工作区' : '全局'}
                  {connection.is_default && ' · 默认连接'}
                </p>
                <p>
                  检查结果：
                  {connection.verification === 'unverified'
                    ? '未验证'
                    : connection.verification === 'files_checked'
                      ? '本机必需文件已检查，尚未验证生成'
                      : connection.verification === 'generation_verified'
                        ? '已有成功生成记录'
                        : connection.verification === 'connection_checked'
                          ? '连接声明已检查，尚未验证生成'
                          : connection.verification}
                </p>
              </div>
              <div className="button-row">
                <Status tone={connection.enabled ? 'good' : 'neutral'}>
                  {connection.enabled ? '已启用' : '已停用'}
                </Status>
                <Button onClick={() => setEditor({ initial: connection })}>编辑</Button>
                <Button disabled={!!checking} onClick={() => void check(connection)}>
                  {checking === connection.id ? '正在检查…' : '检查配置'}
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        !connections.error && (
          <div className="inline-empty">
            <h3>没有模型接入</h3>
            <p>选择接入方式并保存真实生成连接后即可使用。</p>
          </div>
        )
      )}
      {editor && (
        <Dialog
          title={editor.initial ? '编辑生成连接' : '接入生成模型'}
          onClose={() => {
            if (!editorLocked) setEditor(null)
          }}
        >
          <MediaConnectionForm
            workspaceId={workspace.id}
            initial={editor.initial}
            native={connections.data?.native}
            onLockChange={setEditorLocked}
            onCancel={() => setEditor(null)}
            onSaved={async () => {
              setEditor(null)
              setEditorLocked(false)
              setMessage('连接已保存，尚未发起生成。')
              setFailure(false)
              await client.invalidateQueries({ queryKey: ['media-connections'] })
            }}
          />
        </Dialog>
      )}
    </section>
  )
}
