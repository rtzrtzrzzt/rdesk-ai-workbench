import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, Globe, RefreshCw, Settings2, Shield, Wrench } from 'lucide-react'
import { ApiError } from '../../api/client'
import { searchApi } from '../../api/search'
import type { SearchConnectionSave, SearchConnectionView, Workspace } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, EmptyState, PageHeading, Status } from '../../components/ui'
import { connectionDraft, SearchConnectionForm } from './SearchConnectionForm'
import type { SearchSettingsDraft } from './SearchConnectionForm'
import { SearchTester } from './SearchTester'
import { MCPSection } from '../mcp/MCPSection'

type Scope = SearchConnectionSave['scope']
const scopeLabel = (scope: Scope) => (scope === 'user' ? '用户级' : '当前工作区')
const describeError = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请重试。'
function settingsValue(draft: SearchSettingsDraft) {
  const { expected_version, ...settings } = draft.settings
  void expected_version
  return JSON.stringify({ ...settings, engineMode: draft.engineMode })
}

export function ToolsPage({
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
    <div className="page-content tools-page">
      <PageHeading title="工具与扩展">按需连接工具，查看实际可用能力和授权范围。</PageHeading>
      {!authenticated || !workspace ? (
        <EmptyState icon={Wrench} title={authenticated ? '先选择工作区' : '工具服务尚未连接'}>
          搜索配置与当前账号、工作区关联。连接工作台并选择工作区后，可查看真实状态。
        </EmptyState>
      ) : (
        Object.values(known).map((item) => (
          <div key={item.id} hidden={item.id !== workspace.id}>
            <ToolsWorkspace workspace={item} active={active && item.id === workspace.id} />
            <MCPSection workspaceId={item.id} active={active && item.id === workspace.id} />
          </div>
        ))
      )}
      <section className="tools-other-config">
        <h2>其他扩展配置</h2>
        <div>
          <Shield size={17} />
          <span>沙箱与执行策略</span>
          <Status>在任务设置中调整</Status>
        </div>
        <p>
          在“设置 → Agent
          行为”中选择“任务默认”或“当前工作区”，展开高级任务预算与执行设置。实际工具操作继续按当前审批策略检查。
        </p>
      </section>
    </div>
  )
}
function ToolsWorkspace({ workspace, active }: { workspace: Workspace; active: boolean }) {
  const client = useQueryClient()
  const [scope, setScope] = useState<Scope>('workspace')
  const [expanded, setExpanded] = useState(false)
  const [drafts, setDrafts] = useState<Partial<Record<Scope, SearchSettingsDraft>>>({})
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)
  const [notice, setNotice] = useState('')
  const [reloadConfirm, setReloadConfirm] = useState(false)
  const [reloading, setReloading] = useState(false)
  const capability = useQuery({
    queryKey: ['search-capability', workspace.id],
    queryFn: () => searchApi.capability(workspace.id),
    enabled: active,
  })
  const connection = useQuery({
    queryKey: ['search-connection', workspace.id, scope],
    queryFn: () => searchApi.connection(workspace.id, scope),
    enabled: active && expanded,
  })
  const baseline = connectionDraft(scope, connection.data ?? null)
  const draft = drafts[scope] ?? baseline
  const dirty = !!drafts[scope] && settingsValue(draft) !== settingsValue(baseline)
  const effective = capability.data?.connection
  async function save() {
    if (saving || connection.data === undefined || !dirty || draft.conflict) return
    const target = scope
    setSaving(true)
    setNotice('')
    try {
      const updated = await searchApi.save(workspace.id, draft.settings)
      client.setQueryData<SearchConnectionView | null>(
        ['search-connection', workspace.id, target],
        updated,
      )
      setDrafts((old) => {
        const next = { ...old }
        delete next[target]
        return next
      })
      await client.invalidateQueries({ queryKey: ['search-capability', workspace.id] })
      setNotice(`${scopeLabel(target)}搜索连接已保存。请检查当前实际生效连接。`)
    } catch (error) {
      const errorCode =
        error instanceof ApiError
          ? (error.body as { error?: { code?: string } } | undefined)?.error?.code
          : undefined
      // A directory failure happens before the configuration write, so the draft can be retried.
      const beforeWrite =
        errorCode?.startsWith('search_catalog_') ||
        errorCode?.startsWith('search_http_') ||
        errorCode === 'search_redirect_refused' ||
        errorCode === 'search_access_denied' ||
        errorCode === 'search_rate_limited' ||
        errorCode?.startsWith('web_')
      const conflict =
        (error instanceof ApiError && error.status === 409) ||
        error instanceof TypeError ||
        (error instanceof ApiError && error.status >= 500 && !beforeWrite)
      setDrafts((old) => ({
        ...old,
        [target]: {
          ...draft,
          ...(conflict ? { conflict: describeError(error) } : { error: describeError(error) }),
        },
      }))
    } finally {
      setSaving(false)
    }
  }
  async function reload() {
    setReloading(true)
    setNotice('')
    try {
      const latest = await searchApi.connection(workspace.id, scope)
      client.setQueryData(['search-connection', workspace.id, scope], latest)
      setDrafts((old) => {
        const next = { ...old }
        delete next[scope]
        return next
      })
      setReloadConfirm(false)
    } catch (error) {
      setNotice(describeError(error))
    } finally {
      setReloading(false)
    }
  }
  async function check() {
    if (checking || !effective) return
    setChecking(true)
    setNotice('')
    try {
      const current = await searchApi.check(workspace.id)
      client.setQueryData(['search-capability', workspace.id], current)
      await client.invalidateQueries({ queryKey: ['search-connection', workspace.id] })
      if (current.status === 'ready') setNotice('当前生效连接检查已通过。')
    } catch (error) {
      setNotice(describeError(error))
      await capability.refetch()
    } finally {
      setChecking(false)
    }
  }
  return (
    <>
      <section className="section-card search-connection-card">
        <div className="section-title">
          <h2>
            <Globe size={16} />
            联网搜索
          </h2>
          <Status
            tone={
              capability.data?.status === 'ready'
                ? 'good'
                : capability.data?.status === 'unavailable'
                  ? 'warning'
                  : 'neutral'
            }
          >
            {capability.isPending
              ? '正在读取'
              : capability.data?.status === 'ready'
                ? '可用'
                : capability.data?.status === 'not_configured'
                  ? '尚未配置'
                  : capability.data?.status === 'unavailable'
                    ? '当前不可用'
                    : '状态未知'}
          </Status>
        </div>
        <div className="search-connection-summary">
          {capability.isError ? (
            <div className="form-error" role="alert">
              <p>
                {capability.error instanceof ApiError &&
                (capability.error.status === 404 || capability.error.status === 503)
                  ? '搜索服务尚未就绪。'
                  : describeError(capability.error)}
              </p>
              <Button onClick={() => void capability.refetch()}>
                <RefreshCw size={14} />
                重新读取状态
              </Button>
            </div>
          ) : effective ? (
            <>
              <div>
                <h3>{effective.name}</h3>
                <p>{effective.endpoint}</p>
              </div>
              <dl>
                <dt>当前生效范围</dt>
                <dd>
                  {scopeLabel(effective.scope)}
                  {effective.scope === 'user' ? ' · 工作区继承' : ''}
                </dd>
                <dt>启用状态</dt>
                <dd>{effective.enabled ? '已启用' : '已禁用'}</dd>
                <dt>最近检查</dt>
                <dd>
                  {effective.checked_at
                    ? new Date(effective.checked_at).toLocaleString()
                    : '尚未检查'}
                </dd>
              </dl>
            </>
          ) : !capability.isPending ? (
            <p>还没有生效的搜索连接。展开配置，连接实际部署的 SearxNG 服务。</p>
          ) : (
            <p>正在读取连接与能力状态…</p>
          )}
          {capability.data?.error && (
            <p className="form-error" role="status">
              {capability.data.error.message}
            </p>
          )}
          <div className="search-connection-actions">
            <Button
              disabled={!effective?.enabled || checking || saving}
              onClick={() => void check()}
            >
              <RefreshCw size={14} className={checking ? 'spinning' : ''} />
              {checking ? '正在检查…' : '检查当前生效连接'}
            </Button>
            <Button
              variant="ghost"
              aria-expanded={expanded}
              aria-controls={`search-settings-${workspace.id}`}
              onClick={() => setExpanded(!expanded)}
            >
              <Settings2 size={14} />
              连接配置
              <ChevronDown size={14} />
            </Button>
          </div>
          <p className="page-note">
            连接检查会联系配置的搜索服务；查询和网页读取只在你明确操作时发出。
          </p>
        </div>
        <div
          id={`search-settings-${workspace.id}`}
          hidden={!expanded}
          className="search-settings-panel"
        >
          <div className="segmented search-scope" role="group" aria-label="搜索配置范围">
            {(['user', 'workspace'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={scope === value}
                disabled={saving || reloading}
                onClick={() => setScope(value)}
              >
                {scopeLabel(value)}
              </button>
            ))}
          </div>
          {connection.isError ? (
            <div className="form-error" role="alert">
              <p>{describeError(connection.error)}</p>
              <Button onClick={() => void connection.refetch()}>
                <RefreshCw size={14} />
                读取此范围
              </Button>
            </div>
          ) : connection.data === undefined ? (
            <p className="page-note">正在读取此范围的真实配置…</p>
          ) : (
            <SearchConnectionForm
              key={scope}
              workspaceId={workspace.id}
              active={active && expanded}
              draft={draft}
              connection={connection.data}
              busy={saving || checking || reloading}
              dirty={dirty}
              onChange={(value) => setDrafts((old) => ({ ...old, [scope]: value }))}
              onSave={() => void save()}
              onReload={() => {
                if (dirty || draft.conflict) setReloadConfirm(true)
                else void reload()
              }}
            />
          )}
        </div>
      </section>
      {notice && (
        <p className="search-notice" role="status">
          {notice}
        </p>
      )}
      <SearchTester
        workspaceId={workspace.id}
        capability={capability.data}
        onCapabilityChange={() => void capability.refetch()}
      />
      {reloadConfirm && (
        <Dialog
          title="放弃此范围的配置草稿？"
          onClose={() => {
            if (!reloading) setReloadConfirm(false)
          }}
        >
          <p className="memory-confirm-description">
            重新读取将用服务端此范围的配置替换当前未保存内容。若需保留，请先取消并复制所需字段。
          </p>
          <div className="button-row">
            <Button disabled={reloading} onClick={() => setReloadConfirm(false)}>
              保留草稿
            </Button>
            <Button variant="primary" disabled={reloading} onClick={() => void reload()}>
              {reloading ? '正在读取…' : '放弃草稿并读取'}
            </Button>
          </div>
        </Dialog>
      )}
    </>
  )
}
