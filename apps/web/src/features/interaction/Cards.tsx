import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Check, ChevronDown, ShieldCheck, Circle, AlertCircle } from 'lucide-react'
import { Button, Status } from '../../components/ui'
import { ApiError } from '../../api/client'
import { SchemaForm, inspectFormSchema } from './SchemaForm'
import type { SchemaFormValue } from './SchemaForm'
import { isRecord, exactKeys } from '../generated/validation'
import type { Question, QuestionAnswer } from '../../api/types'

export type QuestionView = {
  id: string
  version: number
  title: string
  prompt: string
  context?: string
  status: string
  kind: 'single' | 'multiple' | 'text'
  options: Question['options']
  required?: boolean
  acceptedAnswer?: string
}
export function QuestionCard({
  question,
  onSubmit,
  unavailableReason,
}: {
  unavailableReason?: string
  question: QuestionView
  onSubmit?: (answer: {
    option_ids: string[]
    text: string
    request_id: string
    version: number
  }) => Promise<void>
}) {
  const [selected, setSelected] = useState<string[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [submission, setSubmission] = useState<{
    option_ids: string[]
    text: string
    request_id: string
    version: number
  } | null>(null)
  const pending = question.status === 'pending' && !unavailableReason
  async function submit() {
    if (!onSubmit || busy) return
    const command = submission ?? {
      option_ids: [...selected],
      text,
      request_id: crypto.randomUUID(),
      version: question.version,
    }
    setSubmission(command)
    setBusy(true)
    setError('')
    try {
      await onSubmit(command)
      setSubmission(null)
    } catch (err) {
      const uncertain = err instanceof TypeError || (err instanceof ApiError && err.status >= 500)
      if (!uncertain) setSubmission(null)
      setError(
        `${err instanceof Error ? err.message : '答案未确认'}${uncertain ? ' 核对将使用原答案，请勿重复提交不同内容。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="interaction-card" aria-labelledby={`question-${question.id}`}>
      <div className="card-eyebrow">需要你的回答</div>
      <h3 id={`question-${question.id}`}>{question.title}</h3>
      <p>{question.prompt}</p>
      {question.context && <p className="muted">{question.context}</p>}
      {pending ? (
        <>
          <fieldset disabled={busy || !!submission} className="question-options">
            <legend className="sr-only">{question.prompt}</legend>
            {question.kind === 'text' ? (
              <textarea
                aria-label="你的回答"
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
            ) : (
              question.options.map((option) => (
                <label key={option.id}>
                  <input
                    type={question.kind === 'single' ? 'radio' : 'checkbox'}
                    name={question.id}
                    checked={selected.includes(option.id)}
                    onChange={() => {
                      setSelected(
                        question.kind === 'single'
                          ? [option.id]
                          : selected.includes(option.id)
                            ? selected.filter((id) => id !== option.id)
                            : [...selected, option.id],
                      )
                    }}
                  />
                  <span>
                    {option.label}
                    {option.description && <small>{option.description}</small>}
                  </span>
                </label>
              ))
            )}
          </fieldset>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <Button
            variant="primary"
            disabled={
              !onSubmit ||
              busy ||
              (question.required !== false && !(selected.length || text.trim()))
            }
            onClick={() => void submit()}
          >
            {busy ? '正在提交…' : submission ? '核对本次回答' : '提交回答'}
          </Button>
        </>
      ) : (
        <Status tone={question.status === 'answered' ? 'good' : 'neutral'}>
          {question.status === 'answered'
            ? `答案已收到${question.acceptedAnswer ? `：${question.acceptedAnswer}` : ''}`
            : (unavailableReason ?? '此问题已失效，草稿不会自动提交')}
        </Status>
      )}
    </section>
  )
}
export function ApprovalCard({
  title,
  target,
  scope,
  impact,
  pending,
  onDecide,
  resolvedLabel = '已处理',
}: {
  resolvedLabel?: string
  title: string
  target: string
  scope: ReactNode
  impact: string
  pending: boolean
  onDecide?: (allow: boolean) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function decide(allow: boolean) {
    if (!onDecide || busy) return
    setBusy(true)
    setError('')
    try {
      await onDecide(allow)
    } catch (err) {
      setError(err instanceof Error ? err.message : '授权结果尚未确认。')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="interaction-card approval-card">
      <div className="card-eyebrow">
        <ShieldCheck size={15} />
        需要授权
      </div>
      <h3>{title}</h3>
      <dl>
        <dt>目标</dt>
        <dd>{target}</dd>
        <dt>数据范围</dt>
        <dd>{scope}</dd>
        <dt>预期影响</dt>
        <dd>{impact}</dd>
      </dl>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {pending ? (
        <div className="button-row">
          <Button disabled={!onDecide || busy} onClick={() => void decide(false)}>
            拒绝
          </Button>
          <Button variant="primary" disabled={!onDecide || busy} onClick={() => void decide(true)}>
            {busy ? '正在提交…' : '允许本次操作'}
          </Button>
        </div>
      ) : (
        <Status>{resolvedLabel}</Status>
      )}
    </section>
  )
}
export function StepsCard({
  title,
  steps,
}: {
  title: string
  steps: { id: string; title: string; status: string; detail?: string }[]
}) {
  return (
    <details className="steps-card">
      <summary>
        {title}
        <ChevronDown size={16} />
      </summary>
      <ol>
        {steps.map((step) => (
          <li key={step.id}>
            {step.status === 'succeeded' ? (
              <Check size={16} />
            ) : step.status === 'failed' || step.status === 'outcome_unknown' ? (
              <AlertCircle size={16} />
            ) : (
              <Circle size={14} />
            )}
            <div>
              <span>{step.title}</span>
              {step.detail && <p>{step.detail}</p>}
              {step.status === 'outcome_unknown' && (
                <p className="form-error">操作结果未知，需要先核对，不能直接重试。</p>
              )}
            </div>
          </li>
        ))}
      </ol>
    </details>
  )
}

/** Durable Question adapter: dictionary answers never enter the legacy string view. */
export function RunQuestionCard({
  question,
  onAnswer,
  unavailableReason,
}: {
  unavailableReason?: string
  question: Question
  onAnswer?: (answer: QuestionAnswer['answer'], requestId: string) => Promise<void>
}) {
  if (question.kind === 'form')
    return (
      <FormQuestionCard
        question={question}
        onAnswer={onAnswer}
        unavailableReason={unavailableReason}
      />
    )
  const acceptedAnswer =
    typeof question.answer === 'string'
      ? (question.options.find((option) => option.id === question.answer)?.label ?? question.answer)
      : Array.isArray(question.answer)
        ? question.answer
            .map((id) => question.options.find((option) => option.id === id)?.label ?? id)
            .join('、')
        : undefined
  return (
    <QuestionCard
      unavailableReason={unavailableReason}
      question={{
        id: question.id,
        version: question.version,
        title: '继续前需要确认',
        prompt: question.prompt,
        status: question.status,
        acceptedAnswer,
        kind:
          question.kind === 'text'
            ? 'text'
            : question.kind === 'single_choice'
              ? 'single'
              : 'multiple',
        options: question.options,
      }}
      onSubmit={
        onAnswer
          ? async (answer) => {
              await onAnswer(
                question.kind === 'text'
                  ? answer.text
                  : question.kind === 'single_choice'
                    ? answer.option_ids[0]
                    : answer.option_ids,
                answer.request_id,
              )
            }
          : undefined
      }
    />
  )
}
function acceptedForm(value: unknown): SchemaFormValue | undefined {
  return isRecord(value) &&
    exactKeys(value, ['action', 'fields']) &&
    (value.action === 'accept' || value.action === 'decline' || value.action === 'cancel') &&
    isRecord(value.fields)
    ? { action: value.action, fields: value.fields }
    : undefined
}
function FormQuestionCard({
  question,
  onAnswer,
  unavailableReason,
}: {
  unavailableReason?: string
  question: Question
  onAnswer?: (answer: QuestionAnswer['answer'], requestId: string) => Promise<void>
}) {
  const pendingRequest = useRef<{ value: SchemaFormValue; id: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [unknown, setUnknown] = useState(false)
  const schema = question.form_schema ?? {}
  const inspection = inspectFormSchema(schema)
  const received = acceptedForm(question.answer)
  const pending = question.status === 'pending' && !unavailableReason
  async function submit(value: SchemaFormValue) {
    if (!onAnswer || busy || !pending) return
    const prior = pendingRequest.current
    if (prior && JSON.stringify(prior.value) !== JSON.stringify(value))
      throw new Error('上次表单提交未确认，必须先核对原动作与字段。')
    const command = prior ?? { value: structuredClone(value), id: crypto.randomUUID() }
    pendingRequest.current = command
    setBusy(true)
    setError('')
    setUnknown(false)
    try {
      await onAnswer(command.value, command.id)
      pendingRequest.current = null
    } catch (reason) {
      const uncertain = !(reason instanceof ApiError && reason.status < 500)
      if (!uncertain) pendingRequest.current = null
      setUnknown(uncertain)
      setError(
        `${reason instanceof Error ? reason.message : '表单提交未确认'}${uncertain ? ' 将保留原动作与同一请求标识核对。' : ''}`,
      )
      throw reason
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="interaction-card" aria-labelledby={`question-${question.id}`}>
      <div className="card-eyebrow">需要你的回答</div>
      <h3 id={`question-${question.id}`}>继续前需要确认</h3>
      <p>{question.prompt}</p>
      {inspection.reason ? (
        <>
          <p className="form-error">此表单不能在当前界面填写：{inspection.reason}</p>
          <p className="page-note">
            不会改用普通文本采集秘密或不支持的字段。你可以明确拒绝或取消此问题。
          </p>
          {pending ? (
            <div className="button-row">
              {unknown && pendingRequest.current ? (
                <Button
                  disabled={busy || !onAnswer}
                  onClick={() => {
                    const request = pendingRequest.current
                    if (request) void submit(request.value).catch(() => {})
                  }}
                >
                  核对本次提交
                </Button>
              ) : (
                <>
                  <Button
                    disabled={busy || !onAnswer}
                    onClick={() => void submit({ action: 'decline', fields: {} }).catch(() => {})}
                  >
                    拒绝回答
                  </Button>
                  <Button
                    disabled={busy || !onAnswer}
                    onClick={() => void submit({ action: 'cancel', fields: {} }).catch(() => {})}
                  >
                    取消此问题
                  </Button>
                </>
              )}
            </div>
          ) : (
            <Status tone={question.status === 'answered' ? 'good' : 'neutral'}>
              {question.status === 'answered'
                ? received?.action === 'decline'
                  ? '拒绝已收到'
                  : received?.action === 'cancel'
                    ? '取消已收到'
                    : '答案已收到，字段未展示'
                : (unavailableReason ?? '此问题已失效')}
            </Status>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
        </>
      ) : question.status === 'answered' && !received ? (
        <Status>回答已收到，现有记录无法按当前表单读取</Status>
      ) : (
        <SchemaForm
          schema={schema}
          disabled={!pending || !onAnswer}
          acceptedValue={received}
          onSubmit={submit}
        />
      )}
      {!pending && question.status !== 'answered' && !inspection.reason && (
        <Status>{unavailableReason ?? '此问题已失效，不会提交草稿'}</Status>
      )}
    </section>
  )
}
