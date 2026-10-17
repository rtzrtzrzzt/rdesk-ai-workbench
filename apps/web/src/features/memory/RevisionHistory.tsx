import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../../api/client'
import { memoryApi } from '../../api/memory'
import type { MemoryDocument, MemoryRestore } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, Status } from '../../components/ui'
import './history.css'

const reasonLabels: Record<string, string> = {
  scope_generation_changed: '记忆范围代次已变化，此旧版本不能恢复。',
  current_document_not_active: '当前文档不再处于 active 状态，不能恢复。',
  snapshot_unavailable: '历史快照不可用，无法恢复。',
}
const message = (error: unknown) => (error instanceof Error ? error.message : '请求未完成。')
export function RevisionHistory({
  workspaceId,
  document,
  hasDraft,
  onRestored,
  onReadCurrent,
  onClose,
}: {
  workspaceId: string
  document: MemoryDocument
  hasDraft: boolean
  onRestored: (document: MemoryDocument) => Promise<void>
  onReadCurrent: () => Promise<void>
  onClose: () => void
}) {
  const [version, setVersion] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [notice, setNotice] = useState('')
  const pending = useRef<MemoryRestore | null>(null)
  const list = useQuery({
    queryKey: ['memory-revisions', workspaceId, document.id],
    queryFn: () => memoryApi.revisions(workspaceId, document.id),
  })
  const selected = list.data?.revisions.find((item) => item.version === version)
  const revision = useQuery({
    queryKey: ['memory-revision', workspaceId, document.id, version],
    queryFn: () => memoryApi.revision(workspaceId, document.id, version),
    enabled: !!selected && selected.status === 'available',
  })
  const comparison = useQuery({
    queryKey: ['memory-revision-comparison', workspaceId, document.id, version],
    queryFn: () => memoryApi.compareRevision(workspaceId, document.id, version),
    enabled: !!selected && selected.status === 'available',
  })
  const consistent =
    list.data &&
    comparison.data &&
    comparison.data.current_version === list.data.current_version &&
    comparison.data.scope_generation === list.data.scope_generation
  const canRestore =
    !list.isError &&
    !revision.isError &&
    !comparison.isError &&
    !list.isFetching &&
    !revision.isFetching &&
    !comparison.isFetching &&
    document.status === 'active' &&
    list.data?.current_status === 'active' &&
    selected?.restorable === true &&
    revision.data?.restorable === true &&
    consistent &&
    !conflict
  async function refresh() {
    try {
      const updated = await list.refetch()
      if (updated.error) throw updated.error
      await onReadCurrent()
      if (version && selected?.status === 'available') {
        const result = await comparison.refetch()
        if (result.error) throw result.error
        const view = await revision.refetch()
        if (view.error) throw view.error
      }
      setConflict(false)
      setConfirm(false)
      setNotice('已重新读取服务器版本与比较；编辑器中的本地草稿保留。')
    } catch (error) {
      setNotice(message(error))
    }
  }
  async function restore() {
    if (busy || (!pending.current && (!canRestore || !confirm || !list.data))) return
    let input = pending.current
    if (!input) {
      if (!list.data) return
      input = {
        target_version: version,
        expected_current_version: list.data.current_version,
        expected_scope_generation: list.data.scope_generation,
        request_id: crypto.randomUUID(),
      }
      pending.current = input
    }
    setBusy(true)
    setNotice('')
    try {
      const restored = await memoryApi.restoreRevision(workspaceId, document.id, input)
      await onRestored(restored)
      pending.current = null
      setUnknown(false)
      setConfirm(false)
      setNotice(
        '历史内容已恢复为当前版本；现有历史不会删除。本地编辑草稿仍保留，保存前需重新核对基准。',
      )
      await list.refetch()
      await comparison.refetch()
    } catch (error) {
      const uncertain = !(error instanceof ApiError) || error.status >= 500 || error.status === 408
      setUnknown(uncertain)
      if (!uncertain) pending.current = null
      setConflict(error instanceof ApiError && error.status === 409)
      setNotice(
        `${message(error)}${uncertain ? ' 原目标版本、预期版本、代次与请求标识均保留；核对使用原提交。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      title={`「${document.title}」版本历史`}
      onClose={() => {
        if (!busy && !unknown) onClose()
      }}
    >
      <div className="memory-history">
        <p className="page-note">
          这里只展示服务端实际留存的历史快照。恢复会发布新的当前版本，保留历史；不会静默覆盖未保存草稿。
        </p>
        {hasDraft && (
          <p className="memory-conflict">
            编辑器有未保存草稿。历史查看、比较与恢复不会删除它；恢复后草稿仍基于旧版本。
          </p>
        )}
        <Button disabled={busy} onClick={() => void refresh()}>
          重新读取历史与当前版本
        </Button>
        {list.error && (
          <p role="alert" className="form-error">
            {list.error.message}
          </p>
        )}
        {list.data ? (
          <>
            <p className="page-note">
              当前版本：{list.data.current_version} · 范围代次 {list.data.scope_generation}
            </p>
            <p className="page-note">历史覆盖不完整：{list.data.history_gap_reason}</p>
            {list.data.history_started_at && (
              <p className="page-note">
                留存起点：{new Date(list.data.history_started_at).toLocaleString()}
              </p>
            )}
            {list.data.truncated && (
              <p className="file-partial-warning">版本列表已截断，当前显示不代表全部历史。</p>
            )}
            {list.data.warnings.map((warning, index) => (
              <p key={index} className="file-partial-warning">
                {warning}
              </p>
            ))}
            <label>
              选择实际留存版本
              <select
                value={version}
                disabled={busy || unknown}
                onChange={(event) => {
                  setVersion(event.target.value)
                  setConfirm(false)
                  setConflict(false)
                  setNotice('')
                }}
              >
                <option value="">请选择历史版本</option>
                {list.data.revisions.map((item) => (
                  <option key={item.version} value={item.version}>
                    {new Date(item.captured_at).toLocaleString()} · {item.version.slice(0, 12)} ·{' '}
                    {item.status === 'available' ? '快照可读' : '快照不可用'}
                  </option>
                ))}
              </select>
            </label>
            {!list.data.revisions.length && <p className="page-note">当前没有真实历史快照。</p>}
          </>
        ) : (
          !list.error && <p className="page-note">正在读取历史快照…</p>
        )}
        {selected && (
          <>
            <div className="run-line">
              <h3>{selected.title}</h3>
              <Status>{selected.restorable ? '服务器允许恢复' : '不可恢复'}</Status>
            </div>
            <dl className="run-meta">
              <dt>历史版本</dt>
              <dd>{selected.version}</dd>
              <dt>来源</dt>
              <dd>
                {selected.origin === 'observed_current' ? '观察到的当前文件' : '受管发布快照'}
              </dd>
              <dt>发布日志</dt>
              <dd>
                {selected.journal_id} · {selected.journal_status}
              </dd>
            </dl>
            {!selected.restorable && (
              <p className="memory-conflict">
                {selected.unavailable_reason
                  ? (reasonLabels[selected.unavailable_reason] ?? selected.unavailable_reason)
                  : '服务器未允许此版本恢复。'}
              </p>
            )}
            {revision.error && (
              <p role="alert" className="form-error">
                {revision.error.message}
              </p>
            )}
            {revision.data && (
              <details open>
                <summary>历史 Markdown 正文（纯文本）</summary>
                <pre>{revision.data.body}</pre>
                <p className="page-note">
                  来源：
                  {revision.data.source_refs.length
                    ? revision.data.source_refs.join('、')
                    : '此快照未记录来源引用'}
                </p>
              </details>
            )}
            {comparison.error && (
              <p role="alert" className="form-error">
                {comparison.error.message}
              </p>
            )}
            {comparison.data && (
              <details open>
                <summary>与当前版本比较（纯文本差异）</summary>
                <p className="page-note">
                  历史版本 {comparison.data.target_version} · 当前版本{' '}
                  {comparison.data.current_version}
                </p>
                {comparison.data.title_changed && (
                  <p className="page-note">
                    标题变化：{comparison.data.current_title} → {comparison.data.target_title}
                  </p>
                )}
                <pre>{comparison.data.diff || '服务器比较没有正文差异。'}</pre>
                {comparison.data.truncated && (
                  <p className="file-partial-warning">差异已截断，不能视为完整比较。</p>
                )}
              </details>
            )}
            {comparison.data && !consistent && (
              <p className="memory-conflict">
                比较与版本列表不一致，请先重新读取；不会基于过时信息恢复。
              </p>
            )}
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={confirm}
                disabled={!canRestore || busy || unknown}
                onChange={(event) => setConfirm(event.target.checked)}
              />
              我确认以所选历史内容发布新的当前版本，保留历史与本地未保存草稿
            </label>
            <Button
              variant="primary"
              disabled={busy || unknown || !confirm || !canRestore}
              onClick={() => void restore()}
            >
              确认恢复此历史版本
            </Button>
          </>
        )}
        {unknown && (
          <div className="memory-conflict">
            <p>待核对恢复请求：{pending.current?.request_id}。保留原提交，不采用新的选择。</p>
            <Button disabled={busy} onClick={() => void restore()}>
              核对本次恢复请求
            </Button>
          </div>
        )}
        {conflict && (
          <p className="memory-conflict">
            CAS 检查未通过。当前历史查看和本地编辑草稿均保留，请重新读取后再次明确选择恢复。
          </p>
        )}
        {notice && (
          <p role="status" className="memory-history-message">
            {notice}
          </p>
        )}
        <div className="button-row">
          <Button disabled={busy || unknown} onClick={onClose}>
            关闭历史
          </Button>
        </div>
      </div>
    </Dialog>
  )
}
