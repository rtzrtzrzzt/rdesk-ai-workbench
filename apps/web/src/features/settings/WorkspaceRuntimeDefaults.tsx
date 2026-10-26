import { useSettingsAttention } from './SettingsGroup'
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { settingsApi } from '../../api/settings'
import type {
  ModelConnection,
  RunOptionOverrides,
  Workspace,
  WorkspaceDefaultsView,
} from '../../api/types'
import { Button } from '../../components/ui'
import {
  EffectiveDefaults,
  RuntimeSummary,
  runOptionFields,
  validOption,
  validOverrides,
  ReviewEffortSelect,
} from './runtimeOptions'
import './runtime.css'
import { SandboxSelect } from './SandboxSelect'

export function WorkspaceRuntimeDefaults({
  workspace,
  models,
}: {
  workspace: Workspace
  models: ModelConnection[]
}) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['workspace-runtime-defaults', workspace.id],
    queryFn: () => settingsApi.workspaceRuntime(workspace.id),
  })
  const [editing, setEditing] = useState<WorkspaceDefaultsView | null>(null)
  const [busy, setBusy] = useState(false),
    [conflict, setConflict] = useState(false)
  const [notice, setNotice] = useState('')
  useSettingsAttention(
    'workspace-runtime',
    conflict || query.isError || (!!notice && !!editing),
    '工作区任务默认设置需要核对',
  )
  const [editorOpen, setEditorOpen] = useState(true)
  useEffect(() => {
    if (conflict) setEditorOpen(true)
  }, [conflict])
  const current = editing ?? query.data
  function edit(options: RunOptionOverrides) {
    if (current) setEditing({ ...current, options })
  }
  async function reload() {
    if (editing && !window.confirm('放弃此工作区未保存的配置草稿并重新读取？')) return
    setBusy(true)
    try {
      const result = await query.refetch()
      if (result.error) throw result.error
      setEditing(null)
      setConflict(false)
      setNotice('已读取服务端工作区覆盖与有效来源。')
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '读取未完成。')
    } finally {
      setBusy(false)
    }
  }
  async function save() {
    if (!current || busy || conflict || !validOverrides(current.options)) return
    setBusy(true)
    setNotice('')
    try {
      const saved = await settingsApi.saveWorkspaceRuntime(workspace.id, {
        expected_version: current.version,
        options: current.options,
        model_connection_id: current.model_connection_id,
      })
      client.setQueryData(['workspace-runtime-defaults', workspace.id], saved)
      setEditing(null)
      setConflict(false)
      setNotice('工作区默认已保存，只对新任务生效。')
    } catch (error) {
      setEditing(current)
      setConflict(
        !(error instanceof ApiError) ||
          error.status === 409 ||
          error.status >= 500 ||
          error.status === 408,
      )
      setNotice(error instanceof Error ? error.message : '保存未确认。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="settings-runtime">
      <h2>工作区默认 · {workspace.name}</h2>
      <p className="page-note">
        每项可以覆盖用户 / 系统默认，或恢复继承；保存只作用于新任务。模型生成槽与最多 3 个 Agent
        名额独立。
      </p>
      {query.data && (
        <RuntimeSummary
          options={query.data.effective.options}
          modelId={query.data.effective.model_connection_id}
          models={models}
        />
      )}
      {query.data && <EffectiveDefaults effective={query.data.effective} models={models} />}
      <details
        className="settings-editor"
        open={editorOpen}
        onToggle={(event) => setEditorOpen(event.currentTarget.open)}
      >
        <summary>编辑工作区覆盖{editing ? ' · 有未保存修改' : ''}</summary>
        {current && (
          <form
            className="stacked-form"
            onSubmit={(event) => {
              event.preventDefault()
              void save()
            }}
          >
            <fieldset className="workspace-runtime-fields" disabled={busy || conflict}>
              <label>
                工作区默认模型
                <select
                  value={current.model_connection_id ?? ''}
                  onChange={(event) =>
                    setEditing({ ...current, model_connection_id: event.target.value || null })
                  }
                >
                  <option value="">继承用户默认模型</option>
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
              <label>
                审批方式
                <select
                  value={current.options.approval_mode ?? ''}
                  onChange={(event) =>
                    edit({
                      ...current.options,
                      approval_mode: (event.target.value || null) as 'smart' | 'manual' | null,
                    })
                  }
                >
                  <option value="">继承用户 / 系统</option>
                  <option value="smart">智能审批</option>
                  <option value="manual">手动审批</option>
                </select>
              </label>
              <details className="runtime-advanced" open>
                <summary>高级任务预算与执行设置</summary>
                <ReviewEffortSelect
                  inherit
                  value={current.options.review_reasoning_effort}
                  disabled={busy || conflict}
                  onChange={(review_reasoning_effort) =>
                    edit({ ...current.options, review_reasoning_effort })
                  }
                />
                <div className="run-option-fields">
                  {runOptionFields.map((field) => {
                    const inherited = current.options[field.key] == null
                    return (
                      <div className="workspace-runtime-option" key={field.key}>
                        <label>
                          {field.label}
                          <select
                            aria-label={`${field.label}来源`}
                            value={inherited ? 'inherit' : 'override'}
                            onChange={(event) => {
                              const next = { ...current.options }
                              next[field.key] =
                                event.target.value === 'inherit'
                                  ? null
                                  : (query.data?.effective.options[field.key] ?? null)
                              edit(next)
                            }}
                          >
                            <option value="inherit">继承用户 / 系统</option>
                            <option
                              value="override"
                              disabled={
                                !validOption(field.key, query.data?.effective.options[field.key])
                              }
                            >
                              工作区覆盖
                            </option>
                          </select>
                        </label>
                        {!inherited && (
                          <label>
                            覆盖值
                            <input
                              aria-label={`${field.label}工作区覆盖值`}
                              type="number"
                              min={field.min}
                              max={field.max}
                              step={field.integer ? 1 : 'any'}
                              value={
                                Number.isFinite(current.options[field.key])
                                  ? (current.options[field.key] ?? '')
                                  : ''
                              }
                              onChange={(event) => {
                                const next = { ...current.options }
                                next[field.key] =
                                  event.target.value === '' ? NaN : Number(event.target.value)
                                edit(next)
                              }}
                              required
                            />
                          </label>
                        )}
                        <small>
                          有效范围 {field.min}–{field.max}
                          {inherited ? ' · 本项不保存覆盖值' : ''}
                        </small>
                      </div>
                    )
                  })}
                </div>
                <SandboxSelect
                  value={current.options.sandbox_profile}
                  inherit
                  disabled={busy || conflict}
                  onChange={(sandbox_profile) => edit({ ...current.options, sandbox_profile })}
                />
              </details>
              <Button
                onClick={() => {
                  const options: RunOptionOverrides = {
                    sandbox_profile: null,
                    approval_mode: null,
                    review_reasoning_effort: null,
                  }
                  runOptionFields.forEach((field) => {
                    options[field.key] = null
                  })
                  setEditing({ ...current, options, model_connection_id: null })
                }}
              >
                全部改为继承（保存后生效）
              </Button>
            </fieldset>
            <small className="muted">工作区配置版本 {current.version}</small>
            {!validOverrides(current.options) && (
              <p className="form-error">请填写范围内的覆盖值，或明确选择继承。</p>
            )}
            <Button
              variant="primary"
              type="submit"
              disabled={busy || conflict || !validOverrides(current.options)}
            >
              保存工作区默认
            </Button>
          </form>
        )}
      </details>
      {conflict && (
        <p className="memory-conflict">
          保存结果或版本需要核对。草稿仍保留，不自动覆盖；重新读取前请复制需要保留的设置。
        </p>
      )}
      {query.error && (
        <p role="alert" className="form-error">
          {query.error.message}
        </p>
      )}
      {!query.data && !query.error && <p className="page-note">正在读取工作区默认…</p>}
      <Button disabled={busy} onClick={() => void reload()}>
        {editing ? '放弃草稿并重新读取' : '重新读取工作区默认'}
      </Button>
      {notice && (
        <p role="status" className={conflict ? 'form-error' : 'page-note'}>
          {notice}
        </p>
      )}
    </section>
  )
}
