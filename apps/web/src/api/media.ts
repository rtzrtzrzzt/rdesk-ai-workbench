import { request } from './client'
import type { components } from './schema'

export type MediaConfiguration = components['schemas']['MediaConfiguration']
export type MediaConnectionSave = components['schemas']['MediaConnectionSave']
export type MediaConnectionView = components['schemas']['MediaConnectionView']
export type MediaConnections = components['schemas']['MediaConnections']
export type MediaCreate = components['schemas']['MediaCreate']
export type MediaReference = components['schemas']['MediaReference']
export type MediaTaskView = components['schemas']['MediaTaskView']
export type MediaTaskList = components['schemas']['MediaTaskList']
export type MediaTaskDeletion = components['schemas']['MediaTaskDeletion']
export type MediaDeletionResult = components['schemas']['MediaDeletionResult']
export type MediaReconcile = components['schemas']['MediaReconcile']
export type MediaKind = MediaConnectionView['capability']
export type MediaDriver = MediaConnectionView['driver']

const root = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/media`
const post = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body) })
export const mediaApi = {
  connections: (workspaceId: string, signal?: AbortSignal) =>
    request<MediaConnections>(`${root(workspaceId)}/connections`, { signal }),
  saveConnection: (workspaceId: string, id: string, input: MediaConnectionSave) =>
    request<MediaConnectionView>(
      `${root(workspaceId)}/connections/${encodeURIComponent(id)}`,
      post(input),
    ),
  checkConnection: (workspaceId: string, id: string) =>
    request<MediaConnectionView>(
      `${root(workspaceId)}/connections/${encodeURIComponent(id)}/check`,
      post({}),
    ),
  tasks: (
    workspaceId: string,
    parentRunId?: string,
    signal?: AbortSignal,
    before?: string | null,
  ) => {
    const query = new URLSearchParams()
    if (parentRunId) query.set('parent_run_id', parentRunId)
    if (before) query.set('before', before)
    return request<MediaTaskList>(`${root(workspaceId)}/tasks${query.size ? `?${query}` : ''}`, {
      signal,
    })
  },
  create: (workspaceId: string, input: MediaCreate) =>
    request<MediaTaskView>(`${root(workspaceId)}/tasks`, post(input)),
  task: (workspaceId: string, runId: string, signal?: AbortSignal) =>
    request<MediaTaskView>(`${root(workspaceId)}/tasks/${encodeURIComponent(runId)}`, { signal }),
  cancel: (workspaceId: string, runId: string, requestId: string) =>
    request<MediaTaskView>(
      `${root(workspaceId)}/tasks/${encodeURIComponent(runId)}/cancel`,
      post({ request_id: requestId }),
    ),
  reconcile: (workspaceId: string, runId: string, input: MediaReconcile) =>
    request<MediaTaskView>(
      `${root(workspaceId)}/tasks/${encodeURIComponent(runId)}/reconcile`,
      post(input),
    ),
  deleteTasks: (workspaceId: string, input: MediaTaskDeletion) =>
    request<MediaDeletionResult>(`${root(workspaceId)}/tasks/deletion`, post(input)),
  receipt: (requestId: string) => request<unknown>(`/requests/${encodeURIComponent(requestId)}`),
}
