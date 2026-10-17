import { RefreshCw, Save } from 'lucide-react'
import type { SearchConnectionSave, SearchConnectionView } from '../../api/types'
import { Button, Status } from '../../components/ui'

export type SearchSettingsDraft = {
  settings: SearchConnectionSave
  enginesText: string
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
    enginesText: connection?.engines.join(', ') ?? '',
  }
}
export function SearchConnectionForm({
  draft,
  connection,
  busy,
  dirty,
  onChange,
  onSave,
  onReload,
}: {
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
  const regionInvalid =
    !!settings.region && (settings.language === 'auto' || settings.language === 'all')
  return (
    <form
      className="search-connection-form"
      onSubmit={(event) => {
        event.preventDefault()
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
          引擎（逗号分隔）
          <input
            value={draft.enginesText}
            placeholder="留空不指定引擎"
            onChange={(event) =>
              onChange({ ...draft, enginesText: event.target.value, error: undefined })
            }
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
          disabled={busy || !dirty || !!draft.conflict || regionInvalid}
        >
          <Save size={14} />
          {busy ? '正在保存…' : '保存连接'}
        </Button>
      </div>
    </form>
  )
}
