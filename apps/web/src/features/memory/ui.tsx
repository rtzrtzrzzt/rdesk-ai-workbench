import { useId } from 'react'
import type { ReactNode } from 'react'
import { RefreshCw, Save, Search, Trash2 } from 'lucide-react'
import { Button, PageHeading, Status } from '../../components/ui'

export type MemoryTab = 'documents' | 'rules' | 'proposals'
export type MemoryScope = 'user' | 'workspace'

export function MemoryPageFrame({
  tab,
  onTabChange,
  action,
  children,
}: {
  tab: MemoryTab
  onTabChange: (tab: MemoryTab) => void
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="page-content memory-page">
      <PageHeading title="记忆与规则" action={action}>
        管理长期记忆与明确指令，保留来源和每一次修改。
      </PageHeading>
      <div
        className="memory-tabs"
        role="tablist"
        aria-label="记忆与规则分类"
        onKeyDown={(event) => {
          const tabs: MemoryTab[] = ['documents', 'rules', 'proposals']
          const index = tabs.indexOf(tab)
          const next =
            event.key === 'ArrowRight'
              ? (index + 1) % tabs.length
              : event.key === 'ArrowLeft'
                ? (index + tabs.length - 1) % tabs.length
                : event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? tabs.length - 1
                    : -1
          if (next < 0) return
          event.preventDefault()
          onTabChange(tabs[next])
          event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus()
        }}
      >
        {[
          { id: 'documents' as const, label: '长期记忆' },
          { id: 'rules' as const, label: 'AGENTS 规则' },
          { id: 'proposals' as const, label: '候选记忆' },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`memory-tab-${item.id}`}
            aria-selected={tab === item.id}
            tabIndex={tab === item.id ? 0 : -1}
            aria-controls="memory-panel"
            onClick={() => onTabChange(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <section
        id="memory-panel"
        role="tabpanel"
        aria-labelledby={`memory-tab-${tab}`}
        className="memory-panel"
      >
        {children}
      </section>
    </div>
  )
}

export function MemoryScopePicker({
  value,
  onChange,
  workspaceName,
}: {
  value: MemoryScope
  onChange: (value: MemoryScope) => void
  workspaceName?: string
}) {
  return (
    <div className="segmented memory-scope" role="group" aria-label="记忆适用范围">
      <button type="button" aria-pressed={value === 'user'} onClick={() => onChange('user')}>
        用户级
      </button>
      <button
        type="button"
        aria-pressed={value === 'workspace'}
        disabled={!workspaceName}
        onClick={() => onChange('workspace')}
        title={workspaceName ?? '请先选择工作区'}
      >
        {workspaceName ?? '当前工作区'}
      </button>
    </div>
  )
}

export function MemorySearch({
  value,
  onChange,
  onSearch,
  busy,
}: {
  value: string
  onChange: (value: string) => void
  onSearch: () => void
  busy: boolean
}) {
  return (
    <form
      className="memory-search"
      role="search"
      onSubmit={(event) => {
        event.preventDefault()
        onSearch()
      }}
    >
      <Search size={15} aria-hidden="true" />
      <input
        aria-label="搜索记忆"
        placeholder="搜索中文关键词或路径"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <Button type="submit" variant="ghost" disabled={busy}>
        {busy ? '搜索中…' : '搜索'}
      </Button>
    </form>
  )
}

export function MarkdownEditor({
  title,
  content,
  onChange,
  metadata,
  readOnly = false,
  dirty = false,
  busy = false,
  conflict,
  error,
  message,
  onSave,
  onReload,
  onForget,
}: {
  title: string
  content: string
  onChange?: (content: string) => void
  metadata?: ReactNode
  readOnly?: boolean
  dirty?: boolean
  busy?: boolean
  conflict?: string
  error?: string
  message?: string
  onSave?: () => void
  onReload?: () => void
  onForget?: () => void
}) {
  const editorId = useId()
  return (
    <div className="memory-editor">
      <div className="memory-editor-heading">
        <h2>{title}</h2>
        <Status tone={dirty ? 'warning' : 'neutral'}>
          {readOnly ? '只读' : dirty ? '有未保存修改' : '已读取当前版本'}
        </Status>
      </div>
      {metadata && <div className="memory-metadata">{metadata}</div>}
      {conflict && (
        <div className="memory-conflict" role="alert">
          <strong>版本已变化，尚未保存</strong>
          <p>{conflict}</p>
          <p>你的草稿仍保留在编辑器中。重新读取前可复制需要保留的内容。</p>
        </div>
      )}
      <label className="sr-only" htmlFor={editorId}>
        Markdown 正文
      </label>
      <textarea
        id={editorId}
        className="memory-markdown"
        value={content}
        onChange={(event) => onChange?.(event.target.value)}
        readOnly={readOnly}
        disabled={busy}
        spellCheck={false}
      />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="memory-message" role="status">
          {message}
        </p>
      )}
      <div className="memory-editor-footer">
        <span>
          {readOnly ? '项目文件仅供浏览，保留原有规则。' : 'Markdown 正文 · 保存时检查预期版本'}
        </span>
        <div className="button-row">
          {onForget && (
            <Button variant="ghost" onClick={onForget} disabled={busy}>
              <Trash2 size={14} />
              忘记
            </Button>
          )}
          {onReload && (
            <Button onClick={onReload} disabled={busy}>
              <RefreshCw size={14} />
              重新读取
            </Button>
          )}
          {onSave && !readOnly && (
            <Button variant="primary" onClick={onSave} disabled={busy || !dirty || !!conflict}>
              <Save size={14} />
              {busy ? '正在保存…' : '保存'}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
