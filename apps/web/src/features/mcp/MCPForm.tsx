import { useState } from 'react'
import type { MCPConnectionConfig, MCPSave, MCPSnapshotView, MCPView } from '../../api/types'
import { Button } from '../../components/ui'
import { isRecord } from '../generated/validation'
type LimitsKey =
  | 'connect_timeout_seconds'
  | 'call_timeout_seconds'
  | 'max_input_bytes'
  | 'max_output_bytes'
  | 'max_tools'
  | 'snapshot_ttl_seconds'
  | 'continuation_ttl_seconds'
  | 'max_input_rounds'
const limits: { key: LimitsKey; label: string; min: number; max: number; integer?: boolean }[] = [
  { key: 'connect_timeout_seconds', label: '连接超时（秒）', min: 1, max: 60 },
  { key: 'call_timeout_seconds', label: '调用超时（秒）', min: 1, max: 600 },
  { key: 'max_input_bytes', label: '单次输入字节上限', min: 1024, max: 262144, integer: true },
  { key: 'max_output_bytes', label: '单次输出字节上限', min: 1024, max: 4194304, integer: true },
  { key: 'max_tools', label: '目录工具数上限', min: 1, max: 512, integer: true },
  { key: 'snapshot_ttl_seconds', label: '目录有效期（秒）', min: 10, max: 3600, integer: true },
  { key: 'continuation_ttl_seconds', label: '续接有效期（秒）', min: 10, max: 600, integer: true },
  { key: 'max_input_rounds', label: '交互轮数上限', min: 1, max: 10, integer: true },
]
export function parseArgs(value: string): string[] {
  const parsed: unknown = JSON.parse(value)
  if (
    !Array.isArray(parsed) ||
    parsed.length > 64 ||
    !parsed.every(
      (item): item is string =>
        typeof item === 'string' && item.length <= 8192 && !item.includes('\0'),
    )
  )
    throw new Error('参数必须是至多 64 项的 JSON 字符串数组，单项不超过 8192 字符且不含 NUL。')
  return parsed
}
export function parseEnvironment(value: string): Record<string, string> {
  const parsed: unknown = JSON.parse(value)
  if (
    !isRecord(parsed) ||
    Object.keys(parsed).length > 16 ||
    !Object.entries(parsed).every(
      ([key, ref]) =>
        /^[A-Z_][A-Z0-9_]{0,79}$/.test(key) &&
        !['PATH', 'HOME', 'PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'].includes(key) &&
        typeof ref === 'string' &&
        /^[A-Za-z_][A-Za-z0-9_]{0,79}$/.test(ref),
    )
  )
    throw new Error('环境配置仅接受至多 16 项“环境变量名→已保存凭据名称”，不能覆盖进程启动路径。')
  return Object.fromEntries(Object.entries(parsed).map(([name, ref]) => [name, String(ref)]))
}
export function MCPForm({
  connectionId,
  initial,
  snapshot,
  locked,
  conflict,
  onSave,
  onCancel,
  onReload,
}: {
  connectionId: string
  initial?: MCPView
  snapshot?: MCPSnapshotView
  locked: boolean
  conflict: boolean
  onSave: (input: MCPSave) => Promise<void>
  onCancel: () => void
  onReload: () => Promise<void>
}) {
  const [config, setConfig] = useState<MCPConnectionConfig>(() =>
    initial
      ? structuredClone(initial.configuration)
      : {
          connection_id: connectionId,
          version: 1,
          name: '',
          transport: 'stdio',
          enabled: false,
          mode: 'auto',
          allowed_tools: [],
        },
  )
  const [args, setArgs] = useState(() => JSON.stringify(initial?.configuration.args ?? [], null, 2))
  const [environment, setEnvironment] = useState(() =>
    JSON.stringify(initial?.configuration.env_secret_refs ?? {}, null, 2),
  )
  const [authorized, setAuthorized] = useState(initial?.execution_authorized ?? false)
  const [secrets, setSecrets] = useState<{ id: string; name: string; value: string }[]>([])
  const [clear, setClear] = useState<string[]>([])
  const [error, setError] = useState('')
  const expectedVersion = initial?.version ?? 0
  const toolNames = [
    ...new Set([
      ...(snapshot?.snapshot.tools.map((tool) => tool.name) ?? []),
      ...(config.allowed_tools ?? []),
    ]),
  ]
  function target(patch: Partial<MCPConnectionConfig>) {
    setConfig((previous) => ({ ...previous, ...patch }))
    setAuthorized(false)
  }
  function payload(): MCPSave {
    let configuration: MCPConnectionConfig = {
      ...config,
      connection_id: connectionId,
      version: expectedVersion + 1,
      name: config.name.trim(),
    }
    if (!configuration.name) throw new Error('请输入连接名称。')
    if (configuration.transport === 'stdio') {
      if (!configuration.command?.startsWith('/') || !configuration.cwd?.startsWith('/'))
        throw new Error('程序和工作目录必须是服务端真实存在的绝对路径。')
      configuration = {
        ...configuration,
        args: parseArgs(args),
        env_secret_refs: parseEnvironment(environment),
        endpoint: null,
        bearer_secret_ref: null,
      }
    } else {
      const endpoint = new URL(configuration.endpoint ?? '')
      if (
        !['http:', 'https:'].includes(endpoint.protocol) ||
        !endpoint.hostname ||
        endpoint.username ||
        endpoint.password ||
        endpoint.search ||
        endpoint.hash
      )
        throw new Error('HTTP 端点只能含 origin/path，不能含凭据、query 或 fragment。')
      if (
        configuration.bearer_secret_ref &&
        endpoint.protocol !== 'https:' &&
        !['localhost', '127.0.0.1', '[::1]', '::1'].includes(endpoint.hostname)
      )
        throw new Error('远程 bearer 凭据需要 HTTPS。')
      configuration = { ...configuration, command: null, args: [], cwd: null, env_secret_refs: {} }
    }
    for (const field of limits) {
      const value = configuration[field.key]
      if (
        value !== undefined &&
        (!Number.isFinite(value) ||
          value < field.min ||
          value > field.max ||
          (field.integer && !Number.isInteger(value)))
      )
        throw new Error(`${field.label} 不在支持范围。`)
    }
    if (configuration.enabled && !authorized)
      throw new Error('启用前请明确确认精确程序或 HTTP 端点。')
    const newSecrets: Record<string, string> = {}
    for (const secret of secrets) {
      if (!secret.name && !secret.value) continue
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,79}$/.test(secret.name) || !secret.value)
        throw new Error('新增凭据需要有效名称和非空值；清除凭据请使用明确清除选项。')
      if (Object.hasOwn(newSecrets, secret.name)) throw new Error('凭据名称重复。')
      Object.defineProperty(newSecrets, secret.name, {
        value: secret.value,
        enumerable: true,
        writable: true,
        configurable: true,
      })
    }
    if (clear.some((name) => Object.hasOwn(newSecrets, name)))
      throw new Error('不能在同次请求中既清除又设置同名凭据。')
    return {
      expected_version: expectedVersion,
      configuration,
      execution_authorized: authorized,
      secrets: newSecrets,
      clear_secrets: clear,
      request_id: crypto.randomUUID(),
    }
  }
  let targetText = ''
  try {
    targetText = JSON.stringify(
      config.transport === 'stdio'
        ? { command: config.command ?? '', args: parseArgs(args), cwd: config.cwd ?? '' }
        : { endpoint: config.endpoint ?? '' },
      null,
      2,
    )
  } catch {
    targetText = '参数数组尚不符合 JSON 约定，不能确认或提交。'
  }
  return (
    <form
      className="stacked-form mcp-form"
      onSubmit={(event) => {
        event.preventDefault()
        setError('')
        let input: MCPSave
        try {
          input = payload()
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : '配置不符合协议')
          return
        }
        void onSave(input).catch(() => {})
      }}
    >
      <div className="run-line">
        <h3>{initial ? '编辑 MCP 连接' : '新建 MCP 连接'}</h3>
        <small>
          配置版本 {expectedVersion} · {connectionId}
        </small>
      </div>
      <fieldset disabled={locked || conflict} className="mcp-fields">
        <label>
          连接名称
          <input
            required
            maxLength={100}
            value={config.name}
            onChange={(event) => setConfig({ ...config, name: event.target.value })}
          />
        </label>
        <label>
          传输
          <select
            value={config.transport}
            onChange={(event) => {
              if (event.target.value === 'stdio' || event.target.value === 'streamable_http') {
                target({
                  transport: event.target.value,
                  command: null,
                  cwd: null,
                  args: [],
                  endpoint: null,
                  bearer_secret_ref: null,
                  env_secret_refs: {},
                })
                setArgs('[]')
                setEnvironment('{}')
              }
            }}
          >
            <option value="stdio">stdio · 原生程序</option>
            <option value="streamable_http">Streamable HTTP · 网络端点</option>
          </select>
        </label>
        {config.transport === 'stdio' ? (
          <>
            <label>
              绝对程序路径
              <input
                required
                value={config.command ?? ''}
                onChange={(event) => target({ command: event.target.value })}
              />
            </label>
            <label>
              参数（JSON 字符串数组，不经 shell）
              <textarea
                rows={4}
                value={args}
                spellCheck={false}
                onChange={(event) => {
                  setArgs(event.target.value)
                  setAuthorized(false)
                }}
              />
            </label>
            <label>
              绝对工作目录
              <input
                required
                value={config.cwd ?? ''}
                onChange={(event) => target({ cwd: event.target.value })}
              />
            </label>
            <label>
              环境凭据引用（JSON 对象，只填已保存名称）
              <textarea
                rows={3}
                spellCheck={false}
                value={environment}
                onChange={(event) => {
                  setEnvironment(event.target.value)
                  setAuthorized(false)
                }}
              />
            </label>
          </>
        ) : (
          <>
            <label>
              HTTP 端点
              <input
                type="url"
                required
                value={config.endpoint ?? ''}
                onChange={(event) => target({ endpoint: event.target.value })}
              />
            </label>
            <label>
              Bearer 凭据名称（可选，不填密钥值）
              <input
                value={config.bearer_secret_ref ?? ''}
                onChange={(event) => target({ bearer_secret_ref: event.target.value || null })}
              />
            </label>
          </>
        )}
        <label>
          协议模式
          <select
            value={config.mode ?? 'auto'}
            onChange={(event) =>
              target({ mode: event.target.value === 'legacy' ? 'legacy' : 'auto' })
            }
          >
            <option value="auto">auto · 协商支持的协议</option>
            <option value="legacy">legacy · 传统协议模式</option>
          </select>
        </label>
        <div className="mcp-target">
          <strong>本次精确目标</strong>
          <pre>{targetText}</pre>
          <p>
            {config.transport === 'stdio'
              ? '发现会实际启动上述原生程序；当前不是 OS 隔离沙箱。'
              : '发现会实际访问上述端点并读取工具目录。'}
          </p>
        </div>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={authorized}
            onChange={(event) => setAuthorized(event.target.checked)}
          />
          <span>我确认此精确程序、参数和工作目录或 HTTP 端点，并授权工作台建立连接</span>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={config.enabled ?? false}
            onChange={(event) => setConfig({ ...config, enabled: event.target.checked })}
          />
          <span>启用此 MCP 连接</span>
        </label>
        <details>
          <summary>允许的业务工具</summary>
          <p className="page-note">
            只允许明确选中的工具，最多 128
            项。空名单不允许业务调用；发现目录本身不赋予工具执行权限。
          </p>
          {toolNames.length ? (
            <div className="mcp-tool-options">
              {toolNames.map((name) => (
                <label className="checkbox-label" key={name}>
                  <input
                    type="checkbox"
                    checked={config.allowed_tools?.includes(name) ?? false}
                    disabled={
                      !config.allowed_tools?.includes(name) &&
                      (config.allowed_tools?.length ?? 0) >= 128
                    }
                    onChange={(event) =>
                      setConfig({
                        ...config,
                        allowed_tools: event.target.checked
                          ? [...(config.allowed_tools ?? []), name]
                          : (config.allowed_tools ?? []).filter((tool) => tool !== name),
                      })
                    }
                  />
                  <span>
                    {name}
                    {!snapshot?.snapshot.tools.some((tool) => tool.name === name)
                      ? ' · 不在当前目录'
                      : ''}
                  </span>
                </label>
              ))}
            </div>
          ) : (
            <p className="page-note">
              先保存启用的连接并明确发现目录，再选择工具。保存名单会改变配置版本，随后需重新发现。
            </p>
          )}
        </details>
        <details>
          <summary>凭据（仅本次表单内存）</summary>
          <p className="page-note">
            现有凭据只回显名称。值只通过此写入请求发送，不进入浏览器存储或日志。环境与 bearer
            字段只引用这里保存的名称。
          </p>
          {initial?.secret_names.length ? (
            <div className="mcp-tool-options">
              {initial.secret_names.map((name) => (
                <label className="checkbox-label" key={name}>
                  <input
                    type="checkbox"
                    checked={clear.includes(name)}
                    disabled={!clear.includes(name) && clear.length >= 16}
                    onChange={(event) =>
                      setClear(
                        event.target.checked
                          ? [...clear, name]
                          : clear.filter((value) => value !== name),
                      )
                    }
                  />
                  <span>清除已保存凭据：{name}</span>
                </label>
              ))}
            </div>
          ) : (
            <p className="page-note">尚无已保存凭据名称。</p>
          )}
          {secrets.map((secret, index) => (
            <div className="mcp-secret-row" key={secret.id}>
              <label>
                凭据名称
                <input
                  autoComplete="off"
                  maxLength={80}
                  value={secret.name}
                  onChange={(event) =>
                    setSecrets(
                      secrets.map((item, i) =>
                        i === index ? { ...item, name: event.target.value } : item,
                      ),
                    )
                  }
                />
              </label>
              <label>
                凭据值
                <input
                  type="password"
                  autoComplete="new-password"
                  value={secret.value}
                  onChange={(event) =>
                    setSecrets(
                      secrets.map((item, i) =>
                        i === index ? { ...item, value: event.target.value } : item,
                      ),
                    )
                  }
                />
              </label>
              <Button onClick={() => setSecrets(secrets.filter((item) => item.id !== secret.id))}>
                移除此行
              </Button>
            </div>
          ))}
          <Button
            disabled={secrets.length >= 16}
            onClick={() =>
              setSecrets([...secrets, { id: crypto.randomUUID(), name: '', value: '' }])
            }
          >
            添加凭据输入
          </Button>
        </details>
        <details>
          <summary>连接、大小与续接限制</summary>
          <div className="mcp-limits">
            {limits.map((field) => (
              <label key={field.key}>
                {field.label}
                <input
                  type="number"
                  min={field.min}
                  max={field.max}
                  step={field.integer ? 1 : 'any'}
                  placeholder="服务默认"
                  value={config[field.key] ?? ''}
                  onChange={(event) => {
                    const next = { ...config }
                    if (event.target.value === '') delete next[field.key]
                    else next[field.key] = Number(event.target.value)
                    setConfig(next)
                  }}
                />
                <small>
                  {field.min}–{field.max}
                </small>
              </label>
            ))}
          </div>
        </details>
      </fieldset>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {conflict && (
        <div className="memory-conflict">
          <p>配置版本已变化；放弃草稿后重新读取。密钥输入不会保留。</p>
          <Button disabled={locked} onClick={() => void onReload()}>
            放弃草稿并读取当前版本
          </Button>
        </div>
      )}
      <p className="page-note">
        不要将密钥放入命令参数或
        URL，请使用专用凭据输入与引用。保存只记录配置与凭据，不启动程序、不访问端点。每次保存都会改变版本并使旧目录失效。
      </p>
      <div className="button-row">
        <Button disabled={locked} onClick={onCancel}>
          关闭编辑
        </Button>
        <Button
          type="submit"
          variant="primary"
          disabled={locked || conflict || ((config.enabled ?? false) && !authorized)}
        >
          仅保存配置
        </Button>
      </div>
    </form>
  )
}
