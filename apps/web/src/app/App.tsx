import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BookOpen,
  BarChart3,
  ChevronDown,
  FolderOpen,
  ListTodo,
  LogOut,
  Menu,
  MessageSquare,
  Monitor,
  MoreHorizontal,
  Search,
  PanelLeftClose,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Unplug,
  Wrench,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../api/workbench'
import { settingsApi } from '../api/settings'
import type { Identity, Message, Run, Thread, AttachmentInput, RunOptions } from '../api/types'
import { ApiError, authGeneration } from '../api/client'
import { Button, EmptyState, IconButton, PageHeading, Status } from '../components/ui'
import { Dialog } from '../components/Dialog'
import { filesApi } from '../api/files'
import { AttachmentPicker, MessageAttachments } from '../features/chat/Attachments'
import type { DraftAttachment } from '../features/chat/Attachments'
import { Composer } from '../features/chat/Composer'
import { MessageContent, shouldRenderMessage } from '../features/chat/MessageContent'
import { RunPanel } from '../features/chat/RunPanel'
import { useSteering } from '../features/chat/useSteering'
import { useRunStream } from '../features/chat/useRunStream'
import {
  runTerminal as terminal,
  runLabels as labels,
  acceptsInput,
} from '../features/chat/runState'
import { TaskOptions, validRunOptions } from '../features/chat/TaskOptions'
import { ConnectionsPage, DiagnosticsPage, SettingsPage } from '../features/settings/Settings'
import { AuthView } from './Auth'
import { MemoryPage } from '../features/memory/MemoryPage'
import { FilesPage } from '../features/files/FilesPage'
import { ToolsPage } from '../features/tools/ToolsPage'
import { AnalyticsPage } from '../features/analytics/AnalyticsPage'
import { AutomationPage } from '../features/automation/AutomationPage'
import { WorkspaceManager } from '../features/workspaces/WorkspaceManager'
import { ForkDialog } from '../features/chat/ForkDialog'
import { ConversationExport } from '../features/chat/ConversationExport'
import { useAccountActivity } from '../features/settings/useAccountActivity'
import '../features/chat/conversation.css'
import './navigation.css'

type View =
  | 'chat'
  | 'tasks'
  | 'files'
  | 'memory'
  | 'tools'
  | 'devices'
  | 'connections'
  | 'settings'
  | 'diagnostics'
  | 'analytics'
const navigation: { id: View; label: string; icon: LucideIcon }[] = [
  { id: 'chat', label: '对话', icon: MessageSquare },
  { id: 'tasks', label: '后台任务', icon: ListTodo },
  { id: 'files', label: '文件', icon: FolderOpen },
  { id: 'memory', label: '记忆与规则', icon: BookOpen },
  { id: 'tools', label: '工具与扩展', icon: Wrench },
  { id: 'devices', label: '电脑操作', icon: Monitor },
  { id: 'analytics', label: '数据看板', icon: BarChart3 },
]
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : '操作未完成，请稍后重试。'
const uncertainRequest = (error: unknown) =>
  error instanceof TypeError ||
  (error instanceof ApiError && (error.status >= 500 || error.status === 408))

export function App() {
  const queryClient = useQueryClient()
  const [view, setView] = useState<View>('chat')
  const [sidebar, setSidebar] = useState(true)
  const [mobileSidebar, setMobileSidebar] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [threadFilter, setThreadFilter] = useState('')
  const [settingsVisited, setSettingsVisited] = useState(false)
  const [inputModes, setInputModes] = useState<Record<string, 'steer' | 'draft'>>({})
  const [workspaceId, setWorkspaceId] = useState(
    () => localStorage.getItem('workbench.workspace') ?? '',
  )
  const [threadId, setThreadId] = useState(() => localStorage.getItem('workbench.thread') ?? '')
  const [modelId, setModelId] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [attachmentDrafts, setAttachmentDrafts] = useState<Record<string, DraftAttachment[]>>({})
  const [taskOptions, setTaskOptions] = useState<Record<string, RunOptions | null>>({})
  const [pickingAttachments, setPickingAttachments] = useState(false)
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [notice, setNotice] = useState('')
  const [unconfirmed, setUnconfirmed] = useState(false)
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState('')
  const [creatingWorkspace, setCreatingWorkspace] = useState(false)
  const [managingWorkspaces, setManagingWorkspaces] = useState(false)
  const [forkSource, setForkSource] = useState<{ message: Message; threadId: string } | null>(null)
  const [hasNew, setHasNew] = useState(false)
  const pendingSend = useRef<{
    input: string
    modelId: string | null
    expectedModel: { connection_id: string; version: number }
    threadId: string
    requestId: string
    attachments: AttachmentInput[]
    options: RunOptions | null
    draftKey: string
  } | null>(null)
  const cancelIds = useRef<Record<string, string>>({})
  const scrollRef = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const setup = useQuery({ queryKey: ['setup'], queryFn: api.setupStatus })
  const session = useQuery({
    queryKey: ['session'],
    queryFn: ({ signal }) => api.session(signal),
    enabled: !!setup.data?.configured,
  })
  const authenticated =
    !!session.data && !(session.error instanceof ApiError && session.error.status === 401)
  useAccountActivity(authenticated ? session.data?.user.id : undefined)
  const workspaces = useQuery({
    queryKey: ['workspaces'],
    queryFn: api.workspaces,
    enabled: authenticated,
  })
  const activeWorkspaces =
    workspaces.data?.filter((item) => !item.archived && !item.removed_at) ?? []
  const workspace = activeWorkspaces.find((item) => item.id === workspaceId)
  const threads = useQuery({
    queryKey: ['threads', workspaceId],
    queryFn: () => api.threads(workspaceId),
    enabled: authenticated && !!workspace,
  })
  const thread = threads.data?.find((item) => item.id === threadId)
  const messages = useQuery({
    queryKey: ['messages', threadId],
    queryFn: ({ signal }) => api.messages(threadId, signal),
    enabled: authenticated && !!thread,
  })
  const models = useQuery({ queryKey: ['models'], queryFn: api.models, enabled: authenticated })
  const runs = useQuery({
    queryKey: ['runs', threadId],
    queryFn: () => api.runs(threadId),
    enabled: authenticated && !!thread,
    refetchInterval: 4000,
  })
  const system = useQuery({
    queryKey: ['system'],
    queryFn: api.systemStatus,
    enabled: authenticated && view === 'diagnostics',
  })
  const orderedRuns = [...(runs.data ?? [])].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )
  const currentRun = orderedRuns.find((run) => !terminal.has(run.status))
  const lastRun = currentRun ?? orderedRuns.at(-1)
  const {
    connected: streamConnected,
    streaming,
    clear: clearStreaming,
  } = useRunStream(lastRun, threadId, authenticated)
  const active = !!currentRun
  const draftKey = thread?.id ?? `new:${workspaceId}`
  const draft = drafts[draftKey] ?? ''
  const options = taskOptions[draftKey] ?? null
  const optionsReady = options === null || validRunOptions(options)
  const readyModels = models.data?.filter((model) => model.service_status === 'ready') ?? []
  const defaults = useQuery({
    queryKey: ['workspace-runtime-defaults', workspaceId],
    queryFn: () => settingsApi.workspaceRuntime(workspaceId),
    enabled: authenticated && !!workspace,
  })
  const inheritedModelId = defaults.data?.effective.model_connection_id ?? ''
  const effectiveModel = readyModels.find((model) => model.id === (modelId || inheritedModelId))
  const inheritedModel = models.data?.find((model) => model.id === inheritedModelId)
  const draftAttachments = attachmentDrafts[draftKey] ?? []
  const steering = useSteering(lastRun?.id, refreshRun)
  const canSteer = !!currentRun && acceptsInput(currentRun.status)
  const hasSteering = canSteer || !!steering.value || !!steering.pending
  const steeringMode = hasSteering && inputModes[draftKey] !== 'draft'
  const visibleThreads = (threads.data ?? []).filter((item) =>
    item.title.toLocaleLowerCase().includes(threadFilter.trim().toLocaleLowerCase()),
  )
  const attachmentIds = draftAttachments.map((item) => item.artifact_id)
  const inputPlan = useQuery({
    queryKey: [
      'input-plan',
      workspaceId,
      effectiveModel?.id,
      effectiveModel?.version,
      attachmentIds,
    ],
    queryFn: () => filesApi.inputPlan(workspaceId, effectiveModel?.id ?? '', attachmentIds),
    enabled: authenticated && !!workspace && !!effectiveModel && attachmentIds.length > 0,
  })
  const attachmentSnapshot: AttachmentInput[] = draftAttachments.flatMap((item) => {
    const plan = inputPlan.data?.find((p) => p.artifact_id === item.artifact_id)
    return plan &&
      item.mode &&
      plan.routes.some((route) => route.mode === item.mode && route.available) &&
      (!plan.remote ||
        (item.consent_remote &&
          item.consent_model === `${effectiveModel?.id}:${effectiveModel?.version}` &&
          item.consent_version === plan.version))
      ? [
          {
            artifact_id: item.artifact_id,
            version: plan.version,
            mode: item.mode,
            consent_remote:
              plan.remote &&
              item.consent_remote &&
              item.consent_model === `${effectiveModel?.id}:${effectiveModel?.version}` &&
              item.consent_version === plan.version,
          },
        ]
      : []
  })
  const attachmentsReady =
    !draftAttachments.length ||
    (!inputPlan.isFetching &&
      !inputPlan.isError &&
      attachmentSnapshot.length === draftAttachments.length &&
      attachmentSnapshot.filter((a) => a.mode === 'native_image').length <= 2)

  useEffect(() => {
    const expired = () => {
      void session.refetch()
    }
    window.addEventListener('workbench:unauthorized', expired)
    return () => window.removeEventListener('workbench:unauthorized', expired)
  }, [session.refetch])
  useEffect(() => {
    document.documentElement.dataset.theme = localStorage.getItem('workbench.theme') ?? 'system'
  }, [])
  useEffect(() => {
    if (!workspaces.data) return
    const selectable = workspaces.data.filter((item) => !item.archived && !item.removed_at)
    if (!workspace && workspaceId !== (selectable[0]?.id ?? '')) {
      setWorkspaceId(selectable[0]?.id ?? '')
      setThreadId('')
      clearStreaming()
    }
  }, [workspaces.data, workspace, workspaceId])
  useEffect(() => {
    localStorage.setItem('workbench.workspace', workspaceId)
    localStorage.setItem('workbench.thread', threadId)
  }, [workspaceId, threadId])
  useEffect(() => {
    clearStreaming()
    follow.current = true
    setHasNew(false)
  }, [threadId])
  useEffect(() => {
    if (follow.current) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
    else setHasNew(true)
  }, [messages.data, streaming])
  function navigate(next: View) {
    if (next === 'settings') setSettingsVisited(true)
    setView(next)
    setMobileSidebar(false)
  }
  function chooseWorkspace(id: string) {
    if (!activeWorkspaces.some((item) => item.id === id)) return
    setWorkspaceId(id)
    setModelId('')
    setThreadFilter('')
    setThreadId('')
    clearStreaming()
    setNotice('')
    navigate('chat')
  }
  function chooseThread(item: Thread) {
    setThreadId(item.id)
    clearStreaming()
    setNotice('')
    navigate('chat')
  }
  async function authenticate(username: string, password: string) {
    setAuthBusy(true)
    setAuthError('')
    try {
      const identity = setup.data?.configured
        ? await api.login(username, password)
        : await api.initialize(username, password)
      queryClient.setQueryData<Identity>(['session'], identity)
      await queryClient.invalidateQueries({ queryKey: ['setup'] })
    } catch (error) {
      setAuthError(errorText(error))
    } finally {
      setAuthBusy(false)
    }
  }
  async function send() {
    if (
      sending ||
      !workspace ||
      !effectiveModel ||
      !draft.trim() ||
      active ||
      unconfirmed ||
      !attachmentsReady ||
      !optionsReady
    )
      return
    const generation = authGeneration()
    setSending(true)
    setNotice('')
    try {
      let selectedThread = thread
      if (!selectedThread) {
        selectedThread = await api.createThread(workspace.id)
        if (generation !== authGeneration()) return
        setThreadId(selectedThread.id)
        queryClient.setQueryData<Thread[]>(['threads', workspace.id], (old) => [
          selectedThread!,
          ...(old ?? []),
        ])
        setDrafts((old) => ({ ...old, [selectedThread!.id]: draft }))
        setAttachmentDrafts((old) => ({ ...old, [selectedThread!.id]: draftAttachments }))
        setTaskOptions((old) => ({ ...old, [selectedThread!.id]: options }))
      }
      const prior = pendingSend.current
      if (
        !prior ||
        prior.input !== draft.trim() ||
        prior.modelId !== (modelId || null) ||
        prior.expectedModel.connection_id !== effectiveModel.id ||
        prior.expectedModel.version !== effectiveModel.version ||
        prior.threadId !== selectedThread.id ||
        JSON.stringify(prior.attachments) !== JSON.stringify(attachmentSnapshot) ||
        JSON.stringify(prior.options) !== JSON.stringify(options)
      )
        pendingSend.current = {
          input: draft.trim(),
          modelId: modelId || null,
          expectedModel: { connection_id: effectiveModel.id, version: effectiveModel.version },
          threadId: selectedThread.id,
          requestId: crypto.randomUUID(),
          attachments: attachmentSnapshot,
          options: options === null ? null : { ...options },
          draftKey,
        }
      const request = pendingSend.current!
      await api.startRun(
        request.threadId,
        request.input,
        request.modelId,
        request.requestId,
        request.attachments,
        request.options,
        request.expectedModel,
      )
      if (generation !== authGeneration()) return
      pendingSend.current = null
      setUnconfirmed(false)
      setAttachmentDrafts((old) => ({ ...old, [draftKey]: [], [selectedThread!.id]: [] }))
      setDrafts((old) => ({ ...old, [draftKey]: '', [selectedThread!.id]: '' }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['runs', selectedThread.id] }),
        queryClient.invalidateQueries({ queryKey: ['messages', selectedThread.id] }),
        queryClient.invalidateQueries({ queryKey: ['threads', workspace.id] }),
      ])
    } catch (error) {
      if (generation !== authGeneration()) return
      const uncertain = !!pendingSend.current && uncertainRequest(error)
      setUnconfirmed(uncertain)
      if (!uncertain) pendingSend.current = null
      if (error instanceof ApiError && error.status === 409) void queryClient.invalidateQueries()
      setNotice(
        `${errorText(error)}${uncertain ? ' 请求结果尚未确认，请核对上次请求后再发送新内容。' : ''}`,
      )
    } finally {
      if (generation === authGeneration()) setSending(false)
    }
  }
  async function confirmPending() {
    const pending = pendingSend.current
    if (!pending || sending) return
    const generation = authGeneration()
    setSending(true)
    try {
      await api.startRun(
        pending.threadId,
        pending.input,
        pending.modelId,
        pending.requestId,
        pending.attachments,
        pending.options,
        pending.expectedModel,
      )
      if (generation !== authGeneration()) return
      pendingSend.current = null
      setUnconfirmed(false)
      setNotice('上次请求已确认。')
      setAttachmentDrafts((old) => ({ ...old, [pending.draftKey]: [], [pending.threadId]: [] }))
      setDrafts((old) => ({
        ...old,
        [pending.threadId]:
          old[pending.threadId]?.trim() === pending.input ? '' : (old[pending.threadId] ?? ''),
      }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['runs', pending.threadId] }),
        queryClient.invalidateQueries({ queryKey: ['messages', pending.threadId] }),
      ])
    } catch (error) {
      if (generation !== authGeneration()) return
      const uncertain = uncertainRequest(error)
      if (!uncertain) {
        pendingSend.current = null
        setUnconfirmed(false)
        void queryClient.invalidateQueries()
      }
      setNotice(`${uncertain ? '请求仍未确认：' : ''}${errorText(error)}`)
    } finally {
      if (generation === authGeneration()) setSending(false)
    }
  }
  async function cancel(run: Run) {
    if (stopping) return
    const generation = authGeneration()
    setStopping(true)
    setNotice('')
    cancelIds.current[run.id] ??= crypto.randomUUID()
    try {
      await api.cancelRun(run.id, cancelIds.current[run.id])
      await queryClient.invalidateQueries({ queryKey: ['runs', run.thread_id] })
    } catch (error) {
      if (generation === authGeneration()) setNotice(errorText(error))
    } finally {
      if (generation === authGeneration()) setStopping(false)
    }
  }
  async function refreshRun() {
    await queryClient.invalidateQueries({ queryKey: ['runs', threadId] })
  }
  async function logout() {
    try {
      await api.logout()
      queryClient.clear()
      setThreadId('')
      setDrafts({})
      setAttachmentDrafts({})
      setTaskOptions({})
      clearStreaming()
      setInputModes({})
      steering.reset()
      setSettingsVisited(false)
      pendingSend.current = null
      cancelIds.current = {}
      setUnconfirmed(false)
      setSending(false)
      setStopping(false)
      setNotice('')
      setModelId('')
    } catch (error) {
      setNotice(errorText(error))
    }
  }
  if (setup.isPending || (setup.data?.configured && session.isPending))
    return (
      <main className="app-loading">
        <Sparkles size={27} />
        <span>正在连接工作台…</span>
      </main>
    )
  if (setup.data && (!setup.data.configured || (!authenticated && session.isError)))
    return (
      <AuthView
        mode={setup.data.configured ? 'login' : 'initialize'}
        busy={authBusy}
        error={
          authError ||
          (!setup.data.database_ready
            ? '数据库尚未就绪，请先检查服务配置。'
            : session.error && !(session.error instanceof ApiError && session.error.status === 401)
              ? errorText(session.error)
              : undefined)
        }
        onSubmit={
          setup.data.database_ready
            ? (username, password) => void authenticate(username, password)
            : undefined
        }
      />
    )
  const disabledReason = unconfirmed
    ? '上次发送结果尚未确认，请点击提示中的核对按钮。'
    : !authenticated
      ? '工作台服务尚未连接，草稿可继续编辑。'
      : !workspace
        ? '先创建或选择一个工作区。'
        : !effectiveModel
          ? inheritedModelId && !modelId
            ? '当前默认模型不可用，请检查连接或明确选择另一个模型。'
            : defaults.isFetching
              ? '正在读取工作区默认模型…'
              : '请在输入框选择模型，或在设置中指定默认模型。'
          : !optionsReady
            ? '任务高级设置须符合整数范围及 0.5–0.95 的上下文比例。'
            : !attachmentsReady
              ? '附件需完成预检、选择可用路线，并逐项同意远程传输（最多两张图像）。'
              : active
                ? currentRun?.status === 'held'
                  ? '恢复任务已暂停，先停止此任务后再发送新消息。'
                  : '此处保留下一条消息的草稿，当前任务结束后可发送。'
                : undefined
  const loadedMessages = messages.data ?? []
  const displayMessages: Message[] = [
    ...loadedMessages,
    ...Object.entries(streaming)
      .filter(([id]) => !loadedMessages.some((message) => message.id === id))
      .map(([id, content]) => ({
        id,
        content,
        thread_id: threadId,
        run_id: lastRun?.id ?? '',
        role: 'assistant',
        attachments: [],
        status: 'streaming',
        forkable: false,
        created_at: '',
      })),
  ]
  const composer = (
    <Composer
      controls={
        hasSteering ? (
          <div className="composer-mode-row">
            <div className="segmented" role="group" aria-label="输入用途">
              <button
                aria-pressed={steeringMode}
                onClick={() => setInputModes((old) => ({ ...old, [draftKey]: 'steer' }))}
              >
                补充当前任务{steering.pending ? ' · 待核对' : ''}
              </button>
              <button
                aria-pressed={!steeringMode}
                onClick={() => setInputModes((old) => ({ ...old, [draftKey]: 'draft' }))}
              >
                下一条消息
              </button>
            </div>
            {steeringMode && !canSteer && steering.value && !steering.pending && (
              <Button
                variant="ghost"
                onClick={() => {
                  setDrafts((old) => ({
                    ...old,
                    [draftKey]: [old[draftKey], steering.value].filter(Boolean).join('\n\n'),
                  }))
                  steering.change('')
                  setInputModes((old) => ({ ...old, [draftKey]: 'draft' }))
                }}
              >
                转入下一条消息草稿
              </Button>
            )}
            {steering.notice && (
              <p className="page-note" role="status">
                {steering.notice}
              </p>
            )}
          </div>
        ) : undefined
      }
      options={
        <TaskOptions
          workspaceId={workspace?.id}
          models={models.data ?? []}
          value={options}
          disabled={steeringMode || sending || unconfirmed}
          onChange={(value) => setTaskOptions((old) => ({ ...old, [draftKey]: value }))}
        />
      }
      onAttachments={workspace && effectiveModel ? () => setPickingAttachments(true) : undefined}
      attachmentsDisabled={steeringMode || sending || unconfirmed}
      attachmentSummary={
        !steeringMode && draftAttachments.length ? (
          <button className="attachment-summary" onClick={() => setPickingAttachments(true)}>
            {draftAttachments.length} 份附件 · {attachmentsReady ? '已准备好' : '需要确认'}
          </button>
        ) : undefined
      }
      value={steeringMode ? steering.value : draft}
      onChange={
        steeringMode
          ? steering.change
          : (value) => setDrafts((old) => ({ ...old, [draftKey]: value }))
      }
      onSend={() => void (steeringMode ? steering.send() : send())}
      disabledReason={
        steeringMode
          ? !authenticated
            ? '登录后可补充要求。'
            : !canSteer && !steering.pending
              ? '任务已结束或暂停，补充草稿尚未发送。'
              : undefined
          : disabledReason
      }
      readOnly={steeringMode ? steering.busy || !!steering.pending : sending || unconfirmed}
      sending={steeringMode ? steering.busy : sending}
      running={active}
      sendWhileRunning={steeringMode}
      sendLabel={
        steeringMode ? (steering.pending ? '核对本次补充要求' : '发送补充要求') : '发送消息'
      }
      placeholder={steeringMode ? '补充要求，或调整当前任务的方向…' : '描述你想完成的工作…'}
      stopping={stopping || currentRun?.status === 'cancelling'}
      onStop={currentRun ? () => void cancel(currentRun) : undefined}
      models={
        steeringMode
          ? [
              {
                id: lastRun?.model_connection_id ?? '',
                name:
                  models.data?.find((m) => m.id === lastRun?.model_connection_id)?.name ??
                  '当前任务模型',
              },
            ]
          : [
              {
                id: '',
                name: inheritedModelId
                  ? `默认 · ${inheritedModel?.name ?? '连接不可用'}`
                  : '选择模型',
              },
              ...readyModels,
            ]
      }
      modelId={steeringMode ? (lastRun?.model_connection_id ?? '') : modelId}
      modelDisabled={steeringMode || sending || unconfirmed}
      onModelChange={setModelId}
    />
  )
  const statusItems = system.data
    ? [
        {
          name: '数据库',
          status: system.data.database.status,
          detail: '持久化工作区、会话与任务状态',
        },
        {
          name: '任务 Worker',
          status: system.data.worker.status,
          detail: system.data.worker.last_seen_at
            ? `最近心跳：${new Date(system.data.worker.last_seen_at).toLocaleString()}`
            : '尚未收到 Worker 心跳',
        },
        ...system.data.model_assets.map((asset) => ({
          name: asset.name,
          status: 'downloaded',
          detail:
            asset.verification === 'previously_verified'
              ? '模型文件已下载，沿用此前校验结果；是否提供推理由模型连接确认。'
              : '模型文件已下载',
        })),
      ]
    : []
  return (
    <div className={`workbench ${sidebar ? '' : 'sidebar-collapsed'}`}>
      {mobileSidebar && (
        <button
          className="mobile-scrim"
          aria-label="关闭导航"
          onClick={() => setMobileSidebar(false)}
        />
      )}
      <aside
        className={`sidebar ${mobileSidebar ? 'sidebar-mobile-open' : ''}`}
        aria-label="主导航"
      >
        <div className="sidebar-brand">
          <button className="brand-link" onClick={() => navigate('chat')}>
            <Sparkles size={20} strokeWidth={1.45} />
            <span>AI 工作台</span>
          </button>
          <div className="desktop-toggle">
            <IconButton
              icon={PanelLeftClose}
              label={sidebar ? '收起侧栏' : '展开侧栏'}
              onClick={() => setSidebar(!sidebar)}
            />
          </div>
          <div className="mobile-toggle">
            <IconButton icon={X} label="关闭导航" onClick={() => setMobileSidebar(false)} />
          </div>
        </div>
        <div className="workspace-selector">
          <FolderOpen size={17} />
          <div>
            <select
              aria-label="当前工作区"
              value={workspace?.id ?? ''}
              onChange={(event) => chooseWorkspace(event.target.value)}
              disabled={!activeWorkspaces.length}
            >
              <option value="" disabled>
                {authenticated ? '创建第一个工作区' : '尚未连接工作台'}
              </option>
              {activeWorkspaces.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
            {!workspace && <small>选择项目目录</small>}
          </div>
          <IconButton
            icon={Settings}
            label="管理工作区"
            disabled={!authenticated}
            onClick={() => setManagingWorkspaces(true)}
          />
          <IconButton
            icon={Plus}
            label="创建工作区"
            disabled={!authenticated}
            onClick={() => setCreatingWorkspace(true)}
          />
        </div>
        <div className="sidebar-new">
          <Button
            variant="secondary"
            onClick={() => {
              setThreadId('')
              setNotice('')
              navigate('chat')
            }}
          >
            <Plus size={17} />
            <span>新对话</span>
          </Button>
        </div>
        <nav className="primary-nav">
          {navigation
            .filter((item) => ['chat', 'files', 'memory'].includes(item.id))
            .map((item) => (
              <button
                key={item.id}
                title={item.label}
                className={view === item.id ? 'active' : ''}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={17} strokeWidth={1.6} />
                <span>{item.label}</span>
              </button>
            ))}
          <button
            aria-expanded={moreOpen}
            aria-controls="more-navigation"
            title="更多功能"
            onClick={() => setMoreOpen(!moreOpen)}
            className={
              ['tasks', 'tools', 'devices', 'analytics', 'diagnostics'].includes(view)
                ? 'active'
                : ''
            }
          >
            <MoreHorizontal size={17} />
            <span>更多</span>
            <ChevronDown size={13} />
          </button>
          <div id="more-navigation" hidden={!moreOpen} className="more-navigation">
            {[
              ...navigation.filter((item) =>
                ['tasks', 'tools', 'devices', 'analytics'].includes(item.id),
              ),
              { id: 'diagnostics' as View, label: '诊断', icon: Activity },
            ].map((item) => (
              <button
                key={item.id}
                title={item.label}
                className={view === item.id ? 'active' : ''}
                onClick={() => navigate(item.id)}
              >
                <item.icon size={16} />
                <span>{item.label}</span>
              </button>
            ))}
          </div>
        </nav>
        <div className="sidebar-conversations">
          <div className="sidebar-label">最近对话</div>
          {!!threads.data?.length && (
            <label className="thread-filter">
              <Search size={13} />
              <input
                aria-label="筛选当前工作区对话"
                placeholder="筛选对话"
                value={threadFilter}
                onChange={(event) => setThreadFilter(event.target.value)}
              />
            </label>
          )}
          {threads.isError ? (
            <p>会话暂时无法加载。</p>
          ) : threads.data?.length ? (
            <div className="thread-list">
              {visibleThreads.length === 0 && <p>没有符合的对话。</p>}
              {visibleThreads.map((item) => (
                <button
                  key={item.id}
                  data-thread-id={item.id}
                  aria-current={threadId === item.id && view === 'chat' ? 'page' : undefined}
                  className={threadId === item.id && view === 'chat' ? 'selected' : ''}
                  onClick={() => chooseThread(item)}
                  title={item.title}
                >
                  {item.title || '新对话'}
                </button>
              ))}
            </div>
          ) : (
            <p>
              还没有会话。
              <br />
              你的工作将从一次对话开始。
            </p>
          )}
        </div>
        <nav className="secondary-nav">
          {[
            { id: 'connections' as View, label: '模型与连接', icon: SlidersHorizontal },
            { id: 'settings' as View, label: '设置', icon: Settings },
          ].map((item) => (
            <button
              key={item.id}
              title={item.label}
              className={view === item.id ? 'active' : ''}
              onClick={() => navigate(item.id)}
            >
              <item.icon size={17} strokeWidth={1.6} />
              <span>{item.label}</span>
            </button>
          ))}
          {authenticated && (
            <button className="account-button" title="退出登录" onClick={() => void logout()}>
              <div className="account-avatar">
                {session.data!.user.username.slice(0, 1).toUpperCase()}
              </div>
              <span>{session.data!.user.username}</span>
              <LogOut size={14} />
            </button>
          )}
        </nav>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-leading">
            <div className="mobile-toggle">
              <IconButton icon={Menu} label="打开导航" onClick={() => setMobileSidebar(true)} />
            </div>
            <span>
              {view === 'chat'
                ? (thread?.title ?? '新对话')
                : [
                    ...navigation,
                    { id: 'connections', label: '模型与连接' },
                    { id: 'settings', label: '设置' },
                    { id: 'diagnostics', label: '诊断' },
                  ].find((item) => item.id === view)?.label}
            </span>
            {view === 'chat' && thread && <ConversationExport thread={thread} />}
          </div>
          <button className="connection-indicator" onClick={() => navigate('diagnostics')}>
            {authenticated ? (
              <Status tone="good">已连接{active && !streamConnected ? ' · 状态同步中' : ''}</Status>
            ) : (
              <>
                <Unplug size={13} />
                <span>未连接</span>
              </>
            )}
          </button>
        </header>
        {unconfirmed && (
          <div className="notice" role="alert">
            <span>上次发送结果尚未确认，原请求已保留。核对完成前不能发送新的任务。</span>
            <Button disabled={sending} onClick={() => void confirmPending()}>
              核对上次请求
            </Button>
          </div>
        )}
        {(notice || setup.isError || workspaces.isError || models.isError) && (
          <div className="notice" role="alert">
            <span>{notice || errorText(setup.error || workspaces.error || models.error)}</span>
            {setup.isError && (
              <Button variant="ghost" onClick={() => void setup.refetch()}>
                <RefreshCw size={14} />
                重试连接
              </Button>
            )}
            <IconButton icon={X} label="关闭提示" onClick={() => setNotice('')} />
          </div>
        )}
        <main
          id="main-content"
          className={`main-content ${view === 'chat' ? (displayMessages.length || active ? 'conversation-content' : 'chat-content') : ''}`}
        >
          {view === 'chat' &&
            (displayMessages.length || active ? (
              <>
                <div
                  ref={scrollRef}
                  className="messages-scroll"
                  onScroll={(event) => {
                    const el = event.currentTarget
                    follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
                    if (follow.current) setHasNew(false)
                  }}
                >
                  <div className="messages-column">
                    {thread?.parent_thread_id && (
                      <details className="chat-fork-provenance">
                        <summary>从历史消息继续的对话</summary>
                        <p>
                          来源对话 {thread.parent_thread_id} · 截止消息{' '}
                          {thread.fork_message_id ?? '未记录'}
                        </p>
                      </details>
                    )}
                    {messages.isError && (
                      <p role="alert" className="form-error">
                        {errorText(messages.error)}
                      </p>
                    )}
                    {displayMessages
                      .filter((message) => shouldRenderMessage(message, streaming[message.id]))
                      .map((message) => (
                        <article
                          key={message.id}
                          className={`chat-message message-${message.role}`}
                        >
                          <div className="message-author">
                            {message.role === 'user' ? '你' : '工作台'}
                          </div>
                          <div className="message-body">
                            <MessageAttachments message={message} workspaceId={workspaceId} />
                            <MessageContent
                              role={message.role}
                              content={streaming[message.id] ?? message.content}
                            />
                            {message.forkable === true &&
                              message.status === 'completed' &&
                              streaming[message.id] === undefined && (
                                <div className="message-history-action">
                                  <Button
                                    variant="ghost"
                                    disabled={sending || unconfirmed}
                                    onClick={() => setForkSource({ message, threadId })}
                                  >
                                    从这里继续
                                  </Button>
                                </div>
                              )}
                          </div>
                        </article>
                      ))}
                    {lastRun && (
                      <RunPanel
                        key={lastRun.id}
                        run={lastRun}
                        workspaceId={workspaceId}
                        onOpenThread={async (id) => {
                          await threads.refetch()
                          setThreadId(id)
                          navigate('chat')
                        }}
                      />
                    )}
                  </div>
                </div>
                {hasNew && (
                  <button
                    className="new-content-button"
                    onClick={() => {
                      follow.current = true
                      setHasNew(false)
                      scrollRef.current?.scrollTo({
                        top: scrollRef.current.scrollHeight,
                        behavior: 'smooth',
                      })
                    }}
                  >
                    查看新内容
                    <ChevronDown size={14} />
                  </button>
                )}
                <div className="conversation-composer">{composer} </div>
              </>
            ) : (
              <div className="new-chat">
                <div className="greeting">
                  <div className="greeting-mark">
                    <Sparkles size={31} strokeWidth={1.25} />
                  </div>
                  <h1>今天，想完成什么？</h1>
                  <p>从一个想法、一份资料，或一项具体的工作开始。</p>
                </div>
                {composer}
                {authenticated && (!workspace || !effectiveModel) && (
                  <div className="onboarding-link">
                    {!workspace ? (
                      <button onClick={() => setCreatingWorkspace(true)}>创建工作区</button>
                    ) : (
                      <button onClick={() => navigate('connections')}>连接你的模型</button>
                    )}
                  </div>
                )}
              </div>
            ))}
          {view === 'tasks' && (
            <div className="page-content">
              <PageHeading title="后台任务">
                当前会话的任务状态由服务端持续保存，切换页面不会停止运行。
              </PageHeading>
              {runs.data?.length ? (
                <div className="task-list">
                  {runs.data.map((run) => (
                    <div className="task-row" key={run.id}>
                      <div>
                        <h3>{labels[run.status] ?? run.status}</h3>
                        <p>{new Date(run.created_at).toLocaleString()}</p>
                        {run.error && <p className="form-error">{run.error.message}</p>}
                      </div>
                      <div className="button-row">
                        <Button onClick={() => navigate('chat')}>查看对话</Button>
                        {!terminal.has(run.status) && (
                          <Button
                            disabled={stopping || run.status === 'cancelling'}
                            onClick={() => void cancel(run)}
                          >
                            停止任务
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState icon={ListTodo} title="任务会在这里继续">
                  选择已有会话，或创建一次任务后查看真实状态。
                </EmptyState>
              )}
            </div>
          )}
          <div hidden={view !== 'files'}>
            <FilesPage
              workspace={workspace}
              authenticated={authenticated}
              active={view === 'files'}
            />
          </div>
          <div hidden={view !== 'memory'}>
            <MemoryPage
              workspace={workspace}
              authenticated={authenticated}
              active={view === 'memory'}
            />
          </div>
          <div hidden={view !== 'tools'}>
            <ToolsPage
              workspace={workspace}
              authenticated={authenticated}
              active={view === 'tools'}
            />
          </div>
          <div hidden={view !== 'analytics'}>
            <AnalyticsPage
              authenticated={authenticated}
              active={view === 'analytics'}
              workspaces={workspaces.data ?? []}
              models={models.data ?? []}
              onOpenRun={async (call) => {
                try {
                  const run = await api.run(call.run_id)
                  if (!run.thread_id) {
                    setNotice('此调用没有关联对话，可在看板中查看 Run、Step 与 Attempt 追溯标识。')
                    return
                  }
                  setWorkspaceId(call.workspace_id)
                  setThreadId(run.thread_id)
                  navigate('chat')
                } catch (error) {
                  setNotice(errorText(error))
                }
              }}
            />
          </div>
          <div hidden={view !== 'devices'}>
            <AutomationPage
              workspace={workspace}
              authenticated={authenticated}
              active={view === 'devices'}
            />
          </div>
          {authenticated && settingsVisited && (
            <div hidden={view !== 'settings'}>
              <SettingsPage
                userId={session.data?.user.id}
                workspace={workspace}
                models={models.data ?? []}
              />
            </div>
          )}
          {view === 'connections' && (
            <ConnectionsPage
              models={models.data}
              onUpdate={async (id, input) => {
                await api.updateModel(id, input)
                await models.refetch()
              }}
              onCapabilities={async (id, input) => {
                await api.updateCapabilities(id, input)
                await models.refetch()
              }}
              onImport={async (id) => {
                await api.importCapabilities(id)
                await models.refetch()
              }}
              onRefresh={authenticated ? () => void models.refetch() : undefined}
              onCreate={
                authenticated
                  ? async (input) => {
                      await api.createModel(input)
                      await models.refetch()
                    }
                  : undefined
              }
              onCheck={
                authenticated
                  ? async (id) => {
                      await api.checkModel(id)
                      await models.refetch()
                    }
                  : undefined
              }
            />
          )}
          {view === 'diagnostics' && (
            <DiagnosticsPage
              items={statusItems}
              onRefresh={authenticated ? () => void system.refetch() : undefined}
              busy={system.isFetching}
            />
          )}
        </main>
        <div className="global-status">
          <Status tone={active ? 'warning' : 'neutral'}>
            {active ? labels[currentRun.status] : (workspace?.name ?? '尚未选择工作区')}
          </Status>
          <span>
            {active ? '离开页面后任务仍会继续' : (effectiveModel?.name ?? '配置模型后即可开始工作')}
          </span>
        </div>
      </div>
      {pickingAttachments && workspace && effectiveModel && (
        <AttachmentPicker
          key={workspace.id}
          workspaceId={workspace.id}
          modelId={`${effectiveModel.id}:${effectiveModel.version}`}
          items={draftAttachments}
          plans={inputPlan.data ?? []}
          error={inputPlan.error}
          loading={inputPlan.isFetching}
          locked={sending || unconfirmed}
          onChange={(items) => setAttachmentDrafts((old) => ({ ...old, [draftKey]: items }))}
          onClose={() => setPickingAttachments(false)}
        />
      )}
      {creatingWorkspace && (
        <WorkspaceDialog
          onClose={() => setCreatingWorkspace(false)}
          onCreate={async (name, path) => {
            const created = await api.createWorkspace(name, path)
            await workspaces.refetch()
            chooseWorkspace(created.id)
            setCreatingWorkspace(false)
          }}
        />
      )}
      {managingWorkspaces && (
        <WorkspaceManager
          currentId={workspaceId}
          onClose={() => setManagingWorkspaces(false)}
          onChanged={async () => {
            const result = await workspaces.refetch()
            if (result.error) throw result.error
          }}
          onSelect={chooseWorkspace}
        />
      )}
      {forkSource && (
        <ForkDialog
          message={forkSource.message}
          sourceThreadId={forkSource.threadId}
          onClose={() => setForkSource(null)}
          onCreated={async (created) => {
            queryClient.setQueryData<Thread[]>(['threads', created.workspace_id], (old) => [
              ...(old ?? []).filter((item) => item.id !== created.id),
              created,
            ])
            setWorkspaceId(created.workspace_id)
            setThreadId(created.id)
            clearStreaming()
            setForkSource(null)
            setNotice('历史分支已创建；未发送模型请求。')
            navigate('chat')
          }}
        />
      )}
    </div>
  )
}
function WorkspaceDialog({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (name: string, path: string) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <Dialog title="创建工作区" onClose={onClose}>
      <form
        className="stacked-form"
        onSubmit={(event) => {
          event.preventDefault()
          setBusy(true)
          setError('')
          void onCreate(name.trim(), path.trim())
            .catch((err) => setError(errorText(err)))
            .finally(() => setBusy(false))
        }}
      >
        <label>
          工作区名称
          <input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="为这个项目命名"
          />
        </label>
        <label>
          项目目录
          <input
            required
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="输入服务所在机器的现有绝对路径"
          />
        </label>
        <p className="page-note">目录必须真实存在。工作区只在授权的目录范围内工作。</p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="button-row">
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? '正在创建…' : '创建工作区'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
