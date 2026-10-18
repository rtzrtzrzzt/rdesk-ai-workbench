/** Keep user-facing states consistent across the conversation and its task details. */
export const runTerminal = new Set(['succeeded', 'failed', 'cancelled'])
export const runLabels: Record<string, string> = {
  queued: '排队中',
  running: '正在生成或执行工具',
  paused: '已暂停',
  waiting_user: '等待回答',
  waiting_approval: '等待授权',
  waiting_resource: '等待模型或其他资源',
  waiting_children: '等待子任务',
  created: '等待执行',
  superseded: '已由后续尝试接替',
  cancelling: '正在停止',
  held: '恢复后已暂停',
  succeeded: '已完成',
  failed: '未完成',
  cancelled: '已停止',
  pending: '待执行',
  outcome_unknown: '结果未知',
  not_started: '未开始',
}
export const runLabel = (status: string) => runLabels[status] ?? status
export const acceptsInput = (status: string) =>
  !runTerminal.has(status) && status !== 'held' && status !== 'paused' && status !== 'cancelling'

export function runStatusLabel(run: { status: string; activity?: string | null }) {
  return run.status === 'running'
    ? run.activity === 'tool'
      ? '正在使用工具'
      : run.activity === 'generating'
        ? '正在生成'
        : '正在执行'
    : runLabel(run.status)
}
export function threadStatusLabel(thread: {
  latest_run_status?: string | null
  latest_run_phase?: string | null
  deleting?: boolean
  archived?: boolean
}) {
  if (thread.deleting) return '删除处理中'
  if (thread.archived) return '已归档'
  return thread.latest_run_status
    ? runStatusLabel({ status: thread.latest_run_status, activity: thread.latest_run_phase })
    : '尚未开始'
}
