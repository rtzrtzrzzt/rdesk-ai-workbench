import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, subscribeRun } from '../../api/workbench'
import type { Message, Run } from '../../api/types'
import { runTerminal } from './runState'

/** Text and replay cursor originate in the same server MVCC snapshot. */
export function useRunStream(run: Run | undefined, threadId: string, enabled: boolean) {
  const client = useQueryClient()
  const [connected, setConnected] = useState(false)
  const [streaming, setStreaming] = useState<Record<string, string>>({})
  const [reasoning, setReasoning] = useState<Record<string, string>>({})
  const disconnect = useRef<(() => void) | null>(null)
  const id = run?.id
  const ended = !!run && (runTerminal.has(run.status) || ['held', 'paused'].includes(run.status))
  useEffect(() => {
    setStreaming({})
    setReasoning({})
    setConnected(false)
    if (!id || !enabled) return
    let disposed = false
    let close: (() => void) | undefined
    let retry: ReturnType<typeof setTimeout> | undefined
    let bodies: Record<string, string> = {}
    let thoughts: Record<string, string> = {}
    const abort = new AbortController()
    const dispose = () => {
      disposed = true
      abort.abort()
      close?.()
      if (retry) clearTimeout(retry)
    }
    disconnect.current = dispose
    async function snapshot() {
      await client.cancelQueries({ queryKey: ['messages', threadId] })
      const value = await api.conversationSnapshot(id!, abort.signal)
      if (disposed) return value.run.last_event_seq
      bodies = Object.fromEntries(
        value.messages
          .filter((message) => message.role === 'assistant' && message.run_id === id)
          .map((message) => [message.id, message.content]),
      )
      thoughts = Object.fromEntries(
        value.messages
          .filter((message) => message.role === 'assistant' && message.run_id === id)
          .map((message) => [message.id, message.reasoning ?? '']),
      )
      client.setQueryData(['messages', threadId], value.messages)
      client.setQueryData<Run[]>(
        ['runs', threadId],
        (old) => old?.map((item) => (item.id === id ? value.run : item)) ?? [value.run],
      )
      setStreaming(ended ? {} : { ...bodies })
      setReasoning(ended ? {} : { ...thoughts })
      // A terminal poll can win the race with the last SSE event. Refresh the
      // accompanying activity/tree from this final authoritative boundary too.
      if (ended) {
        void client.invalidateQueries({ queryKey: ['thread-context', threadId] })
        void client.invalidateQueries({ queryKey: ['thread-activity', threadId] })
        void client.invalidateQueries({ queryKey: ['run-agents', value.run.root_run_id ?? id] })
      }
      return value.run.last_event_seq
    }
    async function start() {
      try {
        const cursor = await snapshot()
        if (disposed || ended) return
        close = subscribeRun(
          id!,
          cursor,
          (event) => {
            if (disposed) return
            const { message_id, delta } = event.payload
            if (
              event.type === 'message.delta' &&
              typeof message_id === 'string' &&
              typeof delta === 'string'
            ) {
              bodies[message_id] = (bodies[message_id] ?? '') + delta
              setStreaming({ ...bodies })
            } else if (
              event.type === 'message.reasoning_delta' &&
              typeof message_id === 'string' &&
              typeof delta === 'string'
            ) {
              thoughts[message_id] = (thoughts[message_id] ?? '') + delta
              setReasoning({ ...thoughts })
            } else {
              if (event.type === 'message.completed') {
                const message = event.payload.message as Message
                bodies[message.id] = message.content
                setStreaming({ ...bodies })
                thoughts[message.id] = message.reasoning ?? ''
                setReasoning({ ...thoughts })
              }
              void client.invalidateQueries({ queryKey: ['run-agents', run?.root_run_id ?? id] })
              void client.invalidateQueries({ queryKey: ['run-trace', id] })
              void client.invalidateQueries({ queryKey: ['run-surfaces', id] })
              void client.invalidateQueries({ queryKey: ['runs', threadId] })
              void client.invalidateQueries({ queryKey: ['threads'] })
              void client.invalidateQueries({ queryKey: ['queue', threadId] })
              if (
                [
                  'run.input_applied',
                  'run.input_delivered',
                  'run.input_received',
                  'run.input_processing',
                ].includes(event.type)
              )
                void client.invalidateQueries({ queryKey: ['steering-inputs', id] })
              void client.invalidateQueries({ queryKey: ['messages', threadId] })
              void client.invalidateQueries({ queryKey: ['thread-context', threadId] })
              void client.invalidateQueries({ queryKey: ['thread-activity', threadId] })
              void client.invalidateQueries({ queryKey: ['tool-details', threadId] })
            }
          },
          (value) => {
            if (!disposed) setConnected(value)
          },
          snapshot,
        )
      } catch {
        if (!disposed) retry = setTimeout(() => void start(), 2000)
      }
    }
    void start()
    return () => {
      dispose()
      if (disconnect.current === dispose) disconnect.current = null
    }
    // Polling snapshots must not reset the stream cursor. Ended forces one final authoritative read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, ended, threadId, enabled, client])
  return {
    connected,
    streaming,
    reasoning,
    clear: () => {
      setStreaming({})
      setReasoning({})
    },
    disconnect: () => {
      disconnect.current?.()
      setStreaming({})
      setReasoning({})
      setConnected(false)
    },
  }
}
