/** Keep user-facing states consistent across the conversation and its task details. */
export const runTerminal = new Set(['succeeded', 'failed', 'cancelled'])
export const runLabels: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  waiting_user: '等待回答',
  waiting_approval: '等待授权',
  waiting_resource: '等待模型或其他资源',
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
  !runTerminal.has(status) && status !== 'held' && status !== 'cancelling'
