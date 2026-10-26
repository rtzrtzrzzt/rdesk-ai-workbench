import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { mediaApi, type MediaReconcile, type MediaTaskView } from '../../api/media'
import { Button } from '../../components/ui'
import { describeError, record } from './shared'
import { updateTaskInHistory } from './history'

export function MediaReconcileForm({
  workspaceId,
  task,
}: {
  workspaceId: string
  task: MediaTaskView
}) {
  const client = useQueryClient()
  const [providerJobId, setProviderJobId] = useState(task.provider_job_id ?? '')
  const [confirmedAbsent, setConfirmedAbsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const submitted = useRef<MediaReconcile | null>(null)
  const inFlight = useRef(false)
  const locked = busy || uncertain
  async function received(result: MediaTaskView) {
    updateTaskInHistory(client, workspaceId, result)
    submitted.current = null
    setUncertain(false)
    setFailure(false)
    setMessage(
      result.status === 'cancelled'
        ? '核对已保存，此任务已关闭。'
        : result.deletion_requested
          ? '已登记提供方任务，正在停止；确认资源关闭后移除记录。'
          : '核对已保存，将查询已存在的提供方任务；没有重新提交生成。',
    )
    await client.invalidateQueries({ queryKey: ['media-tasks', workspaceId] })
  }
  async function submit(original = false) {
    if (inFlight.current || (!original && uncertain)) return
    if (!original) {
      const actualId = providerJobId.trim()
      if (!actualId && !confirmedAbsent) return
      submitted.current = {
        provider_job_id: confirmedAbsent ? null : actualId,
        confirmed_not_submitted: confirmedAbsent,
        request_id: crypto.randomUUID(),
      }
    }
    if (!submitted.current) return
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      const result = await mediaApi.reconcile(workspaceId, task.run_id, submitted.current)
      await received(result)
    } catch (error) {
      const unknown =
        original || !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      setUncertain(unknown)
      if (!unknown) submitted.current = null
      setFailure(true)
      setMessage(
        `${describeError(error)}${unknown ? ' 原核对参数和标识已保留，请读取结果或使用原请求重试。' : ''}`,
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  async function readReceipt() {
    if (inFlight.current || !submitted.current) return
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      const receipt = record(await mediaApi.receipt(submitted.current.request_id))
      if (receipt?.run_id !== task.run_id && receipt?.id !== task.run_id)
        throw new Error('未取得与本任务匹配的核对回执，原请求仍保留。')
      await received(await mediaApi.task(workspaceId, task.run_id))
    } catch (error) {
      setFailure(true)
      setMessage(
        error instanceof ApiError && error.status === 404
          ? '尚未查到回执，请稍后核对；原参数和请求标识仍保留。'
          : describeError(error),
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  return (
    <details className="media-reconcile">
      <summary>核对提供方结果</summary>
      <form
        className="stacked-form"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <p className="page-note">
          {task.deletion_requested
            ? '删除尚需关闭原有任务。填写提供方真实任务 ID 后仅查询并停止，不会恢复或重新生成。'
            : '仅填写从提供方实际取得的任务 ID，恢复查询已有任务；不会重新生成。'}
          本机任务按实际进程回执核对，不使用手动关闭。
        </p>
        <label>
          提供方任务 ID
          <input
            maxLength={200}
            pattern="[a-zA-Z0-9_.-]+"
            placeholder="从提供方后台复制真实任务 ID"
            value={providerJobId}
            disabled={locked || confirmedAbsent}
            onChange={(event) => setProviderJobId(event.target.value)}
          />
        </label>
        <p className="page-note">
          任务 ID 恢复查询适用于支持异步任务的通用生成服务；同步图像接口需先在提供方核实结果。
        </p>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={confirmedAbsent}
            disabled={locked || !!task.provider_job_id}
            onChange={(event) => {
              setConfirmedAbsent(event.target.checked)
              if (event.target.checked) setProviderJobId('')
            }}
          />
          <span>我已向提供方核实：该提交确实未被接受，也没有已创建的任务</span>
        </label>
        {task.provider_job_id && (
          <p className="page-note">已有提供方任务 ID，不能声明未接受；请查询或停止该任务。</p>
        )}
        <Button type="submit" disabled={locked || (!providerJobId.trim() && !confirmedAbsent)}>
          {busy
            ? '正在核对…'
            : confirmedAbsent
              ? '确认未接受并关闭此任务'
              : task.deletion_requested
                ? '核对并停止原任务'
                : '恢复查询此任务'}
        </Button>
        {uncertain && (
          <div className="button-row">
            <Button disabled={busy} onClick={() => void readReceipt()}>
              读取核对回执
            </Button>
            <Button disabled={busy} onClick={() => void submit(true)}>
              使用原核对请求重试
            </Button>
          </div>
        )}
        {message && (
          <p className={failure ? 'form-error' : 'page-note'} role={failure ? 'alert' : 'status'}>
            {message}
          </p>
        )}
      </form>
    </details>
  )
}
