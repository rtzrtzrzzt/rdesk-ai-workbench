import type { InfiniteData, QueryClient } from '@tanstack/react-query'
import type { MediaTaskList, MediaTaskView, MediaDeletionResult } from '../../api/media'

export type MediaTaskPages = InfiniteData<MediaTaskList, string | null>
export const mediaHistoryKey = (workspaceId: string) =>
  ['media-tasks', workspaceId, 'pages'] as const
export const mediaTaskKey = (workspaceId: string, runId: string) =>
  ['media-task', workspaceId, runId] as const

export function updateTaskInHistory(client: QueryClient, workspaceId: string, task: MediaTaskView) {
  client.setQueryData(mediaTaskKey(workspaceId, task.run_id), task)
  client.setQueryData<MediaTaskPages>(mediaHistoryKey(workspaceId), (previous) => {
    if (!previous) return previous
    const present = previous.pages.some((page) =>
      page.tasks.some((item) => item.run_id === task.run_id),
    )
    return {
      ...previous,
      pages: previous.pages.map((page, index) => ({
        ...page,
        tasks: present
          ? page.tasks.map((item) => (item.run_id === task.run_id ? task : item))
          : index === 0
            ? [task, ...page.tasks]
            : page.tasks,
      })),
    }
  })
}

export function applyTaskDeletion(
  client: QueryClient,
  workspaceId: string,
  result: MediaDeletionResult,
) {
  const deleted = new Set(result.deleted)
  const stopping = new Set(result.stopping)
  client.setQueryData<MediaTaskPages>(mediaHistoryKey(workspaceId), (previous) =>
    previous
      ? {
          ...previous,
          pages: previous.pages.map((page) => ({
            ...page,
            tasks: page.tasks
              .filter((task) => !deleted.has(task.run_id))
              .map((task) =>
                stopping.has(task.run_id) ? { ...task, deletion_requested: true } : task,
              ),
          })),
        }
      : previous,
  )
}
