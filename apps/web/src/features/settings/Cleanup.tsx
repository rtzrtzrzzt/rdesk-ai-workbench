import { useSettingsAttention } from './SettingsGroup'
import { useId, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { maintenanceApi } from '../../api/maintenance'
import type { CleanupCategoryView, CleanupCreate, CleanupResult } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import { formatBytes } from '../files/FileUpload'
import './cleanup.css'

type Category = CleanupCreate['category']
type Review = {
  category: Category
  preview: CleanupCategoryView | null
  observed_at: string | null
}
const categoryLabels: Record<Category, string> = {
  temporary_uploads: '临时上传块',
  completed_file_staging: '已成功文件任务的临时目录',
}
const protectedLabels: Record<string, string> = {
  originals: '原件',
  artifacts: '产物',
  memory: 'Markdown 记忆与规则',
  checkpoints: '任务检查点',
  command_logs: '命令日志',
  database: '数据库',
  models: '模型',
  ple: 'PLE',
  active_browser_profiles: '浏览器资料',
  backups: '备份',
}
const storageKey = (userId: string) => `workbench.cleanup.${userId}`
function readPending(userId: string): CleanupCreate | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(storageKey(userId)) ?? 'null')
    if (
      !value ||
      typeof value !== 'object' ||
      !('request_id' in value) ||
      !('category' in value) ||
      !('confirm' in value)
    )
      return null
    if (
      typeof value.request_id !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.request_id) ||
      value.confirm !== true
    )
      return null
    if (value.category !== 'temporary_uploads' && value.category !== 'completed_file_staging')
      return null
    return { request_id: value.request_id, category: value.category, confirm: true }
  } catch {
    return null
  }
}
function code(error: unknown) {
  if (
    !(error instanceof ApiError) ||
    !error.body ||
    typeof error.body !== 'object' ||
    !('error' in error.body)
  )
    return ''
  const detail = error.body.error
  return detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string'
    ? detail.code
    : ''
}
function Receipt({ result }: { result: CleanupResult }) {
  return (
    <section className="cleanup-receipt" aria-label="本次清理结果">
      <h3>已确认的清理结果 · {categoryLabels[result.category]}</h3>
      <Status>服务端已完成本次清理</Status>
      <dl className="run-meta">
        <dt>请求标识</dt>
        <dd>{result.request_id}</dd>
        <dt>完成时间</dt>
        <dd>{new Date(result.completed_at).toLocaleString()}</dd>
        <dt>选中 / 检查 / 跳过记录</dt>
        <dd>
          {result.selected_records} / {result.examined_records} / {result.skipped_records}
        </dd>
        <dt>已确认删除文件数</dt>
        <dd>{result.confirmed_removed_files ?? '未知'}</dd>
        <dt>已确认删除的表观大小</dt>
        <dd>
          {result.confirmed_removed_bytes === null
            ? '未知'
            : formatBytes(result.confirmed_removed_bytes)}
        </dd>
        <dt>中断恢复</dt>
        <dd>{result.recovered_interrupted_operation ? '是；累计删除计量可能未知' : '否'}</dd>
      </dl>
      <p className="page-note">{result.note}</p>
      <p className="page-note">删除字节是观测到的文件表观大小，不代表文件系统实际释放的空间。</p>
    </section>
  )
}

export function Cleanup({ userId }: { userId: string }) {
  const client = useQueryClient()
  const confirmId = useId()
  const [open, setOpen] = useState(false)
  const [review, setReview] = useState<Review | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState<CleanupCreate | null>(() => readPending(userId))
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failureCode, setFailureCode] = useState('')
  const [result, setResult] = useState<CleanupResult | null>(null)
  const preview = useQuery({
    queryKey: ['maintenance-cleanup', userId],
    queryFn: maintenanceApi.cleanupPreview,
    enabled: open,
    retry: false,
  })
  useSettingsAttention('cleanup', !!pending, '临时文件清理请求待核对')
  useSettingsAttention('cleanup-error', preview.isError || !!message, '临时文件清理信息需要处理')
  function inspect(category: Category) {
    setReview({
      category,
      preview: preview.data?.categories.find((item) => item.category === category) ?? null,
      observed_at: preview.data?.observed_at ?? null,
    })
    setConfirmed(false)
    if (!pending) {
      setMessage('')
      setFailureCode('')
    }
  }
  async function execute() {
    if (!review || !confirmed || busy) return
    const input: CleanupCreate = pending ?? {
      request_id: crypto.randomUUID(),
      category: review.category,
      confirm: true,
    }
    if (input.category !== review.category) return
    try {
      // Persist only non-secret idempotency metadata before any destructive request.
      localStorage.setItem(storageKey(userId), JSON.stringify(input))
    } catch {
      setMessage('无法保留本次清理的请求标识，本次未提交；请先恢复浏览器本地存储后重试。')
      return
    }
    setPending(input)
    setBusy(true)
    setMessage('')
    setFailureCode('')
    try {
      const receipt = await maintenanceApi.cleanup(input)
      if (
        receipt.request_id !== input.request_id ||
        receipt.category !== input.category ||
        receipt.status !== 'complete'
      )
        throw new Error('清理回执与本次请求不符，请保留原标识继续核对。')
      setResult(receipt)
      setPending(null)
      setReview(null)
      setConfirmed(false)
      try {
        localStorage.removeItem(storageKey(userId))
      } catch {
        setMessage('服务端已确认完成，但浏览器未能清除旧请求标识；刷新后再次核对仍复用原请求。')
      }
      await client.invalidateQueries({ queryKey: ['maintenance-cleanup', userId] })
      await client.invalidateQueries({ queryKey: ['maintenance-diagnostics'] })
    } catch (error) {
      const failure = code(error)
      setFailureCode(failure)
      setConfirmed(false)
      setMessage(
        `${error instanceof Error ? error.message : '清理结果尚未确认。'}${failure === 'maintenance_busy' ? ' 未取得维护窗口；请等待安全停点，重新确认后仍用原请求核对。不会停止正在运行的任务。' : ' 原请求标识与类别已保留；请明确核对本次清理，不会自动重发或创建另一轮清理。'}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <details
      className="settings-cleanup"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>清理临时文件{pending ? ' · 有待核对请求' : ''}</summary>
      <div className="cleanup-content">
        <p className="page-note">
          仅清理登记的临时上传块或已成功文件任务的私有暂存；原件与产物保留。只在取得独占维护窗口后执行，不会为了清理而停止用户任务。
        </p>
        <Button disabled={preview.isFetching || busy} onClick={() => void preview.refetch()}>
          刷新清理预览（只读）
        </Button>
        {preview.error && (
          <p className="form-error" role="alert">
            {preview.error.message}
            {preview.data ? ' 下方保留上次成功预览；重新读取成功后才能发起新的清理。' : ''}
          </p>
        )}
        {preview.data && (
          <>
            <p className="page-note">
              预览时间：{new Date(preview.data.observed_at).toLocaleString()}
              。预览不执行删除，实际处理时会再次检查条件。
            </p>
            <div className="cleanup-categories">
              {preview.data.categories.map((item) => (
                <article key={item.category}>
                  <h3>{categoryLabels[item.category]}</h3>
                  <p>{item.explanation}</p>
                  <dl className="run-meta">
                    <dt>预览可选记录</dt>
                    <dd>
                      {item.eligible_records}
                      {item.partial ? ' · 部分结果' : ''}
                    </dd>
                    <dt>单次选择上限</dt>
                    <dd>{item.selection_limit}</dd>
                  </dl>
                  {item.partial && (
                    <p className="page-note">
                      预览达到数量或扫描范围限制，当前记录数不代表全部候选。
                    </p>
                  )}
                  <Button
                    disabled={busy || !!pending || preview.isError || item.eligible_records === 0}
                    onClick={() => inspect(item.category)}
                  >
                    选择此类临时文件
                  </Button>
                  {item.eligible_records === 0 && (
                    <p className="page-note">本次预览没有可选记录。</p>
                  )}
                </article>
              ))}
            </div>
            {preview.data.categories.length === 0 && (
              <p className="page-note">服务端未返回可清理类别。</p>
            )}
            <p className="page-note">
              不作为清理目标：
              {preview.data.protected_categories
                .map((item) => protectedLabels[item] ?? item)
                .join('、') || '未返回保护分类，请重新读取。'}
            </p>
          </>
        )}
        {!preview.data && !preview.error && (
          <p className="page-note">{open ? '正在读取真实清理预览…' : '展开后可读取清理预览。'}</p>
        )}
        {pending && (
          <div className="memory-conflict">
            <strong>清理请求待核对 · {categoryLabels[pending.category]}</strong>
            <p className="cleanup-request-id">请求标识：{pending.request_id}</p>
            <p>
              核对复用相同标识、类别和确认值。若服务端已冻结候选，只会继续处理该批记录；不会自动再选一批。
            </p>
            <Button disabled={busy} onClick={() => inspect(pending.category)}>
              核对本次清理
            </Button>
          </div>
        )}
        {message && (
          <p role="status" className="maintenance-message">
            {message}
          </p>
        )}
        {result && <Receipt result={result} />}
      </div>
      {review && (
        <Dialog
          title={pending ? '核对本次清理' : '确认清理临时文件'}
          onClose={() => {
            if (!busy) setReview(null)
          }}
        >
          <form
            className="cleanup-review"
            onSubmit={(event) => {
              event.preventDefault()
              void execute()
            }}
          >
            <h3>{categoryLabels[review.category]}</h3>
            {review.preview ? (
              <>
                <p className="page-note">{review.preview.explanation}</p>
                <dl className="run-meta">
                  <dt>{pending ? '只读预览时间' : '确认所据预览时间'}</dt>
                  <dd>
                    {review.observed_at ? new Date(review.observed_at).toLocaleString() : '未取得'}
                  </dd>
                  <dt>预览可选 / 单次上限</dt>
                  <dd>
                    {review.preview.eligible_records} / {review.preview.selection_limit}
                  </dd>
                  <dt>预览完整性</dt>
                  <dd>
                    {review.preview.partial
                      ? '部分结果；可能还有未列出的候选'
                      : '本次预览未报告截断'}
                  </dd>
                </dl>
              </>
            ) : (
              <p className="page-note">
                当前未取得该类别预览；原请求标识与类别仍保留，可继续核对。服务端只处理本请求实际冻结的候选。
              </p>
            )}
            {pending && (
              <p className="page-note cleanup-request-id">
                请求标识：{pending.request_id}
                。当前预览不代表原请求冻结的候选名单；核对可能继续本次尚未完成的清理。
              </p>
            )}
            <p className="page-note">
              仅处理此类别的临时数据，原件、产物、Markdown、检查点、模型 /
              PLE、数据库、命令日志、浏览器资料与备份不作为清理目标。繁忙时不会停任务或自动改用新请求。
            </p>
            {failureCode === 'maintenance_busy' && (
              <p className="memory-conflict">
                维护窗口繁忙，未取得执行许可。等待安全停点后重新确认，仍使用本次标识。
              </p>
            )}
            {message && (
              <p role="alert" className="form-error">
                {message}
              </p>
            )}
            <label className="cleanup-confirm" htmlFor={confirmId}>
              <input
                id={confirmId}
                type="checkbox"
                checked={confirmed}
                disabled={busy}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              我确认{pending ? '核对并继续本次' : '按上述范围'}清理临时文件；原件与产物保留。
            </label>
            <div className="button-row">
              <Button disabled={busy} onClick={() => setReview(null)}>
                返回
              </Button>
              <Button variant="danger" type="submit" disabled={busy || !confirmed}>
                {busy ? '正在等待清理回执…' : pending ? '核对本次清理' : '确认清理此类临时文件'}
              </Button>
            </div>
            {busy && (
              <p className="page-note">
                正在等待服务端回执；不会自动重发。中断后请使用原标识核对。
              </p>
            )}
          </form>
        </Dialog>
      )}
    </details>
  )
}
