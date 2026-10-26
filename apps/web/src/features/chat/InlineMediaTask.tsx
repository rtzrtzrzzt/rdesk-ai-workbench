import { useQuery } from '@tanstack/react-query'
import { mediaApi } from '../../api/media'
import { Button } from '../../components/ui'
import { MediaTaskCard } from '../media/MediaTaskCard'
import { mediaTaskKey } from '../media/history'
import { describeError, taskTerminal } from '../media/shared'

export function InlineMediaTask({ workspaceId, runId }: { workspaceId: string; runId: string }) {
  const task = useQuery({
    queryKey: mediaTaskKey(workspaceId, runId),
    queryFn: ({ signal }) => mediaApi.task(workspaceId, runId, signal),
    refetchInterval: (query) => {
      const current = query.state.data
      if (query.state.error || !current) return false
      return taskTerminal.has(current.status) && current.resources_closed ? false : 2500
    },
  })
  return (
    <div className="tool-media-task" aria-label="生成任务" data-media-run-id={runId}>
      {task.isError ? (
        <div role="alert">
          <p className="form-error">{describeError(task.error)}</p>
          <Button onClick={() => void task.refetch()}>重新读取生成任务</Button>
        </div>
      ) : task.data ? (
        <MediaTaskCard workspaceId={workspaceId} task={task.data} active />
      ) : (
        <p role="status">正在读取生成任务…</p>
      )}
    </div>
  )
}
