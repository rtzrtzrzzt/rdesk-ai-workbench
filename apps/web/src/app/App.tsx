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
  Settings,
  SlidersHorizontal,
  Sparkles,
  Unplug,
  Wrench,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { api } from '../api/workbench'
import { conversationsApi } from '../api/conversations'
import { QueuePanel } from '../features/chat/QueuePanel'
import { ThreadMenu } from '../features/chat/ThreadMenu'
import { settingsApi } from '../api/settings'
import type {
  Identity,
  Message,
  Run,
  Thread,
  Workspace,
  AttachmentInput,
  RunOptions,
  RunCreate,
  AgentTree,
  CommandView,
  DeletionPreview,
} from '../api/types'
import { ApiError, authGeneration } from '../api/client'
import { Button, EmptyState, IconButton, PageHeading, Status } from '../components/ui'
import { Dialog } from '../components/Dialog'
import { filesApi } from '../api/files'
import {
  AttachmentList,
  AttachmentPicker,
  AttachmentUploads,
  MessageAttachments,
} from '../features/chat/Attachments'
import type { DraftAttachment } from '../features/chat/Attachments'
import { useAttachmentUploads } from '../features/chat/useAttachmentUploads'
import { Composer } from '../features/chat/Composer'
import { ReasoningEffortSelect } from '../features/chat/ReasoningEffortSelect'
import type { ReasoningEffort } from '../features/chat/ReasoningEffortSelect'
import { ContextUsage } from '../features/chat/ContextUsage'
import { MessageContent, shouldRenderMessage } from '../features/chat/MessageContent'
import { RunDetails, RunPanel } from '../features/chat/RunPanel'
import { ConversationTimeline } from '../features/chat/ConversationTimeline'
import { Thinking } from '../features/chat/Thinking'
import { useSteering } from '../features/chat/useSteering'
import { useRunStream } from '../features/chat/useRunStream'
import {
  runTerminal as terminal,
  acceptsInput,
  runStatusLabel,
  threadStatusLabel,
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
import { DirectoryPicker } from '../features/workspaces/DirectoryPicker'
import { ForkDialog } from '../features/chat/ForkDialog'
import { ConversationExport } from '../features/chat/ConversationExport'
import { useAccountActivity } from '../features/settings/useAccountActivity'
import '../features/chat/conversation.css'
import '../features/chat/activity.css'
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
  const [inputModes, setInputModes] = useState<Record<string, 'steer' | 'queue'>>({})
  const [defaultInputMode, setDefaultInputMode] = useState<'queue' | 'steer'>(() =>
    localStorage.getItem('workbench.input-mode') === 'steer' ? 'steer' : 'queue',
  )
  const [workspaceId, setWorkspaceId] = useState(
    () => localStorage.getItem('workbench.workspace') ?? '',
  )
  const [threadId, setThreadId] = useState(() => localStorage.getItem('workbench.thread') ?? '')
  const [modelId, setModelId] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [attachmentDrafts, setAttachmentDrafts] = useState<Record<string, DraftAttachment[]>>({})
  const attachmentUploads = useAttachmentUploads((artifact, target) => {
    setAttachmentDrafts((old) => ({
      ...old,
      [target]: [
        ...(old[target] ?? []),
        { artifact_id: artifact.id, filename: artifact.filename, consent_remote: false },
      ],
    }))
    void queryClient.invalidateQueries({ queryKey: ['artifacts', artifact.workspace_id] })
  })
  const [taskOptions, setTaskOptions] = useState<Record<string, RunOptions | null>>({})
  const [reasoningEfforts, setReasoningEfforts] = useState<
    Record<string, Record<string, ReasoningEffort | null>>
  >({})
  const [pickingAttachments, setPickingAttachments] = useState(false)
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [notice, setNotice] = useState('')
  const [unconfirmed, setUnconfirmed] = useState(false)
  const [disconnected, setDisconnected] = useState(false)
  const [authBusy, setAuthBusy] = useState(false)
  const [authError, setAuthError] = useState('')
  const [creatingWorkspace, setCreatingWorkspace] = useState(false)
  const [managingWorkspaces, setManagingWorkspaces] = useState(false)
  const [forkSource, setForkSource] = useState<{ message: Message; threadId: string } | null>(null)
  const [editingMessage, setEditingMessage] = useState<Message | null>(null)
  const [editText, setEditText] = useState('')
  const [editingBusy, setEditingBusy] = useState(false)
  const [editError, setEditError] = useState('')
  const [editUnconfirmed, setEditUnconfirmed] = useState(false)
  const editRequest = useRef<{
    key: string
    id: string
    threadId: string
    messageId: string
    payload: RunCreate
  } | null>(null)
  const [hasNew, setHasNew] = useState(false)
  const pendingSend = useRef<{
    input: string
    modelId: string | null
    expectedModel: { connection_id: string; version: number }
    threadId: string
    requestId: string
    attachments: AttachmentInput[]
    options: RunOptions | null
    reasoningEffort: ReasoningEffort | null
    draftKey: string
  } | null>(null)
  const knownThreadLists = useRef(new Map<string, Thread[]>())
  const knownRunThreads = useRef(new Map<string, string>())
  const conversationUi = useRef<{
    threadId: string
    editingMessage: Message | null
    runId?: string
  }>({ threadId, editingMessage })
  const cancelIds = useRef<Record<string, string>>({})
  const scrollRef = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const session = useQuery({
    queryKey: ['session'],
    queryFn: async ({ signal }) => {
      try {
        return await api.session(signal)
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return api.bootstrap()
        throw error
      }
    },
    retry: false,
    enabled: !disconnected,
  })
  const authenticated =
    !disconnected &&
    !!session.data &&
    !(session.error instanceof ApiError && session.error.status === 401)
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
    queryFn: async () => {
      const listed = await api.threads(workspaceId)
      // Compare only successful server lists from the same workspace. Local cache
      // insertions and the first load are not evidence of permanent deletion.
      const previous = knownThreadLists.current.get(workspaceId)
      knownThreadLists.current.set(workspaceId, listed)
      if (previous) {
        const existing = new Set(listed.map((item) => item.id))
        const removed = previous.filter((item) => !existing.has(item.id))
        if (removed.length)
          await clearDeletedConversations({
            thread_ids: removed.map((item) => item.id),
            run_ids: removed.flatMap((item) => (item.latest_run_id ? [item.latest_run_id] : [])),
          })
      }
      return listed
    },
    enabled: authenticated && !!workspace,
    refetchInterval: 4000,
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
  conversationUi.current = { threadId, editingMessage, runId: lastRun?.id }
  const {
    connected: streamConnected,
    streaming,
    reasoning,
    clear: clearStreaming,
    disconnect: disconnectStream,
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
  const draftUploads = attachmentUploads.items.filter((item) => item.draftKey === draftKey)
  const steering = useSteering(lastRun?.id, refreshRun)
  const canSteer =
    !!currentRun && (acceptsInput(currentRun.status) || currentRun.status === 'paused')
  const steeringMode = active && (inputModes[draftKey] ?? defaultInputMode) === 'steer'
  const selectedEffort = reasoningEfforts[draftKey]?.[effectiveModel?.id ?? '']
  const previousEffort =
    lastRun?.model_connection_id === effectiveModel?.id ? lastRun?.reasoning_effort : null
  const requestedEffort = selectedEffort === undefined ? previousEffort : selectedEffort
  const reasoningEffort =
    requestedEffort && effectiveModel?.reasoning_efforts.includes(requestedEffort)
      ? requestedEffort
      : null
  const composerModel = steeringMode
    ? models.data?.find((model) => model.id === lastRun?.model_connection_id)
    : effectiveModel
  const visibleThreads = (threads.data ?? []).filter(
    (item) =>
      !item.is_subagent &&
      item.title.toLocaleLowerCase().includes(threadFilter.trim().toLocaleLowerCase()),
  )
  const attachmentIds = draftAttachments.map((item) => item.artifact_id)
  const preparedAttachments = useQuery({
    queryKey: [
      'attachment-preparation',
      workspaceId,
      effectiveModel?.id,
      effectiveModel?.version,
      attachmentIds,
    ],
    queryFn: () => filesApi.prepare(workspaceId, effectiveModel?.id ?? '', attachmentIds),
    enabled: authenticated && !!workspace && !!effectiveModel && attachmentIds.length > 0,
    refetchInterval: (query) =>
      query.state.data?.some((item) => item.status === 'preparing') ? 1000 : false,
  })
  const attachmentSnapshot: AttachmentInput[] = draftAttachments.flatMap((item) => {
    const plan = preparedAttachments.data?.find((p) => p.artifact_id === item.artifact_id)
    const attachment = plan?.attachment
    return plan &&
      attachment &&
      plan.status === 'ready' &&
      (!plan.remote ||
        (item.consent_remote &&
          item.consent_model === `${effectiveModel?.id}:${effectiveModel?.version}` &&
          item.consent_version === attachment.version))
      ? [
          {
            ...attachment,
            consent_remote:
              plan.remote &&
              item.consent_remote &&
              item.consent_model === `${effectiveModel?.id}:${effectiveModel?.version}` &&
              item.consent_version === attachment.version,
          },
        ]
      : []
  })
  const attachmentsReady =
    !draftUploads.length &&
    (!draftAttachments.length ||
      (!preparedAttachments.isError &&
        attachmentSnapshot.length === draftAttachments.length &&
        attachmentSnapshot.filter((a) => a.mode === 'native_image').length <= 2))

  function uploadAttachments(files: File[]) {
    if (!workspace || steeringMode || sending || unconfirmed) return
    const supportedSize = files.filter((file) => file.size <= 1024 ** 3)
    const rejected = attachmentUploads.add(
      supportedSize,
      workspace.id,
      draftKey,
      draftAttachments.length,
    )
    if (supportedSize.length !== files.length)
      setNotice('单文件最多 1 GB，超过大小限制的文件尚未上传。')
    else if (rejected) setNotice('每条消息最多添加 8 个附件，超出部分尚未上传。')
  }

  useEffect(() => {
    for (const run of runs.data ?? [])
      if (run.thread_id) knownRunThreads.current.set(run.id, run.thread_id)
  }, [runs.data])
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
    if (thread?.is_subagent) {
      setThreadId(thread.agent_parent_thread_id ?? '')
      clearStreaming()
    }
  }, [thread?.id, thread?.is_subagent, thread?.agent_parent_thread_id])
  useEffect(() => {
    if (threads.data && threadId && !thread && !threads.isFetching) {
      setThreadId('')
      clearStreaming()
    }
  }, [threads.data, threadId, thread, threads.isFetching])
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
  }, [messages.data, streaming, reasoning])
  function navigate(next: View) {
    if (next === 'settings') setSettingsVisited(true)
    setView(next)
    setMobileSidebar(false)
  }
  function chooseWorkspace(id: string) {
    const current = queryClient.getQueryData<Workspace[]>(['workspaces']) ?? []
    if (!current.some((item) => item.id === id && !item.archived && !item.removed_at)) return
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
  async function authenticate() {
    setAuthBusy(true)
    setAuthError('')
    try {
      queryClient.setQueryData<Identity>(['session'], await api.bootstrap())
      setDisconnected(false)
    } catch (error) {
      setAuthError(errorText(error))
    } finally {
      setAuthBusy(false)
    }
  }
  async function sendSteering() {
    if (thread?.archived || thread?.deleting || unconfirmed) return
    const text = steering.pending?.input ?? draft
    const key = draftKey
    const accepted = await steering.send(draft)
    if (accepted) {
      setDrafts((old) => ({ ...old, [key]: old[key]?.trim() === text.trim() ? '' : old[key] }))
      setInputModes((old) => {
        const next = { ...old }
        delete next[key]
        return next
      })
    }
  }
  async function editMessage() {
    if (!editingMessage || editingBusy) return
    if (!editUnconfirmed) {
      if (!thread || !effectiveModel || !editText.trim() || !optionsReady) return
      const key = `${thread.id}:${editingMessage.id}:${editText.trim()}:${effectiveModel.id}:${effectiveModel.version}:${reasoningEffort}:${JSON.stringify(options)}`
      if (editRequest.current?.key !== key) {
        const requestId = crypto.randomUUID()
        editRequest.current = {
          key,
          id: requestId,
          threadId: thread.id,
          messageId: editingMessage.id,
          payload: {
            input: editText.trim(),
            request_id: requestId,
            model_connection_id: modelId || null,
            reasoning_effort: reasoningEffort,
            expected_model: { connection_id: effectiveModel.id, version: effectiveModel.version },
            options,
          },
        }
      }
    }
    const command = editRequest.current
    if (!command) return
    setEditingBusy(true)
    setEditError('')
    try {
      const result = await conversationsApi.edit(
        command.threadId,
        command.messageId,
        command.payload,
      )
      setEditUnconfirmed(false)
      await threads.refetch()
      setThreadId(result.thread.id)
      clearStreaming()
      setEditingMessage(null)
      setEditText('')
      editRequest.current = null
      setNotice('已创建新分支并重新生成，原对话已保留。')
    } catch (error) {
      const uncertain = uncertainRequest(error)
      setEditUnconfirmed(uncertain)
      setEditError(
        `${errorText(error)}${uncertain ? ' 请求结果尚未确认，请核对同一次编辑后再修改内容。' : ''}`,
      )
    } finally {
      setEditingBusy(false)
    }
  }
  async function send() {
    if (
      sending ||
      !workspace ||
      !effectiveModel ||
      !draft.trim() ||
      !!thread?.archived ||
      !!thread?.deleting ||
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
        setReasoningEfforts((old) => ({
          ...old,
          [selectedThread!.id]: old[draftKey] ?? {},
        }))
      }
      const prior = pendingSend.current
      if (
        !prior ||
        prior.input !== draft.trim() ||
        prior.modelId !== (modelId || null) ||
        prior.expectedModel.connection_id !== effectiveModel.id ||
        prior.expectedModel.version !== effectiveModel.version ||
        prior.reasoningEffort !== reasoningEffort ||
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
          reasoningEffort,
          draftKey,
        }
      const request = pendingSend.current!
      await conversationsApi.enqueue(request.threadId, {
        input: request.input,
        model_connection_id: request.modelId,
        request_id: request.requestId,
        attachments: request.attachments,
        options: request.options,
        expected_model: request.expectedModel,
        reasoning_effort: request.reasoningEffort,
      })
      if (generation !== authGeneration()) return
      pendingSend.current = null
      setUnconfirmed(false)
      setInputModes((old) => {
        const next = { ...old }
        delete next[draftKey]
        delete next[selectedThread!.id]
        return next
      })
      setAttachmentDrafts((old) => ({ ...old, [draftKey]: [], [selectedThread!.id]: [] }))
      setDrafts((old) => ({ ...old, [draftKey]: '', [selectedThread!.id]: '' }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['queue', selectedThread.id] }),
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
      await conversationsApi.enqueue(pending.threadId, {
        input: pending.input,
        model_connection_id: pending.modelId,
        request_id: pending.requestId,
        attachments: pending.attachments,
        options: pending.options,
        expected_model: pending.expectedModel,
        reasoning_effort: pending.reasoningEffort,
      })
      if (generation !== authGeneration()) return
      pendingSend.current = null
      setUnconfirmed(false)
      setNotice('上次请求已确认。')
      setInputModes((old) => {
        const next = { ...old }
        delete next[pending.draftKey]
        delete next[pending.threadId]
        return next
      })
      setAttachmentDrafts((old) => ({ ...old, [pending.draftKey]: [], [pending.threadId]: [] }))
      setDrafts((old) => ({
        ...old,
        [pending.threadId]:
          old[pending.threadId]?.trim() === pending.input ? '' : (old[pending.threadId] ?? ''),
      }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['queue', pending.threadId] }),
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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['runs', run.thread_id] }),
        queryClient.invalidateQueries({ queryKey: ['queue', run.thread_id] }),
      ])
    } catch (error) {
      if (generation === authGeneration()) setNotice(errorText(error))
    } finally {
      if (generation === authGeneration()) setStopping(false)
    }
  }
  async function clearDeletedConversations(scope: Pick<DeletionPreview, 'thread_ids' | 'run_ids'>) {
    const threadIds = new Set(scope.thread_ids)
    const runIds = new Set(scope.run_ids)
    for (const [runId, ownerThread] of knownRunThreads.current)
      if (threadIds.has(ownerThread)) runIds.add(runId)
    const draftKeys = new Set(threadIds)
    if (pendingSend.current && threadIds.has(pendingSend.current.threadId))
      draftKeys.add(pendingSend.current.draftKey)
    for (const id of threadIds) {
      queryClient.getQueryData<Run[]>(['runs', id])?.forEach((run) => runIds.add(run.id))
      queryClient.getQueryData<Message[]>(['messages', id])?.forEach((message) => {
        if (message.run_id) runIds.add(message.run_id)
      })
    }
    const trees = queryClient.getQueriesData<AgentTree>({ queryKey: ['run-agents'] })
    for (const [, tree] of trees) {
      if (!tree) continue
      for (const run of [tree.root, ...tree.agents.map((agent) => agent.run)])
        if (run.thread_id && threadIds.has(run.thread_id)) runIds.add(run.id)
    }
    const commandIds = new Set<string>()
    for (const [, commands] of queryClient.getQueriesData<CommandView[]>({
      queryKey: ['commands'],
    }))
      commands?.forEach((command) => {
        if (runIds.has(command.run_id)) commandIds.add(command.id)
      })
    for (const [, command] of queryClient.getQueriesData<CommandView>({ queryKey: ['command'] }))
      if (command && runIds.has(command.run_id)) commandIds.add(command.id)
    const aggregateTrees = new Set(
      trees
        .filter(
          ([, tree]) =>
            tree &&
            [tree.root, ...tree.agents.map((agent) => agent.run)].some((run) => runIds.has(run.id)),
        )
        .map(([key]) => JSON.stringify(key)),
    )
    const threadKeys = new Set([
      'messages',
      'runs',
      'queue',
      'thread-deletion',
      'thread-activity',
      'tool-details',
    ])
    const runKeys = new Set(['run-trace', 'run-agents', 'run-surfaces', 'steering-inputs'])
    const matching = {
      predicate: (query: { queryKey: readonly unknown[] }) => {
        const [kind, id, commandId] = query.queryKey
        return (
          typeof kind === 'string' &&
          ((threadKeys.has(kind) && typeof id === 'string' && threadIds.has(id)) ||
            (runKeys.has(kind) && typeof id === 'string' && runIds.has(id)) ||
            (kind === 'run-agents' && aggregateTrees.has(JSON.stringify(query.queryKey))) ||
            ((kind === 'command' || kind === 'command-output') &&
              typeof commandId === 'string' &&
              commandIds.has(commandId)))
        )
      },
    }
    if (
      threadIds.has(conversationUi.current.threadId) ||
      (conversationUi.current.runId && runIds.has(conversationUi.current.runId))
    ) {
      disconnectStream()
      setThreadId('')
      clearStreaming()
      setPickingAttachments(false)
    }
    const discard = <T,>(old: Record<string, T>) =>
      Object.fromEntries(Object.entries(old).filter(([id]) => !draftKeys.has(id)))
    setDrafts(discard)
    setAttachmentDrafts(discard)
    setTaskOptions(discard)
    setReasoningEfforts(discard)
    setInputModes(discard)
    steering.forget([...runIds])
    if (pendingSend.current && threadIds.has(pendingSend.current.threadId)) {
      pendingSend.current = null
      setUnconfirmed(false)
    }
    if (
      conversationUi.current.editingMessage &&
      threadIds.has(conversationUi.current.editingMessage.thread_id)
    ) {
      setEditingMessage(null)
      setEditText('')
      setEditError('')
      setEditUnconfirmed(false)
      editRequest.current = null
    }
    if (editRequest.current && threadIds.has(editRequest.current.threadId)) {
      editRequest.current = null
      setEditText('')
      setEditError('')
      setEditUnconfirmed(false)
    }
    setForkSource((old) => (old && threadIds.has(old.threadId) ? null : old))
    for (const id of runIds) {
      delete cancelIds.current[id]
      knownRunThreads.current.delete(id)
    }
    for (const [workspaceKey, items] of knownThreadLists.current)
      knownThreadLists.current.set(
        workspaceKey,
        items.filter((item) => !threadIds.has(item.id)),
      )
    // Cancel first so an already-running query cannot refill a removed body.
    await queryClient.cancelQueries(matching)
    queryClient.removeQueries(matching)
    queryClient.setQueriesData<CommandView[]>({ queryKey: ['commands'] }, (old) =>
      old?.filter((command) => !commandIds.has(command.id)),
    )
  }
  async function refreshRun() {
    await queryClient.invalidateQueries({ queryKey: ['runs', threadId] })
  }
  async function logout() {
    try {
      await api.logout()
      setDisconnected(true)
      queryClient.clear()
      knownThreadLists.current.clear()
      knownRunThreads.current.clear()
      setThreadId('')
      setDrafts({})
      setAttachmentDrafts({})
      setTaskOptions({})
      setReasoningEfforts({})
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
  if (session.isPending && !disconnected)
    return (
      <main className="app-loading">
        <Sparkles size={27} />
        <span>正在连接本机工作台…</span>
      </main>
    )
  if (!authenticated)
    return (
      <AuthView
        busy={authBusy}
        error={authError || (session.error ? errorText(session.error) : undefined)}
        onSubmit={() => void authenticate()}
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
              ? draftUploads.length
                ? draftUploads.some((item) => item.status === 'failed')
                  ? '附件上传未完成，请重试或移除失败的文件。'
                  : '附件正在上传，完成后即可发送。'
                : preparedAttachments.data?.some((item) => item.remote && item.attachment)
                  ? '请确认允许将附件发送给当前远程模型。'
                  : '附件正在自动准备；若提示错误，可重试或移除该文件。'
              : thread?.deleting
                ? '对话正在删除，暂时无法发送。'
                : thread?.archived
                  ? '归档对话恢复后可发送；恢复不会自动运行队列。'
                  : undefined
  const loadedMessages = messages.data ?? []
  const displayMessages: Message[] = [
    ...loadedMessages,
    ...[...new Set([...Object.keys(streaming), ...Object.keys(reasoning)])]
      .filter((id) => !loadedMessages.some((message) => message.id === id))
      .map((id) => ({
        id,
        content: streaming[id] ?? '',
        reasoning: reasoning[id] ?? '',
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
        <div className="composer-mode-row">
          <div className="segmented" role="group" aria-label="输入用途">
            <button
              aria-pressed={!steeringMode}
              onClick={() => setInputModes((old) => ({ ...old, [draftKey]: 'queue' }))}
            >
              Queue · 下一条消息
            </button>
            <button
              aria-pressed={steeringMode}
              disabled={!active}
              onClick={() => setInputModes((old) => ({ ...old, [draftKey]: 'steer' }))}
            >
              Steer · 补充当前任务
            </button>
          </div>
          <label className="composer-default-mode">
            运行时默认
            <select
              aria-label="运行时默认输入模式"
              value={defaultInputMode}
              onChange={(event) => {
                const value = event.target.value as 'queue' | 'steer'
                setDefaultInputMode(value)
                localStorage.setItem('workbench.input-mode', value)
                setInputModes((old) => ({ ...old, [draftKey]: value }))
              }}
            >
              <option value="queue">Queue</option>
              <option value="steer">Steer</option>
            </select>
          </label>
          <ContextUsage
            key={`${thread?.id ?? 'new'}:${effectiveModel?.id ?? ''}`}
            threadId={authenticated ? thread?.id : undefined}
            modelId={effectiveModel?.id}
            contextWindow={effectiveModel?.context_window}
            active={active}
            disabledReason={
              thread?.archived || thread?.deleting ? '归档或删除中的对话暂不可压缩。' : undefined
            }
          />
          {steeringMode && (
            <p className="page-note">
              {currentRun?.status === 'paused'
                ? '补充要求会先保存；继续任务后的下一次模型调用时生效。'
                : '生成中会尽快按新要求调整；已开始的工具操作会先执行到安全边界。'}
            </p>
          )}
          {steering.notice && (
            <p className="page-note" role="status">
              {steering.notice}
            </p>
          )}
          {steering.pending && (
            <Button disabled={steering.busy} onClick={() => void sendSteering()}>
              核对本次补充要求
            </Button>
          )}
          {steering.deliveries.slice(-3).map((item) => (
            <p className="steering-delivery" role="status" key={item.id}>
              {item.status === 'delivered'
                ? '已生效'
                : item.status === 'applying'
                  ? '正在按新要求处理'
                  : '已接收 · 正在安排调整'}
              ：{item.input}
            </p>
          ))}
          {steering.deliveryError && (
            <p className="page-note" role="status">
              补充要求投递状态暂时无法读取，将自动重试。
            </p>
          )}
        </div>
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
      onAttachments={workspace ? () => setPickingAttachments(true) : undefined}
      onFiles={workspace ? uploadAttachments : undefined}
      attachmentsDisabled={steeringMode || sending || unconfirmed}
      attachmentSummary={
        !steeringMode && (draftAttachments.length || draftUploads.length) ? (
          <div className="composer-attachments">
            <AttachmentList
              modelId={effectiveModel ? `${effectiveModel.id}:${effectiveModel.version}` : ''}
              items={draftAttachments}
              preparations={preparedAttachments.data ?? []}
              error={preparedAttachments.error}
              locked={sending || unconfirmed}
              onChange={(items) =>
                setAttachmentDrafts((old) => ({
                  ...old,
                  [draftKey]: typeof items === 'function' ? items(old[draftKey] ?? []) : items,
                }))
              }
              onRetry={() => void preparedAttachments.refetch()}
            />
            <AttachmentUploads
              items={draftUploads}
              onRetry={attachmentUploads.retry}
              onRemove={attachmentUploads.remove}
            />
          </div>
        ) : undefined
      }
      value={draft}
      onChange={(value) => setDrafts((old) => ({ ...old, [draftKey]: value }))}
      onSend={() => void (steeringMode ? sendSteering() : send())}
      disabledReason={
        steeringMode
          ? unconfirmed
            ? '上次消息发送尚未确认，请先核对。'
            : thread?.archived || thread?.deleting
              ? '归档或删除中的对话无法补充要求。'
              : !authenticated
                ? '连接本机会话后可补充要求。'
                : !canSteer && !steering.pending
                  ? '当前任务暂不接受补充，输入内容已保留。'
                  : undefined
          : disabledReason
      }
      readOnly={sending || unconfirmed || steering.busy}
      sending={steeringMode ? steering.busy : sending}
      running={active}
      sendLabel={
        steeringMode ? (steering.pending ? '核对本次补充要求' : '发送补充要求') : '加入消息队列'
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
      reasoningControl={
        <ReasoningEffortSelect
          supported={composerModel?.reasoning_efforts ?? []}
          value={steeringMode ? (lastRun?.reasoning_effort ?? null) : reasoningEffort}
          disabled={steeringMode || sending || unconfirmed}
          steering={steeringMode}
          onChange={(value) => {
            if (!effectiveModel) return
            setReasoningEfforts((old) => ({
              ...old,
              [draftKey]: { ...old[draftKey], [effectiveModel.id]: value },
            }))
          }}
        />
      }
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
              {visibleThreads
                .filter((item) => !item.archived)
                .map((item) => (
                  <div className="thread-list-row" key={item.id}>
                    <button
                      data-thread-id={item.id}
                      aria-current={threadId === item.id && view === 'chat' ? 'page' : undefined}
                      className={threadId === item.id && view === 'chat' ? 'selected' : ''}
                      onClick={() => chooseThread(item)}
                      title={item.title}
                    >
                      <span>{item.title || '新对话'}</span>
                      <small>
                        {threadStatusLabel(item)}
                        {item.queue_count ? ` · ${item.queue_count} 条排队` : ''}
                      </small>
                    </button>
                    <ThreadMenu
                      thread={item}
                      onDeleted={clearDeletedConversations}
                      onChanged={async () => {
                        await threads.refetch()
                        await queryClient.invalidateQueries({ queryKey: ['runs'] })
                      }}
                    />
                  </div>
                ))}
              <details className="archived-threads">
                <summary>已归档（{visibleThreads.filter((item) => item.archived).length}）</summary>
                {visibleThreads
                  .filter((item) => item.archived)
                  .map((item) => (
                    <div className="thread-list-row" key={item.id}>
                      <button data-thread-id={item.id} onClick={() => chooseThread(item)}>
                        <span>{item.title}</span>
                        <small>{item.deleting ? '删除处理中' : '已归档'}</small>
                      </button>
                      <ThreadMenu
                        thread={item}
                        onDeleted={clearDeletedConversations}
                        onChanged={async () => {
                          await threads.refetch()
                        }}
                      />
                    </div>
                  ))}
              </details>
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
            <button className="account-button" title="断开本机会话" onClick={() => void logout()}>
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
            {view === 'chat' && thread && (
              <>
                <ConversationExport thread={thread} />
                <ThreadMenu
                  thread={thread}
                  onDeleted={clearDeletedConversations}
                  onChanged={async () => {
                    await threads.refetch()
                    await queryClient.invalidateQueries({ queryKey: ['runs'] })
                  }}
                />
              </>
            )}
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
        {(notice || workspaces.isError || models.isError) && (
          <div className="notice" role="alert">
            <span>{notice || errorText(workspaces.error || models.error)}</span>
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
                    <ConversationTimeline
                      key={threadId}
                      threadId={threadId}
                      messages={displayMessages}
                      runs={orderedRuns}
                      renderMessage={(message) =>
                        shouldRenderMessage(
                          message,
                          streaming[message.id],
                          reasoning[message.id],
                        ) ? (
                          <article
                            key={message.id}
                            className={`chat-message message-${message.role}`}
                          >
                            {message.role === 'user' ? (
                              <div className="message-author">你</div>
                            ) : ['superseded', 'interrupted'].includes(message.status) ? (
                              <div className="message-author">已保留部分输出</div>
                            ) : null}
                            <div className="message-body">
                              <MessageAttachments message={message} workspaceId={workspaceId} />
                              <Thinking
                                text={reasoning[message.id] ?? message.reasoning ?? ''}
                                active={
                                  message.status === 'streaming' &&
                                  !(streaming[message.id] ?? message.content).trim() &&
                                  orderedRuns.some(
                                    (run) => run.id === message.run_id && run.status === 'running',
                                  )
                                }
                                workspaceId={workspaceId}
                              />
                              <MessageContent
                                workspaceId={workspaceId}
                                role={message.role}
                                content={streaming[message.id] ?? message.content}
                              />
                              {message.role === 'user' && message.status === 'completed' && (
                                <div className="message-history-action">
                                  <Button
                                    variant="ghost"
                                    disabled={
                                      sending ||
                                      unconfirmed ||
                                      !!thread?.archived ||
                                      !!thread?.deleting ||
                                      !effectiveModel
                                    }
                                    onClick={() => {
                                      setEditingMessage(message)
                                      setEditText(message.content)
                                      setEditError('')
                                      setEditUnconfirmed(false)
                                    }}
                                  >
                                    编辑并创建分支
                                  </Button>
                                </div>
                              )}
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
                        ) : null
                      }
                      renderRun={(run) => (
                        <RunPanel key={run.id} run={run} workspaceId={workspaceId} />
                      )}
                    />
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
                <div className="conversation-composer">
                  {thread && (
                    <QueuePanel
                      key={thread.id}
                      threadId={thread.id}
                      archived={thread.archived || thread.deleting}
                    />
                  )}{' '}
                  {composer}{' '}
                </div>
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
                {thread && (
                  <QueuePanel
                    key={thread.id}
                    threadId={thread.id}
                    archived={thread.archived || thread.deleting}
                  />
                )}
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
                    <div className="task-record" key={run.id}>
                      <div className="task-row">
                        <div>
                          <h3>{runStatusLabel(run)}</h3>
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
                      <RunDetails run={run} workspaceId={workspaceId} />
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
            {active ? runStatusLabel(currentRun) : (workspace?.name ?? '尚未选择工作区')}
          </Status>
          <span>
            {active
              ? currentRun.status === 'paused' || currentRun.status === 'held'
                ? '任务已暂停，确认后继续'
                : '离开页面后任务仍会继续'
              : (effectiveModel?.name ?? '配置模型后即可开始工作')}
          </span>
        </div>
      </div>
      {pickingAttachments && workspace && (
        <AttachmentPicker
          key={workspace.id}
          workspaceId={workspace.id}
          modelId={effectiveModel ? `${effectiveModel.id}:${effectiveModel.version}` : ''}
          items={draftAttachments}
          preparations={preparedAttachments.data ?? []}
          error={preparedAttachments.error}
          locked={sending || unconfirmed}
          onChange={(items) =>
            setAttachmentDrafts((old) => ({
              ...old,
              [draftKey]: typeof items === 'function' ? items(old[draftKey] ?? []) : items,
            }))
          }
          maxItems={Math.max(0, 8 - draftUploads.length)}
          onFiles={uploadAttachments}
          onRetry={() => void preparedAttachments.refetch()}
          onClose={() => setPickingAttachments(false)}
        />
      )}
      {creatingWorkspace && (
        <WorkspaceDialog
          onClose={() => setCreatingWorkspace(false)}
          onCreate={async (name, path) => {
            const created = await api.createWorkspace(name, path)
            queryClient.setQueryData<Workspace[]>(['workspaces'], (old) => [
              ...(old ?? []).filter((item) => item.id !== created.id),
              created,
            ])
            chooseWorkspace(created.id)
            setCreatingWorkspace(false)
            void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
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
      {editingMessage && (
        <Dialog
          title="编辑消息并创建新分支"
          onClose={() => {
            if (!editingBusy && !editUnconfirmed) {
              setEditingMessage(null)
              setEditText('')
              setEditError('')
              editRequest.current = null
            }
          }}
        >
          <form
            className="stacked-form"
            onSubmit={(event) => {
              event.preventDefault()
              void editMessage()
            }}
          >
            <p>原对话与消息会保留。新分支将从此处使用修改后的内容重新生成。</p>
            <label>
              消息内容
              <textarea
                rows={8}
                maxLength={1048576}
                required
                readOnly={editingBusy || editUnconfirmed}
                value={editText}
                onChange={(event) => setEditText(event.target.value)}
              />
            </label>
            {editingMessage.attachments.length > 0 && (
              <p className="page-note">
                来源消息的附件由服务端保留，新分支继续沿用附件版本与授权。
              </p>
            )}
            {editError && (
              <p role="alert" className="form-error">
                {editError}
              </p>
            )}
            <div className="button-row">
              <Button
                disabled={editingBusy || editUnconfirmed}
                onClick={() => {
                  setEditingMessage(null)
                  setEditText('')
                  setEditError('')
                  editRequest.current = null
                }}
              >
                取消
              </Button>
              <Button
                type="submit"
                disabled={
                  editingBusy ||
                  (!editUnconfirmed && (!editText.trim() || !effectiveModel || !optionsReady))
                }
              >
                {editingBusy
                  ? '正在创建…'
                  : editUnconfirmed
                    ? '核对本次编辑请求'
                    : '创建分支并重新生成'}
              </Button>
            </div>
          </form>
        </Dialog>
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
  const [choosingDirectory, setChoosingDirectory] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return (
    <Dialog title="创建工作区" onClose={onClose}>
      <form
        className="stacked-form"
        onSubmit={(event) => {
          event.preventDefault()
          if (busy || !name.trim() || !path || choosingDirectory) return
          setBusy(true)
          setError('')
          void onCreate(name.trim(), path)
            .catch((err) => setError(errorText(err)))
            .finally(() => setBusy(false))
        }}
      >
        <label>
          工作区名称
          <input
            required
            maxLength={160}
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
            placeholder="为这个项目命名"
          />
        </label>
        <div className="workspace-directory-field">
          <span>项目目录</span>
          {path && <output aria-label="已选项目目录">{path}</output>}
          <Button disabled={busy} onClick={() => setChoosingDirectory(!choosingDirectory)}>
            {choosingDirectory ? '收起文件夹' : path ? '更换文件夹' : '选择文件夹'}
          </Button>
        </div>
        {choosingDirectory && (
          <DirectoryPicker
            initialPath={path || undefined}
            onSelect={(selected) => {
              setPath(selected)
              if (!name.trim())
                setName(selected.split(/[\\/]/).filter(Boolean).at(-1)?.slice(0, 160) ?? '工作区')
              setChoosingDirectory(false)
            }}
            onCancel={() => setChoosingDirectory(false)}
          />
        )}
        <p className="page-note">选择工作台所在电脑上的项目文件夹。</p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="button-row">
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button type="submit" variant="primary" disabled={busy || !path || choosingDirectory}>
            {busy ? '正在创建…' : '创建工作区'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
