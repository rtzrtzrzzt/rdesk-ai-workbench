import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Save, X } from 'lucide-react'
import { searchApi } from '../../api/search'
import type { SearchConnectionSave, SearchConnectionView } from '../../api/types'
import { Button, Status } from '../../components/ui'
import './search-engines.css'

export type SearchSettingsDraft = {
  settings: SearchConnectionSave
  engineMode: 'default' | 'custom'
  error?: string
  conflict?: string
}
export function connectionDraft(
  scope: SearchConnectionSave['scope'],
  connection: SearchConnectionView | null,
): SearchSettingsDraft {
  return {
    settings: {
      scope,
      name: connection?.name ?? '',
      endpoint: connection?.endpoint ?? '',
      enabled: connection?.enabled ?? true,
      engines: connection?.engines ?? [],
      language: connection?.language ?? 'auto',
      region: connection?.region ?? null,
      max_results: connection?.max_results ?? 5,
      timeout_seconds: connection?.timeout_seconds ?? 20,
      expected_version: connection?.version ?? null,
    },
    engineMode: connection?.engines.length ? 'custom' : 'default',
  }
}
export function SearchConnectionForm({
  workspaceId,
  active,
  draft,
  connection,
  busy,
  dirty,
  onChange,
  onSave,
  onReload,
}: {
  workspaceId: string
  active: boolean
  draft: SearchSettingsDraft
  connection: SearchConnectionView | null
  busy: boolean
  dirty: boolean
  onChange: (draft: SearchSettingsDraft) => void
  onSave: () => void
  onReload: () => void
}) {
  const settings = draft.settings
  function change(values: Partial<SearchConnectionSave>) {
    onChange({ ...draft, settings: { ...settings, ...values }, error: undefined })
  }
  const [filter, setFilter] = useState('')
  const [category, setCategory] = useState('')
  const endpoint = settings.endpoint.trim().replace(/\/+$/, '')
  const catalog = useQuery({
    queryKey: ['search-engines', workspaceId, endpoint, settings.timeout_seconds],
    queryFn: () =>
      searchApi.engines(workspaceId, {
        endpoint,
        timeout_seconds: settings.timeout_seconds,
      }),
    // Existing connections load immediately; edited endpoints load only on an explicit click.
    enabled: active && !!endpoint && endpoint === connection?.endpoint,
    retry: false,
    staleTime: 30_000,
  })
  const engines = catalog.data?.engines ?? []
  const categories = [...new Set(engines.flatMap((engine) => engine.categories))].sort()
  const selected = settings.engines ?? []
  const unknown = catalog.data
    ? selected.filter((name) => !engines.some((engine) => engine.name === name))
    : []
  const customEmpty = draft.engineMode === 'custom' && selected.length === 0
  const visibleEngines = engines.filter(
    (engine) =>
      (!category || engine.categories.includes(category)) &&
      (!filter.trim() ||
        engine.name.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase())),
  )
  function chooseEngine(name: string, checked: boolean) {
    change({ engines: checked ? [...selected, name] : selected.filter((value) => value !== name) })
  }
  const regionInvalid =
    !!settings.region && (settings.language === 'auto' || settings.language === 'all')
  return (
    <form
      className="search-connection-form"
      onSubmit={(event) => {
        event.preventDefault()
        if (!busy && dirty && !draft.conflict && !regionInvalid && !customEmpty && !unknown.length)
          onSave()
      }}
    >
      <div className="search-settings-heading">
        <Status tone={dirty ? 'warning' : 'neutral'}>
          {dirty ? '有未保存修改' : connection ? '已读取配置' : '该范围尚未配置'}
        </Status>
        <span>
          版本：{settings.expected_version ?? '新建'}
          {connection && settings.expected_version !== connection.version
            ? ` · 服务端当前版本 ${connection.version}`
            : ''}
        </span>
      </div>
      <div className="search-settings-grid">
        <label>
          连接名称
          <input
            required
            maxLength={100}
            value={settings.name}
            onChange={(event) => change({ name: event.target.value })}
            disabled={busy}
          />
        </label>
        <label>
          SearxNG 端点
          <input
            required
            type="url"
            maxLength={2048}
            placeholder="例如 http://127.0.0.1:8888"
            value={settings.endpoint}
            onChange={(event) => change({ endpoint: event.target.value })}
            disabled={busy}
          />
        </label>
        <label>
          语言
          <input
            required
            minLength={2}
            maxLength={20}
            pattern="[A-Za-z][A-Za-z0-9-]*"
            placeholder="auto 或 zh"
            value={settings.language ?? 'auto'}
            onChange={(event) => change({ language: event.target.value })}
            disabled={busy}
          />
        </label>
        <label>
          地区（可选）
          <input
            maxLength={2}
            pattern="[A-Z]{2}"
            placeholder="例如 CN"
            value={settings.region ?? ''}
            onChange={(event) => change({ region: event.target.value.toUpperCase() || null })}
            disabled={busy}
          />
        </label>
        <label>
          最多结果数
          <input
            required
            type="number"
            min={1}
            max={20}
            value={settings.max_results ?? 5}
            onChange={(event) => change({ max_results: Number(event.target.value) })}
            disabled={busy}
          />
        </label>
        <label>
          请求超时（秒）
          <input
            required
            type="number"
            min={3}
            max={60}
            value={settings.timeout_seconds ?? 20}
            onChange={(event) => change({ timeout_seconds: Number(event.target.value) })}
            disabled={busy}
          />
        </label>
      </div>
      <fieldset className="search-engine-picker" disabled={busy}>
        <legend>搜索引擎</legend>
        <div className="search-engine-modes">
          <label className="checkbox-label">
            <input
              type="radio"
              name={`engine-mode-${workspaceId}-${settings.scope}`}
              checked={draft.engineMode === 'default'}
              onChange={() =>
                onChange({
                  ...draft,
                  engineMode: 'default',
                  settings: { ...settings, engines: [] },
                  error: undefined,
                })
              }
            />
            使用 SearXNG 默认引擎
          </label>
          <label className="checkbox-label">
            <input
              type="radio"
              name={`engine-mode-${workspaceId}-${settings.scope}`}
              checked={draft.engineMode === 'custom'}
              onChange={() => onChange({ ...draft, engineMode: 'custom', error: undefined })}
            />
            自选引擎（最多 10 个）
          </label>
        </div>
        <p className="page-note">
          默认模式跟随此服务的配置；自选仅作用于此连接，不会修改 SearXNG 全局设置。
          目录中的“服务默认”表示默认参与选择，不代表引擎已通过可用性检查。
        </p>
        <div className="search-engine-toolbar">
          <Button
            onClick={() => void catalog.refetch()}
            disabled={busy || !endpoint || catalog.isFetching}
          >
            <RefreshCw size={14} className={catalog.isFetching ? 'spinning' : ''} />
            {catalog.isFetching ? '正在读取目录…' : catalog.data ? '刷新引擎目录' : '读取引擎目录'}
          </Button>
          {catalog.data && (
            <span>
              此服务公开 {engines.length} 个引擎 · 已选 {selected.length}/10
            </span>
          )}
        </div>
        {catalog.isError && (
          <p className="form-error" role="alert">
            {catalog.error instanceof Error ? catalog.error.message : '无法读取引擎目录。'}{' '}
            已有选择已保留，请检查端点后重试。
          </p>
        )}
        {selected.length > 0 && (
          <div className="search-engine-selected" aria-label="已选择的引擎">
            {selected.map((name) => (
              <span key={name} className={unknown.includes(name) ? 'search-engine-unknown' : ''}>
                {name}
                {unknown.includes(name)
                  ? '（当前目录无此名称）'
                  : !catalog.data
                    ? '（待目录核对）'
                    : ''}
                <Button
                  aria-label={`移除引擎 ${name}`}
                  variant="ghost"
                  disabled={busy}
                  onClick={() => chooseEngine(name, false)}
                >
                  <X size={12} />
                </Button>
              </span>
            ))}
          </div>
        )}
        {unknown.length > 0 && (
          <p className="form-error">请移除当前目录中不存在的旧引擎名称，再保存自选配置。</p>
        )}
        {customEmpty && (
          <p className="form-error">请至少勾选一个引擎，或切换为使用 SearXNG 默认引擎。</p>
        )}
        {catalog.data ? (
          <>
            <div className="search-settings-grid search-engine-filters">
              <label>
                查找引擎
                <input
                  type="search"
                  placeholder="按引擎名称查找"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                />
              </label>
              <label>
                分类
                <select value={category} onChange={(event) => setCategory(event.target.value)}>
                  <option value="">全部分类</option>
                  {categories.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="search-engine-list" aria-label="此服务的真实引擎目录">
              {visibleEngines.map((engine) => (
                <label key={engine.name} className="checkbox-label search-engine-option">
                  <input
                    type="checkbox"
                    checked={
                      draft.engineMode === 'default'
                        ? engine.default_enabled
                        : selected.includes(engine.name)
                    }
                    disabled={
                      busy ||
                      draft.engineMode === 'default' ||
                      (!selected.includes(engine.name) && selected.length >= 10)
                    }
                    onChange={(event) => chooseEngine(engine.name, event.target.checked)}
                  />
                  <span>
                    <strong>{engine.name}</strong>
                    <small>{engine.categories.join(' · ') || '未分类'}</small>
                  </span>
                  <span className="search-engine-default">
                    {engine.default_enabled ? '服务默认' : '非默认'}
                  </span>
                </label>
              ))}
              {visibleEngines.length === 0 && <p className="page-note">没有符合查找条件的引擎。</p>}
            </div>
            <p className="page-note">
              目录读取时间：{new Date(catalog.data.fetched_at).toLocaleString()}
              。保存自选配置及执行搜索时会重新核对名称。
            </p>
          </>
        ) : (
          !catalog.isFetching &&
          !catalog.isError && (
            <p className="page-note">填写端点后读取该服务的实际目录，再勾选引擎。</p>
          )
        )}
      </fieldset>
      <label className="checkbox-label search-enable">
        <input
          type="checkbox"
          checked={settings.enabled ?? true}
          onChange={(event) => change({ enabled: event.target.checked })}
          disabled={busy}
        />
        <span>启用此范围的搜索连接</span>
      </label>
      <p className="page-note">
        地区需要明确语言，不能与 auto 或 all
        同时使用。工作区配置优先于用户级配置；保存后请检查当前实际生效连接。
      </p>
      {regionInvalid && <p className="form-error">指定地区时，请选择具体语言。</p>}
      {draft.conflict && (
        <div className="memory-conflict" role="alert">
          <strong>保存结果需要核对</strong>
          <p>{draft.conflict}</p>
          <p>本地草稿仍保留。请重新读取此范围的配置后再保存。</p>
        </div>
      )}
      {draft.error && (
        <p className="form-error" role="alert">
          {draft.error}
        </p>
      )}
      {connection?.last_error && (
        <p className="form-error">此连接最近错误：{connection.last_error.message}</p>
      )}
      <div className="button-row">
        <Button onClick={onReload} disabled={busy}>
          <RefreshCw size={14} />
          重新读取
        </Button>
        <Button
          type="submit"
          variant="primary"
          disabled={
            busy || !dirty || !!draft.conflict || regionInvalid || customEmpty || unknown.length > 0
          }
        >
          <Save size={14} />
          {busy ? '正在保存…' : '保存连接'}
        </Button>
      </div>
    </form>
  )
}
