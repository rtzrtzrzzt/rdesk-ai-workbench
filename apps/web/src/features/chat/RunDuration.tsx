import { useEffect, useState } from 'react'
import { Check, Clock3, LoaderCircle } from 'lucide-react'
import type { Run } from '../../api/types'
import { runTerminal } from './runState'

function durationLabel(milliseconds: number) {
  const seconds = Math.floor(Math.max(0, milliseconds) / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}分${seconds % 60}秒`
  return `${Math.floor(minutes / 60)}小时${minutes % 60}分${seconds % 60}秒`
}

/** Each round keeps its server duration; polling/reloads never restart its clock. */
export function RunDuration({ run }: { run: Run }) {
  const terminal = runTerminal.has(run.status)
  const timing = run.timing
  const [elapsed, setElapsed] = useState(timing?.elapsed_ms ?? 0)
  useEffect(() => {
    setElapsed(timing?.elapsed_ms ?? 0)
    if (!timing || terminal) return
    // Browser wall-clock changes do not alter the server's measured duration.
    const started = performance.now()
    const timer = window.setInterval(
      () => setElapsed(timing.elapsed_ms + performance.now() - started),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [run.id, terminal, timing?.elapsed_ms, timing?.observed_at])
  if (!timing) return null
  const waiting = ['queued', 'paused', 'held', 'waiting_user', 'waiting_approval'].includes(
    run.status,
  )
  const Icon = terminal
    ? run.status === 'succeeded'
      ? Check
      : Clock3
    : waiting
      ? Clock3
      : LoaderCircle
  const label = terminal
    ? '本轮用时'
    : run.status === 'paused' || run.status === 'held'
      ? '已暂停 · 本轮已用'
      : run.status === 'queued'
        ? '排队中 · 本轮已用'
        : run.status === 'waiting_user'
          ? '等待回答 · 本轮已用'
          : run.status === 'waiting_approval'
            ? '等待授权 · 本轮已用'
            : '已工作'
  return (
    <div
      className="run-duration"
      data-run-id={run.id}
      role="timer"
      aria-label="本轮用时"
      title="从本轮任务被接收到结束的总耗时，包含模型、工具、排队与等待。"
    >
      <Icon
        size={13}
        className={!terminal && !waiting ? 'activity-spin' : undefined}
        aria-hidden="true"
      />
      <span>
        {label} {durationLabel(terminal ? timing.elapsed_ms : elapsed)}
      </span>
    </div>
  )
}
