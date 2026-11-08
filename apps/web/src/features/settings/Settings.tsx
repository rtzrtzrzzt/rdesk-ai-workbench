import { useCallback, useState } from 'react'
import { SettingsPanel, useSettingsAttention } from './SettingsGroup'
import { SettingsDisclosure } from './SettingsDisclosure'
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
  ChevronRight,
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
  connections,
}: {
  userId?: string
  workspace?: Workspace
  models?: ModelConnection[]
  experience: ExperiencePreferences
  connections?: ConnectionsActions
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
  const categories: {
    id: string
    title: string
    description: string
    icon: typeof Settings2
    parent?: string
  }[] = [
    { id: 'general', title: '通用', description: '外观与日常消息偏好。', icon: Settings2 },
    ...(userId
      ? [
          {
            id: 'models',
            title: '模型与连接',
            description: '连接聊天模型，按需接入其他生成能力。',
            icon: Server,
          },
          {
            id: 'media-models',
            parent: 'models',
            title: '生成模型',
            description: '接入图像、视频、音频和 3D 生成模型。',
            icon: ImagePlus,
          },
          {
            id: 'agent',
            title: 'Agent 行为',
            description: '选择设置范围，再调整默认行为与规则。',
            icon: ListChecks,
          },
          {
            id: 'tasks',
            parent: 'agent',
            title: '任务默认',
            description: '为所有工作区的新任务设置默认模型与执行方式。',
            icon: ListChecks,
          },
          ...(workspace
            ? [
                {
                  id: 'workspace',
                  parent: 'agent',
                  title: '当前工作区',
                  description: `仅对「${workspace.name}」的新任务生效。`,
                  icon: FolderOpen,
                },
              ]
            : []),
          {
            id: 'rules',
            parent: 'agent',
            title: '全局规则',
            description: '编辑对所有工作区生效的 AGENTS.md。',
            icon: FileText,
          },
          {
            id: 'extensions',
            title: '扩展',
            description: '管理插件与 Skill，选择全局或工作区使用。',
            icon: Package,
          },
          {
            id: 'privacy',
            title: '权限与隐私',
            description: '控制 AI 可以读取的对话与电脑操作权限。',
            icon: Shield,
          },
          {
            id: 'security',
            parent: 'privacy',
            title: '账户会话',
            description: '管理登录会话与本机访问安全。',
            icon: Shield,
          },
          {
            id: 'credentials',
            parent: 'privacy',
            title: '凭据管理',
            description: '管理已保存的模型凭据与加密保险箱。',
            icon: Shield,
          },
          {
            id: 'maintenance',
            title: '数据与维护',
            description: '查看用户目录，按需进行备份与维护。',
            icon: Database,
          },
          {
            id: 'backup',
            parent: 'maintenance',
            title: '备份、恢复与清理',
            description: '创建备份、隔离恢复，或检查可清理的临时文件。',
            icon: Database,
          },
          {
            id: 'api-services',
            title: 'API 服务',
            description: '按顺序配置服务、调用权限与访问地址。',
            icon: Server,
          },
          {
            id: 'api-publish',
            parent: 'api-services',
            title: '发布 Agent',
            description: '选择提供给外部应用的 Agent 与能力。',
            icon: Package,
          },
          {
            id: 'api-clients',
            parent: 'api-services',
            title: '客户端与授权',
            description: '管理调用密钥与每个客户端可使用的服务。',
            icon: Shield,
          },
          {
            id: 'api-listener',
            parent: 'api-services',
            title: 'API 访问地址',
            description: '配置独立 API 地址、网络访问与 HTTPS。',
            icon: Server,
          },
          {
            id: 'advanced',
            title: '高级与诊断',
            description: '查看运行状态，按需展开技术详情或导出诊断。',
            icon: SlidersHorizontal,
          },
        ]
      : []),
  ]
  const selected = categories.some((item) => item.id === category) ? category : 'general'
  const selectedParent = categories.find((item) => item.id === selected)?.parent ?? selected
  function navigateSettings(id: string) {
    setCategory(id)
    requestAnimationFrame(() => {
      document.getElementById(`settings-${id}-title`)?.focus({ preventScroll: true })
      document.getElementById('main-content')?.scrollTo({ top: 0 })
    })
  }
  function categoryAttention(id: string) {
    return attention[id] || categories.some((item) => item.parent === id && attention[item.id])
  }
  return (
    <div className="page-content settings-page">
      <PageHeading title="设置">按需调整工作台的使用方式。</PageHeading>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          {categories
            .filter((item) => !item.parent)
            .map((item) => (
              <button
                key={item.id}
                type="button"
                aria-current={selectedParent === item.id ? 'page' : undefined}
                aria-controls={`settings-${item.id}`}
                onClick={() => navigateSettings(item.id)}
              >
                <item.icon size={16} aria-hidden="true" />
                <span>{item.title}</span>
                {categoryAttention(item.id) && (
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
              back={
                item.parent
                  ? {
                      label:
                        categories.find((parent) => parent.id === item.parent)?.title ?? '返回',
                      onClick: () => navigateSettings(item.parent!),
                    }
                  : undefined
              }
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
              {item.id === 'privacy' && userId && <ConversationHistory userId={userId} />}
              {item.id === 'privacy' && userId && <ComputerControl />}
              {item.id === 'general' && (
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
                  <WorkspaceRuntimeDefaults
                    key={workspace.id}
                    workspace={workspace}
                    models={models}
                  />
                  <SettingsDisclosure title="项目配置文件" summary=".rdesk/config.json">
                    <ProjectConfiguration
                      key={workspace.id}
                      workspaceId={workspace.id}
                      active={selected === 'workspace'}
                    />
                  </SettingsDisclosure>
                </>
              )}
              {item.id === 'security' && userId && (
                <AccountSecurity key={`account-${userId}`} userId={userId} />
              )}
              {item.id === 'credentials' && userId && (
                <Vault key={`vault-${userId}`} userId={userId} />
              )}
              {item.id === 'models' && (
                <ConnectionsPage models={models} {...connections} embedded />
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
                <UserDirectory active={selected === 'maintenance'} />
              )}
              {item.id === 'backup' && userId && <Maintenance key={userId} userId={userId} />}
              {['api-publish', 'api-clients', 'api-listener'].includes(item.id) && userId && (
                <ApiServicesPanel
                  key={`${userId}:${item.id}`}
                  active={selected === item.id}
                  section={
                    item.id === 'api-publish'
                      ? 'services'
                      : item.id === 'api-clients'
                        ? 'clients'
                        : 'listener'
                  }
                />
              )}
              {item.id === 'advanced' && <Diagnostics />}
              {categories.some((child) => child.parent === item.id) && (
                <div className="settings-entries" aria-label={`${item.title}子设置`}>
                  {categories
                    .filter((child) => child.parent === item.id)
                    .map((child) => (
                      <button
                        type="button"
                        key={child.id}
                        className="settings-entry"
                        onClick={() => navigateSettings(child.id)}
                      >
                        <child.icon size={17} aria-hidden="true" />
                        <span>
                          <strong>{child.title}</strong>
                          <small>{child.description}</small>
                        </span>
                        {attention[child.id] && (
                          <span className="settings-nav-attention" aria-label="有待处理事项" />
                        )}
                        <ChevronRight size={16} aria-hidden="true" />
                      </button>
                    ))}
                </div>
              )}
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

export type ConnectionsActions = {
  onRefresh?: () => void
  onCreate?: (input: ModelInput) => Promise<void>
  onUpdate?: (id: string, input: ModelUpdate) => Promise<void>
  onCapabilities?: (id: string, input: CapabilitiesUpdate) => Promise<void>
  onImport?: (id: string) => Promise<void>
  onCheck?: (id: string) => Promise<void>
}

export function ConnectionsPage({
  models = [],
  onRefresh,
  onCreate,
  onCheck,
  onUpdate,
  onCapabilities,
  onImport,
  embedded = false,
}: ConnectionsActions & {
  models?: ModelConnection[]
  embedded?: boolean
}) {
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<ModelConnection | null>(null)
  const [checking, setChecking] = useState<string | null>(null)
  const [error, setError] = useState('')
  return (
    <div className={embedded ? 'settings-connections' : 'page-content'}>
      <PageHeading
        title={embedded ? '聊天模型' : '模型与连接'}
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
