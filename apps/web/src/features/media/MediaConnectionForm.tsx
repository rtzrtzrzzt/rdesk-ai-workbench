import { useRef, useState } from 'react'
import { ApiError } from '../../api/client'
import {
  mediaApi,
  type MediaConfiguration,
  type MediaConnectionSave,
  type MediaConnectionView,
  type MediaDriver,
  type MediaKind,
} from '../../api/media'
import { closePendingRequest } from '../../api/requests'
import { Button } from '../../components/ui'
import { DirectoryPicker } from '../workspaces/DirectoryPicker'
import { capabilities, describeError, drivers, nativeSuggestion, record } from './shared'

export function MediaConnectionForm({
  workspaceId,
  initial,
  native,
  onSaved,
  onCancel,
  onLockChange,
}: {
  workspaceId: string
  initial?: MediaConnectionView
  native: unknown
  onSaved: () => Promise<void>
  onCancel: () => void
  onLockChange: (locked: boolean) => void
}) {
  const [id] = useState(() => initial?.id ?? crypto.randomUUID())
  const [name, setName] = useState(initial?.name ?? '')
  const [driver, setDriver] = useState<MediaDriver>(initial?.driver ?? 'flux3_native')
  const [capability, setCapability] = useState<MediaKind>(initial?.capability ?? 'image')
  const [model, setModel] = useState(initial?.model_identifier ?? '')
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? '')
  const [scope, setScope] = useState<'user' | 'workspace'>(
    initial?.scope === 'workspace' ? 'workspace' : 'user',
  )
  const [configuration, setConfiguration] = useState<MediaConfiguration>(
    initial?.configuration ?? {},
  )
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [remote, setRemote] = useState(initial?.allow_remote ?? false)
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? true)
  const [choosingDirectory, setChoosingDirectory] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [error, setError] = useState('')
  const submitted = useRef<MediaConnectionSave | null>(null)
  const inFlight = useRef(false)
  const suggestion = nativeSuggestion(native)
  const nativeState = record(native)
  const supported = drivers.find((item) => item.id === driver)?.capabilities ?? []
  const locked = busy || uncertain
  function settleLock(busy: boolean, unknown: boolean) {
    setBusy(busy)
    setUncertain(unknown)
    onLockChange(busy || unknown)
  }
  async function saved() {
    setApiKey('')
    submitted.current = null
    settleLock(false, false)
    await onSaved()
  }
  async function save() {
    if (inFlight.current || uncertain) return
    submitted.current ??= {
      name: name.trim(),
      driver,
      capability,
      model_identifier: model.trim(),
      endpoint: driver === 'flux3_native' ? null : endpoint.trim(),
      scope,
      configuration,
      enabled,
      is_default: isDefault,
      allow_remote: driver !== 'flux3_native' && remote,
      ...(apiKey ? { api_key: apiKey } : {}),
      clear_api_key: clearKey,
      expected_version: initial?.version ?? 0,
      request_id: crypto.randomUUID(),
    }
    inFlight.current = true
    settleLock(true, false)
    setError('')
    try {
      await mediaApi.saveConnection(workspaceId, id, submitted.current)
      await saved()
    } catch (error) {
      const unknown = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      if (!unknown) submitted.current = null
      settleLock(false, unknown)
      setError(
        `${describeError(error)}${unknown ? ' 原保存请求仍在内存中，密钥不会写入浏览器存储；请先核对。' : ''}`,
      )
    } finally {
      inFlight.current = false
    }
  }
  async function reconcile(close = false) {
    if (inFlight.current || !submitted.current) return
    inFlight.current = true
    settleLock(true, true)
    setError('')
    try {
      if (close) {
        const closure = await closePendingRequest(submitted.current.request_id)
        if (closure.status === 'sealed') {
          submitted.current = null
          settleLock(false, false)
          return
        }
      }
      const result = record(await mediaApi.receipt(submitted.current.request_id))
      if (result?.id !== id || typeof result.model_identifier !== 'string')
        throw new Error('保存回执与当前连接不匹配，原请求仍保留。')
      await saved()
    } catch (error) {
      settleLock(false, true)
      setError(
        error instanceof ApiError && error.status === 404
          ? '尚未查到保存回执，请稍后核对；这不代表保存已失败。'
          : describeError(error),
      )
    } finally {
      inFlight.current = false
    }
  }
  return (
    <form
      className="stacked-form media-connection-form"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <label>
        连接名称
        <input
          required
          maxLength={160}
          value={name}
          disabled={locked}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label>
        接入方式
        <select
          value={driver}
          disabled={locked}
          onChange={(event) => {
            const selected = event.target.value as MediaDriver
            setDriver(selected)
            if (!drivers.find((item) => item.id === selected)?.capabilities.includes(capability))
              setCapability('image')
            setApiKey('')
          }}
        >
          {drivers.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        生成能力
        <select
          value={capability}
          disabled={locked || supported.length === 1}
          onChange={(event) => setCapability(event.target.value as MediaKind)}
        >
          {capabilities
            .filter((item) => supported.includes(item.id))
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.noun}
              </option>
            ))}
        </select>
      </label>
      {driver === 'flux3_native' && (
        <>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={configuration.expand_prompt ?? true}
              disabled={locked}
              onChange={(event) =>
                setConfiguration({ ...configuration, expand_prompt: event.target.checked })
              }
            />
            使用默认本地对话模型优化提示词
          </label>
          <p className="page-note">无需另加载提示词模型；关闭后直接使用你的描述。</p>
          <Button
            disabled={locked || !suggestion}
            onClick={() => {
              if (!suggestion) return
              setConfiguration((previous) => ({
                ...previous,
                model_directory: suggestion.model_directory,
              }))
              setModel(suggestion.model_identifier)
              setName((previous) => previous || 'FLUX3 本机')
            }}
          >
            填入本机预设配置
          </Button>
          {!suggestion && (
            <p className="page-note">未发现可直接填入的本机配置，可填写已准备好的模型目录。</p>
          )}
          {nativeState?.prepared === false && (
            <p className="page-note">本机模型文件尚未准备完整，填入预设路径不会下载模型。</p>
          )}
          {nativeState?.runtime_prepared === false && (
            <p className="page-note">本机生成运行环境尚未准备完成。</p>
          )}
          <label>
            本机模型目录
            <input
              required
              maxLength={4096}
              value={configuration.model_directory ?? ''}
              disabled={locked}
              onChange={(event) =>
                setConfiguration({ ...configuration, model_directory: event.target.value })
              }
            />
          </label>
          <Button disabled={locked} onClick={() => setChoosingDirectory(!choosingDirectory)}>
            {choosingDirectory ? '收起目录' : '选择模型目录'}
          </Button>
          {choosingDirectory && (
            <DirectoryPicker
              initialPath={configuration.model_directory || undefined}
              onSelect={(model_directory) => {
                setConfiguration({ ...configuration, model_directory })
                setChoosingDirectory(false)
              }}
              onCancel={() => setChoosingDirectory(false)}
            />
          )}
        </>
      )}
      <label>
        模型标识
        <input
          required
          maxLength={256}
          value={model}
          disabled={locked}
          onChange={(event) => setModel(event.target.value)}
        />
      </label>
      {driver !== 'flux3_native' && (
        <>
          <label>
            服务地址
            <input
              required
              type="url"
              maxLength={2048}
              value={endpoint}
              disabled={locked}
              onChange={(event) => setEndpoint(event.target.value)}
            />
          </label>
          <label>
            API 密钥{initial ? '（留空保留原密钥）' : '（可选）'}
            <input
              type="password"
              autoComplete="off"
              maxLength={8192}
              value={apiKey}
              disabled={locked || clearKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          {initial && (
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={clearKey}
                disabled={locked}
                onChange={(event) => {
                  setClearKey(event.target.checked)
                  setApiKey('')
                }}
              />
              <span>清除已保存密钥（{initial.credential_configured ? '已配置' : '未配置'}）</span>
            </label>
          )}
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={remote}
              disabled={locked}
              onChange={(event) => setRemote(event.target.checked)}
            />
            <span>允许向远程生成服务发送本次任务内容</span>
          </label>
        </>
      )}
      <p className="page-note">
        {driver === 'flux3_native'
          ? '使用已准备好的 FLUX3 本机运行环境，不会在保存时下载或加载权重。'
          : driver === 'media_http'
            ? '服务必须实现 workbench-media/1 协议，填写模型名不会自动适配任意生成服务。'
            : '接入支持 Images 接口的图像模型；实际可用能力由服务和模型决定。'}
      </p>
      <label>
        作用范围
        <select
          value={scope}
          disabled={locked}
          onChange={(event) => setScope(event.target.value as 'user' | 'workspace')}
        >
          <option value="user">全局 · 我的所有工作区</option>
          <option value="workspace">当前工作区</option>
        </select>
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={enabled}
          disabled={locked}
          onChange={(event) => setEnabled(event.target.checked)}
        />
        <span>启用此连接</span>
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={isDefault}
          disabled={locked}
          onChange={(event) => setIsDefault(event.target.checked)}
        />
        <span>作为此类生成的默认连接</span>
      </label>
      <details className="settings-editor" open>
        <summary>高级配置</summary>
        <div className="settings-editor-content">
          <label>
            执行时限（秒，留空使用后台预设）
            <input
              type="number"
              min={30}
              max={86400}
              value={configuration.timeout_seconds ?? ''}
              disabled={locked}
              onChange={(event) =>
                setConfiguration({
                  ...configuration,
                  timeout_seconds: event.target.value ? Number(event.target.value) : undefined,
                })
              }
            />
          </label>
          {driver === 'flux3_native' && (
            <>
              <label>
                阶段内存预算（GiB，留空使用后台预设）
                <input
                  type="number"
                  min={2}
                  max={120}
                  step="any"
                  value={configuration.memory_limit_gib ?? ''}
                  disabled={locked}
                  onChange={(event) =>
                    setConfiguration({
                      ...configuration,
                      memory_limit_gib: event.target.value ? Number(event.target.value) : undefined,
                    })
                  }
                />
              </label>
              <label>
                系统内存余量（GiB，留空使用后台预设）
                <input
                  type="number"
                  min={8.5}
                  max={120}
                  step="any"
                  value={configuration.headroom_gib ?? ''}
                  disabled={locked}
                  onChange={(event) =>
                    setConfiguration({
                      ...configuration,
                      headroom_gib: event.target.value ? Number(event.target.value) : undefined,
                    })
                  }
                />
              </label>
              <label>
                GPU allocator 限额（GiB，留空使用后台预设）
                <input
                  type="number"
                  min={1}
                  max={120}
                  step="any"
                  value={configuration.allocator_limit_gib ?? ''}
                  disabled={locked}
                  onChange={(event) =>
                    setConfiguration({
                      ...configuration,
                      allocator_limit_gib: event.target.value
                        ? Number(event.target.value)
                        : undefined,
                    })
                  }
                />
              </label>
              <label>
                注意力实现
                <select
                  value={configuration.attention_backend ?? 'cudnn'}
                  disabled={locked}
                  onChange={(event) =>
                    setConfiguration({
                      ...configuration,
                      attention_backend: event.target.value as 'cudnn' | 'torch_sdpa',
                    })
                  }
                >
                  <option value="cudnn">cuDNN</option>
                  <option value="torch_sdpa">PyTorch SDPA</option>
                </select>
              </label>
            </>
          )}
        </div>
      </details>
      <p className="page-note">保存不会发起生成；密钥只用于连接配置，保存后清空输入。</p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {uncertain && (
        <div className="button-row">
          <Button disabled={busy} onClick={() => void reconcile()}>
            核对保存结果
          </Button>
          <Button disabled={busy} onClick={() => void reconcile(true)}>
            结束核对并解锁编辑
          </Button>
        </div>
      )}
      <div className="button-row">
        <Button disabled={locked} onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" disabled={locked || choosingDirectory}>
          {busy ? '正在保存…' : '保存连接'}
        </Button>
      </div>
    </form>
  )
}
