import type { ReactNode } from 'react'

export function MetricCard({
  title,
  value,
  unit,
  hint,
  children,
}: {
  title: string
  value: ReactNode
  unit?: string
  hint?: ReactNode
  children?: ReactNode
}) {
  return (
    <section className="analytics-metric">
      <h2>{title}</h2>
      <div className="analytics-metric-value">
        {value}
        {unit && <span>{unit}</span>}
      </div>
      {hint && <p className="analytics-metric-hint">{hint}</p>}
      {children}
    </section>
  )
}
export function AnalyticsSection({
  title,
  description,
  children,
  action,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <section className="analytics-section">
      <div className="analytics-section-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}
export const countLabel = (value: number | null) =>
  value === null ? '未知' : value.toLocaleString('zh-CN')
export const numberLabel = (value: number | null, digits = 1) =>
  value === null ? '未知' : value.toLocaleString('zh-CN', { maximumFractionDigits: digits })
export const percentLabel = (value: number | null) =>
  value === null ? '未知' : `${numberLabel(value * 100, 1)}%`
export const coverageLabel = (known: number, total: number) =>
  total === 0 ? '无调用样本' : `${known.toLocaleString()} / ${total.toLocaleString()} 次调用可观测`
export function durationLabel(value: number | null) {
  if (value === null) return '未知'
  return value >= 1000 ? `${numberLabel(value / 1000, 2)} s` : `${numberLabel(value, 0)} ms`
}
export function bytesLabel(value: number | null) {
  if (value === null) return '未知'
  return `${numberLabel(value / 1024 / 1024 / 1024, 2)} GiB`
}
export function utcLabel(value: string, withTime = true) {
  return `${new Date(value).toLocaleString('zh-CN', { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}), hour12: false })} UTC`
}
export const callStatusLabels: Record<string, string> = {
  created: '已创建',
  running: '调用中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  outcome_unknown: '结果待核对',
}
