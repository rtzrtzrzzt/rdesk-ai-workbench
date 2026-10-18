import type { RunCreate } from '../../api/types'

export type ReasoningEffort = NonNullable<RunCreate['reasoning_effort']>

const labels: Record<ReasoningEffort, string> = {
  low: '低',
  medium: '中',
  high: '高',
  xhigh: '极高',
}

export function ReasoningEffortSelect({
  supported,
  value,
  onChange,
  disabled = false,
  steering = false,
}: {
  supported: ReasoningEffort[]
  value: ReasoningEffort | null
  onChange: (value: ReasoningEffort | null) => void
  disabled?: boolean
  steering?: boolean
}) {
  const efforts =
    steering && value && !supported.includes(value) ? [...supported, value] : supported
  if (!efforts.length) return null
  return (
    <label
      className="reasoning-effort-label"
      title={steering ? '补充要求沿用当前任务的思考强度' : '用于接下来发送的任务'}
    >
      <span className="sr-only">思考强度</span>
      <select
        aria-label="思考强度"
        disabled={disabled}
        value={value ?? ''}
        onChange={(event) => onChange((event.target.value || null) as ReasoningEffort | null)}
      >
        <option value="">思考 · 默认</option>
        {efforts.map((effort) => (
          <option key={effort} value={effort}>
            思考 · {labels[effort]}
          </option>
        ))}
      </select>
    </label>
  )
}
