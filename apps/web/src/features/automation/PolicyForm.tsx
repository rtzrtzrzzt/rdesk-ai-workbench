import { useId, useState } from 'react'
import { automationApi } from '../../api/automation'
import type { AutomationCapabilities, AutomationPolicy, AutomationSave } from '../../api/automation'
import { Button } from '../../components/ui'
import { errorText, exactOrigins, number, record, text, uncertain } from './data'

function policyDraft(policy: AutomationPolicy) {
  return {
    browser: policy.configuration.browser_enabled === true,
    origins: (policy.configuration.browser_origins ?? []).join('\n'),
    localOrigins: (policy.configuration.browser_local_origins ?? []).join('\n'),
    capture: policy.configuration.desktop_capture === true,
    control: policy.configuration.desktop_control === true,
    windowId: policy.configuration.target_window_id ?? '',
    pid: policy.configuration.target_window_pid?.toString() ?? '',
    takeover: policy.configuration.manual_takeover_ack === true,
    remote: policy.configuration.remote_model_revisions ?? [],
    ttl: '',
  }
}
export function PolicyForm({
  workspaceId,
  policy,
  capabilities,
  onSaved,
  onReadForeground,
}: {
  workspaceId: string
  policy: AutomationPolicy
  capabilities?: AutomationCapabilities
  onSaved: () => Promise<AutomationPolicy>
  onReadForeground: () => Promise<AutomationCapabilities>
}) {
  const id = useId()
  const [draft, setDraft] = useState(() => policyDraft(policy))
  const [baseVersion, setBaseVersion] = useState(policy.version)
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<AutomationSave | null>(null)
  const [message, setMessage] = useState('')
  const [reload, setReload] = useState(false)
  const [foregroundBusy, setForegroundBusy] = useState(false)
  const models = (capabilities?.remote_models ?? []).flatMap((value) => {
    const item = record(value)
    if (!item) return []
    const modelId = text(item.id),
      version = number(item.version),
      name = text(item.name),
      endpoint = text(item.endpoint),
      target = text(item.target)
    return modelId && version !== null && name && endpoint && target === `${modelId}:${version}`
      ? [{ id: modelId, version, name, endpoint, target }]
      : []
  })
  const knownTargets = new Set(models.map((model) => model.target))
  async function save() {
    if (busy || pending) return
    setMessage('')
    let input: AutomationSave
    try {
      const pid = draft.pid.trim() ? Number(draft.pid) : null
      if (pid !== null && (!Number.isInteger(pid) || pid < 1)) throw new Error('PID 必须是正整数。')
      const ttl = draft.ttl.trim() ? Number(draft.ttl) : undefined
      if (ttl !== undefined && (!Number.isInteger(ttl) || ttl < 10 || ttl > 900))
        throw new Error('桌面授权期限须为 10–900 秒。')
      if (
        draft.control &&
        (!draft.capture || !draft.windowId.trim() || pid === null || !draft.takeover)
      )
        throw new Error('控制需要截屏授权、精确窗口与 PID，以及人工接管确认。')
      input = {
        expected_version: baseVersion,
        request_id: crypto.randomUUID(),
        ...(ttl !== undefined ? { grant_ttl_seconds: ttl } : {}),
        configuration: {
          browser_enabled: draft.browser,
          browser_origins: exactOrigins(draft.origins, 24),
          browser_local_origins: exactOrigins(draft.localOrigins, 4),
          desktop_capture: draft.capture,
          desktop_control: draft.control,
          target_window_id: draft.windowId.trim() || null,
          target_window_pid: pid,
          manual_takeover_ack: draft.takeover,
          remote_model_revisions: draft.remote,
        },
      }
    } catch (error) {
      setMessage(errorText(error))
      return
    }
    setBusy(true)
    setPending(input)
    try {
      const saved = await automationApi.save(workspaceId, input)
      setPending(null)
      setBaseVersion(saved.version)
      setDraft(policyDraft(saved))
      setMessage('授权已保存。旧会话会停止；请新建会话采用此版本。运行时未自动启动。')
      await onSaved().catch((error) =>
        setMessage(`授权已保存，但刷新状态失败：${errorText(error)}`),
      )
    } catch (error) {
      if (uncertain(error))
        setMessage(`保存结果未知，已保留本次配置与请求标识，不会自动重发。${errorText(error)}`)
      else {
        setPending(null)
        setMessage(errorText(error))
      }
    } finally {
      setBusy(false)
    }
  }
  async function copyForeground() {
    setForegroundBusy(true)
    try {
      const result = await onReadForeground()
      const foreground = record(result.foreground)
      const windowId = text(foreground?.window_id),
        pid = number(foreground?.window_pid)
      if (!windowId || pid === null || !Number.isInteger(pid) || pid < 1)
        throw new Error(text(foreground?.reason) ?? '当前无法读取精确前台窗口与 PID。')
      setDraft((old) => ({ ...old, windowId, pid: String(pid), control: false, takeover: false }))
      setMessage('已读取前台窗口元数据；未截图。请核对目标，再明确授权控制。')
    } catch (error) {
      setMessage(errorText(error))
    } finally {
      setForegroundBusy(false)
    }
  }
  return (
    <section className="automation-section">
      <div className="run-line">
        <h2>本工作区授权</h2>
        <span className="page-note">
          服务器版本 {policy.version} · 草稿基于 {baseVersion}
        </span>
      </div>
      <p className="page-note">
        授权和运行时独立。每次保存都会停止旧版本会话；网页代理提供应用约束，不是操作系统沙箱。
      </p>
      <p className="page-note">
        桌面授权到期：
        {policy.grant_expires_at
          ? new Date(policy.grant_expires_at).toLocaleString()
          : '当前没有有效期限'}
      </p>
      <details>
        <summary>配置网页、截屏与控制权限</summary>
        <fieldset disabled={busy || !!pending} className="automation-fields">
          <label className="automation-check">
            <input
              type="checkbox"
              checked={draft.browser}
              onChange={(e) => setDraft({ ...draft, browser: e.target.checked })}
            />
            允许浏览器访问明确列出的网页
          </label>
          <label htmlFor={`${id}-origins`}>公网 Origin · 每行一项</label>
          <textarea
            id={`${id}-origins`}
            value={draft.origins}
            onChange={(e) => setDraft({ ...draft, origins: e.target.value })}
            rows={3}
          />
          <label htmlFor={`${id}-local`}>本地 Origin · 单独授权，每行一项</label>
          <textarea
            id={`${id}-local`}
            value={draft.localOrigins}
            onChange={(e) => setDraft({ ...draft, localOrigins: e.target.value })}
            rows={2}
          />
          <p className="page-note">
            填写完整精确 Origin（协议、主机、可选端口），不含路径或通配符。
          </p>
          <label className="automation-check">
            <input
              type="checkbox"
              checked={draft.capture}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  capture: e.target.checked,
                  ...(e.target.checked ? {} : { control: false }),
                })
              }
            />
            允许捕获桌面目标窗口
          </label>
          <div className="automation-grid">
            <label htmlFor={`${id}-window`}>
              目标窗口 ID
              <input
                id={`${id}-window`}
                value={draft.windowId}
                onChange={(e) =>
                  setDraft({ ...draft, windowId: e.target.value, control: false, takeover: false })
                }
              />
            </label>
            <label htmlFor={`${id}-pid`}>
              目标窗口 PID
              <input
                id={`${id}-pid`}
                type="number"
                min={1}
                step={1}
                value={draft.pid}
                onChange={(e) =>
                  setDraft({ ...draft, pid: e.target.value, control: false, takeover: false })
                }
              />
            </label>
          </div>
          <Button disabled={foregroundBusy} onClick={() => void copyForeground()}>
            {foregroundBusy ? '读取元数据…' : '读取并填入当前前台窗口与 PID'}
          </Button>
          <label className="automation-check">
            <input
              type="checkbox"
              checked={draft.takeover}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  takeover: e.target.checked,
                  ...(e.target.checked ? {} : { control: false }),
                })
              }
            />
            我了解可随时停止或人工接管，已发出的动作不能撤回
          </label>
          <label className="automation-check">
            <input
              type="checkbox"
              checked={draft.control}
              onChange={(e) => setDraft({ ...draft, control: e.target.checked })}
            />
            明确允许向上述目标窗口输入与控制
          </label>
          <label htmlFor={`${id}-ttl`}>
            桌面授权期限（10–900 秒，留空由服务端采用默认值）
            <input
              id={`${id}-ttl`}
              type="number"
              min={10}
              max={900}
              value={draft.ttl}
              onChange={(e) => setDraft({ ...draft, ttl: e.target.value })}
            />
          </label>
          <h3>允许发送网页正文的模型版本</h3>
          <p className="page-note">仅允许网页正文发往所选连接版本，未勾选的版本不获授权。</p>
          <p className="page-note">
            电脑截图仅供本地视觉模型，远程传输关闭。人工捕获、预览与下载保留在本机。
          </p>
          {models.length ? (
            models.map((model) => (
              <label className="automation-check" key={model.target}>
                <input
                  type="checkbox"
                  checked={draft.remote.includes(model.target)}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      remote: e.target.checked
                        ? [...draft.remote, model.target]
                        : draft.remote.filter((value) => value !== model.target),
                    })
                  }
                />
                <span>
                  {model.name} · 版本 {model.version}
                  <small>{model.endpoint}</small>
                </span>
              </label>
            ))
          ) : (
            <p className="page-note">当前没有可列出的真实模型版本。</p>
          )}
          {draft.remote
            .filter((target) => !knownTargets.has(target))
            .map((target) => (
              <label className="automation-check" key={target}>
                <input
                  type="checkbox"
                  checked
                  onChange={() =>
                    setDraft({ ...draft, remote: draft.remote.filter((value) => value !== target) })
                  }
                />
                <span>已保存版本当前不可用：{target}（取消勾选可移除）</span>
              </label>
            ))}
          <Button variant="primary" onClick={() => void save()}>
            保存授权配置
          </Button>
        </fieldset>
      </details>
      <div className="button-row">
        <Button
          disabled={busy}
          onClick={async () => {
            try {
              await onSaved()
              setReload(true)
            } catch (error) {
              setMessage(errorText(error))
            }
          }}
        >
          只读刷新服务器配置
        </Button>
        {reload && (
          <Button
            disabled={busy}
            onClick={async () => {
              if (window.confirm('放弃当前草稿并读取服务器授权？这不会重发任何原请求。')) {
                try {
                  const latest = await onSaved()
                  setPending(null)
                  setDraft(policyDraft(latest))
                  setBaseVersion(latest.version)
                  setReload(false)
                  setMessage('已采用服务器配置，原请求未重发。')
                } catch (error) {
                  setMessage(errorText(error))
                }
              }
            }}
          >
            放弃当前草稿，采用服务器配置
          </Button>
        )}
      </div>
      {pending && (
        <div className="automation-pending">
          <p>待核对请求：{pending.request_id}。保留原配置与标识，不自动重发或改参重试。</p>
        </div>
      )}
      {message && (
        <p role="status" className="automation-message">
          {message}
        </p>
      )}
    </section>
  )
}
