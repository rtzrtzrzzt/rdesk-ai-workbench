import { useSettingsAttention } from './SettingsGroup'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { settingsApi } from '../../api/settings'
import type { DefaultsView, ModelConnection } from '../../api/types'
import { Button } from '../../components/ui'
import { RunOptionsFields, RuntimeSummary, validRunOptions } from './runtimeOptions'
export function RuntimeDefaults({ models }: { models: ModelConnection[] }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ['runtime-defaults'], queryFn: settingsApi.runtime })
  const [editing, setEditing] = useState<DefaultsView | null>(null)
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [message, setMessage] = useState('')
  useSettingsAttention(
    'user-runtime',
    conflict || query.isError || (!!message && !!editing),
    '用户任务默认设置需要核对',
  )
  const [editorOpen, setEditorOpen] = useState(true)
  useEffect(() => {
    if (conflict) setEditorOpen(true)
  }, [conflict])
  const current = editing ?? query.data
  async function reload() {
    if (editing && !window.confirm('放弃未保存的用户默认配置并重新读取？')) return
    setBusy(true)
    const result = await query.refetch()
    if (result.data && !result.isError) {
      setEditing(null)
      setConflict(false)
      setMessage('已重新读取，未保存的修改已放弃。')
    }
    if (result.error) setMessage(result.error.message)
    setBusy(false)
  }
  return (
    <section className="settings-runtime">
      <h2>用户任务默认设置</h2>
      <p className="page-note">
        保存到当前用户，只作用于新任务；已有任务使用创建时冻结的配置。Agent 名额最多 3
        个，模型生成槽独立{query.data?.model_generation_capacity === 1 ? '，当前为 1 个' : ''}。
      </p>
      {query.data && (
        <RuntimeSummary
          options={query.data.options}
          modelId={query.data.model_connection_id}
          models={models}
        />
      )}
      <details
        className="settings-editor"
        open={editorOpen}
        onToggle={(event) => setEditorOpen(event.currentTarget.open)}
      >
        <summary>编辑用户默认设置{editing ? ' · 有未保存修改' : ''}</summary>
        {current ? (
          <form
            className="stacked-form"
            onSubmit={(event) => {
              event.preventDefault()
              if (busy || conflict || !validRunOptions(current.options)) return
              setBusy(true)
              setMessage('')
              void settingsApi
                .saveRuntime({
                  expected_version: current.version,
                  options: current.options,
                  model_connection_id: current.model_connection_id ?? null,
                })
                .then(async (saved) => {
                  client.setQueryData(['runtime-defaults'], saved)
                  setEditing(null)
                  setMessage('用户默认已保存，仅新任务生效。')
                  await client.invalidateQueries({ queryKey: ['workspace-runtime-defaults'] })
                })
                .catch((error) => {
                  setEditing(current)
                  setConflict(
                    !(error instanceof ApiError) ||
                      error.status === 409 ||
                      error.status >= 500 ||
                      error.status === 408,
                  )
                  setMessage(error instanceof Error ? error.message : '保存未完成')
                })
                .finally(() => setBusy(false))
            }}
          >
            <label>
              用户默认模型
              <select
                value={current.model_connection_id ?? ''}
                disabled={busy || conflict}
                onChange={(event) =>
                  setEditing({ ...current, model_connection_id: event.target.value || null })
                }
              >
                <option value="">不设置用户默认模型</option>
                {current.model_connection_id &&
                  !models.some((model) => model.id === current.model_connection_id) && (
                    <option value={current.model_connection_id}>
                      当前连接不在列表：{current.model_connection_id}
                    </option>
                  )}
                {models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name} ·{' '}
                    {model.service_status === 'ready'
                      ? '连接检查就绪'
                      : model.service_status === 'unavailable'
                        ? '当前不可用'
                        : '未检查'}
                  </option>
                ))}
              </select>
            </label>
            <RunOptionsFields
              value={current.options}
              disabled={busy || conflict}
              onChange={(options) => setEditing({ ...current, options })}
            />
            <small className="muted">用户配置版本 {current.version}</small>
            {conflict && (
              <div className="memory-conflict">
                <p>
                  版本或保存结果需要核对。先保留所需内容，再明确放弃草稿重新读取；不会自动重发。
                </p>
                <Button disabled={busy} onClick={() => void reload()}>
                  放弃草稿并重新读取
                </Button>
              </div>
            )}
            <Button
              type="submit"
              variant="primary"
              disabled={busy || conflict || !validRunOptions(current.options)}
            >
              {busy ? '正在保存…' : '保存用户默认'}
            </Button>
          </form>
        ) : (
          <p className="page-note">正在读取真实配置…</p>
        )}
      </details>
      {query.isError && (
        <div role="alert">
          <p className="form-error">{query.error.message}</p>
          <Button disabled={busy} onClick={() => void reload()}>
            重新读取
          </Button>
        </div>
      )}
      {message && (
        <p role="status" className={conflict ? 'form-error' : 'page-note'}>
          {message}
        </p>
      )}
    </section>
  )
}
