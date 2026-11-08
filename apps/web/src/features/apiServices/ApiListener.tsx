import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { agentServicesApi, type ListenerSettings } from '../../api/agentServices'
import { Button, Status } from '../../components/ui'
import { useSettingsAttention } from '../settings/SettingsGroup'

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '操作结果需要核对。'

export function ApiListener({ active }: { active: boolean }) {
  const listener = useQuery({
    queryKey: ['agent-api-listener'],
    queryFn: ({ signal }) => agentServicesApi.listener(signal),
    enabled: active,
    refetchInterval: active ? 5000 : false,
  })
  const [draft, setDraft] = useState<ListenerSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  useSettingsAttention(
    'agent-api-listener',
    !!draft || !!error,
    'API 监听配置有未保存或待核对的修改',
  )
  const value = listener.data
  async function reload() {
    const result = await listener.refetch()
    if (!result.error) {
      setError('')
      setDraft(null)
    }
  }
  return (
    <section className="api-service-section">
      <div className="section-title">
        <h3>API 访问地址</h3>
        {value && (
          <Status tone={value.running ? 'good' : 'neutral'}>
            {value.running ? '运行中' : '未启动'}
          </Status>
        )}
      </div>
      <p className="page-note">
        独立的 API 服务只提供已发布的能力，使用客户端密钥访问。首次使用需保存配置并启动。
      </p>
      {listener.isPending && <p className="page-note">正在读取 API 监听配置…</p>}
      {listener.isError && (
        <div role="alert">
          <p className="form-error">API 监听配置读取失败。</p>
          <Button onClick={() => void reload()}>重新读取配置</Button>
        </div>
      )}
      {value && (
        <>
          <div className="api-listener-address">
            <span>{value.running && value.url ? value.url : `${value.host}:${value.port}`}</span>
            <Status>{value.enabled ? '已允许启动' : '已关闭'}</Status>
          </div>
          {value.message && <p className="page-note">{value.message}</p>}
          {!draft && (
            <div className="button-row">
              <Button
                disabled={busy || value.running}
                onClick={() => {
                  setError('')
                  setDraft({ ...value })
                }}
              >
                编辑访问配置
              </Button>
              <Button
                variant={value.running ? 'secondary' : 'primary'}
                disabled={busy || !!error || (!value.running && !value.enabled)}
                onClick={() => {
                  if (busy) return
                  setBusy(true)
                  setError('')
                  setMessage('')
                  void agentServicesApi
                    .listenerState(!value.running, crypto.randomUUID())
                    .then(async () => {
                      await listener.refetch()
                      setMessage(value.running ? 'API 服务已停止。' : '已提交 API 服务启动请求。')
                    })
                    .catch((err) => setError(errorText(err)))
                    .finally(() => setBusy(false))
                }}
              >
                {busy ? '正在处理…' : value.running ? '停止 API 服务' : '启动 API 服务'}
              </Button>
            </div>
          )}
          {value.running && <p className="page-note">修改访问配置前，请先停止 API 服务。</p>}
        </>
      )}
      {draft && (
        <form
          className="api-service-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (busy) return
            setBusy(true)
            setError('')
            setMessage('')
            void agentServicesApi
              .saveListener({
                expected_version: draft.version,
                enabled: draft.enabled,
                host: draft.host.trim(),
                port: draft.port,
                public_hosts: draft.public_hosts.map((host) => host.trim()).filter(Boolean),
                ssl_certfile: draft.ssl_certfile?.trim() || null,
                ssl_keyfile: draft.ssl_keyfile?.trim() || null,
              })
              .then(async () => {
                await listener.refetch()
                setDraft(null)
                setMessage('访问配置已保存。')
              })
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false))
          }}
        >
          <fieldset disabled={busy || !!error}>
            <label className="api-checkbox">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              允许启动 API 服务
            </label>
            <div className="api-form-grid">
              <label>
                监听地址
                <input
                  required
                  value={draft.host}
                  onChange={(event) => setDraft({ ...draft, host: event.target.value })}
                />
              </label>
              <label>
                端口
                <input
                  required
                  type="number"
                  min={1}
                  max={65535}
                  step={1}
                  value={draft.port}
                  onChange={(event) => setDraft({ ...draft, port: Number(event.target.value) })}
                />
              </label>
            </div>
            <label>
              允许的访问域名或 IP（每行一项）
              <textarea
                rows={3}
                placeholder="api.example.com:443"
                value={draft.public_hosts.join('\n')}
                onChange={(event) =>
                  setDraft({ ...draft, public_hosts: event.target.value.split('\n') })
                }
              />
            </label>
            <details>
              <summary>HTTPS 证书（可选）</summary>
              <label>
                证书文件路径
                <input
                  value={draft.ssl_certfile ?? ''}
                  onChange={(event) => setDraft({ ...draft, ssl_certfile: event.target.value })}
                />
              </label>
              <label>
                私钥文件路径
                <input
                  value={draft.ssl_keyfile ?? ''}
                  onChange={(event) => setDraft({ ...draft, ssl_keyfile: event.target.value })}
                />
              </label>
              <p className="page-note">
                填写本机已有文件路径；对外监听需要 HTTPS
                证书和私钥。通过本机反向代理访问时，可由代理提供 HTTPS。
              </p>
            </details>
          </fieldset>
          <div className="button-row">
            <Button type="submit" variant="primary" disabled={busy || !!error}>
              保存访问配置
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                if (error) void reload()
                else setDraft(null)
              }}
            >
              {error ? '读取最新配置' : '取消'}
            </Button>
          </div>
        </form>
      )}
      {error && (
        <div role="alert">
          <p className="form-error">{error}</p>
          {!draft && (
            <Button disabled={busy} onClick={() => void reload()}>
              核对当前状态
            </Button>
          )}
        </div>
      )}
      {message && (
        <p className="page-note" role="status">
          {message}
        </p>
      )}
    </section>
  )
}
