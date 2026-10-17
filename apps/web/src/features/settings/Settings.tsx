import { useState } from 'react'
import { SettingsGroup } from './SettingsGroup'
import './settings.css'
import { Monitor, Moon, Sun, Shield, Server, RefreshCw } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { ModelForm } from './ModelForm'
import { Capabilities } from './Capabilities'
import { RuntimeDefaults } from './RuntimeDefaults'
import { Maintenance } from './Maintenance'
import { Vault } from './Vault'
import { AccountSecurity } from './AccountSecurity'
import { WorkspaceRuntimeDefaults } from './WorkspaceRuntimeDefaults'
import { Diagnostics } from './Diagnostics'
import type {
  ModelInput,
  ModelConnection,
  ModelUpdate,
  CapabilitiesUpdate,
  Workspace,
} from '../../api/types'
import { Button, PageHeading, Status } from '../../components/ui'

export function SettingsPage({
  userId,
  workspace,
  models = [],
}: {
  userId?: string
  workspace?: Workspace
  models?: ModelConnection[]
}) {
  const [theme, setTheme] = useState(() => localStorage.getItem('workbench.theme') ?? 'system')
  function changeTheme(value: string) {
    setTheme(value)
    localStorage.setItem('workbench.theme', value)
    document.documentElement.dataset.theme = value
  }
  return (
    <div className="page-content settings-page">
      <PageHeading title="设置">调整工作台的外观与使用偏好。</PageHeading>
      <section className="settings-section">
        <div>
          <h2>外观</h2>
          <p>仅影响当前浏览器，立即生效。</p>
        </div>
        <div className="segmented" role="group" aria-label="外观主题">
          {[
            { id: 'light', name: '浅色', icon: Sun },
            { id: 'dark', name: '深色', icon: Moon },
            { id: 'system', name: '跟随系统', icon: Monitor },
          ].map((item) => (
            <button
              key={item.id}
              aria-pressed={theme === item.id}
              onClick={() => changeTheme(item.id)}
            >
              <item.icon size={15} />
              {item.name}
            </button>
          ))}
        </div>
      </section>
      {userId && (
        <>
          <SettingsGroup title="任务偏好" description="为新任务选择默认模型、执行方式与协作规模。">
            <RuntimeDefaults models={models} />
            {workspace && (
              <WorkspaceRuntimeDefaults key={workspace.id} workspace={workspace} models={models} />
            )}
          </SettingsGroup>
          <SettingsGroup title="连接与安全" description="账号、登录会话与模型凭据保护。">
            <AccountSecurity key={`account-${userId}`} userId={userId} />
            <Vault key={`vault-${userId}`} userId={userId} />
            <section className="settings-section settings-guidance">
              <div>
                <h2>工作区与权限</h2>
                <p>
                  文件目录、外部传输和工具权限由当前工作区管理。模型服务在「模型与连接」中配置。
                </p>
              </div>
              <Shield size={20} className="muted" />
            </section>
          </SettingsGroup>
          <SettingsGroup title="备份与维护" description="创建备份、建立隔离副本或清理临时文件。">
            <Maintenance key={userId} userId={userId} />
          </SettingsGroup>
          <SettingsGroup title="高级" description="查看真实服务状态与导出脱敏诊断。">
            <Diagnostics />
            <p className="page-note">未发送的消息只保留在当前页面，凭据不会写入浏览器存储。</p>
          </SettingsGroup>
        </>
      )}
    </div>
  )
}
export function ConnectionsPage({
  models = [],
  onRefresh,
  onCreate,
  onCheck,
  onUpdate,
  onCapabilities,
  onImport,
}: {
  models?: ModelConnection[]
  onRefresh?: () => void
  onCreate?: (input: ModelInput) => Promise<void>
  onUpdate?: (id: string, input: ModelUpdate) => Promise<void>
  onCapabilities?: (id: string, input: CapabilitiesUpdate) => Promise<void>
  onImport?: (id: string) => Promise<void>
  onCheck?: (id: string) => Promise<void>
}) {
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<ModelConnection | null>(null)
  const [checking, setChecking] = useState<string | null>(null)
  const [error, setError] = useState('')
  return (
    <div className="page-content">
      <PageHeading
        title="模型与连接"
        action={
          <div className="button-row">
            <Button disabled={!onRefresh} onClick={onRefresh}>
              <RefreshCw size={15} />
              刷新
            </Button>
            <Button variant="primary" disabled={!onCreate} onClick={() => setAdding(true)}>
              添加连接
            </Button>
          </div>
        }
      >
        配置模型服务，查看实际可用能力。
      </PageHeading>
      <section className="section-card">
        <div className="section-title">
          <h2>模型服务</h2>
          <Status>{models.length ? '已配置' : '尚未配置'}</Status>
        </div>
        {models.length ? (
          <div className="connection-list">
            {models.map((model) => (
              <section className="model-connection" key={model.id}>
                <div>
                  <div>
                    <h3>{model.name}</h3>
                    <p>
                      {model.model_identifier} · {model.endpoint}
                    </p>
                  </div>
                  <div className="button-row">
                    <Status
                      tone={
                        model.service_status === 'ready'
                          ? 'good'
                          : model.service_status === 'unavailable'
                            ? 'warning'
                            : 'neutral'
                      }
                    >
                      {model.service_status === 'ready'
                        ? '可用'
                        : model.service_status === 'unavailable'
                          ? '不可用'
                          : '未验证'}
                    </Status>
                    <Button disabled={!onUpdate} onClick={() => setEditing(model)}>
                      编辑配置
                    </Button>
                    <Button
                      disabled={!onCheck || !!checking}
                      onClick={() => {
                        setChecking(model.id)
                        setError('')
                        void onCheck?.(model.id)
                          .catch((err) => setError(err instanceof Error ? err.message : '检查失败'))
                          .finally(() => setChecking(null))
                      }}
                    >
                      {checking === model.id ? '检查中…' : '检查连接'}
                    </Button>
                  </div>
                </div>
                {onCapabilities && onImport && (
                  <Capabilities
                    key={`${model.id}:${model.version}`}
                    model={model}
                    onSave={(input) => onCapabilities(model.id, input)}
                    onImport={() => onImport(model.id)}
                    onReload={async () => {
                      onRefresh?.()
                    }}
                  />
                )}
              </section>
            ))}
          </div>
        ) : (
          <div className="inline-empty">
            <Server size={23} />
            <h3>连接你的第一个模型</h3>
            <p>模型能力与可用状态会在连接后显示。</p>
            <Button disabled={!onCreate} onClick={() => setAdding(true)}>
              添加模型连接
            </Button>
          </div>
        )}
      </section>
      <p className="page-note">保存连接配置不会自动发起推理；模型验证需要显式执行。</p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {editing && onUpdate && (
        <Dialog title="编辑模型连接" onClose={() => setEditing(null)}>
          <ModelForm
            key={editing.id}
            initial={editing}
            onCancel={() => setEditing(null)}
            onReload={async () => {
              onRefresh?.()
              setEditing(null)
            }}
            onSave={async (input) => {
              await onUpdate(editing.id, { ...input, expected_version: editing.version })
              setEditing(null)
            }}
          />
        </Dialog>
      )}
      {adding && onCreate && (
        <Dialog title="添加模型连接" onClose={() => setAdding(false)}>
          <ModelForm
            onCancel={() => setAdding(false)}
            onSave={async (input) => {
              await onCreate(input)
              setAdding(false)
            }}
          />
        </Dialog>
      )}
    </div>
  )
}
export type DiagnosticItem = { name: string; status: string; detail: string }
export function DiagnosticsPage({
  items = [],
  onRefresh,
  busy = false,
}: {
  items?: DiagnosticItem[]
  onRefresh?: () => void
  busy?: boolean
}) {
  return (
    <div className="page-content">
      <PageHeading
        title="诊断"
        action={
          <Button disabled={!onRefresh || busy} onClick={onRefresh}>
            <RefreshCw size={15} className={busy ? 'spinning' : ''} />
            重新检查
          </Button>
        }
      >
        查看各项服务的真实状态与缺失依赖。
      </PageHeading>
      <section className="section-card">
        <div className="section-title">
          <h2>服务状态</h2>
        </div>
        {items.length ? (
          <div className="diagnostic-list">
            {items.map((item) => (
              <div key={item.name}>
                <div>
                  <h3>{item.name}</h3>
                  <p>{item.detail}</p>
                </div>
                <Status
                  tone={
                    item.status === 'ready' || item.status === 'ok'
                      ? 'good'
                      : item.status === 'error' || item.status === 'unavailable'
                        ? 'warning'
                        : 'neutral'
                  }
                >
                  {item.status === 'ready' || item.status === 'ok'
                    ? '可用'
                    : item.status === 'error' || item.status === 'unavailable'
                      ? '不可用'
                      : item.status === 'downloaded'
                        ? '已下载'
                        : item.status === 'not_started'
                          ? '未启动'
                          : '未知'}
                </Status>
              </div>
            ))}
          </div>
        ) : (
          <div className="inline-empty">
            <p>尚未取得服务诊断结果。</p>
          </div>
        )}
      </section>
      <p className="page-note">
        服务进程、数据库、任务运行时和模型分别检查；单一服务在线不代表全部能力可用。
      </p>
      <Diagnostics initiallyOpen />
    </div>
  )
}
