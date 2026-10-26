import { request } from './client'
import type {
  QueueView,
  QueueItem,
  DeletionPreview,
  DeletionState,
  Run,
  RunCreate,
  Thread,
  SteeringDelivery,
  QueueSteered,
} from './types'
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
const root = (id: string) => `/threads/${encodeURIComponent(id)}`
export const conversationsApi = {
  queue: (id: string) => request<QueueView>(`${root(id)}/queue`),
  enqueue: (id: string, input: RunCreate) => request<QueueItem>(`${root(id)}/queue`, post(input)),
  updateQueue: (
    id: string,
    item: QueueItem,
    changes: { input?: string; position?: number; remove?: boolean },
    request_id: string,
  ) =>
    request<QueueView>(
      `${root(id)}/queue/${encodeURIComponent(item.id)}`,
      post({ version: item.version, request_id, ...changes }),
    ),
  resumeQueue: (id: string, request_id: string) =>
    request<QueueView>(`${root(id)}/queue/resume`, post({ request_id })),
  steerQueue: (id: string, item: QueueItem, run_id: string, request_id: string) =>
    request<QueueSteered>(
      `${root(id)}/queue/${encodeURIComponent(item.id)}/steer`,
      post({ version: item.version, run_id, request_id }),
    ),
  rename: (thread: Thread, title: string, request_id: string) =>
    request<Thread>(
      `${root(thread.id)}/configuration`,
      post({ title, version: thread.version, request_id }),
    ),
  lifecycle: (
    thread: Thread,
    action: 'archive' | 'restore',
    stop_active: boolean,
    request_id: string,
  ) =>
    request<Thread>(
      `${root(thread.id)}/lifecycle`,
      post({ action, stop_active, version: thread.version, request_id }),
    ),
  deletionPreview: (id: string) => request<DeletionPreview>(`${root(id)}/deletion-preview`),
  delete: (id: string, preview_token: string, request_id: string) =>
    request<DeletionState>(`${root(id)}/deletion`, post({ preview_token, request_id })),
  deletion: (id: string) => request<DeletionState | null>(`${root(id)}/deletion`),
  edit: (id: string, message_id: string, input: RunCreate) =>
    request<{ thread: Thread; run: Run }>(`${root(id)}/edit`, post({ message_id, ...input })),
  inputs: (id: string) => request<SteeringDelivery[]>(`/runs/${encodeURIComponent(id)}/inputs`),
  resumeRun: (id: string, request_id: string, extend_budget = false) =>
    request<Run>(`/runs/${encodeURIComponent(id)}/resume`, post({ request_id, extend_budget })),
}
