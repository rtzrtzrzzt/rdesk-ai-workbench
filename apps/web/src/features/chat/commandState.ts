import type { CommandView } from '../../api/types'
export const commandLabels: Record<string, string> = {
  reserved: '已预留',
  launching: '正在派发',
  starting: '正在启动',
  running: '进程运行中',
  stopping: '正在停止',
  completed: '进程已退出',
  failed: '执行未正常完成',
  cancelled: '已记录取消',
  timed_out: '已超时',
  output_limit: '达到输出上限',
  outcome_unknown: '结果未知',
}
export const commandLabel = (status: string) => commandLabels[status] ?? status
export function commandsForRun(commands: CommandView[], runId: string) {
  return commands.filter((command) => command.run_id === runId)
}
export function needsCommandObservation(commands: CommandView[]) {
  return commands.some((command) => !command.owned_resources_closed)
}
