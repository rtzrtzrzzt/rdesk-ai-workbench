export type ReasoningEffort = string
export type ReasoningChoice = { value: string; label: string; kind: string }

export function ReasoningEffortSelect({
  choices,
  value,
  onChange,
  disabled = false,
  steering = false,
  defaultEffort,
  defaultDisabled = false,
  invalidValue,
}: {
  choices: ReasoningChoice[]
  value: ReasoningEffort | null
  onChange: (value: ReasoningEffort | null) => void
  disabled?: boolean
  steering?: boolean
  defaultEffort?: ReasoningEffort | null
  defaultDisabled?: boolean
  invalidValue?: ReasoningEffort | null
}) {
  const efforts =
    steering && value && !choices.some((choice) => choice.value === value)
      ? [...choices, { value, label: `${value}（当前任务）`, kind: 'frozen' }]
      : choices
  if (!efforts.length && !invalidValue) return null
  const defaultLabel = choices.find((choice) => choice.value === defaultEffort)?.label
  return (
    <label
      className="reasoning-effort-label"
      title={steering ? '补充要求沿用当前任务的思考档位' : '用于接下来发送的任务'}
    >
      <span className="sr-only">思考档位</span>
      <select
        aria-label="思考档位"
        disabled={disabled}
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">
          思考 ·{' '}
          {defaultDisabled
            ? '关闭（默认）'
            : defaultEffort
              ? `${defaultLabel ?? defaultEffort}（默认）`
              : '默认'}
        </option>
        {efforts.map((choice) => (
          <option key={choice.value} value={choice.value}>
            思考 · {choice.label}
          </option>
        ))}
      </select>
      {invalidValue && !steering && (
        <span className="page-note" role="status">
          原档位 {invalidValue} 已不可用，本次使用连接默认；可重新选择。
        </span>
      )}
    </label>
  )
}
