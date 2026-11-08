import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, FolderClosed, FolderOpen, Plus, Search, Settings } from 'lucide-react'
import type { DeletionPreview, Thread, Workspace } from '../../api/types'
import { IconButton } from '../../components/ui'
import { ThreadMenu } from '../chat/ThreadMenu'
import { threadStatusLabel } from '../chat/runState'
import './workspaceSidebar.css'

type WorkspaceSidebarProps = {
  workspaces: Workspace[]
  workspaceId: string
  threadId: string
  chatActive: boolean
  authenticated: boolean
  loadThreads: (workspaceId: string) => Promise<Thread[]>
  onSelectWorkspace: (workspaceId: string) => void
  onSelectThread: (workspaceId: string, thread: Thread) => void
  onNewThread: (workspaceId: string) => void
  onCreateWorkspace: () => void
  onManageWorkspaces: () => void
  onDeleted: (scope: DeletionPreview) => Promise<void>
  onThreadsChanged: (workspaceId: string) => Promise<void>
}

const expandedStorageKey = 'workbench.expanded-workspaces'

function readExpanded(): Record<string, boolean> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(expandedStorageKey) ?? '{}')
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {}
    return Object.fromEntries(
      Object.entries(stored).filter(([, value]) => typeof value === 'boolean'),
    )
  } catch {
    return {}
  }
}

export function WorkspaceSidebar(props: WorkspaceSidebarProps) {
  const [filter, setFilter] = useState('')
  const [searching, setSearching] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => ({
    ...readExpanded(),
    ...(props.workspaceId ? { [props.workspaceId]: true } : {}),
  }))

  useEffect(() => {
    if (props.workspaceId)
      setExpanded((previous) =>
        previous[props.workspaceId] ? previous : { ...previous, [props.workspaceId]: true },
      )
  }, [props.workspaceId])

  useEffect(() => {
    try {
      localStorage.setItem(expandedStorageKey, JSON.stringify(expanded))
    } catch {
      // Browsing restrictions should not prevent expanding a workspace.
    }
  }, [expanded])

  const workspaces = props.workspaces.filter((item) => !item.archived && !item.removed_at)

  return (
    <section className="workspace-sidebar" aria-label="工作区与对话">
      <div className="workspace-sidebar-toolbar">
        <span>工作区</span>
        <div className="workspace-sidebar-tools">
          {!!workspaces.length && (
            <IconButton
              icon={Search}
              label="搜索对话"
              onClick={() => {
                setSearching(!searching)
                setFilter('')
              }}
            />
          )}
          <IconButton
            icon={Settings}
            label="管理工作区"
            disabled={!props.authenticated}
            onClick={props.onManageWorkspaces}
          />
          <IconButton
            icon={Plus}
            label="创建工作区"
            disabled={!props.authenticated}
            onClick={props.onCreateWorkspace}
          />
        </div>
      </div>
      {!!workspaces.length && searching && (
        <label className="workspace-thread-search">
          <Search size={14} aria-hidden="true" />
          <input
            type="search"
            aria-label="搜索已展开工作区的对话"
            placeholder="搜索对话…"
            autoFocus
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setFilter('')
                setSearching(false)
              }
            }}
          />
        </label>
      )}
      <div className="workspace-sidebar-groups">
        {workspaces.map((workspace) => (
          <WorkspaceGroup
            key={workspace.id}
            {...props}
            workspace={workspace}
            expanded={!!expanded[workspace.id]}
            filter={filter.trim().toLocaleLowerCase()}
            onToggle={() =>
              setExpanded((previous) => ({
                ...previous,
                [workspace.id]: !previous[workspace.id],
              }))
            }
          />
        ))}
        {!workspaces.length && (
          <div className="workspace-sidebar-empty">
            <p>{props.authenticated ? '添加一个工作区，开始对话。' : '连接工作台后查看工作区。'}</p>
            {props.authenticated && (
              <button type="button" onClick={props.onCreateWorkspace}>
                <Plus size={14} />
                <span>添加工作区</span>
              </button>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function WorkspaceGroup({
  workspace,
  expanded,
  filter,
  onToggle,
  ...props
}: WorkspaceSidebarProps & {
  workspace: Workspace
  expanded: boolean
  filter: string
  onToggle: () => void
}) {
  const current = workspace.id === props.workspaceId
  const threads = useQuery({
    queryKey: ['threads', workspace.id],
    // All observers share the parent's deletion reconciliation and cache cleanup.
    queryFn: () => props.loadThreads(workspace.id),
    enabled: props.authenticated && expanded,
    refetchInterval: props.authenticated && expanded && !current ? 8000 : false,
  })
  const visible = (threads.data ?? []).filter(
    (thread) =>
      !thread.is_subagent && (!filter || thread.title.toLocaleLowerCase().includes(filter)),
  )
  const active = visible.filter((thread) => !thread.archived)
  const archived = visible.filter((thread) => thread.archived)
  const panelId = `workspace-threads-${workspace.id}`

  function renderThread(thread: Thread) {
    const selected = current && props.chatActive && thread.id === props.threadId
    return (
      <div className="thread-list-row workspace-thread-row" key={thread.id}>
        <button
          type="button"
          data-thread-id={thread.id}
          aria-current={selected ? 'page' : undefined}
          className={selected ? 'selected' : ''}
          title={thread.title || '新对话'}
          onClick={() => props.onSelectThread(workspace.id, thread)}
        >
          <span>{thread.title || '新对话'}</span>
          <small>
            {threadStatusLabel(thread)}
            {thread.queue_count ? ` · ${thread.queue_count} 条排队` : ''}
          </small>
        </button>
        <ThreadMenu
          thread={thread}
          onDeleted={props.onDeleted}
          onChanged={() => props.onThreadsChanged(workspace.id)}
        />
      </div>
    )
  }

  return (
    <div className={`workspace-sidebar-group${current ? ' current' : ''}`}>
      <div className="workspace-group-heading">
        <button
          type="button"
          className="workspace-group-toggle"
          aria-label={`${expanded ? '折叠' : '展开'}工作区：${workspace.name}`}
          aria-expanded={expanded}
          aria-controls={panelId}
          title={`${expanded ? '折叠' : '展开'}对话`}
          onClick={onToggle}
        >
          <ChevronDown size={13} />
        </button>
        <button
          type="button"
          className="workspace-group-select"
          title={`${workspace.name}\n${workspace.root_path}`}
          aria-label={`选择工作区：${workspace.name}`}
          aria-pressed={current}
          onClick={() => props.onSelectWorkspace(workspace.id)}
        >
          {expanded ? <FolderOpen size={16} /> : <FolderClosed size={16} />}
          <span>{workspace.name}</span>
        </button>
        <div className="workspace-group-new">
          <IconButton
            icon={Plus}
            label={`在 ${workspace.name} 中新建对话`}
            disabled={!props.authenticated}
            onClick={() => props.onNewThread(workspace.id)}
          />
        </div>
      </div>
      <div id={panelId} className="workspace-group-threads" hidden={!expanded}>
        {threads.isPending && props.authenticated && (
          <p className="workspace-group-message" role="status">
            正在加载对话…
          </p>
        )}
        {threads.isError && (
          <div className="workspace-group-message" role="status">
            <p>对话暂时无法加载。</p>
            <button
              type="button"
              disabled={threads.isFetching}
              onClick={() => void threads.refetch()}
            >
              重试
            </button>
          </div>
        )}
        {threads.data && (
          <div className="thread-list workspace-thread-list">
            {active.map(renderThread)}
            {archived.length > 0 && (
              <details className="archived-threads">
                <summary>已归档（{archived.length}）</summary>
                {archived.map(renderThread)}
              </details>
            )}
            {visible.length === 0 && (
              <p className="workspace-group-message">
                {filter ? '没有匹配的对话。' : '还没有对话，点击 + 开始。'}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
