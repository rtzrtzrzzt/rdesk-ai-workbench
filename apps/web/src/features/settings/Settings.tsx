import { useCallback, useState } from 'react'
import { SettingsPanel, useSettingsAttention } from './SettingsGroup'
import type { ExperiencePreferences } from './useExperiencePreferences'
import './settings.css'
import {
  Monitor,
  Moon,
  Sun,
  Shield,
  Server,
  RefreshCw,
  Settings2,
  Palette,
  ListChecks,
  FolderOpen,
  Database,
  SlidersHorizontal,
  FileText,
  Package,
  ImagePlus,
} from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { ModelForm } from './ModelForm'
import { Capabilities } from './Capabilities'
import { RuntimeDefaults } from './RuntimeDefaults'
import { Maintenance } from './Maintenance'
import { Vault } from './Vault'
import { AccountSecurity } from './AccountSecurity'
import { WorkspaceRuntimeDefaults } from './WorkspaceRuntimeDefaults'
import { UserDirectory } from './UserDirectory'
import { ProjectConfiguration } from './ProjectConfiguration'
import { Diagnostics } from './Diagnostics'
import { ComputerControl } from './ComputerControl'
import { ConversationHistory } from './ConversationHistory'
import { RulesPanel } from '../memory/MemoryPage'
import { ExtensionsPage } from '../extensions/ExtensionsPage'
import { MediaConnectionsPanel } from '../media/MediaConnectionsPanel'
import { ApiServicesPanel } from '../apiServices/ApiServicesPanel'
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
  experience,
}: {
  userId?: string
  workspace?: Workspace
  models?: ModelConnection[]
  experience: ExperiencePreferences
}) {
  const [category, setCategory] = useState('general')
  const [attention, setAttention] = useState<Record<string, boolean>>({})
  const reportAttention = useCallback((id: string, active: boolean) => {
    setAttention((previous) => (previous[id] === active ? previous : { ...previous, [id]: active }))
  }, [])
  const { theme, input_mode: inputMode } = experience.values
  const inputModeLocked = experience.inputLocked
  const preferenceDisabled =
    !experience.ready || experience.busy || experience.pending || experience.conflict
  const categories = [
    { id: 'general', title: '通用', description: '日常聊天和消息发送偏好。', icon: Settings2 },
    { id: 'appearance', title: '外观', description: '选择工作台的显示主题。', icon: Palette },
    ...(userId
      ? [
          {
            id: 'tasks',
            title: '任务偏好',
            description: '为新任务设置默认模型与执行方式。',
            icon: ListChecks,
          },
          ...(workspace
            ? [
                {
                  id: 'workspace',
                  title: '当前工作区',
                  description: `仅对「${workspace.name}」的新任务生效。`,
                  icon: FolderOpen,
                },
              ]
            : []),
          {
            id: 'rules',
            title: '全局规则',
            description: '编辑对所有工作区生效的 AGENTS.md。',
            icon: FileText,
          },
          {
            id: 'security',
            title: '连接与安全',
            description: '管理本机会话和模型凭据。',
            icon: Shield,
          },
          {
            id: 'extensions',
            title: '插件与 Skill',
            description: '安装和管理本机扩展，选择全局或当前工作区使用。',
            icon: Package,
          },
          {
            id: 'media-models',
            title: '生成模型',
            description: '接入图像、视频、音频和 3D 生成模型。',
            icon: ImagePlus,
          },
          {
            id: 'maintenance',
            title: '数据与维护',
            description: '备份、隔离恢复与临时文件清理。',
            icon: Database,
          },
          {
            id: 'api-services',
            title: 'API 服务',
            description: '发布 Agent，管理外部应用权限和 API 访问地址。',
            icon: Server,
          },
          {
            id: 'advanced',
            title: '高级',
            description: '服务状态和诊断信息。',
            icon: SlidersHorizontal,
          },
        ]
      : []),
  ]
  const selected = categories.some((item) => item.id === category) ? category : 'general'
  return (
    <div className="page-content settings-page">
      <PageHeading title="设置">按需调整工作台的使用方式。</PageHeading>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          {categories.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={selected === item.id ? 'page' : undefined}
              aria-controls={`settings-${item.id}`}
              onClick={() => setCategory(item.id)}
            >
              <item.icon size={16} aria-hidden="true" />
              <span>{item.title}</span>
              {attention[item.id] && (
                <span className="settings-nav-attention" aria-label="有待处理事项" />
              )}
            </button>
          ))}
        </nav>
        <div className="settings-panels">
          {categories.map((item) => (
            <SettingsPanel
              key={item.id}
              {...item}
              active={selected === item.id}
              onAttentionChange={reportAttention}
            >
              {item.id === 'general' && (
                <section className="settings-preference">
                  <div>
                    <h3>运行中发送消息</h3>
                    <p>选择 AI 工作时，新消息如何处理。保存到账户后，各浏览器会同步生效。</p>
                  </div>
                  <label className="settings-follow-up">
                    <span className="sr-only">运行中消息的默认行为</span>
                    <select
                      aria-label="运行中消息的默认行为"
                      value={inputMode}
                      disabled={inputModeLocked || preferenceDisabled}
                      onChange={(event) =>
                        void experience.change({
                          input_mode: event.target.value as 'queue' | 'steer',
                        })
                      }
                    >
                      <option value="queue">排入队列</option>
                      <option value="steer">引导当前任务</option>
                    </select>
                  </label>
                  <p className="settings-preference-help">
                    {inputMode === 'queue'
                      ? '当前任务完成后再处理新消息。排队消息可在输入框上方编辑、排序、删除，也可点击「立即引导」调整当前任务。'
                      : '文字消息会尽快补充到当前任务，已开始的工具会先执行到安全边界。任务结束后发送的消息会正常开启下一轮。'}
                  </p>
                  {inputModeLocked && (
                    <p className="page-note" role="status">
                      消息正在发送或结果待核对，处理完成后可更改此项。
                    </p>
                  )}
                  {experience.deferred && (
                    <p className="page-note" role="status">
                      账户默认行为已更新，将在本次发送或待核对请求处理完成后应用。
                    </p>
                  )}
                  <ExperienceFeedback preferences={experience} />
                </section>
              )}
              {item.id === 'general' && userId && <ConversationHistory userId={userId} />}
              {item.id === 'general' && userId && <ComputerControl />}
              {item.id === 'appearance' && (
                <section className="settings-preference">
                  <div>
                    <h3>主题</h3>
                    <p>保存到账户后，各浏览器会同步生效。</p>
                  </div>
                  <div className="segmented" role="group" aria-label="外观主题">
                    {(
                      [
                        { id: 'light', name: '浅色', icon: Sun },
                        { id: 'dark', name: '深色', icon: Moon },
                        { id: 'system', name: '跟随系统', icon: Monitor },
                      ] as const
                    ).map((themeOption) => (
                      <button
                        key={themeOption.id}
                        aria-pressed={theme === themeOption.id}
                        disabled={preferenceDisabled}
                        onClick={() => void experience.change({ theme: themeOption.id })}
                      >
                        <themeOption.icon size={15} />
                        {themeOption.name}
                      </button>
                    ))}
                  </div>
                  <ExperienceFeedback preferences={experience} />
                </section>
              )}
              {item.id === 'tasks' && <RuntimeDefaults models={models} />}
              {item.id === 'rules' &&
                (workspace ? (
                  <RulesPanel workspace={workspace} active={selected === 'rules'} globalOnly />
                ) : (
                  <p className="page-note">先创建或选择一个工作区，即可编辑用户全局 AGENTS.md。</p>
                ))}
              {item.id === 'workspace' && workspace && (
                <>
                  <ProjectConfiguration
                    workspaceId={workspace.id}
                    active={selected === 'workspace'}
                  />
                  <WorkspaceRuntimeDefaults
                    key={workspace.id}
                    workspace={workspace}
                    models={models}
                  />
                </>
              )}
              {item.id === 'security' && userId && (
                <>
                  <AccountSecurity key={`account-${userId}`} userId={userId} />
                  <Vault key={`vault-${userId}`} userId={userId} />
                  <p className="page-note">
                    文件和工具权限由当前工作区管理；模型服务在「模型与连接」中配置。
                  </p>
                </>
              )}
              {item.id === 'extensions' &&
                userId &&
                (workspace ? (
                  <ExtensionsPage
                    key={`${userId}:${workspace.id}`}
                    userId={userId}
                    workspace={workspace}
                    active={selected === 'extensions'}
                  />
                ) : (
                  <p className="page-note">先创建或选择一个工作区，即可检查和安装插件与 Skill。</p>
                ))}
              {item.id === 'media-models' &&
                (workspace ? (
                  <MediaConnectionsPanel
                    key={workspace.id}
                    workspace={workspace}
                    active={selected === 'media-models'}
                  />
                ) : (
                  <p className="page-note">先创建或选择一个工作区，即可接入生成模型。</p>
                ))}
              {item.id === 'maintenance' && userId && (
                <>
                  <UserDirectory active={selected === 'maintenance'} />
                  <Maintenance key={userId} userId={userId} />
                </>
              )}
              {item.id === 'api-services' && userId && (
                <ApiServicesPanel key={userId} active={selected === 'api-services'} />
              )}
              {item.id === 'advanced' && <Diagnostics />}
            </SettingsPanel>
          ))}
        </div>
      </div>
    </div>
  )
}

function ExperienceFeedback({ preferences }: { preferences: ExperiencePreferences }) {
  const needsAttention = preferences.pending || preferences.conflict || preferences.draft
  useSettingsAttention('experience', needsAttention, '账户偏好有待核对或未保存的修改')
  return (
    <div>
      <p className="page-note" role="status">
        {preferences.busy
          ? '正在保存或核对账户偏好…'
          : preferences.message || (!preferences.ready ? '正在读取账户偏好…' : '')}
      </p>
      {preferences.pending ? (
        <Button disabled={preferences.busy} onClick={() => void preferences.retry()}>
          核对本次保存
        </Button>
      ) : (
        <>
          {(preferences.message || preferences.conflict) && (
            <Button disabled={preferences.busy} onClick={() => void preferences.reload()}>
              读取最新设置
            </Button>
          )}
          {preferences.draft && (
            <>
              <Button
                disabled={preferences.busy || preferences.conflict || !preferences.ready}
                onClick={() => void preferences.saveDraft()}
              >
                保存保留的修改
              </Button>
              <Button disabled={preferences.busy} onClick={preferences.discardDraft}>
                放弃未保存修改
              </Button>
            </>
          )}
          {preferences.uninitialized && !preferences.busy && preferences.message && (
            <Button onClick={() => void preferences.initialize()}>迁移本机偏好到账户</Button>
          )}
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
