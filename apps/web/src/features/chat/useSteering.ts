import { useRef, useState } from 'react'
import { api } from '../../api/workbench'
import { ApiError, authGeneration } from '../../api/client'
import type { SteerRun } from '../../api/types'

type Entry = { draft: string; pending: SteerRun | null; busy: boolean; notice: string }
const empty: Entry = { draft: '', pending: null, busy: false, notice: '' }

/** Drafts and uncertain submissions belong to a Run, even when the user switches chats. */
export function useSteering(runId: string | undefined, onRefresh: () => Promise<void>) {
  const [entries, setEntries] = useState<Record<string, Entry>>({})
  const inFlight = useRef(new Set<string>())
  const entry = (runId && entries[runId]) || empty
  function patch(id: string, change: Partial<Entry>) {
    setEntries((previous) => ({ ...previous, [id]: { ...(previous[id] ?? empty), ...change } }))
  }
  async function send() {
    if (!runId || inFlight.current.has(runId) || (!entry.pending && !entry.draft.trim())) return
    const id = runId
    const generation = authGeneration()
    const command = entry.pending ?? { input: entry.draft.trim(), request_id: crypto.randomUUID() }
    inFlight.current.add(id)
    patch(id, { pending: command, busy: true, notice: '' })
    try {
      await api.steer(id, command)
      patch(id, { pending: null, draft: '', notice: '补充要求已收到，将在下一次模型调用时使用。' })
      await onRefresh().catch(() =>
        patch(id, { notice: '补充要求已收到；任务状态暂时未刷新，可稍后重新读取。' }),
      )
    } catch (error) {
      if (generation !== authGeneration()) return
      const uncertain = !(error instanceof ApiError && error.status < 500 && error.status !== 408)
      patch(id, {
        pending: uncertain ? command : null,
        notice: `${error instanceof Error ? error.message : '提交未完成'}${uncertain ? ' 结果未确认，请核对本次补充要求。' : ''}`,
      })
    } finally {
      if (generation === authGeneration()) {
        inFlight.current.delete(id)
        patch(id, { busy: false })
      }
    }
  }
  return {
    ...entry,
    value: entry.pending?.input ?? entry.draft,
    change: (draft: string) => {
      if (runId && !entry.pending && !entry.busy) patch(runId, { draft, notice: '' })
    },
    send,
    reset: () => {
      inFlight.current.clear()
      setEntries({})
    },
  }
}
