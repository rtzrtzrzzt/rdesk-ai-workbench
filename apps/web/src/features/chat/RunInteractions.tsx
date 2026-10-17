import { useRef } from 'react'
import { api } from '../../api/workbench'
import { ApiError } from '../../api/client'
import type { Run, Approval, Question } from '../../api/types'
import { ApprovalCard, RunQuestionCard } from '../interaction/Cards'
import { acceptsInput } from './runState'

const fieldLabels: Record<string, string> = {
  command: '执行命令',
  timeout_seconds: '最长执行秒数',
  path: '文件路径',
  content: '写入内容',
  old_text: '替换前',
  new_text: '替换后',
  tool_name: '工具',
  arguments: '工具参数',
  artifact_id: '文件标识',
  start_line: '起始行',
  limit: '读取行数',
  target_ref: '页面目标',
}
const technical = new Set(['expected_version', 'last_observation', 'session_id', 'connection_id'])
function ArgumentValues({ values }: { values: [string, unknown][] }) {
  return (
    <dl className="approval-arguments">
      {values.map(([key, value]) => (
        <div key={key}>
          <dt>{fieldLabels[key] ?? key}</dt>
          <dd>
            <pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
          </dd>
        </div>
      ))}
    </dl>
  )
}
function ApprovalArguments({ approval }: { approval: Approval }) {
  const entries = Object.entries(approval.arguments)
  return (
    <>
      <ArgumentValues values={entries.filter(([key]) => !technical.has(key))} />
      {entries.some(([key]) => technical.has(key)) && (
        <details className="approval-technical">
          <summary>版本与执行标识</summary>
          <ArgumentValues values={entries.filter(([key]) => technical.has(key))} />
        </details>
      )}
    </>
  )
}

/** Pending decisions must remain reachable when task traces or child agents are collapsed. */
export function RunInteractions({
  run,
  onRefresh,
  historyOnly = false,
  pendingOnly = false,
}: {
  run: Run
  onRefresh: () => Promise<void>
  historyOnly?: boolean
  pendingOnly?: boolean
}) {
  const decisionIds = useRef<Record<string, { approved: boolean; id: string }>>({})
  const available = acceptsInput(run.status)
  const unavailableReason =
    run.status === 'held' ? '此任务已暂停，历史确认只读。' : '此任务已停止接受回答。'
  function questionCard(question: Question) {
    return (
      <RunQuestionCard
        key={`${question.id}:${question.version}`}
        question={question}
        unavailableReason={
          !available && question.status === 'pending' ? unavailableReason : undefined
        }
        onAnswer={
          available
            ? async (answer, requestId) => {
                await api.answer(question, answer, requestId)
                await onRefresh()
              }
            : undefined
        }
      />
    )
  }
  function approvalCard(approval: Approval) {
    return (
      <ApprovalCard
        key={`${approval.id}:${approval.version}`}
        title={approval.summary}
        target={approval.tool_name}
        scope={<ApprovalArguments approval={approval} />}
        impact="仅授权上方这一次操作。"
        pending={available && approval.status === 'pending'}
        resolvedLabel={
          approval.status === 'pending'
            ? unavailableReason
            : approval.status === 'approved'
              ? '已允许本次操作'
              : approval.status === 'rejected'
                ? '已拒绝'
                : '已处理'
        }
        onDecide={
          available
            ? async (approved) => {
                const previous = decisionIds.current[approval.id]
                const decision = previous ?? { approved, id: crypto.randomUUID() }
                if (previous && previous.approved !== approved)
                  throw new Error('上次决定尚未确认，请先核对原决定。')
                decisionIds.current[approval.id] = decision
                try {
                  await api.decide(approval, approved, decision.id)
                } catch (error) {
                  if (error instanceof ApiError && error.status < 500 && error.status !== 408)
                    delete decisionIds.current[approval.id]
                  throw error
                }
                await onRefresh()
              }
            : undefined
        }
      />
    )
  }
  const pendingQuestions = run.questions.filter((item) => available && item.status === 'pending')
  const pendingApprovals = run.approvals.filter((item) => available && item.status === 'pending')
  const historyQuestions = run.questions.filter((item) => !available || item.status !== 'pending')
  const historyApprovals = run.approvals.filter((item) => !available || item.status !== 'pending')
  const historyCount = historyQuestions.length + historyApprovals.length
  return (
    <>
      {!historyOnly && pendingQuestions.map(questionCard)}
      {!historyOnly && pendingApprovals.map(approvalCard)}
      {!pendingOnly && historyCount > 0 && (
        <details className="interaction-history">
          <summary>确认记录 · {historyCount}</summary>
          {historyQuestions.map(questionCard)}
          {historyApprovals.map(approvalCard)}
        </details>
      )}
    </>
  )
}
