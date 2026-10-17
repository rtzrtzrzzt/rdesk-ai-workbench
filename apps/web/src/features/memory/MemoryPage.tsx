import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Check, FileText, Plus, RefreshCw, Shield, X } from 'lucide-react'
import { ApiError } from '../../api/client'
import { memoryApi } from '../../api/memory'
import type { MemoryDocument, MemoryProposal, Workspace } from '../../api/types'
import { Dialog } from '../../components/Dialog'
import { Button, EmptyState, Status } from '../../components/ui'
import { MarkdownEditor, MemoryPageFrame, MemoryScopePicker, MemorySearch } from './ui'
import type { MemoryScope, MemoryTab } from './ui'
import { RevisionHistory } from './RevisionHistory'

type Draft = {
  body: string
  original: string
  version: string
  conflict?: string
  error?: string
  message?: string
}
type Confirmation = {
  title: string
  description: string
  action: string
  danger?: boolean
  run: () => Promise<void>
}
const describeError = (error: unknown) =>
  error instanceof Error ? error.message : '请求未完成，请重试。'
const versionLabel = (version: string) =>
  version === 'missing' ? '尚未创建' : version.slice(0, 12)
const dateLabel = (date: string | null) => (date ? new Date(date).toLocaleString() : '未知')
const statusLabels: Record<string, string> = {
  active: '有效',
  forgotten: '已忘记',
  missing: '文件缺失',
  conflict: '存在冲突',
  outcome_unknown: '写入结果待核对',
  pending: '待审阅',
  approved: '已批准',
  rejected: '已拒绝',
  invalidated: '已失效',
}
function useUnsavedWarning(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
}
function ReadError({
  error,
  retry,
  service = false,
}: {
  error: unknown
  retry: () => void
  service?: boolean
}) {
  const unavailable =
    service && error instanceof ApiError && (error.status === 404 || error.status === 503)
  return (
    <div className="memory-read-error" role="alert">
      <p>{unavailable ? '记忆服务尚未就绪，未读取或更改任何文档。' : describeError(error)}</p>
      <Button onClick={retry}>
        <RefreshCw size={14} />
        重新读取
      </Button>
    </div>
  )
}
function Sources({ refs }: { refs: string[] }) {
  return refs.length ? (
    <ul className="memory-source-list">
      {refs.map((source, index) => (
        <li key={`${source}:${index}`}>{source}</li>
      ))}
    </ul>
  ) : (
    <span>未提供来源引用</span>
  )
}
function ConfirmDialog({
  confirmation,
  onClose,
}: {
  confirmation: Confirmation
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <Dialog
      title={confirmation.title}
      onClose={() => {
        if (!busy) onClose()
      }}
    >
      <p className="memory-confirm-description">{confirmation.description}</p>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="button-row">
        <Button disabled={busy} onClick={onClose}>
          取消
        </Button>
        <Button
          variant={confirmation.danger ? 'danger' : 'primary'}
          disabled={busy}
          onClick={() => {
            setBusy(true)
            setError('')
            void confirmation
              .run()
              .then(onClose)
              .catch((err) => setError(describeError(err)))
              .finally(() => setBusy(false))
          }}
        >
          {busy ? '正在处理…' : confirmation.action}
        </Button>
      </div>
    </Dialog>
  )
}
export function MemoryPage({
  workspace,
  authenticated,
  active,
}: {
  workspace?: Workspace
  authenticated: boolean
  active: boolean
}) {
  const [tab, setTab] = useState<MemoryTab>('documents')
  const [scope, setScope] = useState<MemoryScope>('workspace')
  return (
    <MemoryPageFrame tab={tab} onTabChange={setTab}>
      {!authenticated || !workspace ? (
        <EmptyState icon={BookOpen} title={authenticated ? '先选择一个工作区' : '记忆服务尚未连接'}>
          {authenticated
            ? '用户级记忆也通过当前工作区的授权入口访问。请先选择或创建工作区。'
            : '连接工作台后，可读取真实记忆与规则。'}
        </EmptyState>
      ) : (
        <>
          <div hidden={tab !== 'documents'}>
            <DocumentsPanel
              workspace={workspace}
              scope={scope}
              onScopeChange={setScope}
              active={active && tab === 'documents'}
            />
          </div>
          <div hidden={tab !== 'rules'}>
            <RulesPanel workspace={workspace} active={active && tab === 'rules'} />
          </div>
          <div hidden={tab !== 'proposals'}>
            <ProposalsPanel
              workspace={workspace}
              scope={scope}
              onScopeChange={setScope}
              active={active && tab === 'proposals'}
            />
          </div>
        </>
      )}
    </MemoryPageFrame>
  )
}
function DocumentsPanel({
  workspace,
  scope,
  onScopeChange,
  active,
}: {
  workspace: Workspace
  scope: MemoryScope
  onScopeChange: (scope: MemoryScope) => void
  active: boolean
}) {
  const client = useQueryClient()
  const context = `${workspace.id}:${scope}`
  const [selections, setSelections] = useState<Record<string, string>>({})
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState<{ context: string; q: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const [historyDocument, setHistoryDocument] = useState<MemoryDocument | null>(null)
  const list = useQuery({
    queryKey: ['memory-documents', workspace.id, scope],
    queryFn: () => memoryApi.list(workspace.id, scope),
    enabled: active,
  })
  const selectedId = selections[context] ?? ''
  const document = useQuery({
    queryKey: ['memory-document', workspace.id, selectedId],
    queryFn: () => memoryApi.document(workspace.id, selectedId),
    enabled: active && !!selectedId,
  })
  const results = useQuery({
    queryKey: ['memory-search', workspace.id, scope, search?.q],
    queryFn: () => memoryApi.search(workspace.id, scope, search!.q),
    enabled: active && search?.context === context && !!search.q,
  })
  const searching = search?.context === context && !!search.q
  const current = document.data
  const draft = current ? drafts[current.id] : undefined
  const dirty = !!draft && draft.body !== draft.original
  useUnsavedWarning(Object.values(drafts).some((item) => item.body !== item.original))
  useEffect(() => {
    if (!selectedId && list.data?.documents.length)
      setSelections((old) => ({ ...old, [context]: list.data.documents[0].id }))
  }, [selectedId, list.data, context])
  async function refresh() {
    const refreshed = await list.refetch()
    if (searching) await results.refetch()
    return refreshed.data
  }
  async function reloadDocument(id: string) {
    const latest = await memoryApi.document(workspace.id, id)
    client.setQueryData(['memory-document', workspace.id, id], latest)
    setDrafts((old) => {
      const next = { ...old }
      delete next[id]
      return next
    })
    await refresh()
  }
  function requestReload() {
    if (!current) return
    const id = current.id
    if (dirty)
      setConfirmation({
        title: '重新读取并放弃此草稿？',
        description: '将用服务端当前正文替换本地未保存的修改。若需保留草稿，请先取消并复制内容。',
        action: '放弃草稿并读取',
        run: () => reloadDocument(id),
      })
    else
      void reloadDocument(id).catch((error) =>
        setDrafts((old) => ({
          ...old,
          [id]: {
            body: current.body ?? '',
            original: current.body ?? '',
            version: current.version,
            error: describeError(error),
          },
        })),
      )
  }
  async function saveDocument() {
    if (!current || !draft || !dirty || saving || draft.conflict || current.body === null) return
    const id = current.id
    setSaving(true)
    try {
      const updated = await memoryApi.update(workspace.id, id, {
        body: draft.body,
        expected_version: draft.version,
        request_id: crypto.randomUUID(),
      })
      client.setQueryData(['memory-document', workspace.id, id], updated)
      if (updated.body === null || updated.status !== 'active') {
        setDrafts((old) => ({
          ...old,
          [id]: {
            ...draft,
            conflict: '服务端没有返回可确认的有效正文，请重新读取并核对写入结果。',
          },
        }))
        return
      }
      const savedBody = updated.body
      setDrafts((old) => ({
        ...old,
        [id]: {
          body: savedBody,
          original: savedBody,
          version: updated.version,
          message: '当前版本已保存。',
        },
      }))
      await refresh()
    } catch (error) {
      const conflict =
        (error instanceof ApiError && error.status === 409) ||
        error instanceof TypeError ||
        (error instanceof ApiError && error.status >= 500)
      setDrafts((old) => ({
        ...old,
        [id]: {
          ...draft,
          ...(conflict ? { conflict: describeError(error) } : { error: describeError(error) }),
          message: undefined,
        },
      }))
    } finally {
      setSaving(false)
    }
  }
  function forgetDocument() {
    if (!current) return
    const target = current
    const requestId = crypto.randomUUID()
    setConfirmation({
      title: `忘记「${target.title}」？`,
      description: `此文档将不再作为长期记忆使用，并阻止旧候选将其写回。${dirty ? '本地未保存草稿也将移除。' : ''}这不会清除原始对话、原始文件、备份，或撤回此前已发送给外部服务的内容。`,
      action: '确认忘记',
      danger: true,
      run: async () => {
        await memoryApi.forget(workspace.id, target.id, {
          expected_version: target.version,
          request_id: requestId,
        })
        setDrafts((old) => {
          const next = { ...old }
          delete next[target.id]
          return next
        })
        const refreshed = await refresh()
        setSelections((old) => ({
          ...old,
          [context]: refreshed?.documents.find((item) => item.id !== target.id)?.id ?? '',
        }))
        client.removeQueries({ queryKey: ['memory-document', workspace.id, target.id] })
        await client.invalidateQueries({ queryKey: ['memory-proposals'] })
      },
    })
  }
  return (
    <>
      <div className="memory-toolbar">
        <MemoryScopePicker value={scope} onChange={onScopeChange} workspaceName={workspace.name} />
        <MemorySearch
          value={searchText}
          onChange={setSearchText}
          onSearch={() => setSearch(searchText.trim() ? { context, q: searchText.trim() } : null)}
          busy={results.isFetching}
        />
        <Button
          variant="primary"
          disabled={!list.data || !!list.data.pending_writes.length}
          onClick={() => setCreating(true)}
        >
          <Plus size={14} />
          新建记忆
        </Button>
      </div>
      {list.isError ? (
        <ReadError service error={list.error} retry={() => void list.refetch()} />
      ) : list.isPending ? (
        <p className="page-note">正在读取记忆…</p>
      ) : (
        <>
          {list.data.pending_writes.map((write) => (
            <div className="memory-conflict" key={write.id} role="status">
              <strong>有写入结果需要核对</strong>
              <p>{write.message}</p>
              <p>
                操作：{write.operation} · {dateLabel(write.created_at)}
              </p>
            </div>
          ))}
          {searching && (
            <div className="memory-search-summary">
              <span>{results.data ? `「${results.data.query}」的真实匹配` : '正在搜索…'}</span>
              <Button
                variant="ghost"
                onClick={() => {
                  setSearch(null)
                  setSearchText('')
                }}
              >
                <X size={13} />
                清除搜索
              </Button>
            </div>
          )}
          {results.isError && searching && (
            <ReadError error={results.error} retry={() => void results.refetch()} />
          )}
          {results.data?.truncated && searching && (
            <p className="page-note">结果已截断，请缩小关键词范围；当前列表不是全部匹配。</p>
          )}
          {list.data.documents.length || searching ? (
            <div className="memory-workspace">
              <div
                className="memory-document-list"
                aria-label={searching ? '搜索命中' : '记忆文档'}
              >
                {searching
                  ? results.data?.hits.map((hit, index) => (
                      <button
                        type="button"
                        key={`${hit.document_id}:${hit.start_line}:${index}`}
                        aria-pressed={selectedId === hit.document_id}
                        onClick={() =>
                          setSelections((old) => ({ ...old, [context]: hit.document_id }))
                        }
                      >
                        <strong>{hit.title}</strong>
                        <small>
                          {hit.path} · 第 {hit.start_line}–{hit.end_line} 行
                        </small>
                        <span className="memory-hit-text">{hit.text}</span>
                      </button>
                    ))
                  : list.data.documents.map((item) => (
                      <button
                        type="button"
                        key={item.id}
                        aria-pressed={selectedId === item.id}
                        onClick={() => setSelections((old) => ({ ...old, [context]: item.id }))}
                      >
                        <strong>{item.title}</strong>
                        <small>{item.path}</small>
                        <small>
                          {statusLabels[item.status]} · {versionLabel(item.version)}
                        </small>
                      </button>
                    ))}
                {searching && results.data && !results.data.hits.length && (
                  <p className="memory-list-note">没有找到匹配内容。可更换中文关键词或路径。</p>
                )}
              </div>
              <div className="memory-document-detail">
                {document.isError ? (
                  <ReadError error={document.error} retry={() => void document.refetch()} />
                ) : current ? (
                  current.body === null ? (
                    <EmptyState icon={FileText} title={statusLabels[current.status]}>
                      正文当前不可读取，请核对真实文件或待处理写入后重新读取。
                    </EmptyState>
                  ) : (
                    <MarkdownEditor
                      title={current.title}
                      content={draft?.body ?? current.body}
                      readOnly={current.status !== 'active'}
                      dirty={dirty}
                      busy={saving}
                      conflict={draft?.conflict}
                      error={draft?.error}
                      message={draft?.message}
                      onChange={(body) =>
                        setDrafts((old) => ({
                          ...old,
                          [current.id]: {
                            ...(old[current.id] ?? {
                              original: current.body!,
                              version: current.version,
                            }),
                            body,
                            error: undefined,
                            message: undefined,
                          },
                        }))
                      }
                      onSave={() => void saveDocument()}
                      onReload={requestReload}
                      onForget={current.status === 'active' ? forgetDocument : undefined}
                      metadata={
                        <>
                          <DocumentMetadata document={current} editingVersion={draft?.version} />
                          {current.status === 'active' && (
                            <Button disabled={saving} onClick={() => setHistoryDocument(current)}>
                              版本历史与比较
                            </Button>
                          )}
                        </>
                      }
                    />
                  )
                ) : (
                  <EmptyState
                    icon={FileText}
                    title={document.isFetching ? '正在读取正文…' : '选择一份记忆'}
                  >
                    正文、来源与实际版本会显示在这里。
                  </EmptyState>
                )}
              </div>
            </div>
          ) : (
            <EmptyState
              icon={BookOpen}
              title="还没有长期记忆"
              action={
                <Button
                  onClick={() => setCreating(true)}
                  disabled={!!list.data.pending_writes.length}
                >
                  <Plus size={14} />
                  新建记忆
                </Button>
              }
            >
              只有明确保存的内容才会出现在这里。对话与工作区资料不会自动变成长久记忆。
            </EmptyState>
          )}
        </>
      )}
      <p className="page-note">
        用户级记忆随当前账号生效；工作区级记忆仅用于这个工作区。未保存的正文只保留在当前页面内存中。
      </p>
      {creating && list.data && (
        <CreateMemoryDialog
          scope={scope}
          workspace={workspace}
          generation={list.data.scope_generation}
          onClose={() => setCreating(false)}
          onCreated={async (created) => {
            client.setQueryData(['memory-document', workspace.id, created.id], created)
            setSelections((old) => ({ ...old, [context]: created.id }))
            setSearch(null)
            await refresh()
            setCreating(false)
          }}
        />
      )}
      {confirmation && (
        <ConfirmDialog confirmation={confirmation} onClose={() => setConfirmation(null)} />
      )}
      {historyDocument && (
        <RevisionHistory
          key={historyDocument.id}
          workspaceId={workspace.id}
          document={historyDocument}
          hasDraft={
            !!drafts[historyDocument.id] &&
            drafts[historyDocument.id].body !== drafts[historyDocument.id].original
          }
          onClose={() => setHistoryDocument(null)}
          onReadCurrent={async () => {
            const latest = await memoryApi.document(workspace.id, historyDocument.id)
            client.setQueryData(['memory-document', workspace.id, latest.id], latest)
            setHistoryDocument(latest)
            setDrafts((old) => {
              const existing = old[latest.id]
              return existing &&
                existing.body !== existing.original &&
                existing.version !== latest.version
                ? {
                    ...old,
                    [latest.id]: {
                      ...existing,
                      conflict: '服务器当前版本已变化；本地草稿保留，请核对后再保存。',
                    },
                  }
                : old
            })
          }}
          onRestored={async (restored) => {
            client.setQueryData(['memory-document', workspace.id, restored.id], restored)
            setHistoryDocument(restored)
            setDrafts((old) => {
              const existing = old[restored.id]
              if (!existing) return old
              if (existing.body === existing.original) {
                const next = { ...old }
                delete next[restored.id]
                return next
              }
              return {
                ...old,
                [restored.id]: {
                  ...existing,
                  conflict:
                    '服务器已恢复历史内容；你的草稿仍基于旧版本，未被覆盖。请复制保留或明确重新读取后再编辑。',
                  message: undefined,
                },
              }
            })
            await refresh()
          }}
        />
      )}
    </>
  )
}
function DocumentMetadata({
  document,
  editingVersion,
}: {
  document: MemoryDocument
  editingVersion?: string
}) {
  return (
    <dl>
      <dt>范围</dt>
      <dd>{document.scope === 'user' ? '当前用户' : '当前工作区'}</dd>
      <dt>文件</dt>
      <dd>{document.path}</dd>
      <dt>当前版本</dt>
      <dd title={document.version}>
        {versionLabel(document.version)} · 更新于 {dateLabel(document.updated_at)}
      </dd>
      {editingVersion && editingVersion !== document.version && (
        <>
          <dt>草稿基于</dt>
          <dd title={editingVersion}>{versionLabel(editingVersion)}</dd>
        </>
      )}
      <dt>来源</dt>
      <dd>
        <Sources refs={document.source_refs} />
      </dd>
    </dl>
  )
}
function CreateMemoryDialog({
  scope,
  workspace,
  generation,
  onClose,
  onCreated,
}: {
  scope: MemoryScope
  workspace: Workspace
  generation: number
  onClose: () => void
  onCreated: (document: MemoryDocument) => Promise<void>
}) {
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const submitted = useRef<{ title: string; body: string; requestId: string } | null>(null)
  async function create() {
    if (busy) return
    setBusy(true)
    setError('')
    submitted.current ??= { title: title.trim(), body, requestId: crypto.randomUUID() }
    const input = submitted.current
    try {
      const created = await memoryApi.create(workspace.id, {
        scope,
        title: input.title,
        body: input.body,
        expected_scope_generation: generation,
        request_id: input.requestId,
      })
      await onCreated(created)
    } catch (err) {
      const unknown = err instanceof TypeError || (err instanceof ApiError && err.status >= 500)
      setUncertain(unknown)
      if (!unknown) submitted.current = null
      setError(
        `${describeError(err)}${unknown ? ' 结果尚未确认，再次提交会使用同一内容与请求标识。' : ''}`,
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      title="新建长期记忆"
      onClose={() => {
        if (!busy) onClose()
      }}
    >
      <form
        className="stacked-form"
        onSubmit={(event) => {
          event.preventDefault()
          void create()
        }}
      >
        <p className="page-note">
          范围：{scope === 'user' ? '当前用户' : workspace.name}
          。正文由你明确保存，不自动提取对话内容。
        </p>
        <label>
          标题
          <input
            required
            maxLength={200}
            value={title}
            disabled={busy || uncertain}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Markdown 正文
          <textarea
            required
            maxLength={500000}
            className="memory-create-body"
            value={body}
            disabled={busy || uncertain}
            onChange={(event) => setBody(event.target.value)}
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="button-row">
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !title.trim() || !body.trim()}>
            {busy ? '正在保存…' : uncertain ? '核对本次创建' : '保存记忆'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
function RulesPanel({ workspace, active }: { workspace: Workspace; active: boolean }) {
  const client = useQueryClient()
  const [path, setPath] = useState('')
  const [paths, setPaths] = useState<string[]>([])
  const [selected, setSelected] = useState('global')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const rules = useQuery({
    queryKey: ['memory-rules', workspace.id, paths],
    queryFn: () => memoryApi.rules(workspace.id, paths),
    enabled: active,
  })
  const global = rules.data?.global_rules
  const source = rules.data?.rules.find(
    (rule) => rule.scope !== 'user' && rule.source_path === selected,
  )
  const dirty = !!draft && draft.body !== draft.original
  useUnsavedWarning(dirty)
  async function reload() {
    const latest = await memoryApi.rules(workspace.id, paths)
    client.setQueryData(['memory-rules', workspace.id, paths], latest)
    setDraft(null)
  }
  function requestReload() {
    if (dirty)
      setConfirmation({
        title: '放弃全局规则草稿并重新读取？',
        description: '未保存的全局 AGENTS.md 修改将被服务端当前版本替换。需要保留时请先复制正文。',
        action: '放弃草稿并读取',
        run: reload,
      })
    else
      void reload().catch((error) => {
        if (global)
          setDraft((old) => ({
            body: old?.body ?? global.body,
            original: old?.original ?? global.body,
            version: old?.version ?? global.version,
            ...old,
            error: describeError(error),
          }))
      })
  }
  async function save() {
    if (!global || !draft || !dirty || saving || draft.conflict) return
    setSaving(true)
    try {
      const updated = await memoryApi.updateGlobalRules(workspace.id, {
        body: draft.body,
        expected_version: draft.version,
        request_id: crypto.randomUUID(),
      })
      setDraft({
        body: updated.body,
        original: updated.body,
        version: updated.version,
        message: '全局规则已保存。后续任务将按服务端规则加载边界使用当前版本。',
      })
      await rules.refetch()
    } catch (error) {
      const conflict =
        (error instanceof ApiError && error.status === 409) ||
        error instanceof TypeError ||
        (error instanceof ApiError && error.status >= 500)
      setDraft({
        ...draft,
        ...(conflict ? { conflict: describeError(error) } : { error: describeError(error) }),
        message: undefined,
      })
    } finally {
      setSaving(false)
    }
  }
  return (
    <>
      <form
        className="memory-toolbar memory-rules-filter"
        onSubmit={(event) => {
          event.preventDefault()
          setPaths(path.trim() ? [path.trim()] : [])
          setSelected('global')
        }}
      >
        <label>
          适用路径
          <input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="项目相对路径，可选"
          />
        </label>
        <Button type="submit">
          <RefreshCw size={14} />
          读取规则来源
        </Button>
      </form>
      <p className="page-note memory-rules-note">
        全局规则可由你明确编辑；项目与目录 AGENTS.md 只读展示。目录规则只适用于对应路径及其子树。
      </p>
      {rules.isError ? (
        <ReadError service error={rules.error} retry={() => void rules.refetch()} />
      ) : rules.isPending ? (
        <p className="page-note">正在读取真实规则来源…</p>
      ) : (
        <div className="memory-workspace">
          <div className="memory-document-list" aria-label="规则来源">
            <button
              type="button"
              aria-pressed={selected === 'global'}
              onClick={() => setSelected('global')}
            >
              <strong>用户全局 AGENTS.md</strong>
              <small>{global?.exists ? '可编辑' : '尚未创建，保存后建立'}</small>
            </button>
            {rules.data.rules
              .filter((rule) => rule.scope !== 'user')
              .map((rule) => (
                <button
                  type="button"
                  key={rule.source_path}
                  aria-pressed={selected === rule.source_path}
                  onClick={() => setSelected(rule.source_path)}
                >
                  <strong>{rule.scope === 'project' ? '项目规则' : '目录规则'}</strong>
                  <small>{rule.source_path}</small>
                  <small>只读 · 适用 {rule.applies_to}</small>
                </button>
              ))}
          </div>
          <div className="memory-document-detail">
            {selected === 'global' && global ? (
              <MarkdownEditor
                title="用户全局 AGENTS.md"
                content={draft?.body ?? global.body}
                dirty={dirty}
                busy={saving}
                conflict={draft?.conflict}
                error={draft?.error}
                message={draft?.message}
                onChange={(body) =>
                  setDraft((old) => ({
                    ...old,
                    original: old?.original ?? global.body,
                    version: old?.version ?? global.version,
                    body,
                    error: undefined,
                    message: undefined,
                  }))
                }
                onSave={() => void save()}
                onReload={requestReload}
                metadata={
                  <dl>
                    <dt>来源</dt>
                    <dd>{global.source_path}</dd>
                    <dt>范围</dt>
                    <dd>当前用户全局规则</dd>
                    <dt>版本</dt>
                    <dd title={draft?.version ?? global.version}>
                      {versionLabel(draft?.version ?? global.version)}
                    </dd>
                  </dl>
                }
              />
            ) : source ? (
              <MarkdownEditor
                title={source.scope === 'project' ? '项目 AGENTS.md' : '目录 AGENTS.md'}
                content={source.body}
                readOnly
                metadata={
                  <dl>
                    <dt>来源</dt>
                    <dd>{source.source_path}</dd>
                    <dt>适用范围</dt>
                    <dd>{source.applies_to}</dd>
                    <dt>版本</dt>
                    <dd title={source.version}>{versionLabel(source.version)}</dd>
                    <dt>加载顺序</dt>
                    <dd>{source.precedence}</dd>
                  </dl>
                }
              />
            ) : (
              <EmptyState icon={Shield} title="选择规则来源">
                只显示服务端实际读取到的规则，不从聊天或资料内容推断规则。
              </EmptyState>
            )}
          </div>
        </div>
      )}
      {confirmation && (
        <ConfirmDialog confirmation={confirmation} onClose={() => setConfirmation(null)} />
      )}
    </>
  )
}
function ProposalsPanel({
  workspace,
  scope,
  onScopeChange,
  active,
}: {
  workspace: Workspace
  scope: MemoryScope
  onScopeChange: (scope: MemoryScope) => void
  active: boolean
}) {
  const client = useQueryClient()
  const proposals = useQuery({
    queryKey: ['memory-proposals', workspace.id, scope],
    queryFn: () => memoryApi.proposals(workspace.id, scope),
    enabled: active,
  })
  const [busy, setBusy] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const ids = useRef<Record<string, string>>({})
  async function decide(proposal: MemoryProposal, approved: boolean) {
    if (busy) return
    const key = `${proposal.id}:${proposal.version}:${approved}`
    ids.current[key] ??= crypto.randomUUID()
    setBusy(proposal.id)
    setErrors((old) => ({ ...old, [proposal.id]: '' }))
    try {
      await memoryApi.decideProposal(workspace.id, proposal.id, {
        approved,
        expected_version: proposal.version,
        request_id: ids.current[key],
      })
      await proposals.refetch()
      await client.invalidateQueries({ queryKey: ['memory-documents'] })
      await client.invalidateQueries({ queryKey: ['memory-document'] })
    } catch (error) {
      setErrors((old) => ({ ...old, [proposal.id]: describeError(error) }))
    } finally {
      setBusy('')
    }
  }
  return (
    <>
      <div className="memory-toolbar">
        <MemoryScopePicker value={scope} onChange={onScopeChange} workspaceName={workspace.name} />
        <Button disabled={proposals.isFetching} onClick={() => void proposals.refetch()}>
          <RefreshCw size={14} />
          刷新候选
        </Button>
      </div>
      <p className="page-note memory-rules-note">
        候选尚未成为长期记忆。批准后才由统一服务写入；候选不会修改 AGENTS.md。
      </p>
      {proposals.isError ? (
        <ReadError service error={proposals.error} retry={() => void proposals.refetch()} />
      ) : proposals.isPending ? (
        <p className="page-note">正在读取候选…</p>
      ) : proposals.data.length ? (
        <div className="memory-proposal-list">
          {proposals.data.map((proposal) => (
            <section className="memory-proposal" key={proposal.id}>
              <div className="memory-editor-heading">
                <h2>{proposal.title}</h2>
                <Status
                  tone={
                    proposal.status === 'pending'
                      ? 'warning'
                      : proposal.status === 'approved'
                        ? 'good'
                        : 'neutral'
                  }
                >
                  {statusLabels[proposal.status]}
                </Status>
              </div>
              <div className="memory-metadata">
                <dl>
                  <dt>来源角色</dt>
                  <dd>
                    {
                      { user: '用户', agent: '主 agent', subagent: '子 agent' }[
                        proposal.source_actor
                      ]
                    }
                  </dd>
                  <dt>提出时间</dt>
                  <dd>{dateLabel(proposal.created_at)}</dd>
                  <dt>基础版本</dt>
                  <dd title={proposal.base_version}>{versionLabel(proposal.base_version)}</dd>
                  <dt>来源引用</dt>
                  <dd>
                    <Sources refs={proposal.source_refs} />
                  </dd>
                </dl>
              </div>
              {proposal.body !== null ? (
                <pre className="memory-proposal-body">{proposal.body}</pre>
              ) : (
                <p className="page-note">候选正文不可用，当前不能批准写入。</p>
              )}
              {errors[proposal.id] && (
                <p className="form-error" role="alert">
                  {errors[proposal.id]}
                </p>
              )}
              {proposal.status === 'pending' && (
                <div className="button-row">
                  <Button disabled={!!busy} onClick={() => void decide(proposal, false)}>
                    <X size={14} />
                    拒绝
                  </Button>
                  <Button
                    variant="primary"
                    disabled={!!busy || proposal.body === null}
                    onClick={() => void decide(proposal, true)}
                  >
                    <Check size={14} />
                    {busy === proposal.id ? '正在提交…' : '批准写入'}
                  </Button>
                </div>
              )}
            </section>
          ))}
        </div>
      ) : (
        <EmptyState icon={BookOpen} title="没有待审阅的候选">
          来自运行或子 agent 的候选会在这里显示来源与建议正文，你可以明确批准或拒绝。
        </EmptyState>
      )}
    </>
  )
}
