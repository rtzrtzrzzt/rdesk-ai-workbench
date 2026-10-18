import { useState } from 'react'
import type { Generation, ModelConnection, ModelInput, ModelUpdate } from '../../api/types'
import { ApiError } from '../../api/client'
import { Button } from '../../components/ui'
type GenerationPreferences = Generation & {
  thinking_mode?: 'default' | 'enabled' | 'disabled'
  reasoning_effort?: 'low' | 'medium' | 'high' | 'xhigh' | null
  engine_kind?: 'auto' | 'sglang' | 'vllm' | 'llama_cpp' | 'tensorrt_llm' | 'other'
}
export function ModelForm({
  initial,
  onSave,
  onCancel,
  onReload,
}: {
  initial?: ModelConnection
  onSave: (
    input: ModelInput & Partial<Pick<ModelUpdate, 'expected_version' | 'clear_api_key'>>,
  ) => Promise<void>
  onCancel: () => void
  onReload?: () => Promise<void>
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [provider, setProvider] = useState<ModelInput['provider_type']>(
    initial?.provider_type ?? 'openai_compatible',
  )
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? '')
  const [model, setModel] = useState(initial?.model_identifier ?? '')
  const [key, setKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [remote, setRemote] = useState(initial?.allow_remote ?? false)
  const [context, setContext] = useState(initial?.context_window ?? 8192)
  const [generation, setGeneration] = useState<GenerationPreferences>(
    initial?.generation ?? {
      max_output_tokens: null,
      timeout_seconds: 180,
      temperature: null,
      cache_observation: 'standard' as const,
    },
  )
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [error, setError] = useState('')
  return (
    <form
      className="stacked-form"
      onSubmit={(event) => {
        event.preventDefault()
        setBusy(true)
        setError('')
        setConflict(false)
        void onSave({
          name: name.trim(),
          provider_type: provider,
          endpoint: endpoint.trim(),
          model_identifier: model.trim(),
          ...(key ? { api_key: key } : {}),
          allow_remote: remote,
          context_window: context,
          generation,
          ...(initial ? { expected_version: initial.version, clear_api_key: clearKey } : {}),
        })
          .catch((err) => {
            setConflict(err instanceof ApiError && err.status === 409)
            setError(err instanceof Error ? err.message : '保存失败')
          })
          .finally(() => setBusy(false))
      }}
    >
      <label>
        连接名称
        <input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        接口协议
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value as ModelInput['provider_type'])}
        >
          <option value="openai_compatible">OpenAI 兼容 Chat Completions</option>
          <option value="openai_responses">OpenAI Responses</option>
          <option value="anthropic">Anthropic Messages</option>
        </select>
      </label>
      <label>
        服务地址（Endpoint）
        <input required type="url" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
      </label>
      <label>
        模型标识（Model）
        <input required value={model} onChange={(e) => setModel(e.target.value)} />
      </label>
      <label>
        API 密钥{initial ? '（留空保留原密钥）' : '（可选）'}
        <input
          type="password"
          autoComplete="off"
          disabled={clearKey}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
      </label>
      {initial && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={clearKey}
            onChange={(e) => {
              setClearKey(e.target.checked)
              setKey('')
            }}
          />
          <span>清除已保存密钥（{initial.credential_configured ? '已配置' : '未配置'}）</span>
        </label>
      )}
      <label className="checkbox-label">
        <input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} />
        <span>允许向远程模型发送任务内容（远程附件仍需逐次同意）</span>
      </label>
      <details className="settings-editor model-advanced">
        <summary>高级参数 · 上下文、生成与缓存观测</summary>
        <div className="settings-editor-content">
          <label>
            上下文窗口（token）
            <input
              type="number"
              required
              min={1024}
              max={10000000}
              value={context}
              onChange={(e) => setContext(Number(e.target.value))}
            />
          </label>
          <label>
            输出上限（留空自动使用剩余上下文）
            <input
              type="number"
              min={64}
              max={131072}
              placeholder="自动"
              value={generation.max_output_tokens ?? ''}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  max_output_tokens: e.target.value ? Number(e.target.value) : null,
                })
              }
            />
          </label>
          <label>
            无生成进展超时（秒）
            <input
              type="number"
              required
              min={5}
              max={1800}
              value={generation.timeout_seconds}
              onChange={(e) =>
                setGeneration({ ...generation, timeout_seconds: Number(e.target.value) })
              }
            />
          </label>
          <label>
            Temperature（留空使用服务默认）
            <input
              type="number"
              min={0}
              max={2}
              step="any"
              value={generation.temperature ?? ''}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  temperature: e.target.value === '' ? null : Number(e.target.value),
                })
              }
            />
          </label>
          <label>
            思考模式
            <select
              value={generation.thinking_mode ?? 'default'}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  thinking_mode: e.target.value as GenerationPreferences['thinking_mode'],
                  reasoning_effort:
                    e.target.value === 'disabled' ? null : generation.reasoning_effort,
                })
              }
            >
              <option value="default">服务默认（保持模型原有行为）</option>
              <option value="enabled">开启思考</option>
              <option value="disabled">关闭思考</option>
            </select>
          </label>
          <label>
            思考强度
            <select
              value={generation.reasoning_effort ?? ''}
              disabled={generation.thinking_mode === 'disabled'}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  reasoning_effort: (e.target.value ||
                    null) as GenerationPreferences['reasoning_effort'],
                })
              }
            >
              <option value="">服务默认</option>
              <option value="low">低（low）</option>
              <option value="medium">中（medium）</option>
              {(!/qwen3\.8/i.test(model) || generation.reasoning_effort === 'high') && (
                <option value="high">高（high）</option>
              )}
              <option value="xhigh">极高（xhigh）</option>
            </select>
          </label>
          <label>
            推理引擎声明
            <select
              value={generation.engine_kind ?? 'auto'}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  engine_kind: e.target.value as GenerationPreferences['engine_kind'],
                })
              }
            >
              <option value="auto">自动判断</option>
              <option value="sglang">SGLang</option>
              <option value="vllm">vLLM</option>
              <option value="llama_cpp">llama.cpp</option>
              <option value="tensorrt_llm">TensorRT-LLM</option>
              <option value="other">其他</option>
            </select>
          </label>
          <p className="page-note">
            思考选项取决于模型、接口和实际推理引擎；声明引擎不代表能力已经验证。
            {/qwen3\.8/i.test(model) && 'Qwen3.8 支持 low、medium、xhigh 强度。'}
          </p>
          <label>
            缓存观测
            <select
              value={generation.cache_observation}
              onChange={(e) =>
                setGeneration({
                  ...generation,
                  cache_observation: e.target.value === 'sglang' ? 'sglang' : 'standard',
                })
              }
            >
              <option value="standard">标准用量字段</option>
              <option value="sglang">SGLang 扩展</option>
            </select>
          </label>
          <p className="page-note">
            SGLang 仅适配该引擎的缓存统计扩展，不代表缓存已命中或能力已验证。
          </p>
        </div>
      </details>
      <p className="page-note">
        保存不会发起推理。连接检查只读取模型列表；修改配置可能使能力证据失效。
      </p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {conflict && onReload && (
        <div className="memory-conflict">
          <p>配置已被其他操作更新。重新读取会关闭此草稿，请先保留需要的内容。</p>
          <Button disabled={busy} onClick={() => void onReload()}>
            放弃草稿并重新读取
          </Button>
        </div>
      )}
      <div className="button-row">
        <Button disabled={busy} onClick={onCancel}>
          取消
        </Button>
        <Button type="submit" variant="primary" disabled={busy || conflict}>
          {busy ? '正在保存…' : '保存连接'}
        </Button>
      </div>
    </form>
  )
}
