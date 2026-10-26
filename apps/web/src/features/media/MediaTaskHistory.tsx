import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Trash2 } from 'lucide-react'
import { mediaApi, type MediaKind, type MediaTaskView } from '../../api/media'
import { Button } from '../../components/ui'
import { MediaTaskCard } from './MediaTaskCard'
import { MediaTaskDeletionDialog } from './MediaTaskDeletionDialog'
import { capabilities, taskTerminal } from './shared'
import { mediaHistoryKey } from './history'

export function MediaTaskHistory({
  workspaceId,
  userId,
  capability,
  active,
}: {
  workspaceId: string
  userId: string
  capability: MediaKind
  active: boolean
}) {
  const client = useQueryClient()
  const [selection, setSelection] = useState<string[]>([])
  const [targets, setTargets] = useState<MediaTaskView[] | null>(null)
  const [deletionLocked, setDeletionLocked] = useState(false)
  const tasks = useInfiniteQuery({
    queryKey: mediaHistoryKey(workspaceId),
    queryFn: ({ pageParam, signal }) => mediaApi.tasks(workspaceId, undefined, signal, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_before ?? undefined,
    enabled: active,
    retry: false,
    refetchInterval: (query) =>
      active &&
      !query.state.error &&
      query.state.data?.pages.some((page) =>
        page.tasks.some(
          (task) =>
            task.deletion_requested || !taskTerminal.has(task.status) || !task.resources_closed,
        ),
      )
        ? 2000
        : false,
  })
  const byId = new Map<string, MediaTaskView>()
  for (const page of tasks.data?.pages ?? [])
    for (const task of page.tasks) if (!byId.has(task.run_id)) byId.set(task.run_id, task)
  const allTasks = [...byId.values()]
  const visible = allTasks.filter((task) => task.capability === capability)
  const selectable = visible.filter((task) => !task.deletion_requested)
  const selected = selectable.filter((task) => selection.includes(task.run_id))
  const pageSelection = selectable.slice(0, 200)
  const allSelected =
    pageSelection.length > 0 && pageSelection.every((task) => selection.includes(task.run_id))
  const selectableIds = selectable.map((task) => task.run_id).join(',')
  useEffect(() => {
    setSelection([])
  }, [capability])
  useEffect(() => {
    const present = new Set(selectableIds.split(','))
    setSelection((previous) => {
      const remaining = previous.filter((id) => present.has(id))
      return remaining.length === previous.length ? previous : remaining
    })
  }, [selectableIds])
  const completedTasks = allTasks
    .filter((task) => taskTerminal.has(task.status))
    .map((task) => `${task.run_id}:${task.status}`)
    .join(',')
  useEffect(() => {
    if (completedTasks) {
      void client.invalidateQueries({ queryKey: ['artifacts', workspaceId] })
      void client.invalidateQueries({ queryKey: ['media-connections', workspaceId] })
    }
  }, [completedTasks, client, workspaceId])
  const noun = capabilities.find((item) => item.id === capability)?.noun ?? capability
  return (
    <section className="media-task-history">
      <div className="media-history-heading">
        <h2>生成记录</h2>
        <Button disabled={tasks.isFetching} onClick={() => void tasks.refetch()}>
          <RefreshCw size={14} />
          刷新进度
        </Button>
      </div>
      {visible.length > 0 && (
        <div className="media-history-selection">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={allSelected}
              disabled={deletionLocked || !pageSelection.length}
              ref={(element) => {
                if (element) element.indeterminate = selected.length > 0 && !allSelected
              }}
              onChange={(event) =>
                setSelection(event.target.checked ? pageSelection.map((task) => task.run_id) : [])
              }
            />
            <span>{selectable.length > 200 ? '选择本页前 200 条' : '全选本页'}</span>
          </label>
          <span className="page-note">已选择 {selected.length} 条</span>
          <Button
            variant="danger"
            disabled={deletionLocked || !selected.length}
            onClick={() => setTargets(selected)}
          >
            <Trash2 size={14} />
            删除所选
          </Button>
        </div>
      )}
      <MediaTaskDeletionDialog
        workspaceId={workspaceId}
        userId={userId}
        targets={targets}
        onClose={() => setTargets(null)}
        onCompleted={() => setSelection([])}
        onLockChange={setDeletionLocked}
      />
      {tasks.error && (
        <p className="form-error" role="alert">
          {tasks.error.message}{' '}
          {tasks.isFetchNextPageError
            ? '加载更早记录未完成，已加载内容保留。'
            : '已有生成记录保留，可刷新重试。'}
        </p>
      )}
      {tasks.isPending ? (
        <p className="page-note">正在读取真实生成记录…</p>
      ) : (
        visible.map((task) => (
          <div className="media-history-record" key={task.run_id}>
            <label className="checkbox-label media-record-select">
              <input
                type="checkbox"
                aria-label={`选择 ${task.connection_name}，${new Date(task.created_at).toLocaleString()} 的生成记录`}
                checked={selection.includes(task.run_id)}
                disabled={
                  deletionLocked ||
                  !!task.deletion_requested ||
                  (!selection.includes(task.run_id) && selected.length >= 200)
                }
                onChange={(event) =>
                  setSelection((previous) =>
                    event.target.checked
                      ? [...previous, task.run_id]
                      : previous.filter((id) => id !== task.run_id),
                  )
                }
              />
              <span>{task.deletion_requested ? '正在停止删除' : '选择此记录'}</span>
            </label>
            <MediaTaskCard workspaceId={workspaceId} task={task} active={active} />
          </div>
        ))
      )}
      {!tasks.isPending && !tasks.error && !visible.length && (
        <p className="page-note">当前页没有{noun}生成记录。</p>
      )}
      {tasks.hasNextPage && (
        <div className="media-history-pagination">
          <Button
            disabled={tasks.isFetching}
            onClick={() => {
              if (!tasks.isFetching) void tasks.fetchNextPage()
            }}
          >
            {tasks.isFetchingNextPage
              ? '正在加载…'
              : tasks.isFetchNextPageError
                ? '重试加载更早记录'
                : '加载更早记录'}
          </Button>
        </div>
      )}
    </section>
  )
}
