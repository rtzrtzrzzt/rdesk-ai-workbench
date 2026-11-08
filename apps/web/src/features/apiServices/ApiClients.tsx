import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import {
  agentServicesApi,
  type AgentService,
  type ApiClient,
  type ApiGrant,
} from '../../api/agentServices'
import { Button, Status } from '../../components/ui'
import { useSettingsAttention } from '../settings/SettingsGroup'

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '操作结果需要核对。'
const actions = [
  { id: 'invoke', label: '发起任务' },
  { id: 'read', label: '读取结果' },
  { id: 'write', label: '补充输入与控制任务' },
  { id: 'files', label: '上传和下载文件' },
]

export function ApiClients({ active, services }: { active: boolean; services: AgentService[] }) {
  const queryClient = useQueryClient()
  const clients = useQuery({
    queryKey: ['api-clients'],
    queryFn: ({ signal }) => agentServicesApi.clients(signal),
    enabled: active,
  })
  const [selectedId, setSelectedId] = useState('')
  const [name, setName] = useState('')
  const [rpm, setRpm] = useState('120')
  const [maxRuns, setMaxRuns] = useState('3')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pending = useRef<Parameters<typeof agentServicesApi.createClient>[0] | null>(null)
  useSettingsAttention('api-client-create', adding || !!error, 'API 客户端有未保存或待核对的修改')
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['api-clients'] })
  return (
    <section className="api-service-section">
      <div className="section-title">
        <h3>客户端与授权</h3>
        <Button disabled={adding} onClick={() => setAdding(true)}>
          添加客户端
        </Button>
      </div>
      <p className="page-note">
        为每个外部应用单独分配密钥和服务权限。新客户端需获得授权后才能使用服务。
      </p>
      {clients.isError && (
        <div role="alert">
          <p className="form-error">客户端列表读取失败。</p>
          <Button onClick={() => void clients.refetch()}>重新读取客户端</Button>
        </div>
      )}
      {clients.isPending && <p className="page-note">正在读取客户端…</p>}
      {adding && (
        <form
          className="api-service-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (busy) return
            pending.current ??= {
              request_id: crypto.randomUUID(),
              name: name.trim(),
              requests_per_minute: Number(rpm),
              max_active_runs: Number(maxRuns),
            }
            setBusy(true)
            setError('')
            void agentServicesApi
              .createClient(pending.current)
              .then(async (client) => {
                pending.current = null
                setAdding(false)
                setName('')
                setError('')
                setSelectedId(client.id)
                await refresh()
              })
              .catch((err) => {
                if (err instanceof ApiError && err.status >= 400 && err.status < 500)
                  pending.current = null
                setError(errorText(err))
              })
              .finally(() => setBusy(false))
          }}
        >
          <fieldset disabled={busy || !!pending.current}>
            <label>
              应用名称
              <input
                required
                maxLength={160}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <div className="api-form-grid">
              <label>
                每分钟请求上限
                <input
                  required
                  type="number"
                  min={1}
                  max={6000}
                  step={1}
                  value={rpm}
                  onChange={(event) => setRpm(event.target.value)}
                />
              </label>
              <label>
                同时运行任务上限
                <input
                  required
                  type="number"
                  min={1}
                  max={32}
                  step={1}
                  value={maxRuns}
                  onChange={(event) => setMaxRuns(event.target.value)}
                />
              </label>
            </div>
          </fieldset>
          <div className="button-row">
            <Button type="submit" variant="primary" disabled={busy || !name.trim()}>
              {busy ? '正在保存…' : pending.current ? '核对本次创建' : '创建客户端'}
            </Button>
            <Button
              disabled={busy || !!pending.current}
              onClick={() => {
                setAdding(false)
                setName('')
                setError('')
              }}
            >
              取消
            </Button>
          </div>
        </form>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="api-client-list">
        {clients.data?.map((client) => (
          <button
            type="button"
            className={client.id === selectedId ? 'selected' : ''}
            key={client.id}
            onClick={() => setSelectedId(client.id)}
          >
            <span>{client.name}</span>
            <Status tone={client.enabled ? 'good' : 'neutral'}>
              {client.enabled ? '启用' : '停用'}
            </Status>
          </button>
        ))}
      </div>
      {clients.data?.length === 0 && <p className="page-note">尚未添加客户端。</p>}
      {clients.data?.map((client) => (
        <div key={client.id} hidden={client.id !== selectedId}>
          <ClientDetails
            client={client}
            services={services}
            active={active && client.id === selectedId}
            onRefresh={refresh}
          />
        </div>
      ))}
    </section>
  )
}

function ClientDetails({
  client,
  services,
  active,
  onRefresh,
}: {
  client: ApiClient
  services: AgentService[]
  active: boolean
  onRefresh: () => Promise<void>
}) {
  const [draft, setDraft] = useState<ApiClient | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useSettingsAttention(
    `api-client:${client.id}`,
    !!draft || !!error,
    '客户端设置有未保存或待核对的修改',
  )
  return (
    <div className="api-client-details">
      <div className="section-title">
        <h4>{client.name}</h4>
        <Button disabled={!!draft} onClick={() => setDraft({ ...client })}>
          编辑限额与状态
        </Button>
      </div>
      <p className="page-note">
        每分钟 {client.requests_per_minute} 次请求 · 同时 {client.max_active_runs} 个任务
      </p>
      {draft && (
        <form
          className="api-service-form"
          onSubmit={(event) => {
            event.preventDefault()
            if (busy) return
            setBusy(true)
            setError('')
            void agentServicesApi
              .updateClient(client.id, {
                enabled: draft.enabled,
                requests_per_minute: draft.requests_per_minute,
                max_active_runs: draft.max_active_runs,
                expected_version: draft.version,
              })
              .then(async () => {
                await onRefresh()
                setDraft(null)
              })
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false))
          }}
        >
          <fieldset disabled={busy}>
            <label className="api-checkbox">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })}
              />
              启用此客户端
            </label>
            <div className="api-form-grid">
              <label>
                每分钟请求上限
                <input
                  required
                  type="number"
                  min={1}
                  max={6000}
                  step={1}
                  value={draft.requests_per_minute}
                  onChange={(event) =>
                    setDraft({ ...draft, requests_per_minute: Number(event.target.value) })
                  }
                />
              </label>
              <label>
                同时运行任务上限
                <input
                  required
                  type="number"
                  min={1}
                  max={32}
                  step={1}
                  value={draft.max_active_runs}
                  onChange={(event) =>
                    setDraft({ ...draft, max_active_runs: Number(event.target.value) })
                  }
                />
              </label>
            </div>
          </fieldset>
          {error && (
            <p className="form-error" role="alert">
              {error} 请读取最新状态后核对。
            </p>
          )}
          <div className="button-row">
            <Button type="submit" variant="primary" disabled={busy || !!error}>
              保存
            </Button>
            <Button
              disabled={busy}
              onClick={() => {
                void onRefresh().then(() => {
                  setError('')
                  setDraft(null)
                })
              }}
            >
              {error ? '读取最新状态' : '取消'}
            </Button>
          </div>
        </form>
      )}
      <ClientKeys clientId={client.id} active={active} />
      <ClientGrants clientId={client.id} services={services} active={active} />
    </div>
  )
}

function ClientKeys({ clientId, active }: { clientId: string; active: boolean }) {
  const keys = useQuery({
    queryKey: ['api-client-keys', clientId],
    queryFn: ({ signal }) => agentServicesApi.keys(clientId, signal),
    enabled: active,
  })
  const [secret, setSecret] = useState<string | null>(null)
  const [expiry, setExpiry] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const pending = useRef<{ request_id: string; expires_at: string | null } | null>(null)
  const revokeIds = useRef<Record<string, string>>({})
  useSettingsAttention(
    `api-keys:${clientId}`,
    !!secret || !!error,
    secret ? '新 API 密钥尚未确认保存' : 'API 密钥操作需要核对',
  )
  return (
    <section className="api-client-subsection">
      <h4>访问密钥</h4>
      <p className="page-note">密钥仅在生成时显示一次，请保存到外部应用的安全配置中。</p>
      <div className="api-key-create">
        <label>
          到期时间（可选）
          <input
            type="datetime-local"
            value={expiry}
            disabled={busy || !!pending.current}
            onChange={(event) => setExpiry(event.target.value)}
          />
        </label>
        <Button
          disabled={busy || !!secret}
          onClick={() => {
            if (busy) return
            pending.current ??= {
              request_id: crypto.randomUUID(),
              expires_at: expiry ? new Date(expiry).toISOString() : null,
            }
            setBusy(true)
            setError('')
            setMessage('')
            void agentServicesApi
              .issueKey(clientId, pending.current)
              .then(async (result) => {
                pending.current = null
                setSecret(result.key)
                if (!result.key)
                  setMessage(
                    '本次密钥已生成，明文无法再次显示。若尚未保存，请撤销此密钥后重新生成。',
                  )
                await keys.refetch()
              })
              .catch((err) => {
                if (err instanceof ApiError && err.status >= 400 && err.status < 500)
                  pending.current = null
                setError(errorText(err))
              })
              .finally(() => setBusy(false))
          }}
        >
          {pending.current ? '核对本次生成' : '生成密钥'}
        </Button>
      </div>
      {secret && (
        <div className="api-once-key">
          <strong>新密钥 · 仅显示一次</strong>
          <code>{secret}</code>
          <div className="button-row">
            <Button
              onClick={() =>
                void navigator.clipboard
                  .writeText(secret)
                  .then(() => setMessage('密钥已复制。'))
                  .catch(() => setMessage('无法自动复制，请选中密钥手动复制。'))
              }
            >
              复制密钥
            </Button>
            <Button
              onClick={() => {
                setSecret(null)
                setMessage('密钥已隐藏。')
              }}
            >
              已保存，隐藏密钥
            </Button>
          </div>
        </div>
      )}
      {message && (
        <p className="page-note" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {keys.isError && (
        <div role="alert">
          <p className="form-error">密钥列表读取失败。</p>
          <Button onClick={() => void keys.refetch()}>重新读取密钥</Button>
        </div>
      )}
      {keys.isPending && <p className="page-note">正在读取密钥…</p>}
      {keys.data?.length === 0 && <p className="page-note">尚未生成访问密钥。</p>}
      {keys.data?.map((key) => (
        <div className="api-service-row" key={key.id}>
          <div>
            <code>{key.prefix}…</code>
            <p>
              {key.revoked_at
                ? '已撤销'
                : key.expires_at && Date.parse(key.expires_at) <= Date.now()
                  ? '已到期'
                  : '有效'}
              {key.expires_at
                ? ` · 到期 ${new Date(key.expires_at).toLocaleString()}`
                : ' · 无到期时间'}
            </p>
          </div>
          <Button
            disabled={busy || !!key.revoked_at}
            onClick={() => {
              setBusy(true)
              setError('')
              revokeIds.current[key.id] ??= crypto.randomUUID()
              void agentServicesApi
                .revokeKey(clientId, key.id, revokeIds.current[key.id])
                .then(async () => {
                  await keys.refetch()
                  if (secret?.startsWith(key.prefix)) setSecret(null)
                })
                .catch((err) => setError(errorText(err)))
                .finally(() => setBusy(false))
            }}
          >
            撤销密钥
          </Button>
        </div>
      ))}
    </section>
  )
}

function ClientGrants({
  clientId,
  services,
  active,
}: {
  clientId: string
  services: AgentService[]
  active: boolean
}) {
  const grants = useQuery({
    queryKey: ['api-client-grants', clientId],
    queryFn: ({ signal }) => agentServicesApi.grants(clientId, signal),
    enabled: active,
  })
  const [editing, setEditing] = useState<{ service: AgentService; grant: ApiGrant | null } | null>(
    null,
  )
  useSettingsAttention(`api-grants:${clientId}`, !!editing, '服务授权有未保存的修改')
  return (
    <section className="api-client-subsection">
      <h4>服务授权</h4>
      {grants.isPending && <p className="page-note">正在读取服务授权…</p>}
      {grants.isError && (
        <div role="alert">
          <p className="form-error">服务授权读取失败。</p>
          <Button onClick={() => void grants.refetch()}>重新读取授权</Button>
        </div>
      )}
      {services.map((service) => {
        const grant = grants.data?.find((item) => item.service_id === service.id)
        return (
          <div className="api-service-row" key={service.id}>
            <div>
              <strong>{service.name}</strong>
              <p>
                {grant?.enabled ? '已授权' : '未授权'}
                {!service.enabled ? ' · 服务已停用' : ''}
              </p>
            </div>
            <Button
              disabled={!grants.data || !!editing}
              onClick={() => setEditing({ service, grant: grant ?? null })}
            >
              配置授权
            </Button>
          </div>
        )
      })}
      {!services.length && <p className="page-note">发布服务后，可在这里分配调用权限。</p>}
      {editing && (
        <GrantForm
          key={editing.service.id}
          clientId={clientId}
          service={editing.service}
          initial={editing.grant}
          onClose={() => {
            setEditing(null)
            void grants.refetch()
          }}
          onSaved={async () => {
            await grants.refetch()
            setEditing(null)
          }}
        />
      )}
    </section>
  )
}

function GrantForm({
  clientId,
  service,
  initial,
  onClose,
  onSaved,
}: {
  clientId: string
  service: AgentService
  initial: ApiGrant | null
  onClose: () => void
  onSaved: () => Promise<void>
}) {
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [allowedActions, setAllowedActions] = useState(initial?.actions ?? ['invoke', 'read'])
  const [customTools, setCustomTools] = useState(
    initial?.allowed_tools !== null && initial?.allowed_tools !== undefined,
  )
  const [tools, setTools] = useState(initial?.allowed_tools ?? [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <form
      className="api-service-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (busy) return
        setBusy(true)
        setError('')
        void agentServicesApi
          .saveGrant(clientId, service.id, {
            expected_version: initial?.version ?? 0,
            enabled,
            actions: allowedActions,
            allowed_tools: customTools ? tools : null,
          })
          .then(onSaved)
          .catch((err) => setError(errorText(err)))
          .finally(() => setBusy(false))
      }}
    >
      <h4>{service.name} · 客户端权限</h4>
      <fieldset disabled={busy || !!error}>
        <label className="api-checkbox">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          启用此授权
        </label>
        <div className="api-action-options">
          {actions.map((action) => (
            <label className="api-checkbox" key={action.id}>
              <input
                type="checkbox"
                checked={allowedActions.includes(action.id)}
                onChange={(event) =>
                  setAllowedActions(
                    event.target.checked
                      ? [...allowedActions, action.id]
                      : allowedActions.filter((id) => id !== action.id),
                  )
                }
              />
              {action.label}
            </label>
          ))}
        </div>
        <label className="api-checkbox">
          <input
            type="checkbox"
            checked={customTools}
            onChange={(event) => setCustomTools(event.target.checked)}
          />
          进一步限制此客户端可用的工具
        </label>
        {customTools && (
          <div className="api-action-options">
            {service.definition.allowed_tools.map((tool) => (
              <label className="api-checkbox" key={tool}>
                <input
                  type="checkbox"
                  checked={tools.includes(tool)}
                  onChange={(event) =>
                    setTools(
                      event.target.checked
                        ? [...tools, tool]
                        : tools.filter((name) => name !== tool),
                    )
                  }
                />
                {tool}
              </label>
            ))}
          </div>
        )}
        <p className="page-note">授权始终受服务本身的工具范围约束。</p>
      </fieldset>
      {error && (
        <p className="form-error" role="alert">
          {error} 请重新读取授权后核对。
        </p>
      )}
      <div className="button-row">
        <Button type="submit" variant="primary" disabled={busy || !!error}>
          保存授权
        </Button>
        <Button disabled={busy} onClick={onClose}>
          取消
        </Button>
      </div>
    </form>
  )
}
