import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, FileDiff } from 'lucide-react'
import { changesApi, type RunChanges as RunChangesData } from '../../api/changes'
import type { RunPresentation } from '../../api/presentation'
import { Button } from '../../components/ui'

const coverageLabels: Record<string, string> = {
  pending: '变更仍在记录',
  partial: '仅覆盖部分变更',
  unknown: '变更覆盖范围未知',
}
const changeKinds: Record<string, string> = {
  added: '新增',
  created: '新增',
  modified: '修改',
  deleted: '删除',
  removed: '删除',
  renamed: '重命名',
}

function ChangeFile({
  threadId,
  runId,
  file,
  revision,
}: {
  threadId: string
  runId: string
  file: RunChangesData['files'][number]
  revision: string
}) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['change-detail', threadId, runId, file.id, revision],
    queryFn: ({ signal }) => changesApi.detail(threadId, file.id, runId, signal),
    enabled: open,
  })
  return (
    <section className="run-change-file">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={13} className={open ? 'expanded' : ''} />
        <span className="run-change-path">{file.path}</span>
        <span>{changeKinds[file.kind] ?? '变更'}</span>
        <LineCounts added={file.added_lines} removed={file.removed_lines} />
      </button>
      {open && (
        <div className="run-change-content">
          {detail.isError ? (
            <div role="alert">
              <p className="form-error">无法读取此文件的差异。</p>
              <Button onClick={() => void detail.refetch()}>重新读取差异</Button>
            </div>
          ) : detail.data ? (
            <>
              {detail.data.coverage !== 'complete' && (
                <p className="page-note">
                  {coverageLabels[detail.data.coverage] ?? '此差异的覆盖范围有限'}
                </p>
              )}
              {detail.data.diff ? (
                <pre className="run-change-diff">
                  {detail.data.diff.split('\n').map((line, index) => (
                    <span
                      key={index}
                      className={
                        line.startsWith('+') && !line.startsWith('+++')
                          ? 'diff-added'
                          : line.startsWith('-') && !line.startsWith('---')
                            ? 'diff-removed'
                            : line.startsWith('@@')
                              ? 'diff-hunk'
                              : undefined
                      }
                    >
                      {line}
                      {'\n'}
                    </span>
                  ))}
                </pre>
              ) : (
                <p className="page-note">
                  {detail.data.diff === null
                    ? '此文件的版本无法比较，或差异超出展示范围；当前没有可用的文本差异。'
                    : '此文件没有文本差异。'}
                </p>
              )}
              {detail.data.truncated && <p className="page-note">差异较长，此处展示部分内容。</p>}
            </>
          ) : (
            <p className="page-note">正在读取差异…</p>
          )}
        </div>
      )}
    </section>
  )
}

function LineCounts({ added, removed }: { added: number | null; removed: number | null }) {
  return (
    <span className="run-change-counts">
      {added !== null && <span className="diff-added">+{added}</span>}
      {removed !== null && <span className="diff-removed">−{removed}</span>}
    </span>
  )
}

export function RunChanges({
  threadId,
  group,
  live,
}: {
  threadId: string
  group: RunPresentation
  live: boolean
}) {
  const [open, setOpen] = useState(false)
  const changes = useQuery({
    queryKey: ['run-changes', threadId, group.run_id, group.changes_revision],
    queryFn: ({ signal }) => changesApi.run(threadId, group.run_id, signal),
    enabled: open,
    refetchInterval: open && live ? 2500 : false,
  })
  // An incomplete ledger cannot establish that a run made no file changes.
  if (group.changed_files === 0 && group.changes_coverage === 'complete') return null
  const coverage = changes.data?.coverage ?? group.changes_coverage
  const totals = changes.data?.totals
  return (
    <section className="run-changes" data-run-changes={group.run_id}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
        <FileDiff size={14} />
        <span>
          {(totals?.files ?? group.changed_files) === null
            ? '文件变更'
            : `${totals?.files ?? group.changed_files} 个文件变更`}
        </span>
        <LineCounts
          added={totals ? totals.added_lines : group.added_lines}
          removed={totals ? totals.removed_lines : group.removed_lines}
        />
        {coverage !== 'complete' && <small>{coverageLabels[coverage] ?? '变更覆盖范围未知'}</small>}
        <ChevronRight size={13} className={open ? 'expanded' : ''} />
      </button>
      {open && (
        <div className="run-changes-content">
          {changes.isError ? (
            <div role="alert">
              <p className="form-error">文件变更暂时无法读取。</p>
              <Button onClick={() => void changes.refetch()}>重新读取变更</Button>
            </div>
          ) : changes.data ? (
            <>
              {changes.data.gaps.length > 0 && (
                <p className="page-note">{changes.data.gaps.join('；')}</p>
              )}
              {changes.data.files.map((file) => (
                <ChangeFile
                  key={file.id}
                  threadId={threadId}
                  runId={group.run_id}
                  file={file}
                  revision={changes.data.revision}
                />
              ))}
              {!changes.data.files.length && (
                <p className="page-note">
                  {coverage === 'complete' ? '没有记录到文件变更。' : '当前没有可展示的变更记录。'}
                </p>
              )}
            </>
          ) : (
            <p className="page-note">正在读取文件变更…</p>
          )}
        </div>
      )}
    </section>
  )
}
