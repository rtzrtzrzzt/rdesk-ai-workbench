import type {
  ModelConnection,
  ResolvedDefaults,
  RunOptions,
  RunOptionOverrides,
} from '../../api/types'
import { SandboxSelect, sandboxLabel } from './SandboxSelect'

export type NumericKey = {
  [Key in keyof RunOptions]-?: NonNullable<RunOptions[Key]> extends number ? Key : never
}[keyof RunOptions]

export const runOptionFields: {
  key: NumericKey
  label: string
  min: number
  max: number
  integer: boolean
}[] = [
  {
    key: 'command_timeout_seconds',
    label: '单条命令最长执行秒数',
    min: 1,
    max: 86400,
    integer: true,
  },
  { key: 'child_agent_limit', label: '子 Agent 名额上限', min: 0, max: 3, integer: true },
  { key: 'checkpoint_rounds', label: '阶段检查点模型轮数', min: 1, max: 128, integer: true },
  { key: 'max_model_rounds', label: '单任务模型轮数预算', min: 1, max: 65536, integer: true },
  {
    key: 'root_model_attempt_limit',
    label: '整个任务树模型尝试预算',
    min: 1,
    max: 524288,
    integer: true,
  },
  { key: 'model_retry_limit', label: '模型请求重试上限', min: 0, max: 2, integer: true },
  { key: 'retry_backoff_seconds', label: '模型重试退避秒数', min: 1, max: 30, integer: true },
  { key: 'context_target_ratio', label: '目标上下文占比', min: 0.5, max: 0.95, integer: false },
]
export function validOption(key: NumericKey, value: unknown) {
  const field = runOptionFields.find((item) => item.key === key)
  return (
    !!field &&
    typeof value === 'number' &&
    Number.isFinite(value) &&
    value >= field.min &&
    value <= field.max &&
    (!field.integer || Number.isInteger(value))
  )
}
export function validRunOptions(value: RunOptions) {
  return (
    runOptionFields.every((field) => validOption(field.key, value[field.key])) &&
    (value.sandbox_profile === 'native_scoped' || value.sandbox_profile === 'linux_bwrap') &&
    (value.approval_mode === 'smart' || value.approval_mode === 'manual') &&
    ['default', 'disabled', 'low', 'medium', 'xhigh'].includes(
      value.review_reasoning_effort ?? 'low',
    )
  )
}
export function validOverrides(value: RunOptionOverrides) {
  return (
    (value.sandbox_profile == null ||
      value.sandbox_profile === 'native_scoped' ||
      value.sandbox_profile === 'linux_bwrap') &&
    (value.approval_mode == null ||
      value.approval_mode === 'smart' ||
      value.approval_mode === 'manual') &&
    (value.review_reasoning_effort == null ||
      ['default', 'disabled', 'low', 'medium', 'xhigh'].includes(value.review_reasoning_effort)) &&
    runOptionFields.every(
      (field) =>
        value[field.key] === undefined ||
        value[field.key] === null ||
        validOption(field.key, value[field.key]),
    )
  )
}
export function ReviewEffortSelect({
  value,
  onChange,
  disabled,
  inherit = false,
}: {
  value: RunOptions['review_reasoning_effort'] | null | undefined
  onChange: (value: RunOptions['review_reasoning_effort'] | null) => void
  disabled: boolean
  inherit?: boolean
}) {
  return (
    <label>
      审核思考强度
      <select
        aria-label="审核思考强度"
        value={value ?? (inherit ? '' : 'low')}
        disabled={disabled}
        onChange={(event) =>
          onChange((event.target.value || null) as RunOptions['review_reasoning_effort'] | null)
        }
      >
        {inherit && <option value="">继承用户 / 系统</option>}
        <option value="disabled">关闭思考</option>
        <option value="low">低</option>
        <option value="medium">中</option>
        <option value="xhigh">极高</option>
        <option value="default">沿用模型连接默认</option>
      </select>
      <small className="muted">
        独立于主任务，仅新任务生效；关闭时直接生成审核结论。没有已确认参数适配的模型沿用连接默认。
      </small>
    </label>
  )
}
export function sourceLabel(source: string | undefined) {
  if (!source) return '服务端未给出来源'
  if (source === 'system') return '系统默认'
  if (source === 'user') return '用户默认'
  if (source === 'workspace') return '工作区默认'
  if (source === 'task') return '本次任务'
  if (source === 'unconfigured') return '未配置'
  if (/^user:\d+$/.test(source)) return `用户默认 · 版本 ${source.slice(5)}`
  if (/^workspace:\d+$/.test(source)) return `工作区默认 · 版本 ${source.slice(10)}`
  return source
}
export function RunOptionsFields({
  value,
  onChange,
  disabled,
}: {
  value: RunOptions
  onChange: (value: RunOptions) => void
  disabled: boolean
}) {
  return (
    <>
      <label>
        审批方式
        <select
          value={value.approval_mode ?? 'smart'}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, approval_mode: event.target.value as 'smart' | 'manual' })
          }
        >
          <option value="smart">智能审批</option>
          <option value="manual">手动审批</option>
        </select>
      </label>
      <p className="page-note">智能审批自动允许或拒绝操作；无法确认或审核失败时拒绝，不转人工。</p>
      <details className="runtime-advanced" open>
        <summary>高级任务预算与执行设置</summary>
        <p className="page-note">
          普通命令和长命令共用时间预算；AI 可为单次操作选择更短时限。修改对新任务生效。
        </p>
        <ReviewEffortSelect
          value={value.review_reasoning_effort}
          disabled={disabled}
          onChange={(review_reasoning_effort) =>
            onChange({ ...value, review_reasoning_effort: review_reasoning_effort ?? 'low' })
          }
        />
        <div className="run-option-fields">
          {runOptionFields.map((field) => (
            <label key={field.key}>
              {field.label}
              <input
                type="number"
                min={field.min}
                max={field.max}
                step={field.integer ? 1 : 'any'}
                required
                disabled={disabled}
                value={value[field.key] ?? ''}
                onChange={(event) => {
                  const next = { ...value }
                  if (event.target.value === '') delete next[field.key]
                  else next[field.key] = Number(event.target.value)
                  onChange(next)
                }}
              />
              <small>
                {field.min}–{field.max}
                {field.integer ? ' · 整数' : ' · 小数比例'}
              </small>
            </label>
          ))}
        </div>
        <SandboxSelect
          value={value.sandbox_profile}
          disabled={disabled}
          onChange={(sandbox_profile) => {
            if (sandbox_profile) onChange({ ...value, sandbox_profile })
          }}
        />
      </details>
    </>
  )
}
export function RuntimeSummary({
  options,
  modelId,
  models,
}: {
  options: RunOptions
  modelId?: string | null
  models: ModelConnection[]
}) {
  const model = models.find((item) => item.id === modelId)
  return (
    <dl className="runtime-summary" aria-label="当前已保存的生效配置概要">
      <div>
        <dt>默认模型</dt>
        <dd>{modelId ? (model?.name ?? modelId) : '未配置'}</dd>
      </div>
      <div>
        <dt>执行方式</dt>
        <dd>{sandboxLabel(options.sandbox_profile)}</dd>
      </div>
      <div>
        <dt>协作规模</dt>
        <dd>最多 {options.child_agent_limit ?? '未知'} 个子 Agent</dd>
      </div>
      <div>
        <dt>模型轮数</dt>
        <dd>{options.max_model_rounds ?? '未知'} 轮</dd>
      </div>
    </dl>
  )
}

export function EffectiveDefaults({
  effective,
  models = [],
}: {
  effective: ResolvedDefaults
  models?: ModelConnection[]
}) {
  const model = models.find((item) => item.id === effective.model_connection_id)
  return (
    <details className="runtime-effective" open>
      <summary>服务端解析后的当前有效配置与来源</summary>
      <dl className="run-meta">
        {runOptionFields.map((field) => (
          <div className="runtime-effective-row" key={field.key}>
            <dt>{field.label}</dt>
            <dd>
              {effective.options[field.key] ?? '未知'} · {sourceLabel(effective.sources[field.key])}
            </dd>
          </div>
        ))}
        <dt>执行配置</dt>
        <dd>
          {sandboxLabel(effective.options.sandbox_profile)} ·{' '}
          {sourceLabel(effective.sources.sandbox_profile)}
        </dd>
        <dt>审批方式</dt>
        <dd>
          {effective.options.approval_mode === 'manual' ? '手动审批' : '智能审批'} ·{' '}
          {sourceLabel(effective.sources.approval_mode)}
        </dd>
        <dt>默认模型</dt>
        <dd>
          {effective.model_connection_id
            ? model
              ? model.name
              : effective.model_connection_id
            : '未配置'}{' '}
          · {sourceLabel(effective.model_source)}
        </dd>
        <dt>来源版本</dt>
        <dd>
          用户 {effective.user_version} · 工作区 {effective.workspace_version}
        </dd>
      </dl>
    </details>
  )
}
