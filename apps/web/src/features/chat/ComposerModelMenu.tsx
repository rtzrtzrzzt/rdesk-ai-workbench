import { Check, ChevronDown } from 'lucide-react'
import { ComposerMenu } from './ComposerMenu'
import type { ReasoningChoice, ReasoningEffort } from './ReasoningEffortSelect'

export function ComposerModelMenu({
  models,
  modelId,
  onModelChange,
  choices,
  value,
  onReasoningChange,
  defaultEffort,
  defaultDisabled = false,
  invalidValue,
  steering = false,
  disabled = false,
}: {
  models: { id: string; name: string }[]
  modelId: string
  onModelChange: (id: string) => void
  choices: ReasoningChoice[]
  value: ReasoningEffort | null
  onReasoningChange: (value: ReasoningEffort | null) => void
  defaultEffort?: ReasoningEffort | null
  defaultDisabled?: boolean
  invalidValue?: string | null
  steering?: boolean
  disabled?: boolean
}) {
  const model = models.find((item) => item.id === modelId)
  const defaultLabel = defaultDisabled
    ? '关闭'
    : (choices.find((item) => item.value === defaultEffort)?.label ?? defaultEffort ?? '默认')
  const effort = value
    ? (choices.find((item) => item.value === value)?.label ?? value)
    : defaultLabel
  const efforts =
    steering && value && !choices.some((item) => item.value === value)
      ? [...choices, { value, label: value, kind: 'frozen' }]
      : choices
  return (
    <ComposerMenu
      label="模型与思考强度"
      className="composer-model-menu"
      disabled={disabled || !models.length}
      trigger={
        <>
          <span className="composer-model-name" title={model?.name}>
            {(model?.name ?? '选择模型').replace(/^默认 · /, '')}
          </span>
          {!!efforts.length && <span className="composer-effort">{effort}</span>}
          <ChevronDown size={13} />
        </>
      }
    >
      {(close) => (
        <>
          <div className="composer-menu-heading">模型</div>
          <div className="composer-model-list" role="group" aria-label="可用模型">
            {models.map((item) => (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={item.id === modelId}
                key={item.id}
                title={item.name}
                disabled={disabled}
                onClick={() => onModelChange(item.id)}
              >
                <span>{item.name}</span>
                {item.id === modelId && <Check size={15} />}
              </button>
            ))}
          </div>
          {!!efforts.length && (
            <div className="composer-reasoning-section" role="group" aria-label="思考强度">
              <div className="composer-menu-heading">思考强度</div>
              <div className="composer-effort-choices">
                {[{ value: '', label: `默认 · ${defaultLabel}` }, ...efforts].map((item) => (
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={(value ?? '') === item.value}
                    key={item.value}
                    disabled={disabled}
                    onClick={() => {
                      onReasoningChange(item.value || null)
                      close()
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {invalidValue && !steering && (
            <p className="composer-menu-note">原思考档位已不可用，本次使用连接默认。</p>
          )}
        </>
      )}
    </ComposerMenu>
  )
}
