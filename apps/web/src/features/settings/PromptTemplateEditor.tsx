export type EffortPreset = { id: string; label: string; instructions: string }
export type PromptTemplate = {
  mode: 'native' | 'workbench' | 'custom'
  name: string
  instructions: string
  default_effort: string
  presets: EffortPreset[]
}

export const fivePresets: EffortPreset[] = [
  { id: 'low', label: '低', instructions: '保持分析简短、聚焦，直接解决问题。' },
  { id: 'medium', label: '中', instructions: '正常分析，检查完成任务必需的关键条件。' },
  { id: 'high', label: '高', instructions: '深入分析问题，核验关键假设和可能影响结论的条件。' },
  { id: 'xhigh', label: '极高', instructions: '充分分析，比较可行方案，检查边界条件和反例。' },
  {
    id: 'max',
    label: 'Max',
    instructions: '充分分析复杂问题，核查关键依据，在交付前独立复核结论和必要完成条件。',
  },
]

export const defaultTemplate = (): PromptTemplate => ({
  mode: 'native',
  name: '我的模板',
  instructions: '',
  default_effort: 'medium',
  presets: fivePresets.map((preset) => ({ ...preset })),
})

export function templateError(template: PromptTemplate): string | null {
  if (template.mode !== 'custom') return null
  const ids = template.presets.map((preset) => preset.id)
  if (ids.includes('disabled') || new Set(ids).size !== ids.length)
    return '档位标识必须唯一，不能使用保留标识 disabled。'
  if (!ids.includes(template.default_effort)) return '请从已有档位中选择默认档位。'
  return null
}

export function PromptTemplateEditor({
  value,
  onChange,
}: {
  value: PromptTemplate
  onChange: (value: PromptTemplate) => void
}) {
  const custom = value.mode === 'custom'
  const presets = custom ? value.presets : fivePresets
  const updatePreset = (index: number, change: Partial<EffortPreset>) => {
    const previous = value.presets[index]
    onChange({
      ...value,
      default_effort:
        change.id !== undefined && value.default_effort === previous.id
          ? change.id
          : value.default_effort,
      presets: value.presets.map((preset, position) =>
        position === index ? { ...preset, ...change } : preset,
      ),
    })
  }
  return (
    <div className="prompt-template-editor settings-editor-content">
      <label>
        提示词模板
        <select
          aria-label="提示词模板"
          value={value.mode}
          onChange={(event) =>
            onChange({ ...value, mode: event.target.value as PromptTemplate['mode'] })
          }
        >
          <option value="native">模型原生</option>
          <option value="workbench">工作台五档</option>
          <option value="custom">我的模板</option>
        </select>
      </label>
      <p className="page-note">
        {value.mode === 'native'
          ? '沿用模型原有行为；需要指定原生思考参数时，可在高级参数中配置。'
          : '行为模板通过中文指令引导分析方式，保留模型底层的工具和图片格式。档位不保证固定思考时间。'}
      </p>
      {custom && (
        <>
          <label>
            模板名称
            <input
              required
              maxLength={80}
              value={value.name}
              onChange={(event) => onChange({ ...value, name: event.target.value })}
            />
          </label>
          <label>
            通用指令
            <textarea
              rows={4}
              maxLength={60000}
              placeholder="例如：先确认关键约束，使用中文回答，完成后说明必要验证结果。"
              value={value.instructions}
              onChange={(event) => onChange({ ...value, instructions: event.target.value })}
            />
          </label>
          <label>
            默认档位
            <select
              value={value.default_effort}
              onChange={(event) => onChange({ ...value, default_effort: event.target.value })}
            >
              {value.presets.map((preset, index) => (
                <option key={index} value={preset.id}>
                  {preset.label || preset.id || `档位 ${index + 1}`}
                </option>
              ))}
            </select>
          </label>
        </>
      )}
      {value.mode !== 'native' && (
        <details className="settings-editor" open>
          <summary>{custom ? '编辑分档指令' : '查看工作台五档指令'}</summary>
          <div className="settings-editor-content">
            {presets.map((preset, index) => (
              <fieldset className="prompt-template-preset" key={index}>
                <legend>{custom ? `档位 ${index + 1}` : `${preset.label} · ${preset.id}`}</legend>
                {custom ? (
                  <>
                    <div className="prompt-template-preset-fields">
                      <label>
                        标识
                        <input
                          required
                          pattern="[a-z][a-z0-9_-]{0,31}"
                          maxLength={32}
                          title="以小写字母开头，使用小写字母、数字、下划线或短横线；不能与其他档位重复。"
                          value={preset.id}
                          onChange={(event) => updatePreset(index, { id: event.target.value })}
                        />
                      </label>
                      <label>
                        显示名称
                        <input
                          required
                          maxLength={40}
                          value={preset.label}
                          onChange={(event) => updatePreset(index, { label: event.target.value })}
                        />
                      </label>
                    </div>
                    <label>
                      分档指令
                      <textarea
                        required
                        rows={3}
                        maxLength={12000}
                        value={preset.instructions}
                        onChange={(event) =>
                          updatePreset(index, { instructions: event.target.value })
                        }
                      />
                    </label>
                  </>
                ) : (
                  <p className="page-note">{preset.instructions}</p>
                )}
              </fieldset>
            ))}
            {!custom && (
              <p className="page-note">
                选择“我的模板”可编辑通用和分档指令，已有自定义内容会保留。
              </p>
            )}
          </div>
        </details>
      )}
    </div>
  )
}
