import { advanceAuthGeneration, authGeneration, request, setCsrfToken } from './client'
import type {
  Approval,
  Identity,
  Message,
  ModelConnection,
  ModelInput,
  ModelUpdate,
  CapabilitiesUpdate,
  AttachmentInput,
  ActivityPage,
  ActivityDetails,
  RunOptions,
  RunTrace,
  AgentTree,
  SteerRun,
  InputReceived,
  Question,
  QuestionAnswer,
  Run,
  RunEvent,
  SetupStatus,
  SystemStatus,
  Thread,
  ThreadFork,
  Workspace,
} from './types'
const json = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
async function bootstrap() {
  advanceAuthGeneration()
  const result = await request<Identity>('/auth/bootstrap', json({}))
  setCsrfToken(result.csrf_token)
  return result
}
export const api = {
  setupStatus: () => request<SetupStatus>('/setup/status'),
  bootstrap,
  session: async (signal?: AbortSignal) => {
    const generation = authGeneration()
    const result = await request<Identity>('/auth/session', { signal })
    if (generation !== authGeneration()) throw new DOMException('会话已改变', 'AbortError')
    setCsrfToken(result.csrf_token)
    return result
  },
  logout: async () => {
    advanceAuthGeneration()
    await request<void>('/auth/logout', json({}))
    setCsrfToken(null)
  },
  workspaces: () => request<Workspace[]>('/workspaces'),
  createWorkspace: (name: string, root_path: string) =>
    request<Workspace>('/workspaces', json({ name, root_path })),
  threads: (id: string) => request<Thread[]>(`/workspaces/${id}/threads`),
  createThread: (id: string) => request<Thread>(`/workspaces/${id}/threads`, json({})),
  forkThread: (id: string, input: ThreadFork) =>
    request<Thread>(`/threads/${encodeURIComponent(id)}/fork`, json(input)),
  messages: (id: string, signal?: AbortSignal) =>
    request<Message[]>(`/threads/${id}/messages`, { signal }),
  activity: (id: string, before: string | null, signal?: AbortSignal) =>
    request<ActivityPage>(`/threads/${id}/activity${before ? `?before=${before}` : ''}`, {
      signal,
    }),
  activityDetails: (threadId: string, operationId: string, signal?: AbortSignal) =>
    request<ActivityDetails>(`/threads/${threadId}/activity/${operationId}`, { signal }),
  models: () => request<ModelConnection[]>('/model-connections'),
  createModel: (input: ModelInput) => request<ModelConnection>('/model-connections', json(input)),
  checkModel: (id: string) => request<ModelConnection>(`/model-connections/${id}/checks`, json({})),
  updateModel: (id: string, input: ModelUpdate) =>
    request<ModelConnection>(`/model-connections/${id}/configuration`, json(input)),
  updateCapabilities: (id: string, input: CapabilitiesUpdate) =>
    request<ModelConnection>(`/model-connections/${id}/capabilities`, json(input)),
  importCapabilities: (id: string) =>
    request<ModelConnection>(`/model-connections/${id}/capabilities/import-managed`, json({})),
  runs: (id: string) => request<Run[]>(`/threads/${id}/runs`),
  run: (id: string) => request<Run>(`/runs/${id}`),
  conversationSnapshot: (id: string, signal?: AbortSignal) =>
    request<{ run: Run; messages: Message[] }>(`/runs/${id}/conversation-snapshot`, { signal }),
  agents: (id: string) => request<AgentTree>(`/runs/${id}/agents`),
  trace: (id: string) => request<RunTrace>(`/runs/${id}/trace`),
  steer: (id: string, input: SteerRun) => request<InputReceived>(`/runs/${id}/inputs`, json(input)),
  startRun: (
    id: string,
    input: string,
    model_connection_id: string | null,
    request_id: string,
    attachments: AttachmentInput[] = [],
    options: RunOptions | null = null,
    expected_model: { connection_id: string; version: number } | null = null,
  ) =>
    request<Run>(
      `/threads/${id}/runs`,
      json({
        input,
        ...(model_connection_id ? { model_connection_id } : {}),
        request_id,
        attachments,
        options,
        expected_model,
      }),
    ),
  cancelRun: (id: string, request_id: string) =>
    request<Run>(`/runs/${id}/cancel`, json({ request_id })),
  answer: (question: Question, answer: QuestionAnswer['answer'], request_id: string) =>
    request<Question>(
      `/questions/${question.id}/answers`,
      json({ version: question.version, answer, request_id }),
    ),
  decide: (approval: Approval, approved: boolean, request_id: string) =>
    request<Approval>(
      `/approvals/${approval.id}/decisions`,
      json({ version: approval.version, approved, request_id }),
    ),
  systemStatus: () => request<SystemStatus>('/system/status'),
}
/** The server stream is replayable; reconnect uses the last processed sequence, never a new run. */
export function subscribeRun(
  id: string,
  after: number,
  onEvent: (event: RunEvent) => void,
  onConnection: (connected: boolean) => void,
  onGap: () => Promise<number>,
) {
  let source: EventSource | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let cursor = after
  function connect() {
    source = new EventSource(`/api/v1/runs/${id}/events?after=${cursor}`)
    source.onopen = () => onConnection(true)
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as RunEvent
        if (event.run_id !== id || event.seq <= cursor) return
        if (event.seq !== cursor + 1) {
          source?.close()
          onConnection(false)
          void onGap()
            .then((next) => {
              cursor = next
              if (!disposed) connect()
            })
            .catch(() => {
              if (!disposed) timer = setTimeout(connect, 2000)
            })
          return
        }
        cursor = event.seq
        onEvent(event)
      } catch {
        onConnection(false)
      }
    }
    source.onerror = () => {
      onConnection(false)
      source?.close()
      if (!disposed) timer = setTimeout(connect, 2000)
    }
  }
  connect()
  return () => {
    disposed = true
    source?.close()
    if (timer) clearTimeout(timer)
  }
}
