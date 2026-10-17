import { ApiError } from '../../api/client'

export const record = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
export const text = (value: unknown) => (typeof value === 'string' ? value : null)
export const number = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) ? value : null
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成。'
export const uncertain = (error: unknown) => {
  const body = error instanceof ApiError ? record(error.body) : null
  return (
    !(error instanceof ApiError) ||
    error.status >= 500 ||
    error.status === 408 ||
    record(body?.error)?.kind === 'outcome_unknown'
  )
}
export const json = (value: unknown) => JSON.stringify(value, null, 2)
export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    running: '运行中',
    stopped: '已停止',
    unavailable: '不可用',
    unknown: '状态未知',
    creating: '正在创建',
    active: '活动中',
    stopping: '正在停止',
    taken_over: '已人工接管',
    expired: '已过期',
    outcome_unknown: '结果未知，待核对',
    pending: '等待结果',
    succeeded: '已执行',
    failed: '未执行成功',
    available: '可用',
    needs_permission: '需要系统权限',
    unsupported: '不支持',
    waiting_verification: '等待真实验证',
    unconfigured: '未配置',
  }
  return labels[value] ?? value
}
export function exactOrigins(value: string, limit: number) {
  const values = value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (values.length > limit) throw new Error(`Origin 最多 ${limit} 项。`)
  for (const item of values) {
    let url: URL
    try {
      url = new URL(item)
    } catch {
      throw new Error(`无效的精确 Origin：${item}`)
    }
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.origin !== item ||
      url.hostname.includes('*') ||
      url.username ||
      url.password
    )
      throw new Error(`请填写精确 http(s) Origin，不含路径、尾斜杠或通配符：${item}`)
  }
  return [...new Set(values)]
}
