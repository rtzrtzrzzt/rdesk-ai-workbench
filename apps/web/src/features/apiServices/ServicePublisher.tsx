import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  agentServicesApi,
  type AgentService,
  type ServiceCapabilities,
  type ServiceDefinition,
} from '../../api/agentServices'
import type { RunOptions, Workspace } from '../../api/types'
import { Button, Status } from '../../components/ui'
import { useSettingsAttention } from '../settings/SettingsGroup'
import { revealSettingsControl, SettingsDisclosure } from '../settings/SettingsDisclosure'

const unsupportedLabels: Record<string, string> = {
  host_commands: '主机命令',
  mcp: 'MCP',
  media_generation: '媒体生成',
  source_workspace: '工作区资料共享',
}
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '操作结果需要核对。'

export function ServicePublisher({
  services,
  capabilities,
  workspaces,
  sourcesLoading,
  loading,
  onRefresh,
}: {
  services: AgentService[]
  capabilities?: ServiceCapabilities
  workspaces: Workspace[]
  sourcesLoading: boolean
  loading: boolean
  onRefresh: () => Promise<void>
}) {
  const [editing, setEditing] = useState<AgentService | 'new' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useSettingsAttention('agent-services', !!editing || !!error, 'Agent 服务有未保存或待核对的修改')
  return (
    <section className="api-service-section">
      <div className="section-title">
        <h3>Agent 服务</h3>
        <Button disabled={loading || !!editing || !capabilities} onClick={() => setEditing('new')}>
          发布服务
        </Button>
      </div>
      {loading && <p className="page-note">正在读取可发布的能力…</p>}
      {capabilities && capabilities.models.length === 0 && (
        <p className="page-note">尚无模型连接，请先在「模型与连接」中添加。</p>
      )}
      {!loading && !services.length && <p className="page-note">尚未发布服务。</p>}
      {services.map((service) => (
        <div className="api-service-row" key={service.id}>
          <div>
            <strong>{service.name}</strong>
            <p>{service.description || '未添加说明'}</p>
            <small>
              版本 {service.revision_number} · {service.definition.allowed_tools.length} 项工具
              {service.definition.source_workspace_id && ' · 只读资料快照'}
            </small>
          </div>
          <div className="button-row">
            <Status tone={service.enabled ? 'good' : 'neutral'}>
              {service.enabled ? '已启用' : '已停用'}
            </Status>
            <Button
              disabled={busy || !!editing}
              onClick={() => {
                setError('')
                setEditing(service)
              }}
            >
              编辑
            </Button>
            <Button
              disabled={busy || !!error}
              onClick={() => {
                setBusy(true)
                void agentServicesApi
                  .state(service)
                  .then(onRefresh)
                  .catch((err) => setError(errorText(err)))
                  .finally(() => setBusy(false))
              }}
            >
              {service.enabled ? '停用' : '启用'}
            </Button>
          </div>
        </div>
      ))}
      {error && (
        <div role="alert">
          <p className="form-error">{error}</p>
          <Button onClick={() => void onRefresh().then(() => setError(''))}>读取最新状态</Button>
        </div>
      )}
      {editing && capabilities && (
        <ServiceForm
          key={editing === 'new' ? 'new' : editing.id}
          initial={editing === 'new' ? undefined : editing}
          capabilities={capabilities}
          workspaces={workspaces}
          sourcesLoading={sourcesLoading}
          onCancel={() => {
            setEditing(null)
            void onRefresh()
          }}
          onSaved={async () => {
            await onRefresh()
            setEditing(null)
          }}
        />
      )}
      {capabilities && capabilities.unsupported.length > 0 && (
        <p className="page-note">
          暂未就绪：
          {capabilities.unsupported.map((name) => unsupportedLabels[name] ?? name).join('、')}。
        </p>
      )}
    </section>
  )
}

function ServiceForm({
  initial,
  capabilities,
  workspaces,
  sourcesLoading,
  onCancel,
  onSaved,
}: {
  initial?: AgentService
  capabilities: ServiceCapabilities
  workspaces: Workspace[]
  sourcesLoading: boolean
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [instructions, setInstructions] = useState(initial?.definition.instructions ?? '')
  const [model, setModel] = useState(initial?.definition.model_connection_id ?? '')
  const [tools, setTools] = useState(initial?.definition.allowed_tools ?? [])
  const [sourceMode, setSourceMode] = useState(
    initial?.definition.source_workspace_id ? 'snapshot' : 'none',
  )
  const [sourceWorkspace, setSourceWorkspace] = useState(
    initial?.definition.source_workspace_id ?? '',
  )
  const [sourcePaths, setSourcePaths] = useState(initial?.definition.source_paths?.join('\n') ?? '')
  const sourceWorkspaceControl = useRef<HTMLSelectElement>(null)
  const sourcePathsControl = useRef<HTMLTextAreaElement>(null)
  const paths = sourcePaths
    .split('\n')
    .map((path) => path.trim())
    .filter(Boolean)
  const sourceWorkspaceName = workspaces.find((workspace) => workspace.id === sourceWorkspace)?.name
  const budgetSchema = capabilities.runtime_options_schema.properties?.max_model_rounds
  const childSchema = capabilities.runtime_options_schema.properties?.child_agent_limit
  const supportsChildAgents = capabilities.tools.some((tool) => tool.name === 'spawn_agent')
  const [childLimit, setChildLimit] = useState(
    String(initial?.definition.runtime_options.child_agent_limit ?? 0),
  )
  const [rounds, setRounds] = useState(
    String(initial?.definition.runtime_options.max_model_rounds ?? budgetSchema?.default ?? ''),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [saved, setSaved] = useState(false)
  async function save() {
    if (busy || uncertain || saved) return
    setBusy(true)
    setError('')
    try {
      if (
        sourceMode === 'snapshot' &&
        (!sourceWorkspace ||
          !paths.length ||
          paths.some(
            (path) =>
              path.split('/').every((part) => !part || part === '.') ||
              path.startsWith('/') ||
              path.includes('\\') ||
              path.split('/').includes('..') ||
              /^[A-Za-z]:/.test(path),
          ))
      ) {
        setError('请选择来源工作区，并填写非空的明确相对路径；不能共享整个工作区或填写越界路径。')
        revealSettingsControl(
          sourceWorkspace ? sourcePathsControl.current : sourceWorkspaceControl.current,
        )
        return
      }
      const defaults = Object.fromEntries(
        Object.entries(capabilities.runtime_options_schema.properties ?? {})
          .filter(([, value]) => value.default !== undefined)
          .map(([key, value]) => [key, value.default]),
      )
      const definition: ServiceDefinition = {
        ...initial?.definition,
        model_connection_id: model,
        instructions,
        allowed_tools: tools,
        source_workspace_id: sourceMode === 'snapshot' ? sourceWorkspace : null,
        source_paths: sourceMode === 'snapshot' ? paths : [],
        runtime_options: {
          ...defaults,
          ...initial?.definition.runtime_options,
          max_model_rounds: Number(rounds),
          child_agent_limit: Number(childLimit),
        } as RunOptions,
      }
      const input = { name: name.trim(), description, definition }
      if (initial)
        await agentServicesApi.revise(initial.id, { ...input, expected_version: initial.version })
      else await agentServicesApi.publish(input)
      setSaved(true)
      await onSaved()
    } catch (err) {
      setError(errorText(err))
      // Publishing has no idempotency key. A lost reply must be reconciled from the list.
      setUncertain(
        !(err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 409),
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <form
      className="api-service-form"
      onInvalidCapture={(event) => revealSettingsControl(event.target)}
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <h4>{initial ? '编辑服务' : '发布新服务'}</h4>
      <fieldset disabled={busy || uncertain || saved}>
        <label>
          名称
          <input
            required
            maxLength={160}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          说明
          <textarea
            maxLength={4096}
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <label>
          模型连接
          <select required value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="">选择已配置的模型</option>
            {capabilities.models.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          服务指令
          <textarea
            rows={5}
            maxLength={65536}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </label>
        {capabilities.source_sharing?.mode === 'revision_snapshot' && (
          <SettingsDisclosure
            title="工作区资料共享"
            summary={
              sourceMode === 'snapshot'
                ? `${sourceWorkspaceName ?? (sourceWorkspace || '未选择来源工作区')} · ${paths.length} 条路径 · 只读快照`
                : '未共享工作区资料'
            }
          >
            <label>
              工作区资料共享
              <select value={sourceMode} onChange={(event) => setSourceMode(event.target.value)}>
                <option value="none">不共享工作区资料</option>
                <option value="snapshot">共享明确路径的只读快照</option>
              </select>
            </label>
            {sourceMode === 'snapshot' && (
              <>
                <label>
                  来源工作区
                  <select
                    ref={sourceWorkspaceControl}
                    required
                    value={sourceWorkspace}
                    disabled={sourcesLoading}
                    onChange={(event) => setSourceWorkspace(event.target.value)}
                  >
                    <option value="">
                      {sourcesLoading ? '正在读取工作区…' : '选择来源工作区'}
                    </option>
                    {workspaces.map((workspace) => (
                      <option key={workspace.id} value={workspace.id}>
                        {workspace.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  明确共享的相对路径（每行一个文件或目录）
                  <textarea
                    ref={sourcePathsControl}
                    required
                    rows={4}
                    value={sourcePaths}
                    onChange={(event) => setSourcePaths(event.target.value)}
                  />
                </label>
                <p className="page-note">
                  仅共享列出的文本与代码资料，不自动共享整个工作区或继承来源项目的规则与权限。发布和保存新版本时冻结只读快照；原文件后续修改需再次发布才会生效。
                </p>
              </>
            )}
          </SettingsDisclosure>
        )}
        <SettingsDisclosure
          title="运行预算"
          summary={`最多 ${rounds || '未设置'} 轮${supportsChildAgents ? ` · 子任务上限 ${childLimit || '未设置'}` : ''}`}
        >
          <label>
            每次运行最多模型轮数
            <input
              required
              type="number"
              min={budgetSchema?.minimum ?? 1}
              max={budgetSchema?.maximum}
              step={1}
              value={rounds}
              onChange={(event) => setRounds(event.target.value)}
            />
          </label>
          {supportsChildAgents && (
            <label>
              子任务上限（0 表示关闭）
              <input
                required
                type="number"
                min={childSchema?.minimum ?? 0}
                max={childSchema?.maximum}
                step={1}
                value={childLimit}
                onChange={(event) => setChildLimit(event.target.value)}
              />
            </label>
          )}
        </SettingsDisclosure>
        <SettingsDisclosure title="允许使用的能力" summary={`已选择 ${tools.length} 项工具`}>
          <div className="api-tools">
            <span>允许使用的工具</span>
            {capabilities.tools.map((tool) => (
              <label key={tool.name}>
                <input
                  type="checkbox"
                  checked={tools.includes(tool.name)}
                  onChange={(event) =>
                    setTools(
                      event.target.checked
                        ? [...tools, tool.name]
                        : tools.filter((name) => name !== tool.name),
                    )
                  }
                />
                <span>
                  {tool.name}
                  <small>{tool.description}</small>
                </span>
              </label>
            ))}
          </div>
        </SettingsDisclosure>
      </fieldset>
      {error && (
        <p className="form-error" role="alert">
          {error}
          {uncertain ? ' 请核对服务列表后再继续，避免重复发布。' : ''}
        </p>
      )}
      <div className="button-row">
        <Button
          type="submit"
          variant="primary"
          disabled={busy || uncertain || saved || !model || !name.trim()}
        >
          {busy ? '正在保存…' : initial ? '保存新版本' : '发布服务'}
        </Button>
        <Button disabled={busy} onClick={onCancel}>
          {uncertain || saved ? '保留结果并关闭' : '取消'}
        </Button>
      </div>
    </form>
  )
}
