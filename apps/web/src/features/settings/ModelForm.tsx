import { useState } from 'react'
import type { Generation, ModelConnection, ModelInput, ModelUpdate } from '../../api/types'
import { ApiError } from '../../api/client'
import { Button } from '../../components/ui'
import {
  defaultTemplate,
  PromptTemplateEditor,
  templateError,
  type PromptTemplate,
} from './PromptTemplateEditor'
import type { ReasoningChoice } from '../chat/ReasoningEffortSelect'
import { revealSettingsControl, SettingsDisclosure } from './SettingsDisclosure'
type NativeEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
const nativeEfforts: NativeEffort[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
type GenerationPreferences = Omit<Generation, 'reasoning_effort' | 'prompt_template'> & {
  thinking_mode?: 'default' | 'enabled' | 'disabled'
  reasoning_effort?: string | null
  engine_kind?: 'auto' | 'sglang' | 'vllm' | 'llama_cpp' | 'tensorrt_llm' | 'other'
  reasoning_adapter?: 'auto' | 'none' | 'openai_effort' | 'anthropic_adaptive'
  native_reasoning_efforts?: NativeEffort[]
  prompt_template?: PromptTemplate
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
  const [generation, setGeneration] = useState<GenerationPreferences>(() => {
    const stored:
      | (Omit<GenerationPreferences, 'prompt_template'> & {
          prompt_template?: Partial<PromptTemplate>
        })
      | undefined = initial?.generation
    const fallback = defaultTemplate()
    return {
      max_output_tokens: null,
      timeout_seconds: 180,
      temperature: null,
      cache_observation: 'standard',
      ...stored,
      prompt_template: {
        ...fallback,
        ...stored?.prompt_template,
        presets: stored?.prompt_template?.presets ?? fallback.presets,
      },
    }
  })
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [error, setError] = useState('')
  const template = generation.prompt_template ?? defaultTemplate()
  const nativeChoices =
    generation.reasoning_adapter === 'none'
      ? []
      : generation.reasoning_adapter === 'openai_effort' ||
          generation.reasoning_adapter === 'anthropic_adaptive'
        ? (generation.native_reasoning_efforts ?? []).map((value) => ({ value, label: value }))
        : (
            (initial as (ModelConnection & { reasoning_choices?: ReasoningChoice[] }) | undefined)
              ?.reasoning_choices ?? []
          ).filter((choice) => choice.value !== 'disabled')
  const connectionChoices =
    generation.reasoning_effort &&
    !nativeChoices.some((choice) => choice.value === generation.reasoning_effort)
      ? [
          ...nativeChoices,
          { value: generation.reasoning_effort, label: `${generation.reasoning_effort}（当前值）` },
        ]
      : nativeChoices
  return (
    <form
      className="stacked-form"
      onInvalidCapture={(event) => revealSettingsControl(event.target)}
      onSubmit={(event) => {
        event.preventDefault()
        const invalidTemplate = templateError(template)
        if (invalidTemplate) {
          revealSettingsControl(event.currentTarget.querySelector('.prompt-template-editor'))
          setError(invalidTemplate)
          return
        }
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
      <PromptTemplateEditor
        value={template}
        onChange={(next) => {
          const modeChanged = next.mode !== template.mode
          setGeneration({
            ...generation,
            prompt_template: next,
            reasoning_effort:
              modeChanged ||
              (next.mode === 'custom' &&
                !next.presets.some((preset) => preset.id === generation.reasoning_effort))
                ? null
                : generation.reasoning_effort,
            thinking_mode:
              modeChanged && generation.thinking_mode === 'disabled'
                ? 'default'
                : generation.thinking_mode,
          })
        }}
      />
      <SettingsDisclosure
        className="model-advanced"
        title="高级参数"
        summary={`上下文 ${context.toLocaleString()} token · 生成与缓存观测`}
      >
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
          {template.mode === 'native' && (
            <>
              <label>
                原生思考参数适配
                <select
                  value={generation.reasoning_adapter ?? 'auto'}
                  onChange={(event) =>
                    setGeneration({
                      ...generation,
                      reasoning_adapter: event.target
                        .value as GenerationPreferences['reasoning_adapter'],
                      reasoning_effort: null,
                      thinking_mode: 'default',
                      native_reasoning_efforts:
                        event.target.value === 'anthropic_adaptive'
                          ? (generation.native_reasoning_efforts ?? []).filter((effort) =>
                              ['low', 'medium', 'high', 'max'].includes(effort),
                            )
                          : generation.native_reasoning_efforts,
                    })
                  }
                >
                  <option value="auto">自动适配</option>
                  <option value="none">不发送原生思考参数</option>
                  <option value="openai_effort" disabled={provider === 'anthropic'}>
                    OpenAI 原生 effort
                  </option>
                  <option value="anthropic_adaptive" disabled={provider !== 'anthropic'}>
                    Claude 自适应思考
                  </option>
                </select>
              </label>
              {(generation.reasoning_adapter === 'openai_effort' ||
                generation.reasoning_adapter === 'anthropic_adaptive') && (
                <fieldset className="prompt-template-preset">
                  <legend>声明模型支持的原生档位</legend>
                  <div className="prompt-template-native-efforts">
                    {nativeEfforts
                      .filter(
                        (effort) =>
                          generation.reasoning_adapter !== 'anthropic_adaptive' ||
                          ['low', 'medium', 'high', 'max'].includes(effort),
                      )
                      .map((effort) => (
                        <label className="checkbox-label" key={effort}>
                          <input
                            type="checkbox"
                            checked={generation.native_reasoning_efforts?.includes(effort) ?? false}
                            onChange={(event) =>
                              setGeneration({
                                ...generation,
                                native_reasoning_efforts: event.target.checked
                                  ? [...(generation.native_reasoning_efforts ?? []), effort]
                                  : (generation.native_reasoning_efforts ?? []).filter(
                                      (item) => item !== effort,
                                    ),
                                reasoning_effort:
                                  !event.target.checked && generation.reasoning_effort === effort
                                    ? null
                                    : generation.reasoning_effort,
                                thinking_mode:
                                  !event.target.checked &&
                                  effort === 'none' &&
                                  generation.reasoning_adapter === 'openai_effort' &&
                                  generation.thinking_mode === 'disabled'
                                    ? 'default'
                                    : generation.thinking_mode,
                              })
                            }
                          />
                          <span>{effort}</span>
                        </label>
                      ))}
                  </div>
                  <p className="page-note">
                    请按模型文档勾选；声明不代表服务已经验证支持这些参数。
                  </p>
                </fieldset>
              )}
              <label>
                默认原生思考强度
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
                  {connectionChoices.map((choice) => (
                    <option key={choice.value} value={choice.value}>
                      {choice.label}（{choice.value}）
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
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
      </SettingsDisclosure>
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
