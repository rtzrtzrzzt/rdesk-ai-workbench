import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, authGeneration } from '../../api/client'
import {
  mediaApi,
  type MediaDeletionResult,
  type MediaTaskDeletion,
  type MediaTaskView,
} from '../../api/media'
import { closePendingRequest } from '../../api/requests'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { applyTaskDeletion } from './history'
import { describeError, record, taskTerminal } from './shared'

const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
function readPending(key: string): { input: MediaTaskDeletion | null; error: string } {
  try {
    const stored = localStorage.getItem(key)
    if (!stored) return { input: null, error: '' }
    const input = record(JSON.parse(stored))
    if (
      !input ||
      typeof input.request_id !== 'string' ||
      !uuid.test(input.request_id) ||
      typeof input.stop_active !== 'boolean' ||
      !Array.isArray(input.run_ids) ||
      input.run_ids.length < 1 ||
      input.run_ids.length > 200 ||
      !input.run_ids.every((id: unknown): id is string => typeof id === 'string' && uuid.test(id))
    )
      throw new Error('invalid deletion request')
    return {
      input: {
        request_id: input.request_id,
        run_ids: input.run_ids,
        stop_active: input.stop_active,
      },
      error: '',
    }
  } catch {
    return { input: null, error: '待核对删除请求无法读取，当前暂停新删除；请保留浏览器记录。' }
  }
}
function deletionResult(value: unknown, input: MediaTaskDeletion): MediaDeletionResult {
  const result = record(value)
  const ids = new Set(input.run_ids)
  if (
    !result ||
    !Array.isArray(result.deleted) ||
    !Array.isArray(result.stopping) ||
    !result.deleted.every((id: unknown): id is string => typeof id === 'string' && ids.has(id)) ||
    !result.stopping.every((id: unknown): id is string => typeof id === 'string' && ids.has(id))
  )
    throw new Error('删除回执与本次选择不匹配，原请求仍保留。')
  return { deleted: result.deleted, stopping: result.stopping }
}

export function MediaTaskDeletionDialog({
  workspaceId,
  userId,
  targets,
  onClose,
  onCompleted,
  onLockChange,
}: {
  workspaceId: string
  userId: string
  targets: MediaTaskView[] | null
  onClose: () => void
  onCompleted: () => void
  onLockChange: (locked: boolean) => void
}) {
  const client = useQueryClient()
  const key = `workbench.media-deletion.${userId}.${workspaceId}`
  const [restored] = useState(() => readPending(key))
  const [pending, setPending] = useState<MediaTaskDeletion | null>(restored.input)
  const [busy, setBusy] = useState(false)
  const [stopActive, setStopActive] = useState(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState(false)
  const inFlight = useRef(false)
  const activeCount = (targets ?? []).filter(
    (task) => !taskTerminal.has(task.status) || !task.resources_closed,
  ).length
  const locked = busy || !!pending || !!restored.error
  useEffect(() => {
    onLockChange(locked)
  }, [locked, onLockChange])
  function store(input: MediaTaskDeletion | null) {
    if (input) localStorage.setItem(key, JSON.stringify(input))
    else localStorage.removeItem(key)
    setPending(input)
  }
  async function complete(input: MediaTaskDeletion, response: unknown) {
    const result = deletionResult(response, input)
    applyTaskDeletion(client, workspaceId, result)
    store(null)
    setFailure(false)
    setMessage(
      `已删除 ${result.deleted.length} 条记录${result.stopping.length ? `，${result.stopping.length} 条正在停止，待资源关闭后移除` : ''}。文件库产物保留。`,
    )
    setStopActive(false)
    onCompleted()
    onClose()
    await client.invalidateQueries({ queryKey: ['media-tasks', workspaceId] })
  }
  async function submit(input: MediaTaskDeletion, original = false) {
    if (inFlight.current || restored.error || (!original && pending)) return
    try {
      store(input)
    } catch {
      setFailure(true)
      setMessage('无法保留删除请求标识，本次尚未提交。')
      return
    }
    const generation = authGeneration()
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      const result = await mediaApi.deleteTasks(workspaceId, input)
      if (generation !== authGeneration()) return
      await complete(input, result)
    } catch (error) {
      if (generation !== authGeneration()) return
      let retain =
        original || !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      if (!retain) {
        try {
          store(null)
        } catch {
          retain = true
        }
      }
      setFailure(true)
      setMessage(
        `${describeError(error)}${retain ? ' 原选择、停止选项与请求标识已保留，请先核对。' : ''}`,
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  async function reconcile(close = false) {
    if (inFlight.current || !pending) return
    const input = pending
    const generation = authGeneration()
    inFlight.current = true
    setBusy(true)
    setMessage('')
    setFailure(false)
    try {
      if (close) {
        const result = await closePendingRequest(input.request_id)
        if (generation !== authGeneration()) return
        if (result.status === 'sealed') {
          store(null)
          setMessage('删除请求已封存，原标识不会再次执行。请查看当前记录后再操作。')
          await client.invalidateQueries({ queryKey: ['media-tasks', workspaceId] })
          return
        }
      }
      const result = await mediaApi.receipt(input.request_id)
      if (generation !== authGeneration()) return
      await complete(input, result)
    } catch (error) {
      if (generation !== authGeneration()) return
      setFailure(true)
      setMessage(
        error instanceof ApiError && error.status === 404
          ? '尚未查到删除回执，请求可能仍在处理。原请求保留，可稍后核对。'
          : describeError(error),
      )
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  const status = (
    <>
      {restored.error && (
        <p className="form-error" role="alert">
          {restored.error}
        </p>
      )}
      {pending && (
        <div className="media-pending">
          <p role="status">
            {busy ? '正在处理删除请求…' : '上次删除请求结果待核对。'} 原选择{' '}
            {pending.run_ids.length} 条{pending.stop_active ? '，已选择停止活动任务' : ''}。
          </p>
          <p className="media-request-id">请求标识：{pending.request_id}</p>
          <div className="button-row">
            <Button disabled={busy} onClick={() => void reconcile()}>
              核对删除结果
            </Button>
            <Button disabled={busy} onClick={() => void submit(pending, true)}>
              使用原删除请求重试
            </Button>
            <Button disabled={busy} onClick={() => void reconcile(true)}>
              结束核对并解锁
            </Button>
          </div>
        </div>
      )}
      {message && (
        <p className={failure ? 'form-error' : 'page-note'} role={failure ? 'alert' : 'status'}>
          {message}
        </p>
      )}
    </>
  )
  return (
    <>
      {!targets && status}
      {targets && (
        <Dialog
          title="删除生成记录"
          onClose={() => {
            if (!busy) {
              setStopActive(false)
              onClose()
            }
          }}
        >
          <div className="stacked-form">
            <p>将删除所选 {targets.length} 条生成记录。</p>
            <p className="page-note">文件库中的生成产物和已有文件引用保留，仍可下载使用。</p>
            {activeCount > 0 && (
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={pending?.stop_active ?? stopActive}
                  disabled={locked}
                  onChange={(event) => setStopActive(event.target.checked)}
                />
                <span>停止并删除其中仍在运行或等待资源回收的 {activeCount} 条记录</span>
              </label>
            )}
            {activeCount > 0 && (
              <p className="page-note">
                停止请求提交后，记录会继续显示；待任务真正结束且资源关闭后移除。
              </p>
            )}
            {status}
            <div className="button-row">
              <Button
                disabled={busy}
                onClick={() => {
                  setStopActive(false)
                  onClose()
                }}
              >
                {pending ? '收起核对' : '取消'}
              </Button>
              <Button
                variant="danger"
                disabled={locked || !targets.length || (activeCount > 0 && !stopActive)}
                onClick={() =>
                  void submit({
                    run_ids: targets.map((task) => task.run_id),
                    stop_active: stopActive,
                    request_id: crypto.randomUUID(),
                  })
                }
              >
                {busy ? '正在提交…' : activeCount > 0 ? '停止并删除记录' : '删除记录'}
              </Button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  )
}
